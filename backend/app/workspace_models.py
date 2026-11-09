"""Additive CRM records. Financial jobs remain in the existing Job Tracker tables."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .models import User, now


class Opportunity(Base):
    __tablename__ = "workspace_opportunities"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'shared')", name="ck_workspace_visibility"),
        CheckConstraint("revision > 0", name="ck_workspace_opportunity_revision"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_workspace_amount"),
        CheckConstraint("operations_stage IN ('Prepping', 'Ready to Order')", name="ck_workspace_preorder_stage"),
        CheckConstraint("operations_revision > 0", name="ck_workspace_preorder_revision"),
        Index("ix_workspace_opportunity_owner_stage", "owner_id", "stage"),
        Index("ix_workspace_opportunity_visibility", "visibility"),
        Index("ix_workspace_preorder_stage", "stage", "linked_job_id", "operations_stage"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    customer: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(500))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    stage: Mapped[str] = mapped_column(String(30), default="New Lead")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    next_follow_up: Mapped[date | None] = mapped_column(Date)
    work_scope: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    linked_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    operations_stage: Mapped[str] = mapped_column(String(30), default="Prepping", server_default="Prepping")
    operations_due_date: Mapped[date | None] = mapped_column(Date)
    operations_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    owner: Mapped[User] = relationship()


class WorkRequest(Base):
    __tablename__ = "workspace_requests"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "kind", "version", name="uq_workspace_request_version"),
        CheckConstraint("kind IN ('Design', 'Estimate')", name="ck_workspace_request_kind"),
        CheckConstraint("status IN ('Queued', 'In Progress', 'Needs Information', 'Complete')", name="ck_workspace_request_status"),
        CheckConstraint("version > 0 AND revision > 0", name="ck_workspace_request_revision"),
        Index("uq_workspace_open_request", "opportunity_id", "kind", unique=True,
              postgresql_where=text("status <> 'Complete'"), sqlite_where=text("status <> 'Complete'")),
        Index("ix_workspace_request_status_kind", "status", "kind"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("workspace_opportunities.id"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="Queued")
    context: Mapped[dict] = mapped_column(JSON)
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    result: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    assigned_to: Mapped[User | None] = relationship()
    opportunity: Mapped[Opportunity] = relationship()


class Activity(Base):
    __tablename__ = "workspace_activities"
    __table_args__ = (Index("ix_workspace_activity_opportunity_created", "opportunity_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("workspace_opportunities.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor: Mapped[User] = relationship()


class CommandReceipt(Base):
    __tablename__ = "workspace_commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    command_id: Mapped[str] = mapped_column(String(160), unique=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_role: Mapped[str] = mapped_column(String(20))
    operation: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Preference(Base):
    __tablename__ = "workspace_preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
