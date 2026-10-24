import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@/jobtracker_test?host=/tmp/pg")

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.main import FAILED_LOGINS, app, name_score
from app.models import LaborEntry, User


@pytest.fixture(autouse=True)
def fresh_db():
    FAILED_LOGINS.clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all([
            User(name="Skyler", email="skyler@x.com", password_hash=hash_password("ownerpass"), role="owner"),
            User(name="Omar", email="omar@x.com", password_hash=hash_password("omarpass"), role="crew", hourly_rate=Decimal("40")),
            User(name="Jorge", email="jorge@x.com", password_hash=hash_password("jorgepass"), role="crew",
                 hourly_rate=Decimal("24.50")),
            User(name="Jen", email="jen@x.com", password_hash=hash_password("jenpass1"), role="designer"),
            User(name="Drew", email="drew@x.com", password_hash=hash_password("drewpass1"), role="rep",
                 commission_rate=Decimal("0.30")),
        ])
        db.commit()


def client(email, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"email": email, "password": password}).status_code == 200
    return c


def ids():
    with SessionLocal() as db:
        return {u.name: u.id for u in db.query(User)}


def make_job(owner, **extra):
    body = {"name": "Dustin Selden", "invoice_total": "30000", "material_total": "12000", "boxes": 30,
            "modifications": 2, "rep_id": ids()["Drew"], **extra}
    r = owner.post("/api/jobs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def line(job, n):
    return job["waterfall"]["lines"][n - 1]["amount"]


def test_waterfall_math():
    owner = client("skyler@x.com", "ownerpass")
    job = make_job(owner, referral_amount="500")
    jid = job["id"]
    owner.post(f"/api/jobs/{jid}/ledger", json={"line": 2, "amount": "2000", "note": "CO pantry"})
    owner.post(f"/api/jobs/{jid}/ledger", json={"line": 5, "amount": "700"})
    owner.post(f"/api/jobs/{jid}/ledger", json={"line": 7, "amount": "2800", "payee": "Abraham"})
    owner.post(f"/api/jobs/{jid}/ledger", json={"line": 8, "amount": "80", "payee": "Jen"})
    owner.post("/api/labor", json={"job_id": jid, "hours": "13", "worker_id": ids()["Omar"]})
    job = owner.get(f"/api/jobs/{jid}").json()
    assert line(job, 3) == "32000.00"
    assert line(job, 6) == "520.00"
    # 32000 - (12000 + 700 + 520 + 2800 + 80 + 500) = 15400
    assert job["waterfall"]["job_profit"] == "15400.00"
    assert job["waterfall"]["commission"] == "4620.00"
    assert job["waterfall"]["net"] == "10780.00"
    assert job["waterfall"]["margin_pct"] == "33.7"


def test_unexpected_needs_reason_and_line_is_valid():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    assert owner.post(f"/api/jobs/{jid}/ledger", json={"line": 11, "amount": "100"}).status_code == 422
    assert owner.post(f"/api/jobs/{jid}/ledger", json={"line": 13, "amount": "100"}).status_code == 422


def test_crew_logs_own_hours_without_seeing_dollars():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    r = omar.post("/api/labor", json={"job_id": jid, "hours": "13", "worker_id": ids()["Jorge"]})
    assert r.status_code == 200
    mine = omar.get("/api/labor/mine").json()
    assert len(mine) == 1 and mine[0]["hours"] == "13.00" and mine[0]["job"] == "Dustin Selden"
    assert not any(k in mine[0] for k in ("rate", "total"))
    # worker_id from crew is ignored: the entry is Omar's, at Omar's rate
    assert line(owner.get(f"/api/jobs/{jid}").json(), 6) == "520.00"
    assert omar.get("/api/job-names").json() == [{"id": jid, "name": "Dustin Selden", "boxes": 30, "modifications": 2}]


def test_crew_privacy():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    entry = omar.post("/api/labor", json={"job_id": jid, "hours": "8"}).json()
    jorge = client("jorge@x.com", "jorgepass")
    assert jorge.get("/api/labor/mine").json() == []
    assert jorge.patch(f"/api/labor/{entry['id']}", json={"hours": "1"}).status_code == 404
    assert jorge.delete(f"/api/labor/{entry['id']}").status_code == 404
    for path in ("/api/jobs", f"/api/jobs/{jid}", "/api/users", "/api/audit"):
        assert jorge.get(path).status_code == 403, path
    assert TestClient(app).get("/api/job-names").status_code == 401


def test_crew_entries_lock_after_three_days():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    entry = omar.post("/api/labor", json={"job_id": jid, "hours": "8"}).json()
    assert omar.patch(f"/api/labor/{entry['id']}", json={"hours": "9"}).status_code == 200
    with SessionLocal() as db:
        db.get(LaborEntry, entry["id"]).created_at = datetime.now(timezone.utc) - timedelta(days=4)
        db.commit()
    assert omar.patch(f"/api/labor/{entry['id']}", json={"hours": "10"}).status_code == 403
    assert omar.get("/api/labor/mine").json()[0]["editable"] is False
    assert owner.patch(f"/api/labor/{entry['id']}", json={"hours": "10"}).status_code == 200


def test_rate_change_keeps_history():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    owner.post("/api/labor", json={"job_id": jid, "hours": "10", "worker_id": ids()["Omar"]})
    owner.patch(f"/api/users/{ids()['Omar']}", json={"hourly_rate": "50"})
    owner.post("/api/labor", json={"job_id": jid, "hours": "10", "worker_id": ids()["Omar"]})
    assert line(owner.get(f"/api/jobs/{jid}").json(), 6) == "900.00"


def test_audit_records_who_and_old_value():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    owner.patch(f"/api/jobs/{jid}", json={"invoice_total": "31000"})
    log = owner.get(f"/api/audit?table=jobs&row_id={jid}").json()
    update = next(a for a in log if a["action"] == "update")
    assert update["who"] == "Skyler"
    assert update["changes"]["invoice_total"] == ["30000.00", "31000"]
    users_log = owner.get("/api/audit?table=users").json()
    assert all(a["changes"].get("password_hash", ["***"])[0] == "***" for a in users_log)


def test_closing_a_job_hides_it_from_crew():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    r = owner.patch(f"/api/jobs/{jid}", json={"status": "Closed"})
    assert r.json()["closed_at"] is not None
    omar = client("omar@x.com", "omarpass")
    assert omar.get("/api/job-names").json() == []
    assert omar.post("/api/labor", json={"job_id": jid, "hours": "2"}).status_code == 422


def test_bad_login_and_duplicate_job():
    assert TestClient(app).post("/api/login", json={"email": "omar@x.com", "password": "nope"}).status_code == 401
    owner = client("skyler@x.com", "ownerpass")
    make_job(owner)
    assert owner.post("/api/jobs", json={"name": "Dustin Selden"}).status_code == 409


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def submit_receipt(c, **fields):
    data = {"amount": "180", "purpose": "Home Depot fillers", **{k: str(v) for k, v in fields.items()}}
    return c.post("/api/reimbursements", data=data, files={"receipt": ("r.png", PNG, "image/png")})


def test_assembly_counts_only_after_approval():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    r = omar.post("/api/assembly", json={"job_id": jid, "boxes": 30, "modifications": 2})
    assert r.status_code == 200 and r.json()["total"] == "700.00" and r.json()["status"] == "submitted"
    assert omar.post("/api/assembly", json={"job_id": jid, "extra_cost": "40"}).status_code == 422  # needs a note
    assert line(owner.get(f"/api/jobs/{jid}").json(), 5) == "0.00"
    eid = r.json()["id"]
    assert owner.post(f"/api/assembly/{eid}/status", json={"status": "approved"}).status_code == 200
    assert line(owner.get(f"/api/jobs/{jid}").json(), 5) == "700.00"
    assert omar.delete(f"/api/assembly/{eid}").status_code == 403  # approved entries are locked for crew
    paid = owner.post(f"/api/assembly/{eid}/status", json={"status": "paid"}).json()
    assert paid["status"] == "paid"
    assert owner.post(f"/api/assembly/{eid}/status", json={"status": "rejected"}).status_code == 422
    assert omar.get("/api/assembly/mine").json()[0]["status"] == "paid"
    assert client("jorge@x.com", "jorgepass").get("/api/assembly/mine").json() == []


def test_reimbursement_flow_and_privacy():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    job_r = submit_receipt(omar, job_id=jid).json()
    fuel = submit_receipt(omar, amount="62.40", purpose="Fuel, Circle K").json()
    assert fuel["job"] is None
    bad = omar.post("/api/reimbursements", data={"amount": "5", "purpose": "x"},
                    files={"receipt": ("r.txt", b"hi", "text/plain")})
    assert bad.status_code == 422
    assert omar.post("/api/reimbursements", data={"amount": "5", "purpose": "x"}).status_code == 422  # receipt required

    jorge = client("jorge@x.com", "jorgepass")
    assert jorge.get("/api/reimbursements/mine").json() == []
    assert jorge.get(job_r["receipt_url"]).status_code == 404
    assert jorge.get("/api/approvals").status_code == 403
    got = omar.get(job_r["receipt_url"])
    assert got.status_code == 200 and got.content == PNG

    assert line(owner.get(f"/api/jobs/{jid}").json(), 4) == "12000.00"
    approvals = owner.get("/api/approvals").json()
    assert {x["id"] for x in approvals["reimbursements"]} == {job_r["id"], fuel["id"]}
    owner.post(f"/api/reimbursements/{job_r['id']}/status", json={"status": "approved"})
    owner.post(f"/api/reimbursements/{fuel['id']}/status", json={"status": "approved"})
    assert line(owner.get(f"/api/jobs/{jid}").json(), 4) == "12180.00"  # fuel hits no job
    owner.post(f"/api/reimbursements/{job_r['id']}/status", json={"status": "paid"})
    mine = {x["id"]: x for x in omar.get("/api/reimbursements/mine").json()}
    assert mine[job_r["id"]]["status"] == "paid" and mine[job_r["id"]]["paid_at"]
    assert [x["id"] for x in owner.get("/api/approvals").json()["reimbursements"]] == [fuel["id"]]
    log = owner.get("/api/audit?table=reimbursements").json()
    assert all("PNG" not in str(a["changes"]) for a in log)


def test_design_matching_puts_charge_on_line_8():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    jen = client("jen@x.com", "jenpass1")
    selden = jen.post("/api/designs", json={"design_name": "Selden kitchen", "charge": "80"}).json()
    jen.post("/api/designs", json={"design_name": "Garcia bath", "charge": "120", "revisions": 3})
    assert len(jen.get("/api/designs/mine").json()) == 2
    for path in ("/api/jobs", "/api/designs", f"/api/jobs/{jid}/design-suggestions"):
        assert jen.get(path).status_code == 403, path
    omar = client("omar@x.com", "omarpass")
    assert omar.get("/api/designs/mine").status_code == 403
    assert omar.patch(f"/api/designs/{selden['id']}", json={"charge": "1"}).status_code == 403

    suggestions = owner.get(f"/api/jobs/{jid}/design-suggestions").json()
    assert [s["design_name"] for s in suggestions] == ["Selden kitchen"]
    owner.post(f"/api/designs/{selden['id']}/match", json={"job_id": jid})
    job = owner.get(f"/api/jobs/{jid}").json()
    assert line(job, 8) == "80.00" and job["designs"][0]["design_name"] == "Selden kitchen"
    assert owner.get(f"/api/jobs/{jid}/design-suggestions").json() == []
    assert jen.patch(f"/api/designs/{selden['id']}", json={"charge": "200"}).status_code == 403  # matched = locked
    assert [d["design_name"] for d in owner.get("/api/designs?unmatched=true").json()] == ["Garcia bath"]
    owner.post(f"/api/designs/{selden['id']}/match", json={"job_id": None})
    assert line(owner.get(f"/api/jobs/{jid}").json(), 8) == "0.00"


def test_name_score():
    assert name_score("Selden kitchen", "Dustin Selden") >= 0.5
    assert name_score("Garcia bath", "Dustin Selden") < 0.5


def test_login_locks_after_five_wrong_passwords():
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/login", json={"email": "omar@x.com", "password": "wrong"}).status_code == 401
    assert c.post("/api/login", json={"email": "omar@x.com", "password": "omarpass"}).status_code == 429
    assert c.post("/api/login", json={"email": "jorge@x.com", "password": "jorgepass"}).status_code == 200


def test_payments_balance_fees_and_collections():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    r = owner.post(f"/api/jobs/{jid}/payments", data={"amount": "15000", "method": "card", "payment_type": "deposit",
                                                    "processing_fee": "435"},
                   files={"screenshot": ("s.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["waterfall"]["collected"] == "15000.00" and job["waterfall"]["balance_due"] == "15000.00"
    assert line(job, 10) == "435.00"
    assert owner.get(job["payments"][0]["screenshot_url"]).content == PNG
    job = owner.post(f"/api/jobs/{jid}/payments", data={"amount": "15000", "method": "check", "status": "pending",
                                                       "expected_date": "2026-11-03"}).json()
    assert job["waterfall"]["balance_due"] == "15000.00"  # pending doesn't count yet
    assert owner.post(f"/api/jobs/{jid}/payments", data={"amount": "1", "method": "venmo"}).status_code == 422

    rep = owner.get("/api/collections?period=week").json()
    assert rep["collected"] == [{"rep": "Drew", "amounts": ["0.00"] * 7 + ["15000.00"], "total": "15000.00"}]
    assert rep["sold"][0]["amounts"][-1] == "30000.00"
    assert [p["amount"] for p in rep["pending"]] == ["15000.00"]
    assert rep["outstanding"][0]["balance_due"] == "15000.00"
    assert rep["commission_due"] == []

    pending_id = next(p["id"] for p in job["payments"] if p["status"] == "pending")
    job = owner.patch(f"/api/payments/{pending_id}", json={"status": "received"}).json()
    assert job["waterfall"]["balance_due"] == "0.00"
    owner.patch(f"/api/jobs/{jid}", json={"status": "Closed"})
    due = owner.get("/api/collections").json()["commission_due"]
    # 30000 - 12000 materials - 435 card fee = 17565 profit; 30% = 5269.50
    assert due == [{"job_id": jid, "job": "Dustin Selden", "rep": "Drew", "commission": "5269.50",
                    "closed_at": due[0]["closed_at"]}]
    owner.post(f"/api/jobs/{jid}/commission-paid", json={"paid": True})
    assert owner.get("/api/collections").json()["commission_due"] == []
    omar = client("omar@x.com", "omarpass")
    assert omar.get("/api/collections").status_code == 403
    assert omar.get(job["payments"][0]["screenshot_url"]).status_code == 403


def test_week_buckets_run_sunday_to_saturday():
    from datetime import date
    from app.main import bucket_start, last_buckets
    assert bucket_start(date(2026, 10, 24), "week") == date(2026, 10, 18)  # Saturday -> prior Sunday
    assert bucket_start(date(2026, 10, 18), "week") == date(2026, 10, 18)  # Sunday is its own start
    assert bucket_start(date(2026, 11, 15), "quarter") == date(2026, 10, 1)
    assert last_buckets(date(2026, 1, 10), "month", 3) == [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)]


def test_migrations_match_models_and_adopt_old_databases():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    from app.migrate import ALEMBIC_INI, upgrade

    def reset():
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    reset()
    upgrade()  # fresh database: builds everything from migrations
    command.check(Config(str(ALEMBIC_INI)))  # raises if models and migrations disagree

    # A database made by create_all before migrations existed, missing the payments table and new column.
    reset()
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE payments"))
        conn.execute(text("ALTER TABLE jobs DROP COLUMN commission_paid_at"))
    upgrade()
    assert "payments" in inspect(engine).get_table_names()
    assert "commission_paid_at" in [c["name"] for c in inspect(engine).get_columns("jobs")]
    reset()


def test_smart_input_drafts_an_entry(monkeypatch):
    import anthropic
    from types import SimpleNamespace

    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert owner.post("/api/inbox/parse", data={"text": "x"}).status_code == 503

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    sent = {}

    class FakeClient:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={
                "kind": "hours", "job": "Dustin Selden", "worker": "Omar", "hours": 13, "confidence": "high"})])

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    r = owner.post("/api/inbox/parse", data={"text": "Omar 13 hours Selden"},
                   files={"image": ("s.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == jid and r.json()["worker_id"] == ids()["Omar"]
    prompt = sent["messages"][0]["content"]
    assert prompt[0]["type"] == "image" and "Dustin Selden" in prompt[-1]["text"]

    # A name not on our lists is dropped, never invented.
    FakeClient.create = lambda self, **kw: SimpleNamespace(content=[SimpleNamespace(
        type="tool_use", input={"kind": "payment", "job": "Made Up Job", "amount": 500, "confidence": "low"})])
    assert owner.post("/api/inbox/parse", data={"text": "500 from someone"}).json()["job_id"] is None
    assert owner.post("/api/inbox/parse", data={"text": ""}).status_code == 422
    assert client("omar@x.com", "omarpass").post("/api/inbox/parse", data={"text": "x"}).status_code == 403
