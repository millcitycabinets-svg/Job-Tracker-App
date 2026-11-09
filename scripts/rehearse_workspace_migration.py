#!/usr/bin/env python3
"""PostgreSQL-only, rollback-only rehearsal of schema 0007 -> 0011.

Never deploys or commits. DATABASE_URL is read only from the host environment.
Output contains table counts and SHA-256 digests, never customer rows or secrets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

EXPECTED_REVISION = "0007"
TARGET_REVISION = "0011"
ADVISORY_LOCK = 18383996445


class RehearsalError(RuntimeError):
    """Messages are fixed local check descriptions and contain no database data."""


def revisions(connection, schema):
    quote = connection.dialect.identifier_preparer.quote
    return list(connection.scalars(text(
        f"SELECT version_num FROM {quote(schema)}.alembic_version ORDER BY version_num"
    )))


def workspace_tables(connection, schema):
    return sorted(name for name in inspect(connection).get_table_names(schema=schema) if name.startswith("workspace_"))


def snapshot_tables(connection, schema, tables):
    """Hash ordered canonical JSON rows in memory without printing or saving rows."""
    quote = connection.dialect.identifier_preparer.quote
    inspector = inspect(connection)
    snapshots = {}
    for table in sorted(tables):
        key = inspector.get_pk_constraint(table, schema=schema).get("constrained_columns") or []
        ordering = ", ".join(f"t.{quote(column)}" for column in key) or "to_jsonb(t)::text"
        statement = text(
            f"SELECT to_jsonb(t)::text AS snapshot_row FROM {quote(schema)}.{quote(table)} AS t ORDER BY {ordering}"
        ).execution_options(stream_results=True)
        digest, count = hashlib.sha256(), 0
        result = connection.execute(statement)
        try:
            for row in result:
                encoded = row[0].encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                count += 1
        finally:
            result.close()
        snapshots[table] = {"count": count, "sha256": digest.hexdigest()}
    return snapshots


def set_limits(connection):
    connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
    connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")


def safe_failure(exc):
    details = {"type": type(exc).__name__}
    if isinstance(exc, RehearsalError):
        details["check"] = str(exc)
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate:
        details["sqlstate"] = sqlstate
    return details


def run_rehearsal(database_url):
    url = make_url(database_url.strip())
    if url.get_backend_name() != "postgresql":
        raise RehearsalError("PostgreSQL DATABASE_URL is required; other databases are refused")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    scripts = ScriptDirectory.from_config(cfg)
    if scripts.get_heads() != [TARGET_REVISION]:
        raise RehearsalError("Bundled migration head must be exactly 0011")
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True,
                           connect_args={"connect_timeout": 10})
    report = {"mode": "rollback-only", "expected_revision": EXPECTED_REVISION,
              "target_revision": TARGET_REVISION, "isolation": "REPEATABLE READ",
              "lock_timeout_seconds": 5, "statement_timeout_seconds": 30,
              "advisory_transaction_lock": ADVISORY_LOCK, "status": "failed"}
    schema, failure = None, None
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            transaction = connection.begin()

            def refuse_commit(_connection):
                raise RehearsalError("Commit attempted during rollback-only rehearsal")

            def refuse_sql_commit(_conn, _cursor, statement, _parameters, _context, _executemany):
                # Supplied-connection Alembic must preserve the caller's transaction.
                # Catch ordinary SQL COMMIT/END as well as Connection.commit().
                if re.match(r"^\s*(COMMIT|END|PREPARE\s+TRANSACTION)\b", statement, re.IGNORECASE):
                    raise RehearsalError("Transaction-ending SQL attempted during rehearsal")

            event.listen(connection, "commit", refuse_commit)
            event.listen(connection, "before_cursor_execute", refuse_sql_commit)
            try:
                set_limits(connection)
                connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK})
                schema = connection.scalar(text("SELECT current_schema()"))
                if not schema:
                    raise RehearsalError("No active schema is available")
                report["server_version"] = connection.scalar(text("SHOW server_version"))
                report["before_revision"] = revisions(connection, schema)
                if report["before_revision"] != [EXPECTED_REVISION]:
                    raise RehearsalError("Target must start at exactly revision 0007")
                if workspace_tables(connection, schema):
                    raise RehearsalError("Workspace tables already exist before rehearsal")
                tables = sorted(name for name in inspect(connection).get_table_names(schema=schema)
                                if name != "alembic_version")
                if not {"users", "jobs", "payments", "ledger"}.issubset(tables):
                    raise RehearsalError("Expected legacy Job Tracker tables are missing")
                transaction_id = connection.scalar(text("SELECT txid_current()"))
                report["before_snapshot"] = snapshot_tables(connection, schema, tables)
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, TARGET_REVISION)
                if not transaction.is_active or not connection.in_transaction():
                    raise RehearsalError("Migration ended the caller-owned transaction")
                if connection.scalar(text("SELECT txid_current()")) != transaction_id:
                    raise RehearsalError("Migration changed the caller transaction identity")
                report["inside_revision"] = revisions(connection, schema)
                if report["inside_revision"] != [TARGET_REVISION]:
                    raise RehearsalError("Rehearsal did not reach exactly revision 0011")
                report["inside_workspace_tables"] = workspace_tables(connection, schema)
                expected_tables = {"workspace_opportunities", "workspace_requests", "workspace_activities",
                                   "workspace_commands", "workspace_preferences", "workspace_contact_imports",
                                   "workspace_contact_import_rows", "workspace_contact_import_lock", "workspace_monday_sources"}
                if not expected_tables.issubset(report["inside_workspace_tables"]):
                    raise RehearsalError("Expected workspace tables are missing inside rehearsal")
                report["after_ddl_snapshot"] = snapshot_tables(connection, schema, tables)
                report["legacy_rows_unchanged"] = report["before_snapshot"] == report["after_ddl_snapshot"]
                if not report["legacy_rows_unchanged"]:
                    raise RehearsalError("Legacy row hashes or counts changed during schema migration")
                report["ddl_rehearsal_passed"] = True
            except BaseException as exc:
                failure = exc
                report["failure"] = safe_failure(exc)
            finally:
                # There is intentionally no success branch that commits.
                try:
                    if transaction.is_active:
                        transaction.rollback()
                    else:
                        connection.rollback()
                    report["rollback_called"] = True
                except BaseException as exc:
                    failure = failure or exc
                    report["rollback_failure"] = safe_failure(exc)
                finally:
                    cfg.attributes.pop("connection", None)
                    event.remove(connection, "commit", refuse_commit)
                    event.remove(connection, "before_cursor_execute", refuse_sql_commit)
        # NullPool guarantees a new DBAPI/server connection after the rehearsal.
        if schema is not None:
            with engine.connect().execution_options(isolation_level="REPEATABLE READ") as fresh:
                verification = fresh.begin()
                try:
                    set_limits(fresh)
                    report["after_rollback_revision"] = revisions(fresh, schema)
                    report["after_rollback_workspace_tables"] = workspace_tables(fresh, schema)
                    report["rollback_verified"] = (
                        report["after_rollback_revision"] == [EXPECTED_REVISION]
                        and report["after_rollback_workspace_tables"] == []
                    )
                    if not report["rollback_verified"]:
                        raise RehearsalError("Fresh connection did not confirm original schema after rollback")
                except BaseException as exc:
                    failure = failure or exc
                    report["verification_failure"] = safe_failure(exc)
                finally:
                    verification.rollback()
        if failure is None and report.get("ddl_rehearsal_passed") and report.get("rollback_verified"):
            report["status"] = "passed"
        return report
    except BaseException as exc:
        report["failure"] = safe_failure(exc)
        return report
    finally:
        engine.dispose()


def write_report(path, report):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(report, handle, indent=2)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", help="Optional private JSON report file; output is also printed")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("An explicit PostgreSQL DATABASE_URL in the host environment is required")
    try:
        report = run_rehearsal(database_url)
    except BaseException as exc:
        report = {"mode": "rollback-only", "status": "failed", "failure": safe_failure(exc)}
    if args.report:
        write_report(args.report, report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
