"""Workspace API acceptance through real sessions and an isolated SQLite database.

Every test gets its own tmp_path database. No production engine is connected to,
no schema is dropped, and TestClient lifespan (which runs migrations) is not run.
See docs/PILOT-ACCEPTANCE.md for the explicit disposable DATABASE_URL command.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

if not os.environ.get("DATABASE_URL", "").startswith("sqlite:"):
    raise RuntimeError("Workspace tests require an explicit disposable SQLite DATABASE_URL")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.db import Base, get_db
from app.main import FAILED_LOGINS, app
from app.models import (
    AssemblyEntry, AuditLog, Delivery, DesignEntry, Job, LaborEntry, LedgerEntry,
    Payment, Reimbursement, User,
)

PREFIX = "/api/workspace"
PASSWORD = "local-test-password-only"
FINANCIAL_MODELS = (
    Job, LedgerEntry, Payment, LaborEntry, AssemblyEntry, DesignEntry,
    Reimbursement, Delivery,
)


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def denied(response):
    assert response.status_code in (403, 404), response.text


def command(revision, **changes):
    return {"expected_revision": revision, "command_id": str(uuid4()), **changes}


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def workspace(tmp_path, password_hash, monkeypatch):
    # This engine is constructed here, not imported from app.db. A fresh file
    # makes each test independent without a destructive drop_all operation.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace-test.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    roles = {
        "admin": "owner", "rep_a": "rep", "rep_b": "rep",
        "ops": "operations", "designer": "designer", "crew": "crew",
        "inactive": "rep",
    }
    with sessions() as db:
        users = {
            key: User(
                name=key, email=f"{key}@workspace.test", role=role,
                password_hash=password_hash, active=key != "inactive",
                commission_rate=Decimal("0.30") if role == "rep" else None,
            )
            for key, role in roles.items()
        }
        db.add_all(users.values())
        db.flush()
        ids = {key: user.id for key, user in users.items()}
        jobs = {
            "a_closed": Job(
                name="A closed tracker job", customer="A customer",
                rep_id=ids["rep_a"], invoice_total=Decimal("1000"),
                material_total=Decimal("400"), status="Closed",
                closed_at=datetime.now(timezone.utc),
            ),
            "a_open": Job(
                name="A open tracker job", customer="A next customer",
                rep_id=ids["rep_a"], invoice_total=Decimal("2000"),
                material_total=None, status="Ordered", install_date=date(2026, 11, 19),
            ),
            "b_closed": Job(
                name="B closed tracker job", customer="B customer",
                rep_id=ids["rep_b"], invoice_total=Decimal("3000"),
                material_total=Decimal("1000"), status="Closed",
                closed_at=datetime.now(timezone.utc),
            ),
        }
        db.add_all(jobs.values())
        db.flush()
        job_ids = {key: job.id for key, job in jobs.items()}
        db.add_all([
            Payment(job_id=job_ids["a_closed"], amount=Decimal("1000"), method="ach",
                    payment_type="final", status="received", received_date=date.today(), created_by=ids["admin"]),
            Payment(job_id=job_ids["a_open"], amount=Decimal("500"), method="check",
                    payment_type="deposit", status="received", received_date=date.today(), created_by=ids["admin"]),
            Payment(job_id=job_ids["a_open"], amount=Decimal("250"), method="check",
                    payment_type="progress", status="pending", expected_date=date.today(), created_by=ids["admin"]),
            Payment(job_id=job_ids["b_closed"], amount=Decimal("3000"), method="ach",
                    payment_type="final", status="received", received_date=date.today(), created_by=ids["admin"]),
        ])
        db.commit()

    def isolated_db():
        with sessions() as db:
            yield db

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = isolated_db
    FAILED_LOGINS.clear()
    monkeypatch.setenv("WORKSPACE_ENVIRONMENT", "local-pilot")
    clients = {}

    class Harness:
        user_ids = ids
        jobs = job_ids
        session = sessions

        def client(self, role="rep_a"):
            if role not in clients:
                # No context manager: intentionally do not run app migrations.
                client = TestClient(app, base_url="https://testserver")
                ok(client.post("/api/login", json={
                    "email": f"{role}@workspace.test", "password": PASSWORD,
                }))
                clients[role] = client
            return clients[role]

        def create(self, role="rep_a", **fields):
            return ok(self.client(role).post(f"{PREFIX}/opportunities", json={
                "name": f"Opportunity {uuid4()}", "customer": "Test Customer",
                "visibility": "private", **fields,
            }))

        def detail(self, opportunity, role="rep_a"):
            return ok(self.client(role).get(f"{PREFIX}/opportunities/{opportunity['id']}"))

        def patch(self, opportunity, role="rep_a", **changes):
            return ok(self.client(role).patch(
                f"{PREFIX}/opportunities/{opportunity['id']}",
                json=command(opportunity["revision"], **changes),
            ))

        def requests(self, role="ops"):
            return ok(self.client(role).get(f"{PREFIX}/requests"))

        def financial_snapshot(self):
            with sessions() as db:
                return {
                    model.__tablename__: [tuple(row) for row in db.execute(
                        select(model.__table__).order_by(model.__table__.c.id)
                    )]
                    for model in FINANCIAL_MODELS
                }

        def audit_count(self):
            with sessions() as db:
                return len(list(db.scalars(select(AuditLog))))

    try:
        yield Harness()
    finally:
        for client in clients.values():
            client.close()
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        FAILED_LOGINS.clear()
        engine.dispose()


def test_authentication_and_inactive_sessions(workspace):
    anonymous = TestClient(app, base_url="https://testserver")
    try:
        for endpoint in ("context", "opportunities", "requests", "tracker-jobs", "dashboard", "preferences"):
            assert anonymous.get(f"{PREFIX}/{endpoint}").status_code == 401
        assert anonymous.post("/api/login", json={
            "email": "inactive@workspace.test", "password": PASSWORD,
        }).status_code == 401
        active = workspace.client()
        with workspace.session() as db:
            db.get(User, workspace.user_ids["rep_a"]).active = False
            db.commit()
        assert active.get(f"{PREFIX}/opportunities").status_code == 401
        assert active.put(f"{PREFIX}/preferences", json={"sales_view": "table"}).status_code == 401
    finally:
        anonymous.close()


def test_private_leads_are_scoped_on_lists_search_and_direct_routes(workspace):
    a = workspace.create(name="Private farmed A", notes="PRIVATE-A-NOTES")
    b = workspace.create("rep_b", name="Private farmed B", notes="PRIVATE-B-NOTES")
    shared = workspace.create("admin", name="Shared inbound", visibility="shared")
    for role, allowed, hidden in (("rep_a", a, b), ("rep_b", b, a)):
        client = workspace.client(role)
        rows = ok(client.get(f"{PREFIX}/opportunities?scope=all"))
        assert {r["id"] for r in rows} == {allowed["id"], shared["id"]}
        assert [r["id"] for r in ok(client.get(f"{PREFIX}/opportunities?scope=mine"))] == [allowed["id"]]
        assert [r["id"] for r in ok(client.get(f"{PREFIX}/opportunities?scope=shared"))] == [shared["id"]]
        assert ok(client.get(f"{PREFIX}/opportunities", params={"search": hidden["name"]})) == []
        path = f"{PREFIX}/opportunities/{hidden['id']}"
        denied(client.get(path))
        denied(client.patch(path, json=command(hidden["revision"], notes="stolen")))
        denied(client.post(f"{path}/activities", json={"kind": "Note", "body": "intrusion"}))
        denied(client.post(f"{path}/link-job", json=command(hidden["revision"], job_id=workspace.jobs["a_open"])))
    assert len(ok(workspace.client("admin").get(f"{PREFIX}/opportunities?scope=all"))) == 3


@pytest.mark.parametrize("role", ["ops", "designer", "crew"])
def test_non_sales_roles_cannot_open_or_mutate_full_crm(workspace, role):
    lead = workspace.create(stage="Design", work_scope="Kitchen layout", notes="PRIVATE-SALES-NOTES")
    client = workspace.client(role)
    denied(client.get(f"{PREFIX}/opportunities"))
    denied(client.get(f"{PREFIX}/opportunities/{lead['id']}"))
    denied(client.post(f"{PREFIX}/opportunities", json={"name": "No", "customer": "No"}))
    denied(client.patch(f"{PREFIX}/opportunities/{lead['id']}", json=command(lead["revision"], stage="Won")))
    denied(client.get("/api/jobs"))
    denied(client.get(f"/api/jobs/{workspace.jobs['a_closed']}"))
    denied(client.get("/api/collections"))
    if role == "crew":
        denied(client.get(f"{PREFIX}/requests"))
        denied(client.get(f"{PREFIX}/tracker-jobs"))
    else:
        response = client.get(f"{PREFIX}/requests")
        rows = ok(response)
        assert len(rows) == 1 and rows[0]["scope"] == "Kitchen layout"
        assert "notes" not in rows[0]
        assert "PRIVATE-SALES-NOTES" not in response.text


def test_shared_claim_is_explicit_atomic_and_replayable(workspace):
    shared = workspace.create("admin", visibility="shared")
    path = f"{PREFIX}/opportunities/{shared['id']}"
    a, b = workspace.client("rep_a"), workspace.client("rep_b")
    denied(a.patch(path, json=command(shared["revision"], notes="edit without claim")))
    payload = command(shared["revision"])
    claimed = ok(a.post(f"{path}/claim", json=payload))
    assert claimed["owner_id"] == workspace.user_ids["rep_a"]
    assert claimed["visibility"] == "private"
    audit_count = workspace.audit_count()
    assert ok(a.post(f"{path}/claim", json=payload)) == claimed
    assert workspace.audit_count() == audit_count
    collision = b.post(f"{path}/claim", json=command(shared["revision"]))
    assert collision.status_code in (404, 409), collision.text
    denied(b.get(path))
    assert workspace.detail(claimed)["owner_id"] == workspace.user_ids["rep_a"]
    assert ok(b.get(f"{PREFIX}/opportunities?scope=shared")) == []


def test_simultaneous_claims_have_exactly_one_winner(workspace):
    shared = workspace.create("admin", visibility="shared")
    contenders = [workspace.client("rep_a"), workspace.client("rep_b")]
    barrier = Barrier(2)

    def claim(client):
        barrier.wait(timeout=10)
        return client.post(f"{PREFIX}/opportunities/{shared['id']}/claim", json=command(shared["revision"]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(claim, contenders))
    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in (200, 404, 409) for response in responses)
    winner = next(response.json() for response in responses if response.status_code == 200)
    stored = workspace.detail(shared, "admin")
    assert stored["visibility"] == "private"
    assert stored["owner_id"] == winner["owner_id"]


def test_rep_must_claim_even_a_shared_lead_they_created(workspace):
    shared = workspace.create(visibility="shared")
    path = f"{PREFIX}/opportunities/{shared['id']}"
    client = workspace.client()
    denied(client.patch(path, json=command(shared["revision"], notes="Unclaimed edit")))
    denied(client.post(f"{path}/activities", json={"kind": "Note", "body": "Unclaimed note"}))
    denied(client.post(f"{path}/link-job", json=command(shared["revision"], job_id=workspace.jobs["a_open"])))
    claimed = ok(client.post(f"{path}/claim", json=command(shared["revision"])))
    assert claimed["owner_name"] == "rep_a"
    assert workspace.patch(claimed, notes="Claimed edit")["notes"] == "Claimed edit"


def test_claim_and_admin_reassignment_refresh_work_owner_and_revoke_replay_access(workspace):
    shared = workspace.create("admin", visibility="shared", stage="Design")
    path = f"{PREFIX}/opportunities/{shared['id']}"
    original = workspace.requests()[0]
    assert original["sales_owner_name"] == "admin"
    payload = command(shared["revision"])
    claimed = ok(workspace.client().post(f"{path}/claim", json=payload))
    assert claimed["owner_name"] == "rep_a"
    assert workspace.requests()[0]["sales_owner_name"] == "rep_a"
    assigned = workspace.patch(claimed, "admin", owner_id=workspace.user_ids["rep_b"])
    assert assigned["owner_name"] == "rep_b"
    assert workspace.requests()[0]["sales_owner_name"] == "rep_b"
    denied(workspace.client().get(path))
    denied(workspace.client().post(f"{path}/claim", json=payload))
    assert workspace.detail(assigned, "rep_b")["owner_id"] == workspace.user_ids["rep_b"]


def test_command_receipts_cannot_be_reused_for_different_payload_or_user(workspace):
    a, b = workspace.create(), workspace.create("rep_b")
    path = f"{PREFIX}/opportunities/{a['id']}"
    body = command(a["revision"], notes="SECRET-REPLAY-CONTENT")
    saved = ok(workspace.client().patch(path, json=body))
    different_body = {**body, "notes": "Different command contents"}
    assert workspace.client().patch(path, json=different_body).status_code == 409
    other_user = workspace.client("rep_b").patch(path, json=body)
    assert other_user.status_code in (403, 404, 409)
    assert "SECRET-REPLAY-CONTENT" not in other_user.text
    other_entity = workspace.client("rep_b").patch(
        f"{PREFIX}/opportunities/{b['id']}", json={**body, "expected_revision": b["revision"]},
    )
    assert other_entity.status_code == 409
    assert "SECRET-REPLAY-CONTENT" not in other_entity.text
    assert workspace.detail(saved)["notes"] == "SECRET-REPLAY-CONTENT"


@pytest.mark.parametrize("stage", ["Design", "Estimate"])
def test_handoff_is_immediate_without_deposit_and_does_not_create_finance_job(workspace, stage):
    before = workspace.financial_snapshot()
    lead = workspace.create(stage=stage, amount=None, work_scope=f"Need {stage.lower()}", notes="PRIVATE-NOTES")
    requests = workspace.requests()
    assert len(requests) == 1
    request = requests[0]
    assert request["opportunity_id"] == lead["id"]
    assert request["kind"] == stage and request["status"] == "Queued"
    assert request["scope"] == f"Need {stage.lower()}"
    assert request["version"] == 1
    assert request["linked_job_id"] is None
    assert "notes" not in request
    assert lead["linked_job_id"] is None
    assert workspace.financial_snapshot() == before


def test_handoff_replay_same_stage_stale_edits_and_open_request_reuse(workspace):
    lead = workspace.create(stage="New Lead")
    path = f"{PREFIX}/opportunities/{lead['id']}"
    body = command(lead["revision"], stage="Design", work_scope="Initial layout")
    changed = ok(workspace.client().patch(path, json=body))
    detail = workspace.detail(changed)
    audit_count = workspace.audit_count()
    assert len(detail["requests"]) == 1
    assert ok(workspace.client().patch(path, json=body)) == changed
    assert workspace.detail(changed) == detail
    assert workspace.audit_count() == audit_count
    stale = workspace.client().patch(path, json=command(lead["revision"], name="overwrite"))
    assert stale.status_code == 409, stale.text
    repeated = workspace.patch(changed, stage="Design")
    assert len(workspace.requests()) == 1
    left = workspace.patch(repeated, stage="Follow up")
    reentered = workspace.patch(left, stage="Design")
    requests = workspace.detail(reentered)["requests"]
    assert len(requests) == 1 and requests[0]["version"] == 1


def test_future_revisions_cannot_mutate_opportunities_claims_or_work(workspace):
    lead = workspace.create(stage="Design")
    request = workspace.requests()[0]
    before = workspace.detail(lead)
    audit_count = workspace.audit_count()
    response = workspace.client().patch(f"{PREFIX}/opportunities/{lead['id']}", json=command(
        lead["revision"] + 1, notes="Guess the next revision", stage="Estimate",
    ))
    assert response.status_code == 409, response.text
    response = workspace.client("ops").patch(f"{PREFIX}/requests/{request['id']}", json=command(
        request["revision"] + 1, status="Complete", result="Guess the next work revision",
    ))
    assert response.status_code == 409, response.text
    assert workspace.detail(lead) == before
    assert workspace.audit_count() == audit_count
    shared = workspace.create("admin", visibility="shared")
    response = workspace.client().post(f"{PREFIX}/opportunities/{shared['id']}/claim", json=command(
        shared["revision"] + 1,
    ))
    assert response.status_code == 409, response.text
    unchanged = workspace.detail(shared, "admin")
    assert unchanged["visibility"] == "shared"
    assert unchanged["owner_id"] == workspace.user_ids["admin"]
    assert unchanged["revision"] == shared["revision"]


def test_completed_work_returns_to_sales_and_explicit_reentry_creates_version(workspace):
    lead = workspace.create(stage="Design")
    request = workspace.requests()[0]
    path = f"{PREFIX}/requests/{request['id']}"
    ops = workspace.client("ops")
    missing_result = ops.patch(path, json=command(request["revision"], status="Complete"))
    assert missing_result.status_code == 422, missing_result.text
    result = "Design ready: move pantry left; customer approval needed."
    payload = command(request["revision"], status="Complete", result=result)
    complete = ok(ops.patch(path, json=payload))
    assert complete["status"] == "Complete"
    audit_count = workspace.audit_count()
    assert ok(ops.patch(path, json=payload)) == complete
    assert workspace.audit_count() == audit_count
    detail = workspace.detail(lead)
    assert detail["requests"][0]["result"] == result
    assert workspace.requests("rep_b") == []
    denied(workspace.client().patch(path, json=command(complete["revision"], result="rep edits work")))
    stale = ops.patch(path, json=command(request["revision"], result="stale overwrite"))
    assert stale.status_code == 409, stale.text
    saved = workspace.patch(detail, stage="Design")
    assert len(workspace.requests()) == 1  # Saving the same completed stage is not a revision request.
    left = workspace.patch(saved, stage="Follow up")
    reentered = workspace.patch(left, stage="Design", work_scope="Customer requested revision")
    requests = workspace.detail(reentered)["requests"]
    assert sorted(r["version"] for r in requests) == [1, 2]
    assert {r["status"] for r in requests} == {"Complete", "Queued"}
    assert next(r for r in requests if r["version"] == 1)["result"] == result


def test_operations_assignment_validation_and_request_filters(workspace):
    workspace.create(stage="Design")
    workspace.create("rep_b", stage="Estimate")
    request = next(r for r in workspace.requests() if r["kind"] == "Design")
    path = f"{PREFIX}/requests/{request['id']}"
    ops = workspace.client("ops")
    for user_id in (workspace.user_ids["crew"], workspace.user_ids["inactive"], 999999):
        response = ops.patch(path, json=command(request["revision"], assigned_to_id=user_id))
        assert response.status_code == 422, response.text
    assigned = ok(ops.patch(path, json=command(
        request["revision"], assigned_to_id=workspace.user_ids["designer"],
        status="In Progress", due_date="2026-11-19",
    )))
    assert assigned["assigned_to_id"] == workspace.user_ids["designer"]
    assert assigned["due_date"] == "2026-11-19"
    rows = ok(ops.get(f"{PREFIX}/requests?kind=Design&status=In%20Progress"))
    assert [r["id"] for r in rows] == [request["id"]]


def test_open_request_context_refreshes_but_completed_history_is_preserved(workspace):
    lead = workspace.create(stage="Design", customer="Original customer", work_scope="Original scope")
    original = workspace.requests()[0]
    changed = workspace.patch(lead, customer="Updated customer", phone="555-0100", work_scope="Updated scope")
    refreshed = workspace.requests()[0]
    assert refreshed["id"] == original["id"]
    assert refreshed["scope"] == "Updated scope"
    assert refreshed["customer"] == "Updated customer" and refreshed["phone"] == "555-0100"
    assert refreshed["revision"] > original["revision"]
    stale = workspace.client("ops").patch(f"{PREFIX}/requests/{original['id']}", json=command(
        original["revision"], status="In Progress",
    ))
    assert stale.status_code == 409
    completed = ok(workspace.client("ops").patch(f"{PREFIX}/requests/{refreshed['id']}", json=command(
        refreshed["revision"], status="Complete", result="Design delivered",
    )))
    workspace.patch(changed, customer="Third customer label", work_scope="Different later work")
    historical = workspace.requests()[0]
    assert historical["scope"] == completed["scope"]
    assert historical["customer"] == completed["customer"]
    assert historical["revision"] == completed["revision"]


def test_link_existing_tracker_job_is_scoped_unique_and_financially_read_only(workspace):
    lead = workspace.create(stage="Estimate", amount="99999.00")
    before = workspace.financial_snapshot()
    client = workspace.client()
    path = f"{PREFIX}/opportunities/{lead['id']}/link-job"
    response = client.post(path, json=command(lead["revision"], job_id=workspace.jobs["b_closed"]))
    assert response.status_code in (403, 404), response.text
    response = client.post(path, json=command(lead["revision"], job_id=999999))
    assert response.status_code == 404, response.text
    payload = command(lead["revision"], job_id=workspace.jobs["a_open"])
    linked = ok(client.post(path, json=payload))
    assert linked["linked_job_id"] == workspace.jobs["a_open"]
    audit_count = workspace.audit_count()
    assert ok(client.post(path, json=payload)) == linked
    assert workspace.audit_count() == audit_count
    other = workspace.create()
    duplicate = client.post(f"{PREFIX}/opportunities/{other['id']}/link-job", json=command(
        other["revision"], job_id=workspace.jobs["a_open"],
    ))
    assert duplicate.status_code == 409, duplicate.text
    linked = workspace.patch(linked, amount="1.00", stage="Won")
    ok(client.post(f"{PREFIX}/opportunities/{linked['id']}/activities", json={"kind": "Note", "body": "Logged only"}))
    request = workspace.requests()[0]
    ok(workspace.client("ops").patch(f"{PREFIX}/requests/{request['id']}", json=command(
        request["revision"], status="Complete", result="Estimate returned to sales",
    )))
    assert workspace.financial_snapshot() == before
    assert workspace.requests()[0]["linked_job_id"] == workspace.jobs["a_open"]


def test_tracker_summaries_and_dashboard_finance_use_real_owner_scope(workspace):
    a_rows = ok(workspace.client().get(f"{PREFIX}/tracker-jobs"))
    assert {row["id"] for row in a_rows} == {workspace.jobs["a_open"], workspace.jobs["a_closed"]}
    by_id = {row["id"]: row for row in a_rows}
    closed = by_id[workspace.jobs["a_closed"]]
    assert Decimal(closed["collected"]) == Decimal("1000")
    assert Decimal(closed["balance_due"]) == 0
    assert Decimal(closed["commission_earned"]) == Decimal("180")
    opened = by_id[workspace.jobs["a_open"]]
    assert Decimal(opened["collected"]) == Decimal("500")  # Pending $250 is not received.
    assert Decimal(opened["balance_due"]) == Decimal("1500")
    assert opened["commission_forecast"] is None  # Unknown material cost stays unknown.
    for row in a_rows:
        assert not {"waterfall", "material_total", "job_profit", "costs"} & row.keys()
    assert [r["id"] for r in ok(workspace.client("rep_b").get(f"{PREFIX}/tracker-jobs"))] == [workspace.jobs["b_closed"]]
    dashboard = ok(workspace.client().get(f"{PREFIX}/dashboard"))
    assert dashboard["finance"]["source"] == "Job Tracker"
    assert Decimal(dashboard["finance"]["collected"]) == Decimal("1500")
    assert Decimal(dashboard["finance"]["balance_due"]) == Decimal("1500")
    assert Decimal(dashboard["finance"]["commission_earned"]) == Decimal("180")
    assert dashboard["finance"]["job_count"] == 2
    assert Decimal(dashboard["team"]["collected"]) == Decimal("4500")
    assert not {"commission", "commission_earned", "jobs", "opportunities"} & dashboard["team"].keys()
    for role in ("ops", "designer"):
        for row in ok(workspace.client(role).get(f"{PREFIX}/tracker-jobs")):
            for key in ("commission_forecast", "commission_earned", "commission_paid", "material_total", "waterfall"):
                assert row.get(key) is None
        finance = ok(workspace.client(role).get(f"{PREFIX}/dashboard"))["finance"]
        for key in ("commission_forecast", "commission_earned", "commission_paid"):
            assert finance.get(key) is None


def test_monthly_collections_exclude_prior_month_pending_and_other_reps(workspace):
    with workspace.session() as db:
        payment = db.scalar(select(Payment).where(Payment.job_id == workspace.jobs["a_closed"]))
        payment.received_date = date(2000, 1, 1)
        db.commit()
    finance = ok(workspace.client().get(f"{PREFIX}/dashboard"))["finance"]
    assert Decimal(finance["collected"]) == Decimal("1500")
    assert Decimal(finance["collected_this_month"]) == Decimal("500")


def test_operations_schedule_is_scoped_replayable_and_preserves_financial_values(workspace):
    job_id = workspace.jobs["a_open"]
    path = f"{PREFIX}/tracker-jobs/{job_id}/schedule"
    before = workspace.financial_snapshot()
    payload = {
        "command_id": str(uuid4()), "expected_status": "Ordered",
        "expected_install_date": "2026-11-19", "status": "Assembled", "install_date": "2026-11-20",
    }
    for role in ("rep_a", "rep_b", "designer", "crew"):
        denied(workspace.client(role).patch(path, json=payload))
    ops = workspace.client("ops")
    scheduled = ok(ops.patch(path, json=payload))
    assert scheduled["status"] == "Assembled" and scheduled["install_date"] == "2026-11-20"
    assert not {"commission_earned", "invoice_total", "collected", "material_total"} & scheduled.keys()
    audit_count = workspace.audit_count()
    assert ok(ops.patch(path, json=payload)) == scheduled
    assert workspace.audit_count() == audit_count
    stale = ops.patch(path, json={**payload, "command_id": str(uuid4()), "install_date": "2026-11-21"})
    assert stale.status_code == 409
    after = workspace.financial_snapshot()
    for table in before:
        if table != "jobs":
            assert after[table] == before[table]
    columns = list(Job.__table__.columns.keys())
    for old, new in zip(before["jobs"], after["jobs"]):
        for index, column in enumerate(columns):
            if column not in ("status", "install_date"):
                assert new[index] == old[index], column
    valid = {"command_id": str(uuid4()), "expected_status": "Assembled", "expected_install_date": "2026-11-20"}
    for changes in ({"invoice_total": "1"}, {"rep_id": workspace.user_ids["ops"]}, {"status": "Closed"}, {}):
        assert ops.patch(path, json={**valid, **changes}).status_code == 422
    closed_path = f"{PREFIX}/tracker-jobs/{workspace.jobs['a_closed']}/schedule"
    assert ops.patch(closed_path, json={**valid, "expected_status": "Closed", "expected_install_date": None,
                                       "install_date": "2026-11-20"}).status_code == 409


def test_future_expected_schedule_state_is_rejected_without_writes(workspace):
    before = workspace.financial_snapshot()
    audit_count = workspace.audit_count()
    path = f"{PREFIX}/tracker-jobs/{workspace.jobs['a_open']}/schedule"
    for expected_status, expected_date in (
        ("Assembled", "2026-11-19"),
        ("Ordered", "2026-11-20"),
        ("Closed", "2026-11-20"),
    ):
        response = workspace.client("ops").patch(path, json={
            "command_id": str(uuid4()), "expected_status": expected_status,
            "expected_install_date": expected_date, "status": "Installed",
            "install_date": "2026-11-21",
        })
        assert response.status_code == 409, response.text
    assert workspace.financial_snapshot() == before
    assert workspace.audit_count() == audit_count


def test_original_tracker_delete_cannot_remove_a_linked_job(workspace):
    lead = workspace.create()
    ok(workspace.client().post(f"{PREFIX}/opportunities/{lead['id']}/link-job", json=command(
        lead["revision"], job_id=workspace.jobs["a_open"],
    )))
    before = workspace.financial_snapshot()
    response = workspace.client("admin").delete(f"/api/jobs/{workspace.jobs['a_open']}")
    assert response.status_code == 409, response.text
    assert workspace.financial_snapshot() == before


def test_preorder_queue_contains_only_won_unlinked_jobs_and_projects_private_data(workspace):
    before = workspace.financial_snapshot()
    won_a = workspace.create(stage="Won", name="Won A", amount="99999", notes="PRIVATE-A-NOTES", work_scope="Install cabinets")
    won_b = workspace.create("rep_b", stage="Won", name="Won B", notes="PRIVATE-B-NOTES")
    shared = workspace.create("admin", stage="Won", visibility="shared", name="Won shared")
    workspace.create(stage="Design", name="Still designing")
    workspace.create(stage="Estimate", name="Still estimating")
    workspace.create(stage="Follow up", name="Possible later job")
    allowed_fields = {"id", "name", "customer", "email", "phone", "address", "scope",
                      "sales_owner_name", "status", "due_date", "revision"}
    for role in ("ops", "designer", "admin"):
        response = workspace.client(role).get(f"{PREFIX}/preorder-jobs")
        rows = ok(response)
        assert {r["id"] for r in rows} == {won_a["id"], won_b["id"], shared["id"]}
        assert all(set(row) == allowed_fields for row in rows)
        assert all(row["status"] == "Prepping" for row in rows)
        assert "PRIVATE-A-NOTES" not in response.text and "PRIVATE-B-NOTES" not in response.text
        assert "99999" not in response.text
    for role, own in (("rep_a", won_a), ("rep_b", won_b)):
        rows = ok(workspace.client(role).get(f"{PREFIX}/preorder-jobs"))
        assert {r["id"] for r in rows} == {own["id"], shared["id"]}
    denied(workspace.client("crew").get(f"{PREFIX}/preorder-jobs"))
    assert len(workspace.requests()) == 2
    assert workspace.financial_snapshot() == before


def test_preorder_scheduling_has_separate_revision_replay_and_no_finance_writes(workspace):
    lead = workspace.create(stage="Won", amount="4321", notes="Keep sales notes")
    before = workspace.financial_snapshot()
    original_sales = workspace.detail(lead)
    row = ok(workspace.client("ops").get(f"{PREFIX}/preorder-jobs"))[0]
    path = f"{PREFIX}/preorder-jobs/{lead['id']}"
    payload = command(row["revision"], status="Ready to Order", due_date="2026-11-20")
    saved = ok(workspace.client("ops").patch(path, json=payload))
    assert saved["status"] == "Ready to Order" and saved["due_date"] == "2026-11-20"
    assert saved["revision"] == row["revision"] + 1
    current_sales = workspace.detail(lead)
    for field in ("revision", "name", "customer", "stage", "amount", "notes", "owner_id", "visibility", "requests", "activities"):
        assert current_sales[field] == original_sales[field], field
    audit_count = workspace.audit_count()
    assert ok(workspace.client("ops").patch(path, json=payload)) == saved
    assert workspace.audit_count() == audit_count
    for revision in (row["revision"], saved["revision"] + 1):
        response = workspace.client("ops").patch(path, json=command(revision, due_date="2026-11-21"))
        assert response.status_code == 409, response.text
    cleared = ok(workspace.client("admin").patch(path, json=command(saved["revision"], due_date=None)))
    assert cleared["due_date"] is None
    assert workspace.financial_snapshot() == before


def test_preorder_mutations_reject_sales_design_crew_and_unsafe_payloads(workspace):
    lead = workspace.create(stage="Won")
    row = ok(workspace.client("ops").get(f"{PREFIX}/preorder-jobs"))[0]
    path = f"{PREFIX}/preorder-jobs/{lead['id']}"
    for role in ("rep_a", "rep_b", "designer", "crew"):
        denied(workspace.client(role).patch(path, json=command(row["revision"], status="Ready to Order")))
    for fields in (
        {"status": "Ordered"}, {"status": None}, {"due_date": "tomorrow"},
        {"amount": "1"}, {"notes": "Overwrite sales"}, {"invoice_total": "1"},
        {"owner_id": workspace.user_ids["ops"]}, {"linked_job_id": workspace.jobs["a_open"]},
    ):
        response = workspace.client("ops").patch(path, json=command(row["revision"], **fields))
        assert response.status_code == 422, response.text
    assert ok(workspace.client("ops").get(f"{PREFIX}/preorder-jobs"))[0] == row


def test_preorder_link_removes_entry_and_revokes_pending_edit_and_replay(workspace):
    lead = workspace.create(stage="Won")
    before = workspace.financial_snapshot()
    ops = workspace.client("ops")
    row = ok(ops.get(f"{PREFIX}/preorder-jobs"))[0]
    path = f"{PREFIX}/preorder-jobs/{lead['id']}"
    payload = command(row["revision"], status="Ready to Order")
    saved = ok(ops.patch(path, json=payload))
    linked = ok(workspace.client().post(f"{PREFIX}/opportunities/{lead['id']}/link-job", json=command(
        lead["revision"], job_id=workspace.jobs["a_open"],
    )))
    assert linked["linked_job_id"] == workspace.jobs["a_open"]
    assert ok(ops.get(f"{PREFIX}/preorder-jobs")) == []
    stale = ops.patch(path, json=command(saved["revision"], due_date="2026-11-21"))
    assert stale.status_code == 404, stale.text
    assert ops.patch(path, json=payload).status_code == 404
    assert workspace.financial_snapshot() == before


def test_preorder_return_to_followup_removes_entry_and_revokes_ops_write(workspace):
    lead = workspace.create(stage="Won")
    ops = workspace.client("ops")
    row = ok(ops.get(f"{PREFIX}/preorder-jobs"))[0]
    returned = workspace.patch(lead, stage="Follow up")
    assert returned["stage"] == "Follow up"
    assert ok(ops.get(f"{PREFIX}/preorder-jobs")) == []
    response = ops.patch(f"{PREFIX}/preorder-jobs/{lead['id']}", json=command(row["revision"], status="Ready to Order"))
    assert response.status_code == 404, response.text
    assert workspace.requests() == []


def test_preorder_sales_context_change_invalidates_stale_operations_editor(workspace):
    lead = workspace.create(stage="Won", customer="Original", work_scope="Original instructions")
    ops = workspace.client("ops")
    row = ok(ops.get(f"{PREFIX}/preorder-jobs"))[0]
    workspace.patch(lead, customer="Updated", phone="555-0199", work_scope="Updated instructions")
    updated = ok(ops.get(f"{PREFIX}/preorder-jobs"))[0]
    assert updated["customer"] == "Updated" and updated["phone"] == "555-0199"
    assert updated["scope"] == "Updated instructions"
    assert updated["revision"] > row["revision"]
    response = ops.patch(f"{PREFIX}/preorder-jobs/{lead['id']}", json=command(row["revision"], status="Ready to Order"))
    assert response.status_code == 409, response.text


def test_saved_preferences_are_private_and_survive_new_login(workspace):
    a, b = workspace.client(), workspace.client("rep_b")
    assert ok(a.get(f"{PREFIX}/preferences")) == {}
    preferences = {
        "sales_view": "kanban", "sales_scope": "mine",
        "sales_columns": ["customer", "stage", "amount", "next_follow_up"],
        "home_widgets": ["pipeline", "collections", "work"], "monthly_target": "25000.00",
    }
    saved = ok(a.put(f"{PREFIX}/preferences", json=preferences))
    assert saved["sales_view"] == "kanban"
    assert Decimal(saved["monthly_target"]) == Decimal("25000")
    assert ok(b.get(f"{PREFIX}/preferences")) == {}
    ok(a.post("/api/logout"))
    ok(a.post("/api/login", json={"email": "rep_a@workspace.test", "password": PASSWORD}))
    assert ok(a.get(f"{PREFIX}/preferences")) == saved
    assert Decimal(ok(a.get(f"{PREFIX}/dashboard"))["goal"]["monthly_target"]) == Decimal("25000")


@pytest.mark.parametrize("fields", [
    {"name": "   "}, {"customer": ""}, {"stage": "Ordered"},
    {"visibility": "public"}, {"amount": "NaN"}, {"amount": "Infinity"},
    {"amount": "-1"}, {"amount": "1e100"}, {"next_follow_up": "yesterday"},
    {"invoice_total": "1"}, {"linked_job_id": 1}, {"revision": 50},
])
def test_invalid_opportunity_payloads_are_rejected_without_writes(workspace, fields):
    before = workspace.financial_snapshot()
    response = workspace.client().post(f"{PREFIX}/opportunities", json={
        "name": "Valid name", "customer": "Valid customer", **fields,
    })
    assert response.status_code == 422, response.text
    assert ok(workspace.client().get(f"{PREFIX}/opportunities")) == []
    assert workspace.requests() == []
    assert workspace.financial_snapshot() == before


def test_rep_cannot_assign_private_leads_to_other_users(workspace):
    response = workspace.client().post(f"{PREFIX}/opportunities", json={
        "name": "Forged ownership", "customer": "Customer", "owner_id": workspace.user_ids["rep_b"],
    })
    assert response.status_code in (403, 422), response.text
    lead = workspace.create()
    response = workspace.client().patch(f"{PREFIX}/opportunities/{lead['id']}", json=command(
        lead["revision"], owner_id=workspace.user_ids["rep_b"],
    ))
    assert response.status_code in (403, 422), response.text
    assert workspace.detail(lead)["owner_id"] == workspace.user_ids["rep_a"]


@pytest.mark.parametrize("preferences", [
    {"user_id": 2}, {"sales_view": "invalid"}, {"sales_scope": "others"},
    {"monthly_target": "NaN"}, {"monthly_target": "-1"},
    {"sales_columns": "name"}, {"home_widgets": "finance"},
])
def test_invalid_preferences_cannot_change_saved_state(workspace, preferences):
    client = workspace.client()
    response = client.put(f"{PREFIX}/preferences", json=preferences)
    assert response.status_code == 422, response.text
    assert ok(client.get(f"{PREFIX}/preferences")) == {}


def test_patch_requires_revision_command_and_rejects_unknown_fields(workspace):
    lead = workspace.create()
    path = f"{PREFIX}/opportunities/{lead['id']}"
    for body in (
        {"stage": "Design"},
        {"expected_revision": lead["revision"], "stage": "Design"},
        command(lead["revision"], invoice_total="500"),
        command(lead["revision"], stage=None),
    ):
        response = workspace.client().patch(path, json=body)
        assert response.status_code == 422, response.text
    assert workspace.detail(lead)["stage"] == "New Lead"
    assert workspace.requests() == []


def test_recorded_activities_are_not_delivery_claims(workspace):
    lead = workspace.create()
    client = workspace.client()
    for kind in ("Note", "Call", "Email", "Text"):
        activity = ok(client.post(f"{PREFIX}/opportunities/{lead['id']}/activities", json={
            "kind": kind, "body": f"Manually logged {kind.lower()} outcome",
        }))
        assert activity["kind"] == kind
        assert not {"sent", "delivered", "provider_message_id"} & activity.keys()
    context = ok(client.get(f"{PREFIX}/context"))
    assert context["environment"] == "local-pilot"
    assert context["finance_source"] == "Job Tracker"
    assert context["channels"]
    assert all(channel["status"] == "not_configured" for channel in context["channels"])
    assert len(workspace.detail(lead)["activities"]) >= 4
