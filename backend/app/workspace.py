"""Persistent sales and scoped operations API; Job Tracker retains finance ownership."""
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from .auth import current_user
from .db import get_db
from .models import AuditLog, Job, User, now
from .waterfall import compute, money
from .workspace_models import Activity, CommandReceipt, Opportunity, Preference, WorkRequest

router = APIRouter(prefix="/api/workspace", tags=["workspace"])
STAGES = ("New Lead", "Follow up", "Design", "Estimate", "Ready to Send", "Awaiting Customer", "Won", "Lost")
Stage = Literal["New Lead", "Follow up", "Design", "Estimate", "Ready to Send", "Awaiting Customer", "Won", "Lost"]
WorkState = Literal["Queued", "In Progress", "Needs Information", "Complete"]
BACK_OFFICE = ("owner", "operations", "designer")
SALES = ("owner", "rep")
WORKSPACE = (*SALES, "operations", "designer")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class OpportunityIn(Input):
    name: str = Field(min_length=1, max_length=200)
    customer: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=500)
    visibility: Literal["private", "shared"] = "private"
    owner_id: int | None = Field(default=None, gt=0)
    stage: Stage = "New Lead"
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    next_follow_up: date | None = None
    work_scope: str | None = Field(default=None, max_length=20000)
    notes: str | None = Field(default=None, max_length=20000)


class Command(Input):
    expected_revision: int = Field(ge=1)
    command_id: str = Field(min_length=1, max_length=160)


class OpportunityPatch(Command):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    customer: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=500)
    visibility: Literal["private", "shared"] | None = None
    owner_id: int | None = Field(default=None, gt=0)
    stage: Stage | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    next_follow_up: date | None = None
    work_scope: str | None = Field(default=None, max_length=20000)
    notes: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def non_null_required(self):
        for field in ("name", "customer", "owner_id", "visibility", "stage"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class RequestPatch(Command):
    status: WorkState | None = None
    assigned_to_id: int | None = Field(default=None, gt=0)
    due_date: date | None = None
    result: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def non_null_status(self):
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self


class ActivityIn(Input):
    kind: Literal["Note", "Call", "Email", "Text"]
    body: str = Field(min_length=1, max_length=20000)


class LinkIn(Command):
    job_id: int = Field(gt=0)


class ScheduleIn(Input):
    command_id: str = Field(min_length=1, max_length=160)
    expected_status: Literal["Ordered", "Assembled", "Delivered", "Installed", "Punch", "Closed"]
    expected_install_date: date | None
    status: Literal["Ordered", "Assembled", "Delivered", "Installed", "Punch"] | None = None
    install_date: date | None = None

    @model_validator(mode="after")
    def valid_change(self):
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        if not ({"status", "install_date"} & self.model_fields_set):
            raise ValueError("Provide a status or installation date to update")
        return self


class PreorderPatch(Command):
    status: Literal["Prepping", "Ready to Order"] | None = None
    due_date: date | None = None

    @model_validator(mode="after")
    def valid_change(self):
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        if not ({"status", "due_date"} & self.model_fields_set):
            raise ValueError("Provide a status or due date to update")
        return self


def _role(user, allowed):
    if not user.active:
        raise HTTPException(401, "Not logged in")
    if user.role not in allowed:
        raise HTTPException(403, "This workspace area is not available to your role.")


def _sales_filter(user):
    if user.role == "owner":
        return True
    return or_(Opportunity.owner_id == user.id, Opportunity.visibility == "shared")


def _read_opportunity(db, user, opportunity_id):
    _role(user, SALES)
    row = db.scalar(select(Opportunity).where(Opportunity.id == opportunity_id, _sales_filter(user))
                    .execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Opportunity is unavailable.")
    return row


def _edit_opportunity(db, user, opportunity_id):
    row = _read_opportunity(db, user, opportunity_id)
    if user.role != "owner" and (row.owner_id != user.id or row.visibility == "shared"):
        raise HTTPException(403, "Claim this shared lead before editing it.")
    return row


def _request_access(db, user, request_id, write=False):
    _role(user, BACK_OFFICE if write else WORKSPACE)
    row = db.scalar(select(WorkRequest).where(WorkRequest.id == request_id).execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Work request is unavailable.")
    if user.role == "rep":
        _read_opportunity(db, user, row.opportunity_id)
    return row


def _check_owner(db, user, owner_id):
    if user.role != "owner" and owner_id != user.id:
        raise HTTPException(403, "Only an administrator can assign another sales owner.")
    owner = db.get(User, owner_id)
    if not owner or not owner.active or owner.role not in SALES:
        raise HTTPException(422, "Choose an active salesperson or administrator.")


def _opportunity_out(row):
    fields = ("id", "name", "customer", "email", "phone", "address", "owner_id", "visibility", "stage",
              "next_follow_up", "work_scope", "notes", "revision", "linked_job_id", "created_at", "updated_at")
    return {**{key: getattr(row, key) for key in fields}, "owner_name": row.owner.name,
            "amount": str(money(row.amount)) if row.amount is not None else None}


def _request_out(row):
    # Only request-scoped context is projected. The opportunity's notes are never included.
    return {"id": row.id, "opportunity_id": row.opportunity_id,
            **{k: row.context.get(k) for k in ("opportunity_name", "customer", "email", "phone", "address", "scope", "sales_owner_name")},
            "kind": row.kind, "status": row.status, "assigned_to_id": row.assigned_to_id,
            "assigned_to_name": row.assigned_to.name if row.assigned_to else None,
            "due_date": row.due_date, "result": row.result, "version": row.version,
            "revision": row.revision, "linked_job_id": row.opportunity.linked_job_id,
            "created_at": row.created_at, "updated_at": row.updated_at}


def _activity_out(row):
    return {"id": row.id, "kind": row.kind, "body": row.body,
            "actor_name": row.actor.name, "created_at": row.created_at}


def _fingerprint(body):
    raw = json.dumps(body.model_dump(mode="json", exclude_unset=True), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _match_receipt(receipt, user, operation, entity_id, fingerprint):
    _role(user, WORKSPACE)
    if (receipt.actor_id, receipt.actor_role, receipt.operation, receipt.entity_id, receipt.fingerprint) != (
            user.id, user.role, operation, entity_id, fingerprint):
        raise HTTPException(409, "This command ID was already used for a different action. Refresh and try again.")
    return receipt.response


def _begin_command(db, user, operation, entity_id, body, recheck):
    """Reserve a unique receipt in the same transaction as the change.

    Reservation serializes identical concurrent commands. A failed transaction
    rolls back its reservation; only committed responses can be replayed.
    """
    fingerprint = _fingerprint(body)
    prior = db.scalar(select(CommandReceipt).where(CommandReceipt.command_id == body.command_id))
    if prior:
        recheck()
        return None, _match_receipt(prior, user, operation, entity_id, fingerprint)
    receipt = CommandReceipt(command_id=body.command_id, actor_id=user.id, actor_role=user.role,
                             operation=operation, entity_id=entity_id, fingerprint=fingerprint, response={})
    db.add(receipt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        recheck()
        prior = db.scalar(select(CommandReceipt).where(CommandReceipt.command_id == body.command_id))
        if prior:
            return None, _match_receipt(prior, user, operation, entity_id, fingerprint)
        raise HTTPException(409, "Another change is in progress. Refresh and retry.")
    except OperationalError:
        db.rollback()
        raise HTTPException(409, "Another change is in progress. Retry this action.")
    return receipt, None


def _cas(db, row, expected_revision, changes, bump_operations=False):
    """One conditional SQL write prevents a stale client from overwriting newer work."""
    # The authorized snapshot must itself match the client's revision. A guessed
    # future revision must not become valid after ownership changes concurrently.
    if row.revision != expected_revision:
        db.rollback()
        raise HTTPException(409, "This record changed. Refresh before saving again.")
    # Lock after the command receipt, consistently across mutation routes. This
    # also preserves the independent operations revision if Operations just edited.
    locked = db.scalar(select(type(row)).where(type(row).id == row.id).with_for_update()
                       .execution_options(populate_existing=True))
    if locked is None or row.revision != expected_revision:
        db.rollback()
        raise HTTPException(409, "This record changed. Refresh before saving again.")
    if bump_operations:
        changes = {**changes, "operations_revision": row.operations_revision + 1}
    values = {**changes, "revision": expected_revision + 1, "updated_at": now()}
    old = {key: getattr(row, key) for key in values}
    try:
        result = db.execute(update(type(row)).where(type(row).id == row.id, type(row).revision == expected_revision)
                            .values(**values).execution_options(synchronize_session=False))
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "Another change conflicts with this update. Refresh and retry.")
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "This record changed. Refresh before saving again.")
    # Bulk SQL bypasses ORM change tracking, so record its exact before/after values.
    audit_values = {k: [str(old[k]) if isinstance(old[k], Decimal) else old[k],
                       str(v) if isinstance(v, Decimal) else v] for k, v in values.items() if old[k] != v}
    db.add(AuditLog(user_id=db.info.get("user_id"), table_name=row.__tablename__, row_id=row.id,
                    action="update", changes=jsonable_encoder(audit_values)))
    db.refresh(row)


def _commit(db, response, receipt=None):
    encoded = jsonable_encoder(response)
    if receipt is not None:
        receipt.response = encoded
    try:
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "This change conflicts with another saved record. Refresh and retry.")
    return encoded


def _work_context(opportunity):
    return {"opportunity_name": opportunity.name, "customer": opportunity.customer,
            "email": opportunity.email, "phone": opportunity.phone, "address": opportunity.address,
            "scope": opportunity.work_scope, "sales_owner_name": opportunity.owner.name}


def _refresh_open_work(db, opportunity):
    context = _work_context(opportunity)
    rows = list(db.scalars(select(WorkRequest).where(WorkRequest.opportunity_id == opportunity.id,
                                                   WorkRequest.status != "Complete")))
    for request in rows:
        if request.context != context:
            _cas(db, request, request.revision, {"context": context})


def _ensure_work(db, opportunity, previous_stage=None):
    if opportunity.stage not in ("Design", "Estimate"):
        return
    requests = list(db.scalars(select(WorkRequest).where(WorkRequest.opportunity_id == opportunity.id,
                                WorkRequest.kind == opportunity.stage).order_by(WorkRequest.version.desc())))
    if any(r.status != "Complete" for r in requests):
        return
    # A same-stage edit after completion keeps history. Explicit reentry creates a new version.
    if requests and previous_stage == opportunity.stage:
        return
    db.add(WorkRequest(opportunity_id=opportunity.id, kind=opportunity.stage, context=_work_context(opportunity),
                       version=requests[0].version + 1 if requests else 1, status="Queued"))
    db.flush()


@router.get("/context")
def context(db: Session = Depends(get_db), user: User = Depends(current_user)):
    users = [] if user.role not in WORKSPACE else [
        {"id": u.id, "name": u.name, "role": u.role}
        for u in db.scalars(select(User).where(User.active.is_(True), User.role.in_(WORKSPACE)).order_by(User.name))]
    providers = (("gmail", "Gmail / Google Workspace", "Mailbox sending and replies are not connected."),
                 ("telnyx", "Calls and text messages", "Calling and SMS are not connected."),
                 ("resend", "Email campaigns", "Campaign sending is not connected."),
                 ("smartlead", "Outbound sequences", "Outbound sequences are not connected."))
    return {"environment": os.environ.get("WORKSPACE_ENVIRONMENT", "live"), "finance_source": "Job Tracker",
            "capabilities": {"sales": user.role in SALES, "operations": user.role in BACK_OFFICE,
                             "finance": user.role in SALES}, "users": users,
            "channels": [{"id": id_, "label": label, "status": "not_configured", "detail": detail}
                         for id_, label, detail in providers]}


@router.get("/opportunities")
def opportunities(scope: Literal["mine", "shared", "all"] = "mine", search: str = "", stage: Stage | None = None,
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, SALES)
    q = select(Opportunity).where(_sales_filter(user)).options(selectinload(Opportunity.owner))
    if scope == "mine":
        q = q.where(Opportunity.owner_id == user.id, Opportunity.visibility != "shared")
    elif scope == "shared":
        q = q.where(Opportunity.visibility == "shared")
    if stage:
        q = q.where(Opportunity.stage == stage)
    if search.strip():
        term = search.strip()[:200]
        q = q.where(or_(*(getattr(Opportunity, field).icontains(term, autoescape=True)
                          for field in ("name", "customer", "email", "phone"))))
    return [_opportunity_out(row) for row in db.scalars(q.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc()))]


@router.post("/opportunities")
def create_opportunity(body: OpportunityIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, SALES)
    data = body.model_dump()
    data["owner_id"] = body.owner_id or user.id
    _check_owner(db, user, data["owner_id"])
    row = Opportunity(**data)
    db.add(row)
    db.flush()
    _ensure_work(db, row)
    return _commit(db, _opportunity_out(row))


@router.get("/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _read_opportunity(db, user, opportunity_id)
    acts = db.scalars(select(Activity).where(Activity.opportunity_id == row.id).order_by(Activity.created_at.desc(), Activity.id.desc()))
    requests = db.scalars(select(WorkRequest).where(WorkRequest.opportunity_id == row.id).order_by(WorkRequest.id))
    return {**_opportunity_out(row), "activities": [_activity_out(a) for a in acts], "requests": [_request_out(r) for r in requests]}


@router.patch("/opportunities/{opportunity_id}")
def patch_opportunity(opportunity_id: int, body: OpportunityPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _edit_opportunity(db, user, opportunity_id)
    receipt, replay = _begin_command(db, user, "opportunity.patch", row.id, body,
                                     lambda: _edit_opportunity(db, user, opportunity_id))
    if replay is not None:
        return replay
    changes = body.model_dump(exclude_unset=True, exclude={"expected_revision", "command_id"})
    if "owner_id" in changes:
        _check_owner(db, user, changes["owner_id"])
    previous = row.stage
    actual_changes = {key for key, value in changes.items() if getattr(row, key) != value}
    affects_preorder = bool(actual_changes & {"name", "customer", "email", "phone", "address", "work_scope", "owner_id", "stage"})
    _cas(db, row, body.expected_revision, changes,
         bump_operations=affects_preorder and (previous == "Won" or changes.get("stage") == "Won"))
    if {"name", "customer", "email", "phone", "address", "work_scope", "owner_id"} & changes.keys():
        _refresh_open_work(db, row)
    _ensure_work(db, row, previous)
    return _commit(db, _opportunity_out(row), receipt)


@router.post("/opportunities/{opportunity_id}/claim")
def claim_opportunity(opportunity_id: int, body: Command, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _read_opportunity(db, user, opportunity_id)
    receipt, replay = _begin_command(db, user, "opportunity.claim", row.id, body,
                                     lambda: _read_opportunity(db, user, opportunity_id))
    if replay is not None:
        return replay
    if row.visibility != "shared":
        raise HTTPException(409, "This lead is no longer available in the shared pool.")
    _cas(db, row, body.expected_revision, {"visibility": "private", "owner_id": user.id},
         bump_operations=row.stage == "Won" and row.owner_id != user.id)
    _refresh_open_work(db, row)
    return _commit(db, _opportunity_out(row), receipt)


@router.post("/opportunities/{opportunity_id}/activities")
def add_activity(opportunity_id: int, body: ActivityIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _edit_opportunity(db, user, opportunity_id)
    # Lock/check ownership at commit time too; this route has no client revision field.
    _cas(db, row, row.revision, {})
    activity = Activity(opportunity_id=row.id, actor_id=user.id, kind=body.kind, body=body.body)
    db.add(activity)
    db.flush()
    return _commit(db, _activity_out(activity))


@router.post("/opportunities/{opportunity_id}/link-job")
def link_job(opportunity_id: int, body: LinkIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _edit_opportunity(db, user, opportunity_id)
    receipt, replay = _begin_command(db, user, "opportunity.link_job", row.id, body,
                                     lambda: _edit_opportunity(db, user, opportunity_id))
    if replay is not None:
        return replay
    # Keep the finance ownership used for authorization stable until this link commits.
    job = db.scalar(select(Job).where(Job.id == body.job_id).with_for_update())
    if not job or (user.role == "rep" and job.rep_id != user.id):
        raise HTTPException(404, "Job Tracker record is unavailable.")
    if row.linked_job_id not in (None, body.job_id):
        raise HTTPException(409, "This opportunity is already linked to a different Job Tracker record.")
    if db.scalar(select(Opportunity.id).where(Opportunity.linked_job_id == job.id, Opportunity.id != row.id)):
        raise HTTPException(409, "That Job Tracker record is already linked to another opportunity.")
    _cas(db, row, body.expected_revision, {"linked_job_id": job.id},
         bump_operations=row.stage == "Won" and row.linked_job_id != job.id)
    return _commit(db, _opportunity_out(row), receipt)


def _preorder_out(row):
    return {"id": row.id, "name": row.name, "customer": row.customer, "email": row.email,
            "phone": row.phone, "address": row.address, "scope": row.work_scope,
            "sales_owner_name": row.owner.name, "status": row.operations_stage,
            "due_date": row.operations_due_date, "revision": row.operations_revision}


def _preorder_access(db, user, opportunity_id, write=False):
    _role(user, ("owner", "operations") if write else WORKSPACE)
    q = select(Opportunity).where(Opportunity.id == opportunity_id, Opportunity.stage == "Won",
                                  Opportunity.linked_job_id.is_(None))
    if user.role == "rep":
        q = q.where(_sales_filter(user))
    row = db.scalar(q.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Pre-order job is unavailable.")
    return row


@router.get("/preorder-jobs")
def preorder_jobs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, WORKSPACE)
    q = select(Opportunity).where(Opportunity.stage == "Won", Opportunity.linked_job_id.is_(None))
    if user.role == "rep":
        q = q.where(_sales_filter(user))
    return [_preorder_out(row) for row in db.scalars(q.options(selectinload(Opportunity.owner))
                                                    .order_by(Opportunity.updated_at.desc(), Opportunity.id.desc()))]


@router.patch("/preorder-jobs/{opportunity_id}")
def patch_preorder(opportunity_id: int, body: PreorderPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _preorder_access(db, user, opportunity_id, write=True)
    receipt, replay = _begin_command(db, user, "preorder.patch", row.id, body,
                                     lambda: _preorder_access(db, user, opportunity_id, write=True))
    if replay is not None:
        return replay
    if row.operations_revision != body.expected_revision:
        db.rollback()
        raise HTTPException(409, "This pre-order job changed. Refresh before saving.")
    # A sales revocation/link and an operations change serialize on this row.
    locked = db.scalar(select(Opportunity).where(Opportunity.id == row.id).with_for_update()
                       .execution_options(populate_existing=True))
    if locked is None or row.stage != "Won" or row.linked_job_id is not None:
        db.rollback()
        raise HTTPException(404, "Pre-order job is unavailable.")
    if row.operations_revision != body.expected_revision:
        db.rollback()
        raise HTTPException(409, "This pre-order job changed. Refresh before saving.")
    changes = {"operations_revision": body.expected_revision + 1, "updated_at": now()}
    if "status" in body.model_fields_set:
        changes["operations_stage"] = body.status
    if "due_date" in body.model_fields_set:
        changes["operations_due_date"] = body.due_date
    before = {key: getattr(row, key) for key in changes}
    try:
        result = db.execute(update(Opportunity).where(Opportunity.id == row.id,
                           Opportunity.operations_revision == body.expected_revision,
                           Opportunity.stage == "Won", Opportunity.linked_job_id.is_(None))
                           .values(**changes).execution_options(synchronize_session=False))
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "Another change is in progress. Refresh and retry.")
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "This pre-order job changed. Refresh before saving.")
    db.add(AuditLog(user_id=user.id, table_name=row.__tablename__, row_id=row.id, action="update",
                    changes=jsonable_encoder({key: [before[key], value] for key, value in changes.items()
                                               if before[key] != value})))
    db.refresh(row)
    return _commit(db, _preorder_out(row), receipt)


@router.get("/requests")
def requests(status: WorkState | None = None, kind: Literal["Design", "Estimate"] | None = None,
             db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, WORKSPACE)
    q = select(WorkRequest).options(selectinload(WorkRequest.assigned_to), selectinload(WorkRequest.opportunity))
    if user.role == "rep":
        q = q.join(Opportunity).where(_sales_filter(user))
    if status:
        q = q.where(WorkRequest.status == status)
    if kind:
        q = q.where(WorkRequest.kind == kind)
    return [_request_out(row) for row in db.scalars(q.order_by(WorkRequest.created_at.desc(), WorkRequest.id.desc()))]


@router.patch("/requests/{request_id}")
def patch_request(request_id: int, body: RequestPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _request_access(db, user, request_id, write=True)
    receipt, replay = _begin_command(db, user, "request.patch", row.id, body,
                                     lambda: _request_access(db, user, request_id, write=True))
    if replay is not None:
        return replay
    changes = body.model_dump(exclude_unset=True, exclude={"expected_revision", "command_id"})
    if changes.get("assigned_to_id") is not None:
        assignee = db.get(User, changes["assigned_to_id"])
        if not assignee or not assignee.active or assignee.role not in BACK_OFFICE:
            raise HTTPException(422, "Assign this request to an active operations, design, or admin user.")
    target = changes.get("status", row.status)
    result = changes.get("result", row.result)
    if target == "Complete" and not (result or "").strip():
        raise HTTPException(422, "Add the completed result before marking this request complete.")
    if row.status == "Complete" and target != "Complete":
        raise HTTPException(409, "Completed work stays in history. Reenter Design or Estimate in Sales to request a new revision.")
    just_completed = row.status != "Complete" and target == "Complete"
    _cas(db, row, body.expected_revision, changes)
    if just_completed:
        db.add(Activity(opportunity_id=row.opportunity_id, actor_id=user.id, kind="Note",
                        body=f"{row.kind} version {row.version} completed: {result}"))
    return _commit(db, _request_out(row), receipt)


def _jobs(db, user, team=False):
    q = select(Job).options(selectinload(Job.rep), selectinload(Job.ledger), selectinload(Job.labor),
                           selectinload(Job.assembly), selectinload(Job.designs), selectinload(Job.reimbursements), selectinload(Job.payments))
    if user.role == "rep" and not team:
        q = q.where(Job.rep_id == user.id)
    return list(db.scalars(q.order_by(Job.created_at.desc(), Job.id.desc())))


def _job_out(job, user):
    base = {"id": job.id, "name": job.name, "customer": job.customer, "rep_id": job.rep_id,
            "rep_name": job.rep.name if job.rep else None, "status": job.status, "install_date": job.install_date}
    if user.role not in SALES:
        return base
    # Import the authoritative eligibility helper at call time to avoid a main/router cycle.
    from .main import commission_is_due
    w = compute(job)
    known = not w["material_pending"] and not w["costs_waiting"]
    due = commission_is_due(job, w)
    paid = job.commission_paid_at is not None
    return {**base, "invoice_total": str(money(job.invoice_total)), "collected": w["collected"],
            "balance_due": w["balance_due"],
            "commission_forecast": (w["commission"] if known else None) if not due and not paid else "0.00",
            "commission_earned": w["commission"] if due else "0.00",
            "commission_paid": (w["commission"] if known else None) if paid else "0.00"}


@router.get("/tracker-jobs")
def tracker_jobs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, WORKSPACE)
    return [_job_out(job, user) for job in _jobs(db, user)]


def _schedule_access(db, user, job_id):
    _role(user, ("owner", "operations"))
    job = db.scalar(select(Job).where(Job.id == job_id).execution_options(populate_existing=True))
    if job is None:
        raise HTTPException(404, "Job Tracker record is unavailable.")
    return job


@router.patch("/tracker-jobs/{job_id}/schedule")
def schedule_job(job_id: int, body: ScheduleIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = _schedule_access(db, user, job_id)
    receipt, replay = _begin_command(db, user, "job.schedule", job.id, body,
                                     lambda: _schedule_access(db, user, job_id))
    if replay is not None:
        return replay
    if job.status == "Closed":
        raise HTTPException(409, "Closed jobs must be reopened by the owner in Job Tracker before scheduling changes.")
    if (job.status, job.install_date) != (body.expected_status, body.expected_install_date):
        db.rollback()
        raise HTTPException(409, "This job's status or date changed. Refresh before saving.")
    changes = body.model_dump(exclude_unset=True, include={"status", "install_date"})
    before = {key: getattr(job, key) for key in changes}
    try:
        changed = db.execute(update(Job).where(Job.id == job_id, Job.status == body.expected_status,
                                               Job.install_date == body.expected_install_date)
                             .values(**changes).execution_options(synchronize_session=False))
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "Another scheduling change is in progress. Refresh and retry.")
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "This job's status or date changed. Refresh before saving.")
    db.add(AuditLog(user_id=user.id, table_name="jobs", row_id=job.id, action="update",
                    changes=jsonable_encoder({key: [before[key], value] for key, value in changes.items()
                                               if before[key] != value})))
    db.refresh(job)
    return _commit(db, _job_out(job, user), receipt)


def _sum_nullable(values):
    values = list(values)
    return None if any(value is None for value in values) else str(money(sum((Decimal(str(v)) for v in values), Decimal(0))))


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, WORKSPACE)
    today = datetime.now(ZoneInfo("America/Phoenix")).date()
    opps = []
    if user.role in SALES:
        q = select(Opportunity)
        if user.role == "rep":
            q = q.where(Opportunity.owner_id == user.id, Opportunity.visibility != "shared")
        opps = list(db.scalars(q))
    pipeline = [o for o in opps if o.stage not in ("Won", "Lost")]
    by_stage = [{"stage": stage, "count": len([o for o in pipeline if o.stage == stage]),
                 "value": _sum_nullable(o.amount for o in pipeline if o.stage == stage)} for stage in STAGES if stage not in ("Won", "Lost")]
    work_query = select(WorkRequest).where(WorkRequest.status != "Complete")
    if user.role == "rep":
        work_query = work_query.join(Opportunity).where(_sales_filter(user))
    work = list(db.scalars(work_query))
    jobs = _jobs(db, user)
    summaries = [_job_out(j, user) for j in jobs]
    finance_fields = ("collected", "balance_due", "commission_forecast", "commission_earned", "commission_paid")
    finance = {"source": "Job Tracker", "job_count": len(jobs),
               **{key: _sum_nullable(j[key] for j in summaries) if user.role in SALES else None for key in finance_fields}}
    month_start = today.replace(day=1)
    finance["collected_this_month"] = _sum_nullable(
        p.amount for job in jobs for p in job.payments
        if p.status == "received" and p.received_date is not None and month_start <= p.received_date <= today
    ) if user.role in SALES else None
    team_jobs = _jobs(db, user, team=True) if user.role in SALES else []
    team = {"collected": _sum_nullable(compute(j)["collected"] for j in team_jobs) if user.role in SALES else None,
            "job_count": len(team_jobs) if user.role in SALES else len(jobs)}
    pref = db.scalar(select(Preference).where(Preference.user_id == user.id))
    recent = []
    if user.role in SALES:
        acts = db.scalars(select(Activity).join(Opportunity).where(_sales_filter(user))
                          .order_by(Activity.created_at.desc(), Activity.id.desc()).limit(12))
        recent = [_activity_out(a) for a in acts]
    return {"pipeline": {"count": len(pipeline), "value": _sum_nullable(o.amount for o in pipeline),
                         "follow_ups_due": sum(o.next_follow_up is not None and o.next_follow_up <= today for o in pipeline),
                         "by_stage": by_stage},
            "work": {"open": len(work), "design": sum(r.kind == "Design" for r in work), "estimate": sum(r.kind == "Estimate" for r in work)},
            "finance": finance, "team": team,
            "goal": {"monthly_target": pref.settings.get("monthly_target") if pref else None}, "recent_activity": recent}


class PreferencesIn(Input):
    sales_columns: list[str] | None = Field(default=None, max_length=20)
    sales_view: Literal["table", "kanban"] | None = None
    sales_scope: Literal["mine", "shared", "all"] | None = None
    home_widgets: list[str] | None = Field(default=None, max_length=20)
    monthly_target: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

    @field_validator("sales_columns")
    @classmethod
    def columns(cls, value):
        allowed = {"name", "customer", "stage", "owner", "owner_name", "owner_id", "amount", "next_follow_up", "visibility", "email", "phone", "address", "linked_job_id"}
        if value is not None and (len(set(value)) != len(value) or any(v not in allowed for v in value)):
            raise ValueError("Choose supported sales columns without duplicates.")
        return value

    @field_validator("home_widgets")
    @classmethod
    def widgets(cls, value):
        allowed = {"pipeline", "followups", "collections", "balance", "commission", "work", "goal", "team"}
        if value is not None and (len(set(value)) != len(value) or any(v not in allowed for v in value)):
            raise ValueError("Choose supported widgets without duplicates.")
        return value


@router.get("/preferences")
def preferences(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, WORKSPACE)
    pref = db.scalar(select(Preference).where(Preference.user_id == user.id))
    return pref.settings if pref else {}


@router.put("/preferences")
def put_preferences(body: PreferencesIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, WORKSPACE)
    # Serializes first-save upserts and later edits for this user without a global lock.
    db.execute(update(User).where(User.id == user.id).values(active=User.active))
    pref = db.scalar(select(Preference).where(Preference.user_id == user.id))
    if pref is None:
        pref = Preference(user_id=user.id, settings={})
        db.add(pref)
    changes = body.model_dump(mode="json", exclude_unset=True)
    if "monthly_target" in changes and body.monthly_target is not None:
        changes["monthly_target"] = str(money(body.monthly_target))
    pref.settings = {**pref.settings, **changes}
    pref.updated_at = now()
    return _commit(db, pref.settings)


@router.get("/website-lead-alerts")
def website_lead_alerts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Team-wide actionable alerts; claimed leads disappear for everyone."""
    _role(user, SALES)
    from .website_leads import WebsiteLeadReceipt
    criteria = (Opportunity.visibility == "shared", Opportunity.stage.notin_(("Won", "Lost")))
    base = select(Opportunity).join(WebsiteLeadReceipt, WebsiteLeadReceipt.opportunity_id == Opportunity.id).where(*criteria)
    count = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(WebsiteLeadReceipt.source_sequence.desc()).limit(5))
    return {"count": count, "items": [{"id": row.id, "name": row.name} for row in rows]}
