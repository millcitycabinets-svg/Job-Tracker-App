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

from . import audit  # noqa: F401  (registers the audit listener)
from .auth import check_password, current_user, hash_password, owner_only, require
from .db import Base, engine, get_db
from .models import (BOX_RATE, JOB_STATUSES, LEDGER_LINES, MOD_RATE, ROLES, TRANSITIONS, AssemblyEntry, AuditLog, Job,
                     DesignEntry, LaborEntry, LedgerEntry, Reimbursement, User)
from .waterfall import compute, labor_total, money

CREW_EDIT_WINDOW = timedelta(days=3)

@asynccontextmanager
async def lifespan(_app):
    # shortcut: create_all only adds new tables; switch to Alembic before changing columns on live data
    Base.metadata.create_all(engine)
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
    out = {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "active": u.active}
    if with_pay:
        out["hourly_rate"] = str(u.hourly_rate) if u.hourly_rate is not None else None
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


class UserPatch(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: str | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    commission_rate: Decimal | None = Field(default=None, ge=0, le=1)
    active: bool | None = None


def check_role(role):
    if role is not None and role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")


@app.get("/api/users")
def list_users(db: Session = Depends(get_db), _=Depends(owner_only)):
    return [user_out(u, with_pay=True) for u in db.scalars(select(User).order_by(User.name))]


@app.post("/api/users")
def create_user(body: UserIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    check_role(body.role)
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "That email already has a login")
    u = User(name=body.name.strip(), email=email, password_hash=hash_password(body.password), role=body.role,
             hourly_rate=body.hourly_rate, commission_rate=body.commission_rate)
    db.add(u)
    db.commit()
    return user_out(u, with_pay=True)


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, db: Session = Depends(get_db), _=Depends(owner_only)):
    u = get_or_404(db, User, user_id)
    data = body.model_dump(exclude_unset=True)
    check_role(data.get("role"))
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
        if not rep or rep.role != "rep":
            raise HTTPException(422, "rep_id must be a sales rep")
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
                 selectinload(Job.designs).selectinload(DesignEntry.designer))
    )
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def job_fields(job: Job):
    out = {c: getattr(job, c) for c in (
        "id", "name", "customer", "rep_id", "manufacturer", "boxes", "modifications", "install_date",
        "status", "frozen", "created_at", "closed_at")}
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
    }


@app.get("/api/jobs")
def list_jobs(status: str | None = None, rep_id: int | None = None, db: Session = Depends(get_db),
              _=Depends(owner_only)):
    q = select(Job).options(selectinload(Job.ledger), selectinload(Job.labor), selectinload(Job.rep),
                            selectinload(Job.assembly), selectinload(Job.reimbursements), selectinload(Job.designs))
    if status:
        q = q.where(Job.status == status)
    if rep_id:
        q = q.where(Job.rep_id == rep_id)
    out = []
    for job in db.scalars(q.order_by(Job.created_at.desc())):
        w = compute(job)
        out.append({**job_fields(job), "adjusted_revenue": w["lines"][2]["amount"], "job_profit": w["job_profit"],
                    "net": w["net"], "margin_pct": w["margin_pct"], "material_pending": w["material_pending"]})
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
    editable = user.role == "owner" or datetime.now(timezone.utc) - e.created_at < CREW_EDIT_WINDOW
    return {"id": e.id, "job_id": e.job_id, "job": e.job.name, "work_date": e.work_date, "hours": str(e.hours),
            "editable": editable}


def labor_for_edit(db: Session, entry_id: int, user: User) -> LaborEntry:
    e = get_or_404(db, LaborEntry, entry_id)
    if user.role != "owner":
        if e.worker_id != user.id:
            raise HTTPException(404, "LaborEntry not found")
        if datetime.now(timezone.utc) - e.created_at >= CREW_EDIT_WINDOW:
            raise HTTPException(403, "This entry is locked. Ask Skyler to change it.")
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
    if worker.hourly_rate is None:
        raise HTTPException(422, f"{worker.name} has no hourly rate set")
    e = LaborEntry(job_id=body.job_id, worker_id=worker.id, work_date=body.work_date or date.today(),
                   hours=body.hours, rate=worker.hourly_rate, created_by=user.id)
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
                      box_rate=BOX_RATE, mod_rate=MOD_RATE,
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
            "job": d.job.name if d.job else None}


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
        if d.job_id:
            raise HTTPException(403, "This design is already matched to a job. Ask Skyler to change it.")
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
    return {"assembly": [assembly_out(e) for e in a], "reimbursements": [reimbursement_out(x) for x in r]}


@app.post("/api/assembly/{entry_id}/status")
def assembly_status(entry_id: int, body: StatusIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    e = get_or_404(db, AssemblyEntry, entry_id)
    set_status(e, body)
    db.commit()
    return assembly_out(e)


@app.post("/api/reimbursements/{rid}/status")
def reimbursement_status(rid: int, body: StatusIn, db: Session = Depends(get_db), _=Depends(owner_only)):
    r = get_or_404(db, Reimbursement, rid)
    set_status(r, body)
    db.commit()
    return reimbursement_out(r)


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
        return FileResponse(DIST / "index.html")
