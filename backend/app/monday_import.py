"""Conservative, additive Monday migration. Never writes financial Job Tracker tables.

The CLI defaults to an offline plan. Applying requires an explicitly verified
target and backup reference; Monday remains a read-only source.
"""
import hashlib
import json
from collections import Counter
from datetime import date, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Job, User, now
from .workspace_models import Activity, Opportunity

BOARD_ID = "18383996445"
GROUPS = {
    "group_mm2tdmmv": ("Design", {"Design"}, "Design", None),
    "group_mm2tgr4b": ("Estimate", {"Estimate"}, "Estimate", None),
    "group_mm57zhzt": ("Prepping for order", {"Prepping for Order"}, "Won", "Prepping"),
    "group_mm32gnha": ("Ready to Order", {"Ready to Order"}, "Won", "Ready to Order"),
    "group_mm33j1f3": ("Ordered", {"ORDERED"}, "Won", None),
    "group_mm2twmct": ("Active", {"ACTIVE", "READY TO SEND"}, None, None),
}


class MondaySource(Base):
    __tablename__ = "workspace_monday_sources"
    __table_args__ = (UniqueConstraint("board_id", "item_id", name="uq_workspace_monday_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[str] = mapped_column(String(64))
    item_id: Mapped[str] = mapped_column(String(64))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("workspace_opportunities.id"), unique=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_group_id: Mapped[str] = mapped_column(String(100))
    source_group_title: Mapped[str] = mapped_column(String(200))
    source_status: Mapped[str] = mapped_column(String(100))
    source_updated_at: Mapped[str | None] = mapped_column(String(64))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    owner_review: Mapped[bool] = mapped_column(Boolean, default=False)
    sales_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(Text)
    import_target: Mapped[str] = mapped_column(String(200))
    backup_reference: Mapped[str] = mapped_column(Text)
    imported_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def raw_value(column):
    value = column.get("value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def column_text(columns, column_id, key=None):
    column = columns.get(column_id, {})
    value = raw_value(column)
    if key and isinstance(value, dict) and value.get(key):
        return str(value[key]).strip()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return str(column.get("text") or "").strip() or None


def status_label(columns, board):
    column = columns.get("status", {})
    if column.get("text"):
        return column["text"]
    value = raw_value(column) or {}
    index = value.get("index") if isinstance(value, dict) else None
    metadata = next((c for c in board.get("columns", []) if c["id"] == "status"), {})
    labels = metadata.get("settings", {}).get("labels", [])
    if isinstance(labels, dict):
        return labels.get(str(index), "")
    return next((label["label"] for label in labels if label.get("index") == index), "")


def build_plan(export, mapping, db=None):
    """Prepare records without writing. Missing owner mappings become admin review.

    Ordered items require an explicit item-id -> tracker-job-id mapping with
    evidence. Active is classification-only unless import_active_sales is true.
    db optionally validates IDs and reports existing source receipts on rerun.
    """
    board = export.get("board", {})
    if str(board.get("id")) != BOARD_ID or str(mapping.get("board_id", BOARD_ID)) != BOARD_ID:
        raise ValueError("This importer only accepts the verified Mill City sales board")
    admin_id = mapping.get("admin_user_id")
    if admin_id is not None and (not isinstance(admin_id, int) or isinstance(admin_id, bool) or admin_id < 1):
        raise ValueError("admin_user_id must be a positive integer")
    owners = mapping.get("owners", {})
    links = mapping.get("job_links", {})
    entries, seen = [], set()
    for group in export.get("groups", []):
        page = group.get("items_page", {})
        if page.get("cursor") is not None:
            raise ValueError(f"Incomplete source group export: {group.get('id')}")
        for item in page.get("items", []):
            item_id = str(item["id"])
            if item_id in seen:
                raise ValueError(f"Duplicate Monday item ID in export: {item_id}")
            seen.add(item_id)
            group_id = str(group["id"])
            columns = {c["id"]: c for c in item.get("column_values", [])}
            status = status_label(columns, board)
            source_url = f"https://mill-city-cabinetry.monday.com/boards/{BOARD_ID}/pulses/{item_id}"
            reasons, review = [], []
            entry = {"item_id": item_id, "group_id": group_id, "group_title": group["title"], "sales_review": False,
                     "source_status": status, "source_url": source_url, "fingerprint": fingerprint(item),
                     "source_item": item, "action": "hold", "reasons": reasons, "owner_review_reasons": review}
            entries.append(entry)
            if group_id not in GROUPS:
                reasons.append("Group is outside the authorized first migration batch")
                continue
            _, accepted, stage, operations_stage = GROUPS[group_id]
            if status not in accepted:
                reasons.append("Group/status mismatch requires classification")
            if item.get("state", "active") != "active":
                reasons.append("Source item is not active")
            if group_id == "group_mm2twmct":
                # Explicit Mill City decision: the uncertain Active backlog goes
                # to Sales follow-up regardless of its old ACTIVE/READY TO SEND label.
                stage = "Follow up"
                entry["sales_review"] = True
                if not mapping.get("import_active_sales", False):
                    reasons.append("Active group is classification-only; not imported in the operations batch")
            people = raw_value(columns.get("person", {})) or {}
            assignments = people.get("personsAndTeams", []) if isinstance(people, dict) else []
            owner_ids = [str(person.get("id")) for person in assignments if person.get("kind") == "person"]
            entry["monday_owner_ids"] = owner_ids
            mapped = owners.get(owner_ids[0]) if len(assignments) == len(owner_ids) == 1 else None
            if not isinstance(mapped, int) or isinstance(mapped, bool) or mapped < 1:
                mapped = admin_id
                review.append("Monday owner missing, multiple, team-assigned or not mapped; administrator must verify")
            if mapped is None:
                reasons.append("Verified administrator and owner mapping are not available")
            if db is not None and mapped is not None:
                target_owner = db.get(User, mapped)
                if not target_owner or not target_owner.active or target_owner.role not in ("owner", "rep"):
                    review.append("Mapped owner is not an active sales/admin user; administrator must verify")
                    mapped = admin_id
            job_link = links.get(item_id)
            linked_job_id = None
            if job_link is not None:
                if (not isinstance(job_link, dict) or not isinstance(job_link.get("job_id"), int)
                        or isinstance(job_link.get("job_id"), bool) or job_link.get("job_id", 0) < 1
                        or not str(job_link.get("evidence") or "").strip()):
                    reasons.append("Tracker link lacks an explicit numeric job ID and source evidence")
                else:
                    linked_job_id = job_link["job_id"]
                    entry["link_evidence"] = job_link["evidence"]
                    if db is not None:
                        job = db.get(Job, linked_job_id)
                        if job is None:
                            reasons.append("Explicit tracker job ID does not exist in this target")
                        elif mapped != job.rep_id:
                            mapped = admin_id
                            review.append("Monday owner and linked tracker owner differ; preserve tracker ownership and require admin review")
                        duplicate = db.scalar(select(Opportunity.id).where(Opportunity.linked_job_id == linked_job_id))
                        if duplicate is not None:
                            reasons.append("Tracker job is already linked to an opportunity; reconcile existing source explicitly")
            if group_id == "group_mm33j1f3" and linked_job_id is None:
                reasons.append("Ordered job requires an explicit verified existing Job Tracker match")
            name = str(item.get("name") or "").strip()
            email = column_text(columns, "email_mkxvrx3x", "email")
            phone = column_text(columns, "phone_mkxvw0je", "phone")
            address = column_text(columns, "text_mkxvm83r")
            notes = column_text(columns, "long_text_mm5akq90", "text")
            for field, value, limit in (("name", name, 200), ("email", email, 320), ("phone", phone, 80),
                                        ("address", address, 500), ("notes", notes, 20000)):
                if value and len(value) > limit:
                    reasons.append(f"{field} exceeds target length; preserve source and review")
            if not name:
                reasons.append("Source item has no usable name")
            preferred_delivery = column_text(columns, "date_mm5azt7v", "date")
            scope = f"Existing Monday {group['title']} work. Review source item and its current attachments: {source_url}"
            if preferred_delivery:
                scope += f"\nOriginal Monday preferred delivery: {preferred_delivery}. Confirm scheduling separately."
            entry["opportunity"] = {
                "name": name, "customer": name, "email": email, "phone": phone, "address": address,
                "owner_id": mapped, "visibility": "private", "stage": stage,
                "amount": None, "notes": notes, "work_scope": scope,
                "linked_job_id": linked_job_id, "operations_stage": operations_stage or "Prepping",
            }
            if db is not None:
                existing = db.scalar(select(MondaySource).where(MondaySource.board_id == BOARD_ID, MondaySource.item_id == item_id))
                if existing is not None:
                    entry["action"] = "skip_existing"
                    entry["existing_opportunity_id"] = existing.opportunity_id
                    entry["source_changed"] = existing.source_fingerprint != entry["fingerprint"]
                    continue
            if not reasons:
                entry["action"] = "import"
    return {"board_id": BOARD_ID, "source_exported_at": export.get("exported_at"),
            "source_fingerprint": fingerprint(export), "mapping_fingerprint": fingerprint(mapping),
            "admin_user_id": admin_id, "entries": entries, "summary": summarize(entries)}


def summarize(entries):
    return {"total": len(entries), "actions": dict(Counter(e["action"] for e in entries)),
            "by_group": dict(Counter(e["group_title"] for e in entries)),
            "owner_review": sum(bool(e["owner_review_reasons"]) for e in entries),
            "sales_review": sum(e["sales_review"] for e in entries),
            "hold_reasons": dict(Counter(reason for e in entries if e["action"] == "hold" for reason in e["reasons"]))}


def apply_plan(db, export, mapping, *, target_id, backup_reference):
    """Apply one transaction; skip prior sources, never update an existing opportunity.

    The caller supplies the verified target identity and backup reference. This
    helper does not commit: the CLI controls atomic commit/rollback.
    """
    if not str(target_id).strip() or not str(backup_reference).strip():
        raise ValueError("Verified target identity and backup reference are required")
    admin = db.get(User, mapping.get("admin_user_id"))
    if admin is None or not admin.active or admin.role != "owner":
        raise ValueError("Import actor must be a verified active administrator in this target")
    db.info["user_id"] = admin.id
    plan = build_plan(export, mapping, db)
    from .workspace import _ensure_work
    imported = []
    for entry in plan["entries"]:
        if entry["action"] != "import":
            continue
        row = Opportunity(**entry["opportunity"])
        db.add(row)
        db.flush()
        _ensure_work(db, row)
        review = "; ".join(entry["owner_review_reasons"]) or None
        source = MondaySource(
            board_id=BOARD_ID, item_id=entry["item_id"], opportunity_id=row.id,
            source_url=entry["source_url"], source_group_id=entry["group_id"], source_group_title=entry["group_title"],
            source_status=entry["source_status"], source_updated_at=entry["source_item"].get("updated_at"),
            source_fingerprint=entry["fingerprint"], source_snapshot={
                "item": entry["source_item"], "exported_at": plan["source_exported_at"],
                "owner_ids": entry["monday_owner_ids"], "link_evidence": entry.get("link_evidence"),
            }, owner_review=bool(review), sales_review=entry["sales_review"], review_reason=review,
            import_target=target_id, backup_reference=backup_reference, imported_by=admin.id,
        )
        db.add(source)
        body = f"Imported from Monday item {entry['item_id']}. Original group: {entry['group_title']}; status: {entry['source_status']}. {entry['source_url']}"
        if review:
            body += f"\nAdministrator review: {review}"
        if entry["sales_review"]:
            body += "\nSales review: moved from the uncertain Active backlog to Follow up. Confirm the current opportunity and next customer contact."
        db.add(Activity(opportunity_id=row.id, actor_id=admin.id, kind="Note", body=body))
        db.flush()
        imported.append({"item_id": entry["item_id"], "opportunity_id": row.id, "owner_review": bool(review)})
    return {"summary": plan["summary"], "imported": imported, "target_id": target_id}
