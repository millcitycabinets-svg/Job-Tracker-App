from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("owner", "designer", "crew", "rep")
JOB_STATUSES = ("Ordered", "Assembled", "Delivered", "Installed", "Punch", "Closed")
# Waterfall lines the owner can post ledger entries to (see waterfall.py for the full list).
LEDGER_LINES = (2, 4, 5, 6, 7, 8, 9, 10, 11)

Money = Numeric(12, 2)


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    hourly_rate: Mapped[Decimal | None] = mapped_column(Money)
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))  # 0.30 = 30%
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    customer: Mapped[str | None] = mapped_column(String(200))
    rep_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    manufacturer: Mapped[str | None] = mapped_column(String(100))
    invoice_total: Mapped[Decimal] = mapped_column(Money, default=0)
    countertop_invoice: Mapped[Decimal] = mapped_column(Money, default=0)
    material_total: Mapped[Decimal | None] = mapped_column(Money)  # None until the manufacturer total is known
    boxes: Mapped[int] = mapped_column(Integer, default=0)
    modifications: Mapped[int] = mapped_column(Integer, default=0)
    install_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="Ordered")
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    referral_amount: Mapped[Decimal] = mapped_column(Money, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rep: Mapped[User | None] = relationship()
    ledger: Mapped[list["LedgerEntry"]] = relationship(back_populates="job", order_by="LedgerEntry.id")
    labor: Mapped[list["LaborEntry"]] = relationship(back_populates="job", order_by="LaborEntry.id")
    assembly: Mapped[list["AssemblyEntry"]] = relationship(viewonly=True, order_by="AssemblyEntry.id")
    reimbursements: Mapped[list["Reimbursement"]] = relationship(viewonly=True, order_by="Reimbursement.id")


class LedgerEntry(Base):
    """A dollar event the owner posts to one waterfall line (change order, material, countertop, sub, ...)."""

    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    line: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Money)  # positive = adds to that line
    payee: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    entry_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    job: Mapped[Job] = relationship(back_populates="ledger")


class LaborEntry(Base):
    __tablename__ = "labor"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    work_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    rate: Mapped[Decimal] = mapped_column(Money)  # copied at entry so rate changes never rewrite history
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    job: Mapped[Job] = relationship(back_populates="labor")
    worker: Mapped[User] = relationship(foreign_keys=[worker_id])


# shortcut: assembly pay rates are fixed here; move to a settings screen if they start changing
BOX_RATE = Decimal("20")
MOD_RATE = Decimal("50")
# submitted -> approved -> paid, or submitted -> rejected. Only approved and paid entries count toward job costs.
TRANSITIONS = {"submitted": {"approved", "rejected"}, "approved": {"paid", "submitted"}, "paid": {"approved"}}
COUNTED = ("approved", "paid")


class AssemblyEntry(Base):
    __tablename__ = "assembly"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    work_date: Mapped[date] = mapped_column(Date)
    boxes: Mapped[int] = mapped_column(Integer, default=0)
    modifications: Mapped[int] = mapped_column(Integer, default=0)
    extra_cost: Mapped[Decimal] = mapped_column(Money, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    box_rate: Mapped[Decimal] = mapped_column(Money)  # copied at entry
    mod_rate: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    status_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship()
    worker: Mapped[User] = relationship()

    @property
    def total(self) -> Decimal:
        return self.boxes * self.box_rate + self.modifications * self.mod_rate + (self.extra_cost or 0)


class Reimbursement(Base):
    __tablename__ = "reimbursements"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Money)
    purpose: Mapped[str] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))  # None = overhead (fuel and the like)
    spent_on: Mapped[date] = mapped_column(Date)
    # Receipts live in Postgres so they're covered by the managed database's backups; App Platform disks are wiped on deploy.
    receipt: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    receipt_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    status_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job | None] = relationship()
    user: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    table_name: Mapped[str] = mapped_column(String(50))
    row_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(10))  # create / update / delete
    changes: Mapped[dict] = mapped_column(JSON)  # {field: [old, new]}
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
