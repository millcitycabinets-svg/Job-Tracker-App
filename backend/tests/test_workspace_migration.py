"""Rehearse additive upgrades in a subprocess with a disposable SQLite database."""
import os
from pathlib import Path
import subprocess
import sys


def test_workspace_upgrade_preserves_existing_finance_records(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    script = r'''
from datetime import date
from decimal import Decimal
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from app import models
from app.db import SessionLocal, engine
from app.migrate import ALEMBIC_INI
from app.models import User, Job, Payment, Delivery

cfg = Config(str(ALEMBIC_INI))
command.upgrade(cfg, '0006')
# Exercise the SQLite-compatible 0007 table alteration with actual delivery rows.
with engine.begin() as conn:
    conn.execute(text("INSERT INTO users (id, name, email, password_hash, role, active, pay_type) VALUES (1, 'Migration test', 'migration@example.test', 'unusable', 'owner', 1, 'hourly')"))
    conn.execute(text("INSERT INTO jobs (id, name, customer, rep_id, invoice_total, countertop_invoice, material_total, boxes, modifications, status, frozen, referral_amount, created_at) VALUES (1, 'Migration sample', 'Sample', 1, 2000, 0, 700, 10, 0, 'Ordered', 0, 0, CURRENT_TIMESTAMP)"))
    conn.execute(text("INSERT INTO deliveries (id, job_id, driver_id, delivered_on, created_by, created_at) VALUES (1, 1, 1, '2026-11-09', 1, CURRENT_TIMESTAMP)"))
command.upgrade(cfg, '0007')
with SessionLocal() as db:
    db.add(Payment(job_id=1, amount=Decimal('600'), method='ach', status='received',
                   received_date=date(2026, 11, 9), created_by=1))
    db.commit()
legacy = sorted(inspect(engine).get_table_names())
legacy.remove('alembic_version')
def snapshot():
    with engine.connect() as conn:
        return {name: list(conn.execute(text('SELECT * FROM "' + name + '" ORDER BY id'))) for name in legacy}
before = snapshot()
command.upgrade(cfg, '0008')
with engine.begin() as conn:
    conn.execute(text("INSERT INTO workspace_opportunities (id, name, customer, owner_id, visibility, stage, revision, created_at, updated_at) VALUES (1, 'Existing won opportunity', 'Test customer', 1, 'private', 'Won', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    conn.execute(text("INSERT INTO workspace_requests (id, opportunity_id, kind, status, context, version, revision, created_at, updated_at) VALUES (1, 1, 'Design', 'Queued', '{}', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    conn.execute(text("INSERT INTO workspace_activities (id, opportunity_id, actor_id, kind, body, created_at) VALUES (1, 1, 1, 'Note', 'Preserve this design history', CURRENT_TIMESTAMP)"))
command.upgrade(cfg, 'head')
assert before == snapshot(), 'Workspace upgrade changed existing records'
tables = set(inspect(engine).get_table_names())
assert {'workspace_opportunities', 'workspace_requests', 'workspace_commands', 'workspace_activities', 'workspace_preferences',
        'workspace_contact_imports', 'workspace_contact_import_rows', 'workspace_contact_import_lock',
        'workspace_monday_sources', 'workspace_mailboxes', 'workspace_communications', 'workspace_campaigns', 'workspace_oauth_attempts', 'workspace_contact_permissions'} <= tables
with engine.connect() as conn:
    assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '0017'
    assert tuple(conn.execute(text('SELECT name, stage, operations_stage, operations_revision FROM workspace_opportunities WHERE id=1')).one()) == ('Existing won opportunity', 'Won', 'Prepping', 1)
    assert conn.scalar(text('SELECT COUNT(*) FROM workspace_requests WHERE opportunity_id=1')) == 1
    assert conn.scalar(text('SELECT body FROM workspace_activities WHERE opportunity_id=1')) == 'Preserve this design history'
    assert conn.scalar(text('PRAGMA foreign_keys')) == 1
    assert conn.scalar(text('SELECT COUNT(*) FROM workspace_contact_import_lock WHERE id=1')) == 1
    assert list(conn.execute(text('PRAGMA foreign_key_check'))) == []
# Existing previews and imported provenance must also survive repeated startup.
from app.contact_import_models import ContactImportBatch, ContactImportRow
from app.monday_import import MondaySource
from app.models import now
with SessionLocal() as db:
    batch = ContactImportBatch(creator_id=1, owner_id=1, visibility='private', filename='test.csv',
        file_digest='a' * 64, status='committed', columns=['name', 'email'],
        mapping={'name': 'name', 'email': 'email'}, suggested_mapping={},
        summary={'total_rows': 1}, errors=[], result={'created_count': 1}, committed_at=now())
    db.add(batch)
    db.flush()
    db.add(ContactImportRow(batch_id=batch.id, row_number=2, data={'name': 'Existing won opportunity'},
        status='created', errors=[], opportunity_id=1))
    db.add(MondaySource(board_id='test-board', item_id='test-item', opportunity_id=1,
        source_url='https://example.test/1', source_group_id='test-group', source_group_title='Design',
        source_status='Design', source_fingerprint='b' * 64, source_snapshot={'preserve': True},
        owner_review=False, sales_review=False, import_target='disposable-test',
        backup_reference='test-fixture', imported_by=1))
    db.commit()
with engine.connect() as conn:
    import_before = {name: list(conn.execute(text('SELECT * FROM "' + name + '" ORDER BY id')))
                    for name in ('workspace_contact_imports', 'workspace_contact_import_rows', 'workspace_monday_sources')}
command.upgrade(cfg, 'head')
assert before == snapshot(), 'Repeated startup migration changed existing records'
with engine.connect() as conn:
    for name, saved in import_before.items():
        assert saved == list(conn.execute(text('SELECT * FROM "' + name + '" ORDER BY id'))), name

command.downgrade(cfg, '0007')
assert before == snapshot(), 'Workspace rollback changed existing records'
command.upgrade(cfg, 'head')
assert before == snapshot(), 'Workspace reapply changed existing records'
'''
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'migration.sqlite'}", "PYTHONPATH": str(backend)}
    result = subprocess.run([sys.executable, "-c", script], cwd=backend, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_supplied_migration_connection_preserves_caller_rollback(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    script = r'''
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from app.db import engine
from app.migrate import ALEMBIC_INI

cfg = Config(str(ALEMBIC_INI))
command.upgrade(cfg, '0009')
with engine.begin() as conn:
    conn.execute(text("INSERT INTO users (id, name, email, password_hash, role, active, pay_type) VALUES (1, 'Outside transaction', 'rollback@example.test', 'unusable', 'owner', 1, 'hourly')"))
original_tables = set(inspect(engine).get_table_names())
# Do not let Alembic silently create and commit its own transaction for a
# caller which forgot to begin the rollback-only rehearsal.
with engine.connect() as conn:
    cfg.attributes['connection'] = conn
    try:
        command.upgrade(cfg, 'head')
    except RuntimeError as exc:
        assert 'active caller-owned transaction' in str(exc)
    else:
        raise AssertionError('An unbegun caller connection was accepted')
with engine.connect() as conn:
    transaction = conn.begin()
    # sqlite3's legacy mode does not emit BEGIN for DDL. Explicit BEGIN makes
    # this a real rollback rehearsal instead of a false virtual transaction.
    conn.exec_driver_sql('BEGIN')
    conn.execute(text("UPDATE users SET name='Inside transaction' WHERE id=1"))
    cfg.attributes['connection'] = conn
    command.upgrade(cfg, 'head')
    assert transaction.is_active and conn.in_transaction(), 'Alembic ended the caller transaction'
    assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '0017'
    assert 'workspace_contact_imports' in inspect(conn).get_table_names()
    assert 'workspace_monday_sources' in inspect(conn).get_table_names()
    transaction.rollback()
assert set(inspect(engine).get_table_names()) == original_tables, 'DDL escaped caller rollback'
with engine.connect() as conn:
    assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '0009'
    assert conn.scalar(text('SELECT name FROM users WHERE id=1')) == 'Outside transaction'
    assert list(conn.execute(text('PRAGMA foreign_key_check'))) == []
# A caller which deliberately commits can persist the same upgrade.
with engine.begin() as conn:
    cfg.attributes['connection'] = conn
    command.upgrade(cfg, 'head')
with engine.connect() as conn:
    assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '0017'
    assert conn.scalar(text('SELECT COUNT(*) FROM workspace_contact_import_lock')) == 1
'''
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'rollback.sqlite'}", "PYTHONPATH": str(backend)}
    result = subprocess.run([sys.executable, "-c", script], cwd=backend, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
