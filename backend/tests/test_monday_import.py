"""Monday imports are additive, replay-safe, source-traceable and finance read-only."""
import copy
import json

import pytest
from sqlalchemy import select

from app.monday_import import BOARD_ID, MondaySource, apply_plan, build_plan
from app.workspace_models import Activity, Opportunity, WorkRequest
from test_workspace import password_hash, workspace  # reuse isolated DB and real auth fixtures


def item(item_id, status, owner="11", **extra):
    return {
        "id": str(item_id), "name": f"Monday project {item_id}", "state": "active",
        "url": f"https://mill-city-cabinetry.monday.com/boards/{BOARD_ID}/pulses/{item_id}",
        "updated_at": "2026-11-09T12:00:00Z", "subitems": [],
        "column_values": [
            {"id": "status", "type": "status", "text": status, "value": json.dumps({"index": 0})},
            {"id": "person", "type": "people", "text": "Sales rep", "value": json.dumps({"personsAndTeams": [{"id": int(owner), "kind": "person"}]})},
            {"id": "email_mkxvrx3x", "type": "email", "text": "Display label", "value": json.dumps({"email": "customer@example.test", "text": "Display label"})},
            {"id": "phone_mkxvw0je", "type": "phone", "text": "Display phone", "value": json.dumps({"phone": "+15550123456", "countryShortName": "US"})},
            {"id": "text_mkxvm83r", "type": "text", "text": "100 Test Road", "value": json.dumps("100 Test Road")},
            {"id": "long_text_mm5akq90", "type": "long_text", "text": "Private sales note", "value": json.dumps({"text": "Private sales note"})},
            {"id": "date_mm5azt7v", "type": "date", "text": "2026-12-01", "value": json.dumps({"date": "2026-12-01"})},
            {"id": "file_mm2sendv", "type": "file", "text": "drawing.pdf", "value": json.dumps({"files": [{"assetId": 9876, "name": "drawing.pdf"}]})},
        ], **extra,
    }


def export_fixture():
    groups = [
        ("group_mm2tdmmv", "Design", [item(100, "Design"), item(105, "ACTIVE")]),
        ("group_mm2tgr4b", "Estimate", [item(101, "Estimate", owner="99")]),
        ("group_mm57zhzt", "Prepping for order", [item(102, "Prepping for Order")]),
        ("group_mm33j1f3", "Ordered", [item(103, "ORDERED")]),
        ("group_mm2twmct", "Active", [item(104, "ACTIVE")]),
        ("group_title", "Follow up", [item(106, "Design")]),
    ]
    return {"schema_version": 1, "exported_at": "2026-11-09T13:00:00Z", "board": {"id": BOARD_ID, "columns": []},
            "groups": [{"id": group_id, "title": title, "items_page": {"cursor": None, "items": items}}
                       for group_id, title, items in groups]}


def mapping_fixture(workspace):
    return {"board_id": BOARD_ID, "admin_user_id": workspace.user_ids["admin"],
            "owners": {"11": workspace.user_ids["rep_a"]},
            "job_links": {"103": {"job_id": workspace.jobs["a_open"], "evidence": "Explicit ID/source correspondence verified in this test"}},
            "import_active_sales": False}


def test_monday_import_preserves_source_private_ownership_and_financial_tables(workspace):
    before = workspace.financial_snapshot()
    source, mapping = export_fixture(), mapping_fixture(workspace)
    with workspace.session() as db:
        plan = build_plan(source, mapping, db)
        assert plan["summary"]["actions"] == {"import": 4, "hold": 3}
        result = apply_plan(db, source, mapping, target_id="isolated-test", backup_reference="test-fixture")
        db.commit()
        assert len(result["imported"]) == 4
        receipts = {row.item_id: row for row in db.scalars(select(MondaySource))}
        assert set(receipts) == {"100", "101", "102", "103"}
        design = db.get(Opportunity, receipts["100"].opportunity_id)
        assert design.owner_id == workspace.user_ids["rep_a"] and design.visibility == "private"
        assert design.email == "customer@example.test" and design.phone == "+15550123456"
        assert design.notes == "Private sales note" and design.amount is None
        estimate = db.get(Opportunity, receipts["101"].opportunity_id)
        assert estimate.owner_id == workspace.user_ids["admin"]
        assert receipts["101"].owner_review
        assert receipts["100"].source_snapshot["item"]["column_values"] == source["groups"][0]["items_page"]["items"][0]["column_values"]
        assert len(list(db.scalars(select(WorkRequest)))) == 2
        assert len(list(db.scalars(select(Activity)))) == 4
        assert all("Private sales note" not in request.context.get("scope", "") for request in db.scalars(select(WorkRequest)))
    assert workspace.financial_snapshot() == before


def test_monday_rerun_skips_existing_and_preserves_manual_changes(workspace):
    source, mapping = export_fixture(), mapping_fixture(workspace)
    with workspace.session() as db:
        apply_plan(db, source, mapping, target_id="isolated-test", backup_reference="test-fixture")
        db.commit()
        receipt = db.scalar(select(MondaySource).where(MondaySource.item_id == "100"))
        lead = db.get(Opportunity, receipt.opportunity_id)
        lead.name = "Manually edited project"
        lead.notes = "New work in the app"
        db.commit()
        source["groups"][0]["items_page"]["items"][0]["name"] = "Updated source name"
        plan = build_plan(source, mapping, db)
        entry = next(row for row in plan["entries"] if row["item_id"] == "100")
        assert entry["action"] == "skip_existing" and entry["source_changed"] is True
        result = apply_plan(db, source, mapping, target_id="isolated-test", backup_reference="test-fixture")
        db.commit()
        assert result["imported"] == []
        db.refresh(lead)
        assert lead.name == "Manually edited project" and lead.notes == "New work in the app"
        assert len(list(db.scalars(select(MondaySource)))) == 4
        assert len(list(db.scalars(select(WorkRequest)))) == 2
        assert len(list(db.scalars(select(Activity)))) == 4


def test_monday_ordered_without_explicit_proof_stays_held(workspace):
    source, mapping = export_fixture(), mapping_fixture(workspace)
    for links in ({}, {"103": {"job_id": workspace.jobs["a_open"]}}, {"103": {"job_id": True, "evidence": "invalid"}}):
        mapping["job_links"] = links
        with workspace.session() as db:
            entry = next(row for row in build_plan(source, mapping, db)["entries"] if row["item_id"] == "103")
            assert entry["action"] == "hold"
    assert workspace.financial_snapshot()["jobs"]


def test_monday_owner_conflict_requires_admin_review_without_financial_reassignment(workspace):
    source, mapping = export_fixture(), mapping_fixture(workspace)
    mapping["job_links"]["103"]["job_id"] = workspace.jobs["b_closed"]
    before = workspace.financial_snapshot()
    with workspace.session() as db:
        apply_plan(db, source, mapping, target_id="isolated-test", backup_reference="test-fixture")
        db.commit()
        record = db.scalar(select(MondaySource).where(MondaySource.item_id == "103"))
        lead = db.get(Opportunity, record.opportunity_id)
        assert record.owner_review and lead.owner_id == workspace.user_ids["admin"]
        assert lead.linked_job_id == workspace.jobs["b_closed"]
    assert workspace.financial_snapshot() == before


def test_monday_rejects_incomplete_wrong_board_and_duplicate_source(workspace):
    mapping = mapping_fixture(workspace)
    for transform in (
        lambda source: source["board"].update(id="other"),
        lambda source: source["groups"][0]["items_page"].update(cursor="more-items"),
        lambda source: source["groups"][0]["items_page"]["items"].append(copy.deepcopy(source["groups"][0]["items_page"]["items"][0])),
    ):
        source = export_fixture()
        transform(source)
        with pytest.raises(ValueError):
            build_plan(source, mapping)


def test_monday_apply_requires_real_administrator_target_and_backup(workspace):
    source, mapping = export_fixture(), mapping_fixture(workspace)
    with workspace.session() as db:
        for target, backup in (("", "backup"), ("target", "")):
            with pytest.raises(ValueError):
                apply_plan(db, source, mapping, target_id=target, backup_reference=backup)
        mapping["admin_user_id"] = workspace.user_ids["rep_a"]
        with pytest.raises(ValueError):
            apply_plan(db, source, mapping, target_id="test", backup_reference="backup")


@pytest.mark.parametrize("original_status", ["ACTIVE", "READY TO SEND"])
def test_monday_active_backlog_is_sales_followup_only_with_review_provenance(workspace, original_status):
    source, mapping = export_fixture(), mapping_fixture(workspace)
    source["groups"] = [next(group for group in source["groups"] if group["id"] == "group_mm2twmct")]
    source["groups"][0]["items_page"]["items"][0]["column_values"][0]["text"] = original_status
    mapping["import_active_sales"] = True
    before = workspace.financial_snapshot()
    with workspace.session() as db:
        result = apply_plan(db, source, mapping, target_id="isolated-test", backup_reference="test-fixture")
        db.commit()
        assert len(result["imported"]) == 1
        receipt = db.scalar(select(MondaySource))
        lead = db.get(Opportunity, receipt.opportunity_id)
        assert lead.stage == "Follow up" and lead.linked_job_id is None and lead.visibility == "private"
        assert receipt.sales_review and receipt.source_status == original_status
        assert list(db.scalars(select(WorkRequest))) == []
        activity = db.scalar(select(Activity))
        assert "Sales review" in activity.body and original_status in activity.body
    assert workspace.financial_snapshot() == before
