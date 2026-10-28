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


def test_crew_logs_own_hours_and_sees_only_own_pay():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    r = omar.post("/api/labor", json={"job_id": jid, "hours": "13", "worker_id": ids()["Jorge"]})
    assert r.status_code == 200
    mine = omar.get("/api/labor/mine").json()
    assert len(mine) == 1 and mine[0]["hours"] == "13.00" and mine[0]["job"] == "Dustin Selden"
    assert mine[0]["total"] == "520.00" and mine[0]["status"] == "submitted"  # his own pay, nothing about the job
    # Hours count on the job only after the owner approves them.
    assert line(owner.get(f"/api/jobs/{jid}").json(), 6) == "0.00"
    week = owner.get("/api/approvals").json()["hours"]
    assert [(h["worker"], h["hours"]) for h in week] == [("Omar", "13.00")]  # crew's worker_id was ignored
    owner.post(f"/api/labor/{week[0]['id']}/status", json={"status": "approved"})
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


def test_crew_entries_lock_once_approved():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    entry = omar.post("/api/labor", json={"job_id": jid, "hours": "8"}).json()
    assert omar.patch(f"/api/labor/{entry['id']}", json={"hours": "9"}).status_code == 200
    owner.post(f"/api/labor/{entry['id']}/status", json={"status": "approved"})
    assert omar.patch(f"/api/labor/{entry['id']}", json={"hours": "10"}).status_code == 403
    assert omar.delete(f"/api/labor/{entry['id']}").status_code == 403
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


def test_pay_types_box_rate_and_deliveries():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    u = ids()
    assert owner.patch(f"/api/users/{u['Omar']}", json={"pay_type": "per_box", "box_rate": "25"}).status_code == 200
    assert owner.patch(f"/api/users/{u['Omar']}", json={"pay_type": "weekly"}).status_code == 422
    omar = client("omar@x.com", "omarpass")
    assert omar.get("/api/me").json()["pay_type"] == "per_box"
    assert omar.post("/api/assembly", json={"job_id": jid, "boxes": 10, "modifications": 1}).json()["total"] == "300.00"

    owner.patch(f"/api/users/{u['Jorge']}", json={"pay_type": "salary"})
    jorge = client("jorge@x.com", "jorgepass")
    assert jorge.post("/api/labor", json={"job_id": jid, "hours": 3}).status_code == 422  # salaried: no hours
    assert jorge.post("/api/deliveries", json={"job_id": jid}).status_code == 200
    assert jorge.post("/api/deliveries", json={"job_id": jid}).status_code == 409  # same drop twice
    assert owner.post("/api/deliveries", json={"job_id": jid, "driver_id": u["Omar"]}).json()["driver"] == "Omar"
    mine = jorge.get("/api/deliveries").json()
    assert [c["total"] for c in mine["counts"]] == [1] and len(mine["deliveries"]) == 1  # drivers see only their own
    counts = {c["driver"]: c for c in owner.get("/api/deliveries").json()["counts"]}
    assert counts["Jorge"]["week"] == 1 and counts["Omar"]["month"] == 1
    assert omar.delete(f"/api/deliveries/{mine['deliveries'][0]['id']}").status_code == 403
    assert client("jen@x.com", "jenpass1").get("/api/deliveries").status_code == 403


def test_deliveries_from_calendar(monkeypatch):
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner, name="Dallin Hatch - Elissa")["id"]
    monkeypatch.setenv("INTAKE_TOKEN", "secret")
    bot = TestClient(app, headers={"x-intake-token": "secret"})

    def event(eid, title, day="2026-10-13"):
        r = bot.post("/api/intake/delivery", json={"event_id": eid, "title": title, "delivered_on": day})
        assert r.status_code == 200, r.text
        return r.json()

    assert TestClient(app).post("/api/intake/delivery", json={"event_id": "x", "title": "OUTBOUND - A",
                                                              "delivered_on": "2026-10-13"}).status_code == 401
    assert event("e0", "INBOUND - Dallin Hatch - Elissa")["outcome"] == "ignored"  # stock coming in, not a drop
    assert event("e1", "OUTBOUND (PT 1 )- Dallin Hatch - Elissa") == {"outcome": "created", "job": "Dallin Hatch - Elissa"}
    assert event("e2", "OUTBOUND (PT 2) - Dallin Hatch - Elissa", "2026-10-14")["outcome"] == "created"
    assert event("e1", "OUTBOUND (PT 1 )- Dallin Hatch - Elissa", "2026-10-12")["outcome"] == "updated"  # moved, not doubled
    assert event("e3", "DIRECT - Rugby Material to Karissa Abbott") == {"outcome": "created", "job": None}
    assert event("e5", "OUTBOUND: Dallin Hatch - Elissa SOAZ17082 trailer needed", "2026-10-21")["job"] == "Dallin Hatch - Elissa"
    assert event("e6", "Outbound — Dallin Hatch - Elissa", "2026-10-22")["job"] == "Dallin Hatch - Elissa"

    owner.post("/api/deliveries", json={"job_id": jid, "driver_id": ids()["Jorge"], "delivered_on": "2026-10-20"})
    assert event("e4", "OUTBOUND - Dallin Hatch - Elissa PT 3", "2026-10-20")["outcome"] == "linked"  # logged by hand too

    data = owner.get("/api/deliveries").json()
    assert {c["driver"]: c["total"] for c in data["counts"]} == {"Delivery team": 5, "Jorge": 1}
    assert "DIRECT - Rugby Material to Karissa Abbott" in [d["job"] for d in data["deliveries"]]  # no job: show the title


def test_rep_sees_only_own_sales():
    owner = client("skyler@x.com", "ownerpass")
    make_job(owner)  # Drew's
    make_job(owner, name="Skyler's own", rep_id=ids()["Skyler"])
    drew = client("drew@x.com", "drewpass1")
    mine = drew.get("/api/sales/mine").json()
    assert [j["name"] for j in mine["jobs"]] == ["Dustin Selden"]
    job = mine["jobs"][0]
    assert job["sold"] == "30000.00" and job["commission_state"] == "pending"
    assert "material_total" not in job and "waterfall" not in job  # no job costs for reps
    assert drew.get("/api/jobs").status_code == 403
    assert client("omar@x.com", "omarpass").get("/api/sales/mine").status_code == 403


def test_import_past_invoices(monkeypatch):
    owner = client("skyler@x.com", "ownerpass")
    make_job(owner)
    monkeypatch.setenv("INTAKE_TOKEN", "secret")
    bot = TestClient(app, headers={"x-intake-token": "secret"})
    assert TestClient(app).get("/api/intake/jobs").status_code == 401
    assert [j["name"] for j in bot.get("/api/intake/jobs").json()] == ["Dustin Selden"]

    r = bot.post("/api/intake/import-invoices", json=[
        {"name": "Mitch Gai - 519 N 167th Dr (#2254)", "customer": "Mitch Gai", "invoice_total": "4011.08",
         "open_balance": "0", "invoice_date": "2026-01-05", "qb_number": "2254"},
        {"name": "Lauren Panichelle (NJ)", "invoice_total": "43665", "open_balance": "21832.50", "invoice_date": "2026-06-03"},
        {"name": "Dustin Selden", "invoice_total": "1", "open_balance": "0", "invoice_date": "2026-06-03"},
    ]).json()
    assert r == {"created": 2, "skipped": ["Dustin Selden"]}
    jobs = {j["name"]: j for j in bot.get("/api/intake/jobs").json()}
    paid = jobs["Mitch Gai - 519 N 167th Dr (#2254)"]
    assert paid["status"] == "Closed" and paid["balance_due"] == "0.00" and paid["rep"] == "Skyler"
    open_ = jobs["Lauren Panichelle (NJ)"]
    assert open_["status"] == "Installed" and open_["balance_due"] == "21832.50"
    due = owner.get("/api/collections?period=year").json()
    assert due["commission_due"] == []  # old jobs never show commission as due, even after a rep is assigned
    assert "Lauren Panichelle (NJ)" in [o["job"] for o in due["outstanding"]]


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
    assert line(owner.get(f"/api/jobs/{jid}").json(), 8) == "0.00"  # not approved yet
    assert {d["design_name"] for d in owner.get("/api/approvals").json()["designs"]} == {"Selden kitchen", "Garcia bath"}
    owner.post(f"/api/designs/{selden['id']}/status", json={"status": "approved"})
    job = owner.get(f"/api/jobs/{jid}").json()
    assert line(job, 8) == "80.00" and job["designs"][0]["design_name"] == "Selden kitchen"
    assert owner.get(f"/api/jobs/{jid}/design-suggestions").json() == []
    assert jen.patch(f"/api/designs/{selden['id']}", json={"charge": "200"}).status_code == 403  # approved = locked
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
    # A cost still waiting for approval holds the commission back until it's approved.
    omar = client("omar@x.com", "omarpass")
    owner.patch(f"/api/jobs/{jid}", json={"status": "Installed"})
    entry = omar.post("/api/labor", json={"job_id": jid, "hours": "10"}).json()
    owner.patch(f"/api/jobs/{jid}", json={"status": "Closed"})
    assert owner.get("/api/collections").json()["commission_due"] == []
    owner.post(f"/api/labor/{entry['id']}/status", json={"status": "approved"})
    due = owner.get("/api/collections").json()["commission_due"]
    assert due[0]["commission"] == "5149.50"  # (17565 - 400 labor) x 30%
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
        conn.execute(text("DROP TABLE intake_emails"))  # added after migrations existed
        conn.execute(text("DROP TABLE deliveries"))
        conn.execute(text("ALTER TABLE users DROP COLUMN pay_type, DROP COLUMN box_rate"))
        for table in ("labor", "designs"):  # approval columns also came later
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN status, DROP COLUMN status_note, DROP COLUMN paid_at"))
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
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=1500, output_tokens=200),
                                   content=[SimpleNamespace(type="tool_use", input={
                "kind": "hours", "job": "Dustin Selden", "worker": "Omar", "hours": 13, "confidence": "high"})])

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    r = owner.post("/api/inbox/parse", data={"text": "Omar 13 hours Selden"},
                   files={"image": ("s.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == jid and r.json()["worker_id"] == ids()["Omar"]
    assert r.json()["cost_usd"] == 0.005  # 1500 in x $2/M + 200 out x $10/M
    prompt = sent["messages"][0]["content"]
    assert prompt[0]["type"] == "image" and "Dustin Selden" in prompt[-1]["text"]
    assert sent["tool_choice"] == {"type": "auto"}  # forced tool choice is rejected by current models

    # A name not on our lists is dropped, never invented.
    FakeClient.create = lambda self, **kw: SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=1), content=[SimpleNamespace(
        type="tool_use", input={"kind": "payment", "job": "Made Up Job", "amount": 500, "confidence": "low"})])
    assert owner.post("/api/inbox/parse", data={"text": "500 from someone"}).json()["job_id"] is None
    assert owner.post("/api/inbox/parse", data={"text": ""}).status_code == 422
    assert client("omar@x.com", "omarpass").post("/api/inbox/parse", data={"text": "x"}).status_code == 403


def test_ready_to_order_intake(monkeypatch):
    from app import inbox

    owner = client("skyler@x.com", "ownerpass")
    replies = {}
    monkeypatch.setattr(inbox, "read_rto", lambda subject, body, pdf: replies[subject])
    monkeypatch.setenv("INTAKE_TOKEN", "secret")
    bot = TestClient(app, headers={"x-intake-token": "secret"})

    def send(mid, subject, thread="t1", body="filled in", **parsed):
        replies[subject] = {"is_vendor_reply": False, "is_change_order": False, **parsed}
        r = bot.post("/api/intake/email", data={"message_id": mid, "thread_id": thread, "subject": subject, "body": body})
        assert r.status_code == 200, r.text
        return r.json()

    assert TestClient(app).post("/api/intake/email", data={"message_id": "x", "subject": "s"}).status_code == 401

    seen = {}
    monkeypatch.setattr(inbox, "read_rto", lambda subject, body, pdfs: seen.setdefault("pdfs", pdfs) and {
        "is_vendor_reply": True, "is_change_order": False})
    bot.post("/api/intake/email", data={"message_id": "multi", "subject": "Re: READY TO ORDER: X", "body": "b"},
             files=[("pdf", ("SO1.pdf", b"%PDF-1", "application/pdf")), ("pdf", ("INV1.pdf", b"%PDF-2", "application/pdf")),
                    ("pdf", ("logo.png", PNG, "image/png"))])
    assert [n for n, _ in seen["pdfs"]] == ["SO1.pdf", "INV1.pdf"]  # every PDF reaches Claude, images don't
    monkeypatch.setattr(inbox, "read_rto", lambda subject, body, pdf: replies[subject])

    r = send("m1", "READY TO ORDER: Keith Olson - Centennial Vanity", job_name="Keith Olson - Centennial Vanity",
             rep_name="Drew", invoice_total=9000, manufacturer="Highland", boxes=10, modifications=1)
    assert r["outcome"] == "created"
    jid = r["job_id"]
    assert send("m1", "READY TO ORDER: Keith Olson - Centennial Vanity")["duplicate"] is True

    # Vendor reply on the same thread fills the material total.
    assert send("v1", "Re: READY TO ORDER: Keith Olson - Centennial Vanity", is_vendor_reply=True,
                material_total=3800)["outcome"] == "material"
    # Add-on on its own thread attaches as a change order; its vendor reply adds material.
    r = send("m2", "READY TO ORDER: Keith Olson - Centennial Vanity Add On", thread="t2",
             job_name="Keith Olson - Centennial Vanity", is_change_order=True, invoice_total=1200, boxes=2)
    assert r["outcome"] == "attached" and r["job_id"] == jid
    send("v2", "Re: READY TO ORDER: Keith Olson - Centennial Vanity Add On", thread="t2", is_vendor_reply=True,
         material_total=500)
    # The owner's copy of the same add-on (own thread, same subject) must not add the $1,200 again.
    send("m2-copy", "READY TO ORDER: Keith Olson - Centennial Vanity Add On", thread="t2b",
         job_name="Keith Olson - Centennial Vanity", is_change_order=True, invoice_total=1200, boxes=2)
    job = owner.get(f"/api/jobs/{jid}").json()
    assert job["rep_name"] == "Drew" and job["boxes"] == 12
    assert line(job, 3) == "10200.00" and line(job, 4) == "4300.00"

    # Blank template from the owner is ignored; a near-miss name waits for the owner.
    assert send("m3", "READY TO ORDER: Mitch", thread="t3", body="FINISHES - [Supplier - Finish]")["outcome"] == "ignored"
    near = send("m4", "READY TO ORDER: Keith Olsen - Centennial Vanity", thread="t4",
                job_name="Keith Olsen - Centennial Vanity", invoice_total=50)
    assert near["outcome"] == "unmatched"
    assert [e["id"] for e in owner.get("/api/intake?outcome=unmatched").json()] == [near["id"]]
    assert owner.post(f"/api/intake/{near['id']}/resolve", json={}).json()["outcome"] == "resolved"
    assert owner.post(f"/api/intake/{near['id']}/resolve", json={}).status_code == 422
    assert client("omar@x.com", "omarpass").get("/api/intake").status_code == 403


def test_my_pay_by_week():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    omar = client("omar@x.com", "omarpass")
    a = omar.post("/api/labor", json={"job_id": jid, "hours": "10", "work_date": "2026-10-20"}).json()  # Tue
    b = omar.post("/api/labor", json={"job_id": jid, "hours": "5", "work_date": "2026-10-13"}).json()  # prior week
    omar.post("/api/assembly", json={"job_id": jid, "boxes": 10, "work_date": "2026-10-21"})
    owner.post(f"/api/labor/{b['id']}/status", json={"status": "approved"})
    owner.post(f"/api/labor/{b['id']}/status", json={"status": "paid"})
    owner.post(f"/api/labor/{a['id']}/status", json={"status": "approved"})
    pay = omar.get("/api/pay/mine").json()
    assert [(w["week"], w["waiting"], w["owed"], w["paid"]) for w in pay["weeks"]] == [
        ("2026-10-18", "200.00", "400.00", "0.00"), ("2026-10-11", "0.00", "0.00", "200.00")]
    assert (pay["waiting"], pay["owed"], pay["paid"]) == ("200.00", "400.00", "200.00")

    jen = client("jen@x.com", "jenpass1")
    jen.post("/api/designs", json={"design_name": "Selden kitchen", "charge": "80", "design_date": "2026-10-20"})
    assert jen.get("/api/pay/mine").json()["waiting"] == "80.00"
    assert client("jorge@x.com", "jorgepass").get("/api/pay/mine").json()["weeks"] == []
    assert client("drew@x.com", "drewpass1").get("/api/pay/mine").status_code == 403
    assert omar.post(f"/api/labor/{a['id']}/status", json={"status": "paid"}).status_code == 403


def test_rep_comes_from_email_recipients(monkeypatch):
    from app import inbox

    monkeypatch.setattr(inbox, "read_rto", lambda subject, body, pdf: {
        "is_vendor_reply": False, "is_change_order": False, "job_name": subject.split(": ")[1], "rep_name": "Drew"})
    monkeypatch.setenv("INTAKE_TOKEN", "secret")
    bot = TestClient(app, headers={"x-intake-token": "secret"})
    owner = client("skyler@x.com", "ownerpass")

    def job_rep(mid, subject, recipients, sender="Delani Smith <delani@x.com>"):
        r = bot.post("/api/intake/email", data={"message_id": mid, "subject": subject, "body": "b",
                                                "recipients": recipients, "sender": sender}).json()
        return owner.get(f"/api/jobs/{r['job_id']}").json()["rep_name"]

    # Claude's guess is ignored when we know who was copied; no rep copied = the owner's own sale.
    assert job_rep("1", "READY TO ORDER: Alpha", "skyler@x.com, m.j@highland.com") == "Skyler"
    assert job_rep("2", "READY TO ORDER: Bravo", "skyler@x.com, Drew <DREW@x.com>") == "Drew"


def test_delete_job_keeps_pay_and_reimbursements(monkeypatch):
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner, name="Test job")["id"]
    owner.post(f"/api/jobs/{jid}/ledger", json={"line": 2, "amount": "500"})
    r = owner.post(f"/api/jobs/{jid}/payments", data={"amount": "1000", "method": "check", "status": "received",
                                                     "received_date": "2026-10-01"})
    assert r.status_code == 200, r.text
    omar = client("omar@x.com", "omarpass")
    lid = omar.post("/api/labor", json={"job_id": jid, "hours": 2}).json()["id"]
    r = owner.delete(f"/api/jobs/{jid}")
    assert r.status_code == 409 and "hours" in r.json()["detail"]  # crew pay is never deleted silently
    assert owner.delete(f"/api/labor/{lid}").status_code == 200
    assert omar.delete(f"/api/jobs/{jid}").status_code == 403
    assert owner.delete(f"/api/jobs/{jid}").status_code == 200
    assert owner.get(f"/api/jobs/{jid}").status_code == 404
    assert [j["name"] for j in owner.get("/api/jobs").json()] == []
    assert any(a["action"] == "delete" and a["table"] == "jobs" for a in owner.get("/api/audit").json())


def test_rep_stats():
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)  # Drew: 30,000 sold, 12,000 material
    make_job(owner, name="No material yet", material_total=None, invoice_total="5000")
    stats = owner.get("/api/reps/stats?period=all").json()
    drew = next(r for r in stats["reps"] if r["rep"] == "Drew")
    assert drew["deal_count"] == 2 and drew["sold"] == "35000.00" and drew["avg_deal"] == "17500.00"
    assert drew["profit"] == "18000.00" and drew["margin_pct"] == "60.0"  # only the job with costs in
    assert drew["commission_pending"] == "5400.00" and drew["missing_costs"] == 1
    assert owner.get("/api/reps/stats?period=week").status_code == 422
    assert client("drew@x.com", "drewpass1").get("/api/reps/stats").status_code == 403


def test_reconcile_with_quickbooks(monkeypatch):
    from app import inbox
    from app.db import SessionLocal
    from app.models import User
    owner = client("skyler@x.com", "ownerpass")
    with SessionLocal() as db:  # Delani sends the order emails and is copied on the owner's forwards
        db.add(User(name="Delani", email="delani@x.com", password_hash="x", role="rep"))
        db.commit()
    monkeypatch.setenv("INTAKE_TOKEN", "secret")
    bot = TestClient(app, headers={"x-intake-token": "secret"})
    replies = {}
    monkeypatch.setattr(inbox, "read_rto", lambda subject, body, pdfs: replies[subject])

    def send(mid, subject, sender, recipients, **p):
        replies[subject] = {"is_vendor_reply": False, "is_change_order": False, **p}
        return bot.post("/api/intake/email", data={"message_id": mid, "subject": subject, "body": "b",
                                                   "sender": sender, "recipients": recipients}).json()

    job = send("a", "READY TO ORDER: Echo Canyon", "Delani <delani@x.com>", "skyler@x.com", job_name="Echo Canyon",
               invoice_total=0)
    # Owner's forward copies Delani; she must not become the rep.
    send("b", "READY TO ORDER: Echo Canyon Change Order", "skyler@x.com", "delani@x.com, mike@hci.com",
         job_name="Echo Canyon", is_change_order=True, invoice_total=567.97)
    jid = job["job_id"]
    assert owner.get(f"/api/jobs/{jid}").json()["rep_name"] == "Skyler"
    # A rep who only replies on a thread is still a rep.
    drew_job = send("c", "READY TO ORDER: Drew Deal", "Delani <delani@x.com>", "drew@x.com", job_name="Drew Deal")
    send("d", "Re: READY TO ORDER: Drew Deal", "Drew <drew@x.com>", "delani@x.com", job_name="Drew Deal")
    assert owner.get(f"/api/jobs/{drew_job['job_id']}").json()["rep_name"] == "Drew"
    bot.post("/api/intake/reconcile", json=[{"job_id": drew_job["job_id"], "qb_total": "100", "qb_paid": "0",
                                             "last_invoice_date": "2026-10-20"}])
    assert owner.get(f"/api/jobs/{drew_job['job_id']}").json()["rep_name"] == "Drew"

    r = bot.post("/api/intake/reconcile", json=[{"job_id": jid, "qb_total": "13312.82", "qb_paid": "13312.82",
                                                 "last_invoice_date": "2026-05-11"}]).json()
    j = owner.get(f"/api/jobs/{jid}").json()
    assert line(j, 3) == "13312.82" and j["waterfall"]["balance_due"] == "0.00"
    assert j["status"] == "Closed" and j["rep_name"] == "Skyler" and "email change order removed" in r[0]["changes"]
    # Paid-up but material cost unknown: commission is not due yet.
    assert owner.get("/api/collections?period=year").json()["commission_due"] == []


def test_set_reps_by_name(monkeypatch):
    owner = client("skyler@x.com", "ownerpass")
    jid = make_job(owner)["id"]
    monkeypatch.setenv("INTAKE_TOKEN", "secret")
    bot = TestClient(app, headers={"x-intake-token": "secret"})
    r = bot.post("/api/intake/set-reps", json=[{"job_id": jid, "rep": "Skyler"}, {"job_id": jid, "rep": "Kenneth Bodine"}])
    assert [x["result"] for x in r.json()] == ["Drew -> Skyler", "no login named Kenneth Bodine"]
    assert owner.get(f"/api/jobs/{jid}").json()["rep_name"] == "Skyler"
    assert TestClient(app).post("/api/intake/set-reps", json=[]).status_code == 401
