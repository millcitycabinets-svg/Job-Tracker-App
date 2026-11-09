import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres@/jobtracker?host=/tmp/pg")
if DATABASE_URL.startswith("postgresql://"):  # DigitalOcean hands out plain postgresql:// URLs
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def sqlite_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
