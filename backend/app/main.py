import hmac
import os
import re
import time
from difflib import SequenceMatcher
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from . import audit, inbox, migrate  # noqa: F401  (audit registers its listener)
from .auth import check_password, current_user, hash_password, owner_only, require
from .db import get_db
from .models import (BOX_RATE, JOB_STATUSES, LEDGER_LINES, MOD_RATE, PAY_TYPES, ROLES, TRANSITIONS, AssemblyEntry, AuditLog,
                     Delivery, Job, PAYMENT_METHODS, PAYMENT_TYPES, DesignEntry, IntakeEmail, LaborEntry, LedgerEntry, Payment, Reimbursement, User)
from .waterfall import compute, labor_total, money


@asynccontextmanager
async def lifespan(_app):
    migrate.upgrade()
    yield


HTTPS_ONLY = os.environ.get("HTTPS_ONLY", "0") == "1"
if HTTPS_ONLY and not os.environ.get("SESSION_SECRET"):
    raise RuntimeError("SESSION_SECRET must be set in production")

app = FastAPI(title="Mill City Job Tracker", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-only-secret"),
    https_only=HTTPS_ONLY,
    same_site="lax",
    max_age=30 * 24 * 3600,
)


def get_or_404(db: Session, model, id_: int):
    obj = db.get(model, id_)
    if not obj:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


# ---------- auth ----------

class LoginIn(BaseModel):
    email: str
    password: str


def user_out(u: User, with_pay: bool = False):
    out = {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "active": u.active, "pay_type": u.pay_type}
    if with_pay:
        out["hourly_rate"] = str(u.hourly_rate) if u.hourly_rate is not None else None
        out["box_rate"] = str(u.box_rate if u.box_rate is not None else BOX_RATE)
        out["commission_rate"] = str(u.commission_rate) if u.commission_rate is not None else None
    return out


# shortcut: failed-login counts live in memory, so they reset on deploy and assume one app instance; move to the database if you scale out
FAILED_LOGINS: dict[str, list[float]] = {}
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60


@app.post("/api/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    cutoff = time.monotonic() - LOCKOUT_SECONDS
    recent = [t for t in FAILED_LOGINS.get(email, []) if t > cutoff]
    if len(recent) >= MAX_FAILED_LOGINS:
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.active or not check_password(body.password, user.password_hash):
        FAILED_LOGINS[email] = recent + [time.monotonic()]
        raise HTTPException(401, "Wrong email or password")
    FAILED_LOGINS.pop(email, None)
    request.session["user_id"] = user.id
    return user_out(user)


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


# ---------- users (owner) ----------

class UserIn(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    role: str
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    commission_rate: Decimal | None = Field(default=None, ge=0, le=1)
    pay_type: str = "hourly"
    box_rate: Decimal | None = Field(default=None, ge=0)


class UserPatch(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: str | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    commission_rate: Decimal | None = Field(default=None, ge=0, le=1)
    active: bool | None = None
    pay_type: str | None = None
    box_rate: Decimal | None = Field(default=None, ge=0)


def check_role(role, pay_type=None):
    if role is not None and role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if pay_type is not None and pay_type not in PAY_TYPES:
        raise HTTPException(422, f"pay_type must be one of {PAY_TYPES}")


@app.get("/api/users")
def list_users(db: Session = Depends(get_db), _=Depends(owner_only)):
    return [user_out(u, with_pay=True) for u in db.scalars(select(User).order_by(User.name))]


@app.post("/api/users")
def create_user(body: UserIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    check_role(body.role, body.pay_type)
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "That email already has a login")
    u = User(name=body.name.strip(), email=email, password_hash=hash_password(body.password), role=body.role,
             hourly_rate=body.hourly_rate, commission_rate=body.commission_rate,
             pay_type=body.pay_type, box_rate=body.box_rate)
    db.add(u)
    db.commit()
    return user_out(u, with_pay=True)


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, db: Session = Depends(get_db), _=Depends(owner_only)):
    u = get_or_404(db, User, user_id)
    data = body.model_dump(exclude_unset=True)
    check_role(data.get("role"), data.get("pay_type"))
    if "password" in data:
        u.password_hash = hash_password(data.pop("password"))
    for k, v in data.items():
        setattr(u, k, v)
    db.commit()
    return user_out(u, with_pay=True)


# ---------- jobs (owner) ----------

class JobIn(BaseModel):
    name: str = Field(min_length=1)
    customer: str | None = None
    rep_id: int | None = None
    manufacturer: str | None = None
    invoice_total: Decimal = Field(default=Decimal(0), ge=0)
    countertop_invoice: Decimal = Field(default=Decimal(0), ge=0)
    material_total: Decimal | None = Field(default=None, ge=0)
    boxes: int = Field(default=0, ge=0)
    modifications: int = Field(default=0, ge=0)
    install_date: date | None = None
    status: str = "Ordered"
    frozen: bool = False
    referral_amount: Decimal = Field(default=Decimal(0), ge=0)


class JobPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    customer: str | None = None
    rep_id: int | None = None
    manufacturer: str | None = None
    invoice_total: Decimal | None = Field(default=None, ge=0)
    countertop_invoice: Decimal | None = Field(default=None, ge=0)
    material_total: Decimal | None = Field(default=None, ge=0)
    boxes: int | None = Field(default=None, ge=0)
    modifications: int | None = Field(default=None, ge=0)
    install_date: date | None = None
    status: str | None = None
    frozen: bool | None = None
    referral_amount: Decimal | None = Field(default=None, ge=0)


def check_job_fields(db: Session, data: dict, job_id: int | None = None):
    if data.get("status") is not None and data["status"] not in JOB_STATUSES:
        raise HTTPException(422, f"status must be one of {JOB_STATUSES}")
    if data.get("rep_id") is not None:
        rep = db.get(User, data["rep_id"])
        if not rep or rep.role not in ("rep", "owner"):  # the owner sells too
            raise HTTPException(422, "rep_id must be a sales rep or the owner")
    if data.get("name"):
        clash = db.scalar(select(Job).where(Job.name == data["name"].strip()))
        if clash and clash.id != job_id:
            raise HTTPException(409, "A job with that name already exists")


def load_job(db: Session, job_id: int) -> Job:
    job = db.scalar(
        select(Job).where(Job.id == job_id)
        .options(selectinload(Job.ledger), selectinload(Job.labor).selectinload(LaborEntry.worker), selectinload(Job.rep),
                 selectinload(Job.assembly).selectinload(AssemblyEntry.worker),
                 selectinload(Job.reimbursements).selectinload(Reimbursement.user),
                 selectinload(Job.designs).selectinload(DesignEntry.designer), selectinload(Job.payments))
    )
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def job_fields(job: Job):
    out = {c: getattr(job, c) for c in (
        "id", "name", "customer", "rep_id", "manufacturer", "boxes", "modifications", "install_date",
        "status", "frozen", "created_at", "closed_at", "commission_paid_at")}
    for c in ("invoice_total", "countertop_invoice", "material_total", "referral_amount"):
        v = getattr(job, c)
        out[c] = str(v) if v is not None else None
    out["rep_name"] = job.rep.name if job.rep else None
    return out


def job_out(job: Job):
    w = compute(job)
    return {
        **job_fields(job),
        "waterfall": w,
        "ledger": [
            {"id": e.id, "line": e.line, "amount": str(e.amount), "payee": e.payee, "note": e.note,
             "entry_date": e.entry_date} for e in job.ledger
        ],
        "labor": [
            {"id": e.id, "worker": e.worker.name, "work_date": e.work_date, "hours": str(e.hours),
             "rate": str(e.rate), "total": str(labor_total(e))} for e in job.labor
        ],
        "assembly": [assembly_out(e) for e in job.assembly],
        "reimbursements": [reimbursement_out(r) for r in job.reimbursements],
        "designs": [design_out(d) for d in job.designs],
        "payments": [payment_out(p) for p in job.payments],
    }


@app.get("/api/jobs")
def list_jobs(status: str | None = None, rep_id: int | None = None, db: Session = Depends(get_db),
              _=Depends(owner_only)):
    q = select(Job).options(selectinload(Job.ledger), selectinload(Job.labor), selectinload(Job.rep),
                            selectinload(Job.assembly), selectinload(Job.reimbursements), selectinload(Job.designs),
                            selectinload(Job.payments))
    if status:
        q = q.where(Job.status == status)
    if rep_id:
        q = q.where(Job.rep_id == rep_id)
    out = []
    for job in db.scalars(q.order_by(Job.created_at.desc())):
        w = compute(job)
        out.append({**job_fields(job), "adjusted_revenue": w["lines"][2]["amount"], "job_profit": w["job_profit"],
                    "net": w["net"], "margin_pct": w["margin_pct"], "material_pending": w["material_pending"],
                    "balance_due": w["balance_due"]})
    return out


@app.post("/api/jobs")
def create_job(body: JobIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    data = body.model_dump()
    check_job_fields(db, data)
    data["name"] = data["name"].strip()
    job = Job(**data)
    db.add(job)
    db.commit()
    return job_out(load_job(db, job.id))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), _=Depends(owner_only)):
    return job_out(load_job(db, job_id))


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, body: JobPatch, db: Session = Depends(get_db), _=Depends(owner_only)):
    job = get_or_404(db, Job, job_id)
    data = body.model_dump(exclude_unset=True)
    check_job_fields(db, data, job_id)
    if data.get("status") == "Closed" and job.status != "Closed":
        job.closed_at = datetime.now(timezone.utc)
    elif "status" in data and data["status"] != "Closed":
        job.closed_at = None
    for k, v in data.items():
        setattr(job, k, v.strip() if k == "name" else v)
    db.commit()
    return job_out(load_job(db, job_id))


# ---------- ledger (owner) ----------

class LedgerIn(BaseModel):
    line: int
    amount: Decimal
    payee: str | None = None
    note: str | None = None
    entry_date: date | None = None


class LedgerPatch(BaseModel):
    line: int | None = None
    amount: Decimal | None = None
    payee: str | None = None
    note: str | None = None
    entry_date: date | None = None


def check_ledger(data: dict):
    if data.get("line") is not None and data["line"] not in LEDGER_LINES:
        raise HTTPException(422, f"line must be one of {LEDGER_LINES}")
    if data.get("amount") is not None and data["amount"] == 0:
        raise HTTPException(422, "amount can't be zero")
    if data.get("line") == 11 and not (data.get("note") or "").strip():
        raise HTTPException(422, "Unexpected costs need a reason in the note")


@app.post("/api/jobs/{job_id}/ledger")
def add_ledger(job_id: int, body: LedgerIn, db: Session = Depends(get_db), user: User = Depends(owner_only)):
    get_or_404(db, Job, job_id)
    data = body.model_dump()
    check_ledger(data)
    data["entry_date"] = data["entry_date"] or date.today()
    db.add(LedgerEntry(job_id=job_id, created_by=user.id, **data))
    db.commit()
    return job_out(load_job(db, job_id))


@app.patch("/api/ledger/{entry_id}")
def update_ledger(entry_id: int, body: LedgerPatch, db: Session = Depends(get_db), _=Depends(owner_only)):
    e = get_or_404(db, LedgerEntry, entry_id)
    data = body.model_dump(exclude_unset=True)
    check_ledger({"line": e.line, "note": e.note, **data})
    for k, v in data.items():
        setattr(e, k, v)
    db.commit()
    return job_out(load_job(db, e.job_id))


@app.delete("/api/ledger/{entry_id}")
def delete_ledger(entry_id: int, db: Session = Depends(get_db), _=Depends(owner_only)):
    e = get_or_404(db, LedgerEntry, entry_id)
    job_id = e.job_id
    db.delete(e)
    db.commit()
    return job_out(load_job(db, job_id))


# ---------- job names (everyone logged in; names only) ----------

@app.get("/api/job-names")
def job_names(db: Session = Depends(get_db), _=Depends(current_user)):
    jobs = db.scalars(select(Job).where(Job.status != "Closed").order_by(Job.name))
    return [{"id": j.id, "name": j.name, "boxes": j.boxes, "modifications": j.modifications} for j in jobs]


# ---------- labor (crew logs their own; owner logs anyone's) ----------

class LaborIn(BaseModel):
    job_id: int
    hours: Decimal = Field(gt=0, le=24)
    work_date: date | None = None
    worker_id: int | None = None  # owner only


class LaborPatch(BaseModel):
    job_id: int | None = None
    hours: Decimal | None = Field(default=None, gt=0, le=24)
    work_date: date | None = None


def crew_view(e: LaborEntry, user: User):
    """A worker sees their own pay on their own hours; nothing about the job's money."""
    return {"id": e.id, "job_id": e.job_id, "job": e.job.name, "worker": e.worker.name, "work_date": e.work_date,
            "hours": str(e.hours), "total": str(labor_total(e)), "status": e.status, "status_note": e.status_note,
            "editable": user.role == "owner" or e.status == "submitted"}


def labor_for_edit(db: Session, entry_id: int, user: User) -> LaborEntry:
    e = get_or_404(db, LaborEntry, entry_id)
    if user.role != "owner":
        if e.worker_id != user.id:
            raise HTTPException(404, "LaborEntry not found")
        if e.status != "submitted":
            raise HTTPException(403, "This entry is already approved. Ask Skyler to change it.")
    return e


def open_job(db: Session, job_id: int) -> Job:
    job = get_or_404(db, Job, job_id)
    if job.status == "Closed":
        raise HTTPException(422, "That job is closed")
    return job


@app.post("/api/labor")
def add_labor(body: LaborIn, db: Session = Depends(get_db), user: User = Depends(require("owner", "crew"))):
    open_job(db, body.job_id)
    if user.role == "owner":
        if not body.worker_id:
            raise HTTPException(422, "Pick who worked")
        worker = get_or_404(db, User, body.worker_id)
    else:
        worker = user  # crew can only log their own hours
    if worker.pay_type == "salary":
        raise HTTPException(422, f"{worker.name} is salaried; hours aren't tracked")
    if worker.hourly_rate is None:
        raise HTTPException(422, f"{worker.name} has no hourly rate set")
    e = LaborEntry(job_id=body.job_id, worker_id=worker.id, work_date=body.work_date or date.today(),
                   hours=body.hours, rate=worker.hourly_rate, created_by=user.id,
                   status="approved" if user.role == "owner" else "submitted")
    db.add(e)
    db.commit()
    return crew_view(e, user)


@app.get("/api/labor/mine")
def my_labor(db: Session = Depends(get_db), user: User = Depends(current_user)):
    entries = db.scalars(select(LaborEntry).where(LaborEntry.worker_id == user.id)
                         .options(selectinload(LaborEntry.job)).order_by(LaborEntry.work_date.desc(), LaborEntry.id.desc()))
    return [crew_view(e, user) for e in entries]


@app.patch("/api/labor/{entry_id}")
def update_labor(entry_id: int, body: LaborPatch, db: Session = Depends(get_db),
                 user: User = Depends(require("owner", "crew"))):
    e = labor_for_edit(db, entry_id, user)
    data = body.model_dump(exclude_unset=True)
    if "job_id" in data:
        open_job(db, data["job_id"])
    for k, v in data.items():
        setattr(e, k, v)
    db.commit()
    return crew_view(e, user)


@app.delete("/api/labor/{entry_id}")
def delete_labor(entry_id: int, db: Session = Depends(get_db), user: User = Depends(require("owner", "crew"))):
    db.delete(labor_for_edit(db, entry_id, user))
    db.commit()
    return {"ok": True}


# ---------- assembly (crew submits, owner approves) ----------

class AssemblyIn(BaseModel):
    job_id: int
    boxes: int = Field(default=0, ge=0)
    modifications: int = Field(default=0, ge=0)
    extra_cost: Decimal = Field(default=Decimal(0), ge=0)
    note: str | None = None
    work_date: date | None = None
    worker_id: int | None = None  # owner only


def assembly_out(e: AssemblyEntry):
    return {"id": e.id, "job_id": e.job_id, "job": e.job.name, "worker": e.worker.name, "work_date": e.work_date,
            "boxes": e.boxes, "modifications": e.modifications, "extra_cost": str(e.extra_cost), "note": e.note,
            "total": str(money(e.total)), "status": e.status, "status_note": e.status_note}


@app.post("/api/assembly")
def add_assembly(body: AssemblyIn, db: Session = Depends(get_db), user: User = Depends(require("owner", "crew"))):
    open_job(db, body.job_id)
    if not (body.boxes or body.modifications or body.extra_cost):
        raise HTTPException(422, "Enter boxes, modifications or an extra cost")
    if body.extra_cost and not (body.note or "").strip():
        raise HTTPException(422, "Say what the extra cost was for")
    worker = user
    if user.role == "owner":
        if not body.worker_id:
            raise HTTPException(422, "Pick who did the assembly")
        worker = get_or_404(db, User, body.worker_id)
    e = AssemblyEntry(job_id=body.job_id, worker_id=worker.id, work_date=body.work_date or date.today(),
                      boxes=body.boxes, modifications=body.modifications, extra_cost=body.extra_cost, note=body.note,
                      box_rate=worker.box_rate if worker.box_rate is not None else BOX_RATE, mod_rate=MOD_RATE,
                      status="approved" if user.role == "owner" else "submitted")
    db.add(e)
    db.commit()
    return assembly_out(e)


@app.get("/api/assembly/mine")
def my_assembly(db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(AssemblyEntry).where(AssemblyEntry.worker_id == user.id).order_by(AssemblyEntry.id.desc())
    return [assembly_out(e) for e in db.scalars(q)]


@app.delete("/api/assembly/{entry_id}")
def delete_assembly(entry_id: int, db: Session = Depends(get_db), user: User = Depends(require("owner", "crew"))):
    e = get_or_404(db, AssemblyEntry, entry_id)
    if user.role != "owner" and (e.worker_id != user.id or e.status != "submitted"):
        raise HTTPException(403, "Only entries waiting for approval can be deleted")
    db.delete(e)
    db.commit()
    return {"ok": True}


# ---------- deliveries (drivers log drop-offs; counted, not paid) ----------

class DeliveryIn(BaseModel):
    job_id: int
    delivered_on: date | None = None
    note: str | None = None
    driver_id: int | None = None  # owner only


TEAM = "Delivery team"  # calendar deliveries don't say who drove


def delivery_out(d: Delivery):
    return {"id": d.id, "job_id": d.job_id, "job": d.job.name if d.job else d.title, "driver_id": d.driver_id,
            "driver": d.driver.name if d.driver else TEAM, "delivered_on": d.delivered_on, "note": d.note,
            "from_calendar": d.calendar_event_id is not None}


@app.post("/api/deliveries")
def add_delivery(body: DeliveryIn, db: Session = Depends(get_db), user: User = Depends(require("owner", "crew"))):
    get_or_404(db, Job, body.job_id)
    driver = get_or_404(db, User, body.driver_id) if user.role == "owner" and body.driver_id else user
    when = body.delivered_on or date.today()
    if db.scalar(select(Delivery).where(Delivery.job_id == body.job_id, Delivery.driver_id == driver.id,
                                        Delivery.delivered_on == when)):
        raise HTTPException(409, "That delivery is already logged")
    d = Delivery(job_id=body.job_id, driver_id=driver.id, delivered_on=when, note=body.note, created_by=user.id)
    db.add(d)
    db.commit()
    return delivery_out(d)


@app.get("/api/deliveries")
def list_deliveries(db: Session = Depends(get_db), user: User = Depends(require("owner", "crew"))):
    q = select(Delivery).options(selectinload(Delivery.job), selectinload(Delivery.driver))
    if user.role != "owner":
        q = q.where(Delivery.driver_id == user.id)
    rows = list(db.scalars(q.order_by(Delivery.delivered_on.desc(), Delivery.id.desc())))
    today = date.today()
    counts: dict[str, dict] = {}
    for d in rows:
        who = d.driver.name if d.driver else TEAM
        c = counts.setdefault(who, {"driver": who, "week": 0, "month": 0, "total": 0})
        c["total"] += 1
        c["week"] += bucket_start(d.delivered_on, "week") == bucket_start(today, "week")
        c["month"] += (d.delivered_on.year, d.delivered_on.month) == (today.year, today.month)
    return {"deliveries": [delivery_out(d) for d in rows[:300]], "counts": sorted(counts.values(), key=lambda c: c["driver"])}


@app.delete("/api/deliveries/{did}")
def delete_delivery(did: int, db: Session = Depends(get_db), user: User = Depends(require("owner", "crew"))):
    d = get_or_404(db, Delivery, did)
    if user.role != "owner" and d.driver_id != user.id:
        raise HTTPException(403, "Not your delivery")
    db.delete(d)
    db.commit()
    return {"ok": True}


# ---------- reimbursements (anyone but reps submits with a receipt; owner approves and pays) ----------

MAX_RECEIPT_BYTES = 10 * 1024 * 1024


def reimbursement_out(r: Reimbursement):
    return {"id": r.id, "user": r.user.name, "amount": str(r.amount), "purpose": r.purpose, "job_id": r.job_id,
            "job": r.job.name if r.job else None, "spent_on": r.spent_on, "status": r.status,
            "status_note": r.status_note, "paid_at": r.paid_at, "receipt_url": f"/api/reimbursements/{r.id}/receipt"}


@app.post("/api/reimbursements")
async def add_reimbursement(
    amount: Decimal = Form(gt=0, le=100000),
    purpose: str = Form(min_length=1),
    job_id: int | None = Form(default=None),
    spent_on: date | None = Form(default=None),
    receipt: UploadFile = File(),
    db: Session = Depends(get_db),
    user: User = Depends(require("owner", "crew", "designer")),
):
    if job_id:
        get_or_404(db, Job, job_id)
    kind = receipt.content_type or ""
    if not (kind.startswith("image/") or kind == "application/pdf"):
        raise HTTPException(422, "Receipt must be a photo or PDF")
    data = await receipt.read(MAX_RECEIPT_BYTES + 1)
    if not data or len(data) > MAX_RECEIPT_BYTES:
        raise HTTPException(422, "Receipt must be under 10 MB")
    r = Reimbursement(user_id=user.id, amount=amount, purpose=purpose.strip(), job_id=job_id or None,
                      spent_on=spent_on or date.today(), receipt=data, receipt_type=kind)
    db.add(r)
    db.commit()
    return reimbursement_out(r)


@app.get("/api/reimbursements/mine")
def my_reimbursements(db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(Reimbursement).where(Reimbursement.user_id == user.id).order_by(Reimbursement.id.desc())
    return [reimbursement_out(r) for r in db.scalars(q)]


def own_or_owner(r: Reimbursement, user: User):
    if user.role != "owner" and r.user_id != user.id:
        raise HTTPException(404, "Reimbursement not found")


@app.get("/api/reimbursements/{rid}/receipt")
def receipt(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = get_or_404(db, Reimbursement, rid)
    own_or_owner(r, user)
    return Response(r.receipt, media_type=r.receipt_type, headers={"Cache-Control": "private, max-age=3600"})


@app.delete("/api/reimbursements/{rid}")
def delete_reimbursement(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = get_or_404(db, Reimbursement, rid)
    own_or_owner(r, user)
    if user.role != "owner" and r.status != "submitted":
        raise HTTPException(403, "Only reimbursements waiting for approval can be deleted")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---------- designs (designer logs; owner matches to jobs) ----------

class DesignIn(BaseModel):
    design_name: str = Field(min_length=1, max_length=200)
    design_date: date | None = None
    revisions: int = Field(default=0, ge=0)
    charge: Decimal = Field(ge=0, le=100000)
    note: str | None = None


class DesignPatch(BaseModel):
    design_name: str | None = Field(default=None, min_length=1, max_length=200)
    design_date: date | None = None
    revisions: int | None = Field(default=None, ge=0)
    charge: Decimal | None = Field(default=None, ge=0, le=100000)
    note: str | None = None


class MatchIn(BaseModel):
    job_id: int | None


def design_out(d: DesignEntry):
    return {"id": d.id, "designer": d.designer.name, "design_name": d.design_name, "design_date": d.design_date,
            "revisions": d.revisions, "charge": str(d.charge), "note": d.note, "job_id": d.job_id,
            "job": d.job.name if d.job else None, "status": d.status, "status_note": d.status_note,
            "paid_at": d.paid_at}


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def name_score(a: str, b: str) -> float:
    """0..1 likeness of a design name and a job name ("Selden kitchen" vs "Dustin Selden")."""
    wa, wb = _words(a), _words(b)
    overlap = len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0
    return max(overlap, SequenceMatcher(None, a.lower(), b.lower()).ratio())


def editable_design(db: Session, design_id: int, user: User) -> DesignEntry:
    d = get_or_404(db, DesignEntry, design_id)
    if user.role != "owner":
        if d.designer_id != user.id:
            raise HTTPException(404, "DesignEntry not found")
        if d.status != "submitted":
            raise HTTPException(403, "This design is already approved. Ask Skyler to change it.")
    return d


@app.post("/api/designs")
def add_design(body: DesignIn, db: Session = Depends(get_db), user: User = Depends(require("designer"))):
    d = DesignEntry(designer_id=user.id, design_name=body.design_name.strip(), design_date=body.design_date or date.today(),
                    revisions=body.revisions, charge=body.charge, note=body.note)
    db.add(d)
    db.commit()
    return design_out(d)


@app.get("/api/designs/mine")
def my_designs(db: Session = Depends(get_db), user: User = Depends(require("designer"))):
    q = select(DesignEntry).where(DesignEntry.designer_id == user.id).order_by(DesignEntry.design_date.desc(), DesignEntry.id.desc())
    return [design_out(d) for d in db.scalars(q)]


@app.patch("/api/designs/{design_id}")
def update_design(design_id: int, body: DesignPatch, db: Session = Depends(get_db),
                  user: User = Depends(require("owner", "designer"))):
    d = editable_design(db, design_id, user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v.strip() if k == "design_name" else v)
    db.commit()
    return design_out(d)


@app.delete("/api/designs/{design_id}")
def delete_design(design_id: int, db: Session = Depends(get_db), user: User = Depends(require("owner", "designer"))):
    db.delete(editable_design(db, design_id, user))
    db.commit()
    return {"ok": True}


@app.get("/api/designs")
def all_designs(unmatched: bool = False, db: Session = Depends(get_db), _=Depends(owner_only)):
    q = select(DesignEntry).order_by(DesignEntry.design_date.desc(), DesignEntry.id.desc())
    if unmatched:
        q = q.where(DesignEntry.job_id.is_(None))
    return [design_out(d) for d in db.scalars(q)]


@app.post("/api/designs/{design_id}/match")
def match_design(design_id: int, body: MatchIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    d = get_or_404(db, DesignEntry, design_id)
    if body.job_id is not None:
        get_or_404(db, Job, body.job_id)
    d.job_id = body.job_id
    db.commit()
    return design_out(d)


@app.get("/api/jobs/{job_id}/design-suggestions")
def design_suggestions(job_id: int, db: Session = Depends(get_db), _=Depends(owner_only)):
    job = get_or_404(db, Job, job_id)
    names = [job.name] + ([job.customer] if job.customer else [])
    scored = [(max(name_score(d.design_name, n) for n in names), d)
              for d in db.scalars(select(DesignEntry).where(DesignEntry.job_id.is_(None)))]
    scored = sorted((x for x in scored if x[0] >= 0.5), key=lambda x: -x[0])[:5]
    return [{**design_out(d), "score": round(s, 2)} for s, d in scored]


# ---------- payments (owner) ----------

def payment_out(p: Payment):
    return {"id": p.id, "job_id": p.job_id, "amount": str(p.amount), "method": p.method,
            "payment_type": p.payment_type, "status": p.status, "expected_date": p.expected_date,
            "received_date": p.received_date, "processing_fee": str(p.processing_fee), "note": p.note,
            "screenshot_url": f"/api/payments/{p.id}/screenshot" if p.screenshot_type else None}


def check_payment(data: dict):
    if data.get("method") is not None and data["method"] not in PAYMENT_METHODS:
        raise HTTPException(422, f"method must be one of {PAYMENT_METHODS}")
    if data.get("payment_type") is not None and data["payment_type"] not in PAYMENT_TYPES:
        raise HTTPException(422, f"payment_type must be one of {PAYMENT_TYPES}")
    if data.get("status") is not None and data["status"] not in ("pending", "received"):
        raise HTTPException(422, "status must be pending or received")


@app.post("/api/jobs/{job_id}/payments")
async def add_payment(
    job_id: int,
    amount: Decimal = Form(gt=0, le=10_000_000),
    method: str = Form(),
    payment_type: str = Form(default="progress"),
    status: str = Form(default="received"),
    expected_date: date | None = Form(default=None),
    received_date: date | None = Form(default=None),
    processing_fee: Decimal = Form(default=Decimal(0), ge=0),
    note: str | None = Form(default=None),
    screenshot: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(owner_only),
):
    get_or_404(db, Job, job_id)
    check_payment({"method": method, "payment_type": payment_type, "status": status})
    data, kind = None, None
    if screenshot and screenshot.filename:
        kind = screenshot.content_type or ""
        if not (kind.startswith("image/") or kind == "application/pdf"):
            raise HTTPException(422, "Screenshot must be a photo or PDF")
        data = await screenshot.read(MAX_RECEIPT_BYTES + 1)
        if len(data) > MAX_RECEIPT_BYTES:
            raise HTTPException(422, "Screenshot must be under 10 MB")
    p = Payment(job_id=job_id, amount=amount, method=method, payment_type=payment_type, status=status,
                expected_date=expected_date,
                received_date=(received_date or date.today()) if status == "received" else None,
                processing_fee=processing_fee, note=note, screenshot=data, screenshot_type=kind, created_by=user.id)
    db.add(p)
    db.commit()
    return job_out(load_job(db, job_id))


class PaymentPatch(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)
    method: str | None = None
    payment_type: str | None = None
    status: str | None = None
    expected_date: date | None = None
    received_date: date | None = None
    processing_fee: Decimal | None = Field(default=None, ge=0)
    note: str | None = None


@app.patch("/api/payments/{pid}")
def update_payment(pid: int, body: PaymentPatch, db: Session = Depends(get_db), _=Depends(owner_only)):
    p = get_or_404(db, Payment, pid)
    data = body.model_dump(exclude_unset=True)
    check_payment(data)
    for k, v in data.items():
        setattr(p, k, v)
    if p.status == "received" and not p.received_date:
        p.received_date = date.today()
    if p.status == "pending":
        p.received_date = None
    db.commit()
    return job_out(load_job(db, p.job_id))


@app.delete("/api/payments/{pid}")
def delete_payment(pid: int, db: Session = Depends(get_db), _=Depends(owner_only)):
    p = get_or_404(db, Payment, pid)
    job_id = p.job_id
    db.delete(p)
    db.commit()
    return job_out(load_job(db, job_id))


@app.get("/api/payments/{pid}/screenshot")
def payment_screenshot(pid: int, db: Session = Depends(get_db), _=Depends(owner_only)):
    p = get_or_404(db, Payment, pid)
    if not p.screenshot_type:
        raise HTTPException(404, "No screenshot")
    return Response(p.screenshot, media_type=p.screenshot_type, headers={"Cache-Control": "private, max-age=3600"})


# ---------- smart input (owner) ----------

@app.post("/api/inbox/parse")
async def inbox_parse(
    text: str = Form(default="", max_length=4000),
    image: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    _=Depends(owner_only),
):
    data, kind = None, None
    if image and image.filename:
        kind = image.content_type or ""
        if kind not in ("image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"):
            raise HTTPException(422, "Use a JPG, PNG or PDF")
        data = await image.read(MAX_RECEIPT_BYTES + 1)
        if len(data) > MAX_RECEIPT_BYTES:
            raise HTTPException(422, "File must be under 10 MB")
    if not text.strip() and not data:
        raise HTTPException(422, "Type something or add a screenshot")
    jobs = list(db.scalars(select(Job).where(Job.status != "Closed").order_by(Job.name)))
    crew = list(db.scalars(select(User).where(User.role == "crew", User.active).order_by(User.name)))
    entry = inbox.parse(text, data, kind, [j.name for j in jobs], [u.name for u in crew])
    by_name = {j.name: j.id for j in jobs}
    entry["job_id"] = by_name.get(entry.get("job"))
    entry["worker_id"] = next((u.id for u in crew if u.name == entry.get("worker")), None)
    return entry


# ---------- ready-to-order email intake (Gmail automation -> app) ----------

TEMPLATE_MARKER = "[Supplier - Finish]"  # Skyler's blank template sent to Delani; only her filled-in copy counts


def automation_user(db: Session) -> User:
    owner = db.scalar(select(User).where(User.role == "owner", User.active).order_by(User.id))
    if not owner:
        raise HTTPException(503, "No owner login exists yet")
    return owner


def rep_on_thread(db: Session, recipients: str, sender: str | None) -> str | None:
    """The job belongs to the rep copied on the email (Drew or Garner on To/CC); nobody copied = the owner's own job.

    The sender is skipped because Delani sends every ready-to-order email; the owner reassigns her own jobs by hand.
    """
    emails = lambda text: {a.lower() for a in re.findall(r"[\w.+-]+@[\w.-]+", text or "")}  # noqa: E731
    addresses = emails(recipients) - emails(sender)  # "Delani Smith <delani@...>" works too
    for rep in db.scalars(select(User).where(User.role == "rep", User.active).order_by(User.id)):
        if rep.email.lower() in addresses:
            return rep.name
    return None


def match_rep(db: Session, name: str | None) -> int | None:
    if not name:
        return None
    first = name.strip().lower().split()[0] if name.strip() else ""
    for rep in db.scalars(select(User).where(User.role.in_(("rep", "owner")), User.active)):
        if rep.name.lower() == name.strip().lower() or rep.name.lower().split()[0] == first:
            return rep.id
    return None


def best_job(db: Session, name: str) -> tuple[Job | None, float]:
    best, score = None, 0.0
    for job in db.scalars(select(Job).where(Job.status != "Closed")):
        s = 1.0 if job.name.lower() == name.lower() else name_score(name, job.name)
        if s > score:
            best, score = job, s
    return best, score


def apply_to_job(db: Session, job: Job, p: dict, owner: User) -> str:
    """Attach an email's numbers to an existing job. Change orders add lines; a repeat email only fills blanks."""
    if p.get("is_change_order"):
        note = "From ready-to-order email (change order / add-on)"
        if p.get("invoice_total"):
            db.add(LedgerEntry(job_id=job.id, line=2, amount=Decimal(str(p["invoice_total"])), note=note, created_by=owner.id))
        if p.get("material_total"):
            db.add(LedgerEntry(job_id=job.id, line=4, amount=Decimal(str(p["material_total"])), note=note, created_by=owner.id))
        job.boxes += p.get("boxes") or 0
        job.modifications += p.get("modifications") or 0
        return "attached"
    if job.material_total is None and p.get("material_total"):
        job.material_total = Decimal(str(p["material_total"]))
    if not job.invoice_total and p.get("invoice_total"):
        job.invoice_total = Decimal(str(p["invoice_total"]))
    if not job.rep_id or job.rep_id == owner.id:  # a later email copying a rep beats the no-rep default
        job.rep_id = match_rep(db, p.get("rep_name")) or job.rep_id or owner.id
    job.manufacturer = job.manufacturer or p.get("manufacturer")
    job.boxes = job.boxes or p.get("boxes") or 0
    job.modifications = job.modifications or p.get("modifications") or 0
    return "attached"


def create_job_from(db: Session, p: dict, subject: str, owner: User) -> Job:
    name = (p.get("job_name") or subject.split(":", 1)[-1]).strip()[:200]
    # No rep on the email = the owner's own sale.
    job = Job(name=name, customer=p.get("customer"), rep_id=match_rep(db, p.get("rep_name")) or owner.id,
              manufacturer=p.get("manufacturer"), invoice_total=Decimal(str(p.get("invoice_total") or 0)),
              material_total=Decimal(str(p["material_total"])) if p.get("material_total") else None,
              boxes=p.get("boxes") or 0, modifications=p.get("modifications") or 0)
    db.add(job)
    db.flush()
    return job


def route_email(db: Session, p: dict, thread_id: str | None, subject: str) -> tuple[str, Job | None]:
    owner = automation_user(db)
    if p.get("is_vendor_reply"):
        first = db.scalar(select(IntakeEmail).where(IntakeEmail.thread_id == thread_id, IntakeEmail.job_id.is_not(None))
                          .order_by(IntakeEmail.id)) if thread_id else None
        if not first or not p.get("material_total"):
            return ("ignored" if not p.get("material_total") else "unmatched"), None
        job = db.get(Job, first.job_id)
        if first.parsed.get("is_change_order"):
            db.add(LedgerEntry(job_id=job.id, line=4, amount=Decimal(str(p["material_total"])),
                               note="Vendor sales order (change order / add-on)", created_by=owner.id))
            return "material", job
        if job.material_total is None:
            job.material_total = Decimal(str(p["material_total"]))
            return "material", job
        return "unmatched", job  # job already has a material total; let the owner decide

    name = (p.get("job_name") or subject.split(":", 1)[-1]).strip()
    job, score = best_job(db, name)
    if p.get("is_change_order"):
        return (apply_to_job(db, job, p, owner), job) if job and score >= 0.75 else ("unmatched", None)
    if job and score == 1.0:
        return apply_to_job(db, job, p, owner), job
    if job and score >= 0.75:
        return "unmatched", None  # close to an existing job but not the same name: owner picks
    return "created", create_job_from(db, p, subject, owner)


def intake_out(e: IntakeEmail):
    return {"id": e.id, "subject": e.subject, "sender": e.sender, "outcome": e.outcome, "job_id": e.job_id,
            "job": e.job.name if e.job else None, "parsed": e.parsed, "created_at": e.created_at}


def check_intake_token(request: Request):
    token = os.environ.get("INTAKE_TOKEN")
    if not token:
        raise HTTPException(503, "INTAKE_TOKEN is not set on the app")
    if not hmac.compare_digest(request.headers.get("x-intake-token", ""), token):
        raise HTTPException(401, "Bad intake token")


DELIVERY_PREFIX = re.compile(r"^\s*(OUTBOUND|DIRECT)\b[^-–]*[-–]\s*", re.I)  # "OUTBOUND (PT 1 )- Dallin Hatch"
PART_SUFFIX = re.compile(r"\s*\(?\s*PT\.?\s*\d+\s*\)?\s*$", re.I)  # "Jamie Reyna PT 3"


class CalendarDeliveryIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=300)
    title: str = Field(max_length=300)
    delivered_on: date


@app.post("/api/intake/delivery")
def intake_delivery(body: CalendarDeliveryIn, request: Request, db: Session = Depends(get_db)):
    """An ended event on the DELIVERY calendar. OUTBOUND and DIRECT are drop-offs; INBOUND is stock coming in."""
    check_intake_token(request)
    if not DELIVERY_PREFIX.match(body.title):
        return {"outcome": "ignored"}
    name = PART_SUFFIX.sub("", DELIVERY_PREFIX.sub("", body.title)).strip()
    job, score = None, 0.0
    for j in db.scalars(select(Job)):
        s = 1.0 if j.name.lower() == name.lower() else name_score(name, j.name)
        if s > score:
            job, score = j, s
    job = job if score >= 0.75 else None

    d = db.scalar(select(Delivery).where(Delivery.calendar_event_id == body.event_id))
    outcome = "updated"
    if not d and job:  # a driver may have logged this drop by hand already
        d = db.scalar(select(Delivery).where(Delivery.job_id == job.id, Delivery.delivered_on == body.delivered_on,
                                             Delivery.calendar_event_id.is_(None)))
        outcome = "linked"
    if not d:
        d = Delivery(driver_id=None, created_by=automation_user(db).id)
        db.add(d)
        outcome = "created"
    d.calendar_event_id, d.title, d.delivered_on = body.event_id, body.title, body.delivered_on
    d.job_id = job.id if job else d.job_id
    db.commit()
    return {"outcome": outcome, "job": job.name if job else None}


@app.post("/api/intake/email")
async def intake_email(
    request: Request,
    message_id: str = Form(min_length=1, max_length=200),
    subject: str = Form(max_length=500),
    body: str = Form(default=""),
    thread_id: str | None = Form(default=None),
    sender: str | None = Form(default=None),
    recipients: str = Form(default=""),  # To + CC addresses, comma separated
    pdf: list[UploadFile] = File(default=[]),  # every PDF on the email; repeat the field for each
    db: Session = Depends(get_db),
):
    check_intake_token(request)
    seen = db.scalar(select(IntakeEmail).where(IntakeEmail.message_id == message_id))
    if seen:
        return {**intake_out(seen), "duplicate": True}

    if TEMPLATE_MARKER in body:
        p, outcome, job = {"reason": "blank template"}, "ignored", None
    else:
        pdfs, budget = [], MAX_RECEIPT_BYTES * 2  # stay well under Claude's request size limit
        for f in pdf:
            if f.filename and (f.content_type == "application/pdf" or f.filename.lower().endswith(".pdf")):
                data = await f.read(budget + 1)
                if len(data) <= budget:
                    pdfs.append((f.filename, data))
                    budget -= len(data)
        p = inbox.read_rto(subject, body, pdfs)
        if recipients.strip():
            p["rep_name"] = rep_on_thread(db, recipients, sender)
        outcome, job = route_email(db, p, thread_id, subject)
    e = IntakeEmail(message_id=message_id, thread_id=thread_id, sender=sender, subject=subject[:500], parsed=p,
                    outcome=outcome, job_id=job.id if job else None)
    db.add(e)
    db.commit()
    return intake_out(e)


@app.get("/api/intake")
def intake_list(outcome: str | None = None, db: Session = Depends(get_db), _=Depends(owner_only)):
    q = select(IntakeEmail).order_by(IntakeEmail.id.desc()).limit(200)
    if outcome:
        q = q.where(IntakeEmail.outcome == outcome)
    return [intake_out(e) for e in db.scalars(q)]


class ResolveIn(BaseModel):
    job_id: int | None = None  # attach to this job; omit to create a new job from the email


@app.post("/api/intake/{intake_id}/resolve")
def intake_resolve(intake_id: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(owner_only)):
    e = get_or_404(db, IntakeEmail, intake_id)
    if e.outcome != "unmatched":
        raise HTTPException(422, "This email was already handled")
    if body.job_id:
        job = get_or_404(db, Job, body.job_id)
        if e.parsed.get("is_vendor_reply") and e.parsed.get("material_total"):
            db.add(LedgerEntry(job_id=job.id, line=4, amount=Decimal(str(e.parsed["material_total"])),
                               note="Vendor sales order", created_by=user.id))
        else:
            apply_to_job(db, job, e.parsed, user)
    else:
        check_job_fields(db, {"name": (e.parsed.get("job_name") or e.subject.split(":", 1)[-1]).strip()})
        job = create_job_from(db, e.parsed, e.subject, user)
    e.job_id, e.outcome = job.id, "resolved"
    db.commit()
    return intake_out(e)


# ---------- collections report (owner) ----------

PERIODS = ("day", "week", "month", "quarter", "year")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def bucket_start(d: date, period: str) -> date:
    if period == "day":
        return d
    if period == "week":  # weeks run Sunday to Saturday
        return d - timedelta(days=(d.weekday() + 1) % 7)
    if period == "month":
        return d.replace(day=1)
    if period == "quarter":
        return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    return date(d.year, 1, 1)


def bucket_label(start: date, period: str) -> str:
    md = f"{MONTHS[start.month - 1]} {start.day}"
    return {"day": md, "week": f"Wk of {md}", "month": f"{MONTHS[start.month - 1]} {start.year}",
            "quarter": f"Q{(start.month - 1) // 3 + 1} {start.year}", "year": str(start.year)}[period]


def last_buckets(today_: date, period: str, count: int) -> list[date]:
    starts = [bucket_start(today_, period)]
    while len(starts) < count:
        starts.insert(0, bucket_start(starts[0] - timedelta(days=1), period))
    return starts


def commission_is_due(job: Job, w: dict) -> bool:
    """Closed, paid in full, every cost approved, not frozen and not already paid."""
    return (job.status == "Closed" and not job.frozen and not job.commission_paid_at
            and Decimal(w["balance_due"]) <= 0 and not w["costs_waiting"] and Decimal(w["commission"]) > 0)


@app.get("/api/collections")
def collections(period: str = "week", count: int = 8, db: Session = Depends(get_db), _=Depends(owner_only)):
    if period not in PERIODS:
        raise HTTPException(422, f"period must be one of {PERIODS}")
    count = max(1, min(count, 60))
    starts = last_buckets(date.today(), period, count)
    index = {s: i for i, s in enumerate(starts)}

    jobs = list(db.scalars(select(Job).options(
        selectinload(Job.ledger), selectinload(Job.labor), selectinload(Job.rep), selectinload(Job.assembly),
        selectinload(Job.reimbursements), selectinload(Job.designs), selectinload(Job.payments))))

    def zeros():
        return [Decimal(0)] * count

    collected, sold, methods = {}, {}, {}
    pending, outstanding, commission_due = [], [], []
    for job in jobs:
        rep = job.rep.name if job.rep else "No rep"
        w = compute(job)
        sold_at = index.get(bucket_start(job.created_at.date(), period))
        if sold_at is not None:
            sold.setdefault(rep, zeros())[sold_at] += Decimal(w["lines"][2]["amount"])
        last_paid = None
        for p in job.payments:
            if p.status == "pending":
                pending.append({**payment_out(p), "job": job.name, "rep": rep})
                continue
            last_paid = max(filter(None, (last_paid, p.received_date)))
            i = index.get(bucket_start(p.received_date, period))
            if i is not None:
                collected.setdefault(rep, zeros())[i] += p.amount
                methods.setdefault(p.method, zeros())[i] += p.amount
        balance = Decimal(w["balance_due"])
        if balance > 0 and job.status != "Closed":
            outstanding.append({"job_id": job.id, "job": job.name, "rep": rep, "balance_due": str(balance),
                                "last_payment": last_paid,
                                "days_since_payment": (date.today() - last_paid).days if last_paid else None})
        if commission_is_due(job, w):
            commission_due.append({"job_id": job.id, "job": job.name, "rep": rep, "commission": w["commission"],
                                   "closed_at": job.closed_at})

    def rows(table):
        return [{"rep": rep, "amounts": [str(money(v)) for v in vals], "total": str(money(sum(vals)))}
                for rep, vals in sorted(table.items())]

    totals = [sum(vals[i] for vals in collected.values()) for i in range(count)]
    return {
        "period": period,
        "buckets": [{"start": s, "label": bucket_label(s, period)} for s in starts],
        "collected": rows(collected),
        "collected_total": [str(money(t)) for t in totals],
        "sold": rows(sold),
        "methods": rows(methods),
        "pending": sorted(pending, key=lambda p: str(p["expected_date"] or "")),
        "outstanding": sorted(outstanding, key=lambda o: -Decimal(o["balance_due"])),
        "commission_due": commission_due,
    }


class CommissionPaidIn(BaseModel):
    paid: bool


@app.post("/api/jobs/{job_id}/commission-paid")
def commission_paid(job_id: int, body: CommissionPaidIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    job = get_or_404(db, Job, job_id)
    job.commission_paid_at = datetime.now(timezone.utc) if body.paid else None
    db.commit()
    return job_out(load_job(db, job_id))


# ---------- approvals (owner) ----------

class StatusIn(BaseModel):
    status: str
    note: str | None = None


def set_status(obj, body: StatusIn):
    if body.status not in TRANSITIONS.get(obj.status, ()):
        raise HTTPException(422, f"Can't go from {obj.status} to {body.status}")
    obj.status = body.status
    obj.status_note = body.note
    obj.paid_at = datetime.now(timezone.utc) if body.status == "paid" else None


@app.get("/api/approvals")
def approvals(db: Session = Depends(get_db), _=Depends(owner_only)):
    open_statuses = ("submitted", "approved")
    a = db.scalars(select(AssemblyEntry).where(AssemblyEntry.status.in_(open_statuses)).order_by(AssemblyEntry.id))
    r = db.scalars(select(Reimbursement).where(Reimbursement.status.in_(open_statuses)).order_by(Reimbursement.id))
    h = db.scalars(select(LaborEntry).where(LaborEntry.status.in_(open_statuses))
                   .options(selectinload(LaborEntry.job), selectinload(LaborEntry.worker))
                   .order_by(LaborEntry.work_date, LaborEntry.id))
    d = db.scalars(select(DesignEntry).where(DesignEntry.status.in_(open_statuses)).order_by(DesignEntry.design_date))
    owner = db.get(User, db.info["user_id"])
    return {"assembly": [assembly_out(e) for e in a], "reimbursements": [reimbursement_out(x) for x in r],
            "hours": [{**crew_view(e, owner), "week": bucket_start(e.work_date, "week")} for e in h],
            "designs": [design_out(x) for x in d]}


@app.post("/api/assembly/{entry_id}/status")
def assembly_status(entry_id: int, body: StatusIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    e = get_or_404(db, AssemblyEntry, entry_id)
    set_status(e, body)
    db.commit()
    return assembly_out(e)


@app.post("/api/labor/{entry_id}/status")
def labor_status(entry_id: int, body: StatusIn, db: Session = Depends(get_db), user: User = Depends(owner_only)):
    e = get_or_404(db, LaborEntry, entry_id)
    set_status(e, body)
    db.commit()
    return crew_view(e, user)


@app.post("/api/designs/{design_id}/status")
def design_status(design_id: int, body: StatusIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    d = get_or_404(db, DesignEntry, design_id)
    set_status(d, body)
    db.commit()
    return design_out(d)


@app.post("/api/reimbursements/{rid}/status")
def reimbursement_status(rid: int, body: StatusIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    r = get_or_404(db, Reimbursement, rid)
    set_status(r, body)
    db.commit()
    return reimbursement_out(r)


# ---------- my sales (reps see their own jobs, money in and commission; never job costs) ----------

@app.get("/api/sales/mine")
def my_sales(db: Session = Depends(get_db), user: User = Depends(require("rep"))):
    jobs = db.scalars(select(Job).where(Job.rep_id == user.id).order_by(Job.created_at.desc()).options(
        selectinload(Job.ledger), selectinload(Job.labor), selectinload(Job.rep), selectinload(Job.assembly),
        selectinload(Job.reimbursements), selectinload(Job.designs), selectinload(Job.payments)))
    out, totals = [], {"sold": Decimal(0), "collected": Decimal(0), "balance": Decimal(0),
                       "commission_due": Decimal(0), "commission_paid": Decimal(0), "commission_pending": Decimal(0)}
    for job in jobs:
        w = compute(job)
        if job.commission_paid_at:
            state = "paid"
        elif commission_is_due(job, w):
            state = "due"
        else:
            state = "pending"  # not closed and paid in full yet, so the number can still change
        sold, collected, balance = Decimal(w["lines"][2]["amount"]), Decimal(w["collected"]), Decimal(w["balance_due"])
        totals["sold"] += sold
        totals["collected"] += collected
        totals["balance"] += max(balance, Decimal(0))
        if not w["material_pending"]:  # without material cost the profit, and so the commission, is overstated
            totals[f"commission_{state}"] += Decimal(w["commission"])
        out.append({"id": job.id, "name": job.name, "status": job.status, "created_at": job.created_at,
                    "sold": str(money(sold)), "collected": str(money(collected)), "balance_due": str(money(balance)),
                    "commission": None if w["material_pending"] else w["commission"], "commission_state": state})
    return {"jobs": out, "totals": {k: str(money(v)) for k, v in totals.items()},
            "commission_rate": str(user.commission_rate) if user.commission_rate is not None else None}


# ---------- my pay (crew and designer see their own earnings) ----------

@app.get("/api/pay/mine")
def my_pay(db: Session = Depends(get_db), user: User = Depends(require("crew", "designer"))):
    items = []  # (date, amount, status, what)
    for e in db.scalars(select(LaborEntry).where(LaborEntry.worker_id == user.id).options(selectinload(LaborEntry.job))):
        items.append((e.work_date, labor_total(e), e.status, f"{e.hours} h · {e.job.name}"))
    for e in db.scalars(select(AssemblyEntry).where(AssemblyEntry.worker_id == user.id)):
        if e.status != "rejected":
            items.append((e.work_date, money(e.total), e.status, f"Assembly · {e.job.name}"))
    for d in db.scalars(select(DesignEntry).where(DesignEntry.designer_id == user.id)):
        if d.status != "rejected":
            items.append((d.design_date, money(d.charge), d.status, f"Design · {d.design_name}"))

    weeks: dict[date, dict] = {}
    for when, amount, status, what in items:
        w = weeks.setdefault(bucket_start(when, "week"), {"waiting": Decimal(0), "owed": Decimal(0), "paid": Decimal(0), "items": []})
        w[{"submitted": "waiting", "approved": "owed", "paid": "paid"}[status]] += amount
        w["items"].append({"date": when, "what": what, "amount": str(amount), "status": status})
    out = []
    for start in sorted(weeks, reverse=True):
        w = weeks[start]
        out.append({"week": start, "label": bucket_label(start, "week"), "waiting": str(money(w["waiting"])),
                    "owed": str(money(w["owed"])), "paid": str(money(w["paid"])),
                    "items": sorted(w["items"], key=lambda i: i["date"], reverse=True)})
    total = lambda k: str(money(sum(Decimal(w[k]) for w in out)))  # noqa: E731
    return {"weeks": out, "waiting": total("waiting"), "owed": total("owed"), "paid": total("paid")}


# ---------- audit (owner) ----------

@app.get("/api/audit")
def audit_log(table: str | None = None, row_id: int | None = None, limit: int = 200,
              db: Session = Depends(get_db), _=Depends(owner_only)):
    q = select(AuditLog)
    if table:
        q = q.where(AuditLog.table_name == table)
    if row_id:
        q = q.where(AuditLog.row_id == row_id)
    users = {u.id: u.name for u in db.scalars(select(User))}
    return [{"id": a.id, "who": users.get(a.user_id), "table": a.table_name, "row_id": a.row_id, "action": a.action,
             "changes": a.changes, "at": a.at} for a in db.scalars(q.order_by(AuditLog.id.desc()).limit(min(limit, 1000)))]


# ---------- frontend ----------

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (DIST / path).resolve()
        # Top-level files from frontend/public (icons, manifest); anything else is the app.
        if path and file.parent == DIST.resolve() and file.is_file():
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
