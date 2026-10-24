import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@/jobtracker_test?host=/tmp/pg")

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import LaborEntry, User


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all([
            User(name="Skyler", email="skyler@x.com", password_hash=hash_password("ownerpass"), role="owner"),
            User(name="Omar", email="omar@x.com", password_hash=hash_password("omarpass"), role="crew", hourly_rate=Decimal("40")),
            User(name="Jorge", email="jorge@x.com", password_hash=hash_password("jorgepass"), role="crew",
                 hourly_rate=Decimal("24.50")),
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
    assert omar.get("/api/job-names").json() == [{"id": jid, "name": "Dustin Selden"}]


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
