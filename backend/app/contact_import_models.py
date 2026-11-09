"""Saved CSV previews and provenance; imports never assert permission to market."""
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now


class ContactImportBatch(Base):
    __tablename__ = "workspace_contact_imports"
    __table_args__ = (
        CheckConstraint("status IN ('preview', 'committed')", name="ck_contact_import_status"),
        CheckConstraint("visibility IN ('private', 'shared')", name="ck_contact_import_visibility"),
        Index("ix_contact_import_creator_created", "creator_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    filename: Mapped[str] = mapped_column(String(255))
    file_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="preview")
    columns: Mapped[list] = mapped_column(JSON)
    mapping: Mapped[dict] = mapped_column(JSON)
    suggested_mapping: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContactImportRow(Base):
    __tablename__ = "workspace_contact_import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_contact_import_row"),
        CheckConstraint("status IN ('valid', 'duplicate', 'invalid', 'created')", name="ck_contact_import_row_status"),
        Index("ix_contact_import_row_opportunity", "opportunity_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("workspace_contact_imports.id"))
    row_number: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))
    errors: Mapped[list] = mapped_column(JSON)
    duplicate_reason: Mapped[str | None] = mapped_column(String(200))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("workspace_opportunities.id"), unique=True)
    source_kind: Mapped[str] = mapped_column(String(20), default="csv", server_default="csv")
    lead_kind: Mapped[str] = mapped_column(String(20), default="prospect", server_default="prospect")
    marketing_consent: Mapped[str] = mapped_column(String(30), default="not_recorded", server_default="not_recorded")


class ContactImportLock(Base):
    """One row serializes imports across actors so a simultaneous import dedupes."""
    __tablename__ = "workspace_contact_import_lock"
    __table_args__ = (CheckConstraint("id = 1", name="ck_contact_import_lock_singleton"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
