"""Bring the database schema up to date. Runs on every app start.

Schema changes: edit models.py, then from backend/ run
    alembic revision --autogenerate -m "what changed"
review the new file in migrations/versions/, and commit it. The next deploy applies it.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .db import engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def upgrade():
    cfg = Config(str(ALEMBIC_INI))
    tables = set(inspect(engine).get_table_names())
    if "jobs" in tables and "alembic_version" not in tables:
        # Database was created before migrations existed: mark where it stands, then upgrade from there.
        command.stamp(cfg, "0002" if "payments" in tables else "0001")
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    upgrade()
