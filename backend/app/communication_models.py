"""Communications are additive; no Job Tracker financial tables are changed."""
from datetime import datetime
from sqlalchemy import String, Text, ForeignKey, DateTime, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .models import now


class Mailbox(Base):
    __tablename__ = 'workspace_mailboxes'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    encrypted_token: Mapped[str] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OAuthAttempt(Base):
    __tablename__ = 'workspace_oauth_attempts'
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    expires: Mapped[int] = mapped_column()


class Communication(Base):
    __tablename__ = 'workspace_communications'
    __table_args__ = (UniqueConstraint('actor_id', 'command_id', name='uq_communication_command'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey('workspace_opportunities.id'), index=True)
    command_id: Mapped[str] = mapped_column(String(160))
    channel: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(20), default='outbound')
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(200), default='')
    body: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(30), default='draft')
    provider_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContactPermission(Base):
    __tablename__ = 'workspace_contact_permissions'
    __table_args__ = (UniqueConstraint('channel', 'address', name='uq_contact_permission'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Campaign(Base):
    __tablename__ = 'workspace_campaigns'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    audience: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    opportunity_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
