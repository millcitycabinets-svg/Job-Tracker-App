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
assert {'workspace_opportunities', 'workspace_requests', 'workspace_commands', 'workspace_activities', 'workspace_preferences'} <= tables
with engine.connect() as conn:
    assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '0009'
    assert tuple(conn.execute(text('SELECT name, stage, operations_stage, operations_revision FROM workspace_opportunities WHERE id=1')).one()) == ('Existing won opportunity', 'Won', 'Prepping', 1)
    assert conn.scalar(text('SELECT COUNT(*) FROM workspace_requests WHERE opportunity_id=1')) == 1
    assert conn.scalar(text('SELECT body FROM workspace_activities WHERE opportunity_id=1')) == 'Preserve this design history'
    assert conn.scalar(text('PRAGMA foreign_keys')) == 1
command.upgrade(cfg, 'head')
assert before == snapshot(), 'Repeated startup migration changed existing records'
command.downgrade(cfg, '0007')
assert before == snapshot(), 'Workspace rollback changed existing records'
command.upgrade(cfg, 'head')
assert before == snapshot(), 'Workspace reapply changed existing records'
'''
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'migration.sqlite'}", "PYTHONPATH": str(backend)}
    result = subprocess.run([sys.executable, "-c", script], cwd=backend, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
