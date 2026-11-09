"""Fictional pilot records, created once through the actual finance models/CRM API."""
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Job, Payment, User

PASSWORD = "MillCityPilot!20"  # Public localhost-only test credentials, never production.
ACCOUNTS = [
    ("admin", "Alex · Admin", "owner"),
    ("sales", "Morgan · Sales", "rep"),
    ("sales2", "Taylor · Sales", "rep"),
    ("operations", "Jordan · Operations", "operations"),
    ("design", "Casey · Design", "designer"),
    ("crew", "Sam · Crew", "crew"),
]


def seed(app, pilot_dir):
    marker = pilot_dir / "seeded-v2"
    if marker.exists():
        return
    today = date.today()
    with SessionLocal() as db:
        unknown = db.scalars(select(User).where(~User.email.like("%@pilot.millcity.test"))).first()
        if unknown:
            raise RuntimeError("Pilot seed refused: database contains a non-pilot account.")
        users = {}
        for short, name, role in ACCOUNTS:
            email = f"{short}@pilot.millcity.test"
            user = db.scalar(select(User).where(User.email == email))
            if not user:
                user = User(name=name, email=email, role=role, active=True,
                            password_hash=hash_password(PASSWORD),
                            commission_rate=Decimal("0.30") if role in ("owner", "rep") else None,
                            hourly_rate=Decimal("40") if role == "crew" else None)
                db.add(user)
                db.flush()
            users[short] = user.id
        rows = [
            ("Maple House", "Maple sample household", "sales", "Ordered", "28000", "12000", "14000", 8),
            ("Desert Loft", "Desert sample household", "sales", "Installed", "19000", "8300", "19000", -2),
            ("Juniper Kitchen", "Juniper sample household", "sales", "Closed", "22000", "10000", "22000", -12),
            ("Cedar Bath", "Cedar sample household", "sales2", "Ordered", "6200", None, "3100", 13),
        ]
        jobs = {}
        for name, customer, rep, status, invoice, materials, collected, days in rows:
            fullname = f"Sample — {name}"
            job = db.scalar(select(Job).where(Job.name == fullname))
            if not job:
                job = Job(name=fullname, customer=customer, rep_id=users[rep], status=status,
                          invoice_total=Decimal(invoice), material_total=Decimal(materials) if materials else None,
                          boxes=24, install_date=today + timedelta(days=days),
                          closed_at=datetime.now(timezone.utc) if status == "Closed" else None,
                          commission_paid_at=datetime.now(timezone.utc) if status == "Closed" else None)
                db.add(job)
                db.flush()
                db.add(Payment(job_id=job.id, amount=Decimal(collected), method="ach", status="received",
                               payment_type="final" if collected == invoice else "deposit",
                               received_date=today - timedelta(days=3), created_by=users["admin"],
                               note="Fictional pilot payment; no money was transferred."))
            jobs[name] = job.id
        db.commit()

    client = TestClient(app)

    def call(method, path, body=None):
        response = client.request(method, "/api" + path, json=body) if body is not None else client.request(method, "/api" + path)
        if response.status_code >= 400:
            raise RuntimeError(f"Pilot setup {method} {path}: {response.status_code} {response.text[:400]}")
        return response.json()

    call("POST", "/login", {"email": "admin@pilot.millcity.test", "password": PASSWORD})
    existing = {o["name"]: o for o in call("GET", "/workspace/opportunities?scope=all")}
    leads = [
        ("Alder Kitchen", "Alder sample household", "sales", "Design", "private", "24500", -1, "Create initial kitchen design. Island seating for four; pantry wall and 36-inch range.", None),
        ("Sage Remodel", "Sage sample household", "sales", "Estimate", "private", "18000", 1, "Prepare cabinet estimate for a two-wall kitchen. Include two finish options.", None),
        ("Oak Lane Builders", "Oak Lane sample builder", "sales", "Follow up", "private", "42000", 0, "", None),
        ("Mesa Townhomes", "Mesa sample builder", "sales", "Awaiting Customer", "private", "36000", 2, "", None),
        ("Palo Verde Homes", "Palo Verde sample builder", "admin", "New Lead", "shared", "32000", 3, "", None),
        ("Willow Interiors", "Willow sample studio", "admin", "New Lead", "shared", "12500", 2, "", None),
        ("Acacia Guest House", "Acacia sample household", "sales2", "Design", "private", "11000", 1, "Design compact kitchenette with accessible storage.", None),
        ("Cottonwood Bath", "Cottonwood sample household", "sales2", "Estimate", "private", "8500", 0, "Estimate a double vanity with linen tower.", None),
        ("Maple House", "Maple sample household", "sales", "Won", "private", "28000", 8, "", "Maple House"),
        ("Desert Loft", "Desert sample household", "sales", "Won", "private", "19000", -2, "", "Desert Loft"),
        ("Juniper Kitchen", "Juniper sample household", "sales", "Won", "private", "22000", -12, "", "Juniper Kitchen"),
        ("Cedar Bath", "Cedar sample household", "sales2", "Won", "private", "6200", 13, "", "Cedar Bath"),
        ("Canyon Residence", "Canyon sample household", "sales", "Won", "private", "31500", 5, "Confirm final cabinet finish and appliance dimensions before ordering.", None),
        ("Mesquite Office", "Mesquite sample company", "sales2", "Won", "private", "9600", 6, "Approved cabinet list is ready for final purchasing review.", None),
    ]
    for name, customer, owner, stage, visibility, amount, days, scope, job in leads:
        fullname = f"Sample — {name}"
        lead = existing.get(fullname)
        if not lead:
            lead = call("POST", "/workspace/opportunities", {
                "name": fullname, "customer": customer, "owner_id": users[owner], "stage": stage,
                "visibility": visibility, "amount": amount, "next_follow_up": (today + timedelta(days=days)).isoformat(),
                "email": name.lower().replace(" ", ".") + "@example.test", "phone": "602-555-0100",
                "address": "Fictional pilot address · Phoenix, AZ", "work_scope": scope,
                "notes": "Fictional test record. This private sales note is excluded from Operations.",
            })
        if job and not lead.get("linked_job_id"):
            call("POST", f"/workspace/opportunities/{lead['id']}/link-job", {
                "job_id": jobs[job], "expected_revision": lead["revision"], "command_id": str(uuid4()),
            })
    for request in call("GET", "/workspace/requests"):
        if request["opportunity_name"].startswith("Sample — ") and request["assigned_to_id"] is None:
            call("PATCH", f"/workspace/requests/{request['id']}", {
                "expected_revision": request["revision"], "command_id": str(uuid4()),
                "assigned_to_id": users["design"] if request["kind"] == "Design" else users["operations"],
                "due_date": (today + timedelta(days=2)).isoformat(),
            })
    for preorder in call("GET", "/workspace/preorder-jobs"):
        if preorder["name"] == "Sample — Mesquite Office" and preorder["status"] == "Prepping":
            call("PATCH", f"/workspace/preorder-jobs/{preorder['id']}", {
                "expected_revision": preorder["revision"], "command_id": str(uuid4()),
                "status": "Ready to Order", "due_date": (today + timedelta(days=3)).isoformat(),
            })
    client.close()
    marker.write_text("Fictional seed completed. Do not delete to reset; back up the pilot directory first.\n")
    print("Created fictional test records. Local account details are in PILOT.md.")
