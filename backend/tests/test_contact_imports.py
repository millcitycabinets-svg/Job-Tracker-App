"""CSV import acceptance with real sessions and a disposable database per test.

No production engine is connected to and application lifespan is never run.
"""
import csv
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

if not os.environ.get("DATABASE_URL", "").startswith("sqlite:"):
    raise RuntimeError("Contact import tests require an explicit disposable SQLite DATABASE_URL")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.contact_import_models import ContactImportBatch, ContactImportLock, ContactImportRow
from app.db import Base, get_db
from app.main import FAILED_LOGINS, app
from app.models import Job, User
from app.workspace_models import CommandReceipt, Opportunity, WorkRequest

PREFIX = "/api/workspace/imports"
PASSWORD = "csv-test-password-only"


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def csv_bytes(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def imports(tmp_path, password_hash):
    engine = create_engine(f"sqlite:///{tmp_path / 'contact-imports.sqlite'}",
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def fk_on(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    roles = {"admin": "owner", "a": "rep", "b": "rep", "ops": "operations", "designer": "designer", "crew": "crew"}
    with sessions() as db:
        users = {key: User(name=key, email=f"{key}@csv.test", role=role,
                           password_hash=password_hash, active=True) for key, role in roles.items()}
        db.add_all(users.values())
        db.add(ContactImportLock(id=1, revision=0))
        db.commit()
        ids = {key: row.id for key, row in users.items()}

    def isolated_db():
        with sessions() as db:
            yield db

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = isolated_db
    FAILED_LOGINS.clear()
    clients = []

    class Harness:
        user_ids = ids
        session = sessions

        def client(self, role="a"):
            client = TestClient(app, base_url="https://testserver")
            ok(client.post("/api/login", json={"email": f"{role}@csv.test", "password": PASSWORD}))
            clients.append(client)
            return client

        def preview(self, content=None, role="a", **data):
            content = content if content is not None else b"name,email\r\nAlice,alice@example.test\r\n"
            if "mapping" in data and isinstance(data["mapping"], dict):
                data["mapping"] = json.dumps(data["mapping"])
            return self.client(role).post(f"{PREFIX}/preview", files={"file": ("leads.csv", content, "text/csv")}, data=data)

        def commit(self, batch, role="a", command_id=None, client=None, **extra):
            return (client or self.client(role)).post(f"{PREFIX}/{batch['id']}/commit",
                json={"command_id": command_id or str(uuid4()), **extra})

        def lead(self, role="a", **values):
            with sessions() as db:
                row = Opportunity(name="Existing", customer="Existing", owner_id=ids[role],
                                  visibility=values.pop("visibility", "private"), stage="New Lead", **values)
                db.add(row)
                db.commit()
                return row.id

        def count(self, model):
            with sessions() as db:
                return db.scalar(select(func.count()).select_from(model))

    try:
        yield Harness()
    finally:
        for client in clients:
            client.close()
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        engine.dispose()


def test_template_auth_and_headers(imports):
    response = imports.client().get(f"{PREFIX}/template")
    assert response.status_code == 200
    assert response.text == "name,customer,email,phone,address,notes,source_url\r\n"
    assert "attachment" in response.headers["content-disposition"]
    assert TestClient(app).get(f"{PREFIX}/template").status_code == 401


@pytest.mark.parametrize("role", ["ops", "designer", "crew"])
def test_no_operations_import_access(imports, role):
    assert imports.client(role).get(f"{PREFIX}/template").status_code == 403
    assert imports.preview(role=role).status_code == 403
    batch = ok(imports.preview())
    assert imports.commit(batch, role=role).status_code == 403


def test_bom_quoted_multiline_mapping_and_provenance(imports):
    body = csv_bytes(["Full Name", "Company", "Email Address", "Phone", "Notes", "Website"], [
        ["Alice Smith", 'Cabinets, "West"', " ALICE@EXAMPLE.TEST ", "(602) 555-1234", "First line\nsecond, line", "https://example.test/list"],
    ])
    batch = ok(imports.preview(body, mapping={}))
    assert batch["columns"] == ["Full Name", "Company", "Email Address", "Phone", "Notes", "Website"]
    assert batch["valid_count"] == 1
    assert batch["rows"][0]["data"]["notes"] == "First line\nsecond, line"
    assert batch["rows"][0]["data"]["customer"] == 'Cabinets, "West"'
    assert imports.count(Opportunity) == 0
    result = ok(imports.commit(batch))
    assert result["created_count"] == 1 and result["skipped_count"] == 0
    assert imports.count(Job) == imports.count(WorkRequest) == 0
    with imports.session() as db:
        lead = db.get(Opportunity, result["created_ids"][0])
        assert lead.stage == "New Lead" and lead.owner_id == imports.user_ids["a"] and lead.visibility == "private"
        assert lead.email == "alice@example.test" and lead.amount is None
        row = db.scalar(select(ContactImportRow))
        assert row.data["source_url"] == "https://example.test/list"
        assert (row.source_kind, row.lead_kind, row.marketing_consent) == ("csv", "prospect", "not_recorded")
        assert row.opportunity_id == lead.id


def test_unrecognized_headers_are_discoverable_then_remappable(imports):
    content = b"Builder label,Main number,Ignore\nLocal Build,602-555-1234,Won\n"
    first = ok(imports.preview(content))
    assert first["headers"] == ["Builder label", "Main number", "Ignore"]
    assert first["errors"] and first["invalid_count"] == 1 and first["valid_count"] == 0
    second = ok(imports.preview(content, mapping={"customer": "Builder label", "phone": "Main number", "notes": ""}))
    assert second["valid_count"] == 1 and second["rows"][0]["data"]["name"] == "Local Build"
    assert second["mapping"] == {"customer": "Builder label", "phone": "Main number"}
    assert ok(imports.commit(second))["created_count"] == 1


def test_first_last_name_fallback(imports):
    batch = ok(imports.preview(b"First Name,Last Name,Email\nAlice,Smith,a@example.test\n"))
    assert batch["rows"][0]["data"]["name"] == batch["rows"][0]["data"]["customer"] == "Alice Smith"
    assert batch["valid_count"] == 1


@pytest.mark.parametrize("mapping", ["not-json", "[]", '{"unexpected":"name"}', '{"name":4}',
    '{"name":"Missing","email":"email"}', '{"name":"name"}'])
def test_invalid_mapping_is_human_readable(imports, mapping):
    response = imports.preview(mapping=mapping)
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)
    assert imports.count(ContactImportBatch) == 0


def test_duplicates_within_file_and_existing_visible_only(imports):
    imports.lead(email="visible@example.test")
    imports.lead(role="b", phone="+1 602 555 9999", visibility="shared")
    imports.lead(role="b", email="private@example.test")
    body = csv_bytes(["company", "email", "phone"], [
        ["Visible", "VISIBLE@EXAMPLE.TEST", ""],
        ["Shared", "", "602-555-9999"],
        ["Private should not be revealed", "private@example.test", ""],
        ["New", "new@example.test", "6025558888"],
        ["Repeat email", "NEW@example.test", ""],
        ["Repeat phone", "different@example.test", "+1 (602) 555-8888 ext 123"],
    ])
    batch = ok(imports.preview(body))
    assert (batch["valid_count"], batch["duplicate_count"], batch["invalid_count"]) == (2, 4, 0)
    assert batch["rows"][2]["status"] == "valid" and batch["rows"][2]["duplicate_reason"] is None
    assert "Existing" not in json.dumps(batch)
    result = ok(imports.commit(batch))
    assert (result["created_count"], result["duplicate_count"]) == (2, 4)


def test_invalid_rows_skip_explicitly_and_newlead_only(imports):
    body = csv_bytes(["name", "email", "phone", "source_url", "stage"], [
        ["Good", "good@example.test", "", "https://example.test", "Design"],
        ["Missing contact", "", "", "", ""],
        ["Bad email", "broken", "", "", ""],
        ["", "no-name@example.test", "", "", ""],
        ["Bad phone", "", "123", "", ""],
        ["Bad source", "bad-url@example.test", "", "javascript:alert(1)", ""],
    ])
    batch = ok(imports.preview(body))
    assert batch["invalid_count"] == 5
    result = ok(imports.commit(batch))
    assert (result["created_count"], result["invalid_count"], result["skipped_count"]) == (1, 5, 5)
    assert imports.count(WorkRequest) == 0
    with imports.session() as db:
        assert db.scalar(select(Opportunity.stage)) == "New Lead"


def test_preview_limit_full_summary_and_all_rows_commit(imports):
    batch = ok(imports.preview(csv_bytes(["company", "email"], [[f"Company{i}", f"c{i}@example.test"] for i in range(125)])))
    assert len(batch["rows"]) == batch["preview_limit"] == 100
    assert batch["summary"]["total_rows"] == batch["valid_count"] == 125
    assert ok(imports.commit(batch))["created_count"] == 125


@pytest.mark.parametrize("content,code", [
    (b"", 400), (b"name,email\n", 400), (b"name,name\nA,B\n", 400),
    (b"name,\nA,B\n", 400), (b'name,email\n"unclosed,a@example.test\n', 400),
    (b"name,email\nA,\xff\n", 400), (b"x" * (5 * 1024 * 1024 + 1), 413),
    (b"name,email\n" + b"A,a@example.test\n" * 5001, 413),
])
def test_malformed_or_oversized_file_no_saved_batch(imports, content, code):
    assert imports.preview(content).status_code == code
    assert imports.count(ContactImportBatch) == 0


def test_bad_row_width_and_length_are_invalid(imports):
    batch = ok(imports.preview(b"name,email\nAlice,a@example.test,extra\n" + b"a" * 201 + b",b@example.test\n"))
    assert batch["invalid_count"] == 2
    assert batch["rows"][0]["errors"] and batch["rows"][1]["errors"]


def test_owner_assignment_and_creator_access(imports):
    assert imports.preview(owner_id=imports.user_ids["a"]).status_code == 403
    assert imports.preview(role="admin", owner_id=imports.user_ids["ops"]).status_code == 422
    batch = ok(imports.preview(role="admin", owner_id=imports.user_ids["b"], visibility="shared"))
    assert imports.commit(batch, role="b").status_code == 404
    result = ok(imports.commit(batch, role="admin"))
    with imports.session() as db:
        lead = db.get(Opportunity, result["created_ids"][0])
        assert lead.owner_id == imports.user_ids["b"] and lead.visibility == "shared"
    own = ok(imports.preview())
    assert imports.commit(own, role="admin").status_code == 404


def test_permission_and_owner_status_revalidated(imports):
    batch = ok(imports.preview(role="admin", owner_id=imports.user_ids["b"]))
    client = imports.client("admin")
    with imports.session() as db:
        db.get(User, imports.user_ids["b"]).active = False
        db.commit()
    assert imports.commit(batch, client=client).status_code == 422
    assert imports.count(Opportunity) == 0
    with imports.session() as db:
        db.get(User, imports.user_ids["b"]).active = True
        db.get(User, imports.user_ids["admin"]).role = "rep"
        db.commit()
    assert imports.commit(batch, client=client).status_code == 403
    assert imports.count(Opportunity) == 0


def test_replay_is_durable_payload_bound_and_scoped_after_assignment(imports):
    batch = ok(imports.preview())
    command_id = str(uuid4())
    first = ok(imports.commit(batch, command_id=command_id))
    second = ok(imports.commit(batch, command_id=command_id))
    assert second["replayed"] and second["created_ids"] == first["created_ids"]
    assert ok(imports.commit(batch))["replayed"]
    assert imports.count(Opportunity) == 1
    other = ok(imports.preview(b"name,email\nBob,bob@example.test\n"))
    assert imports.commit(other, command_id=command_id).status_code == 409
    assert imports.commit(batch, role="b", command_id=command_id).status_code == 404
    with imports.session() as db:
        db.get(Opportunity, first["created_ids"][0]).owner_id = imports.user_ids["b"]
        db.commit()
    replay = ok(imports.commit(batch, command_id=command_id))
    assert replay["created_ids"] == [] and replay["created_count"] == 1
    assert imports.count(Opportunity) == 1


def test_receipt_rejects_role_change_and_unknown_payload(imports):
    batch = ok(imports.preview())
    command_id = str(uuid4())
    client = imports.client()
    ok(imports.commit(batch, command_id=command_id, client=client))
    with imports.session() as db:
        db.get(User, imports.user_ids["a"]).role = "owner"
        db.commit()
    assert imports.commit(batch, command_id=command_id, client=client).status_code == 409
    assert imports.commit(batch, stage="Won", client=client).status_code == 422


def test_commit_dedupes_contacts_added_after_preview(imports):
    batch = ok(imports.preview())
    imports.lead(email="ALICE@example.test")
    result = ok(imports.commit(batch))
    assert result["created_count"] == 0 and result["duplicate_count"] == 1
    assert imports.count(Opportunity) == 1


@pytest.mark.parametrize("same_batch,same_command", [(True, True), (True, False), (False, False)])
def test_concurrent_commit_no_duplicate_opportunities(imports, same_batch, same_command):
    first = ok(imports.preview())
    second = first if same_batch else ok(imports.preview())
    clients = [imports.client(), imports.client()]
    barrier = Barrier(2)
    commands = [str(uuid4()), str(uuid4())]
    if same_command:
        commands[1] = commands[0]

    def commit(index):
        barrier.wait(timeout=10)
        batch = [first, second][index]
        return imports.commit(batch, client=clients[index], command_id=commands[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(commit, range(2)))
    results = [ok(response) for response in responses]
    assert imports.count(Opportunity) == 1
    assert all(result["status"] == "committed" for result in results)
    if not same_batch:
        assert sorted(result["created_count"] for result in results) == [0, 1]
    else:
        assert sum(result["replayed"] for result in results) == 1


def test_shared_imports_concurrently_dedupe_across_reps(imports):
    batches = [ok(imports.preview(role=role, visibility="shared")) for role in ("a", "b")]
    clients = [imports.client("a"), imports.client("b")]
    barrier = Barrier(2)

    def commit(index):
        barrier.wait(timeout=10)
        return ok(imports.commit(batches[index], client=clients[index]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, range(2)))
    assert imports.count(Opportunity) == 1
    assert sorted(result["created_count"] for result in results) == [0, 1]
