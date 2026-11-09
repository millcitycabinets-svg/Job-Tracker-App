"""Read-only, consistent PostgreSQL data backup for a release.

Run inside the existing app container. Output contains customer data and password
hashes: keep the archive private. Restore into the schema at `source_revision`
using PostgreSQL COPY FROM ... FORMAT BINARY; never restore over a live database.
Schema migrations at `source_commit` are the schema definition for this archive.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import psycopg
from psycopg import sql


def backup(destination, source_commit):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = os.environ["DATABASE_URL"].strip().replace("postgresql+psycopg://", "postgresql://", 1)
    manifest = {"format": "postgresql-binary-copy-v1", "source_commit": source_commit,
                "created_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    # open with restrictive permissions before writing any sensitive bytes
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output, psycopg.connect(url) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        manifest["source_revision"] = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        manifest["postgres_version"] = db.execute("SHOW server_version").fetchone()[0]
        tables = db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for (table,) in tables:
                identifier = sql.Identifier("public", table)
                count = db.execute(sql.SQL("SELECT count(*) FROM {}").format(identifier)).fetchone()[0]
                digest = hashlib.sha256()
                size = 0
                with archive.open(f"tables/{table}.copy", "w") as target:
                    with db.cursor().copy(sql.SQL("COPY {} TO STDOUT WITH (FORMAT BINARY)").format(identifier)) as stream:
                        for block in stream:
                            data = bytes(block)
                            digest.update(data)
                            size += len(data)
                            target.write(data)
                manifest["tables"][table] = {"rows": count, "bytes": size, "sha256": digest.hexdigest()}
            manifest["sequences"] = {}
            for (name,) in db.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public'").fetchall():
                value, called = db.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(sql.Identifier("public", name))).fetchone()
                manifest["sequences"][name] = {"last_value": value, "is_called": called}
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        db.rollback()
    data = destination.read_bytes()
    print(json.dumps({"path": str(destination), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "revision": manifest["source_revision"], "tables": {k: v["rows"] for k, v in manifest["tables"].items()}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    backup(args.destination, args.source_commit)
