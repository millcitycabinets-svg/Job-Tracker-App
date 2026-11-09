"""Review and commit CSV prospect lists. This module does not send outreach."""
import csv
import hashlib
import io
import json
import re
from pathlib import PurePath
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .auth import current_user
from .contact_import_models import ContactImportBatch, ContactImportLock, ContactImportRow
from .db import get_db
from .models import User, now
from .workspace import Input, SALES, _begin_command, _check_owner, _commit, _role, _sales_filter
from .workspace_models import Opportunity

router = APIRouter(prefix="/api/workspace/imports", tags=["contact imports"])
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
PREVIEW_LIMIT = 100
FIELDS = ("name", "customer", "email", "phone", "address", "notes", "source_url")
LIMITS = {"name": 200, "customer": 200, "email": 320, "phone": 80, "address": 500,
          "notes": 20000, "source_url": 2000}
ALIASES = {
    "name": ("name", "fullname", "contactname", "contact", "person", "leadname", "projectname"),
    "customer": ("customer", "company", "companyname", "business", "businessname", "organization", "account", "customername"),
    "email": ("email", "emailaddress", "contactemail", "workemail", "business email"),
    "phone": ("phone", "phonenumber", "telephone", "mobile", "mobilephone", "contactphone"),
    "address": ("address", "streetaddress", "businessaddress", "fulladdress", "location"),
    "notes": ("notes", "note", "description", "comments"),
    "source_url": ("sourceurl", "source", "website", "websiteurl", "url", "linkedin", "linkedinurl"),
}
PROVENANCE = {"source_kind": "csv", "lead_kind": "prospect", "marketing_consent": "not_recorded"}


class ImportCommit(Input):
    command_id: str = Field(min_length=1, max_length=160)


def _header_key(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _mapping(columns, raw_mapping):
    normalized = {_header_key(header): header for header in columns}
    suggested = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            if _header_key(alias) in normalized:
                suggested[field] = normalized[_header_key(alias)]
                break
    try:
        supplied = json.loads(raw_mapping) if raw_mapping else {}
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(422, "Column mapping must be a JSON object, for example {\"email\":\"Email Address\"}.")
    if not isinstance(supplied, dict):
        raise HTTPException(422, "Column mapping must be a JSON object of field names and CSV headers.")
    unknown = set(supplied) - set(FIELDS)
    if unknown:
        raise HTTPException(422, f"Unknown mapped fields: {', '.join(sorted(unknown))}. Use: {', '.join(FIELDS)}.")
    if any(not isinstance(value, str) for value in supplied.values()):
        raise HTTPException(422, "Each mapped header must be a string. Use an empty string to skip a field.")
    mapping = {key: value for key, value in supplied.items() if value} if supplied else suggested.copy()
    missing = set(mapping.values()) - set(columns)
    if missing:
        raise HTTPException(422, f"Mapped headers were not found in this CSV: {', '.join(sorted(missing))}.")
    # Names assembled from first/last name are an automatic fallback only.
    has_person_parts = not supplied and any(key in normalized for key in ("firstname", "lastname"))
    errors = []
    if not ({"name", "customer"} & set(mapping)) and not has_person_parts:
        errors.append("Map a contact name or company/customer column before importing.")
    if not ({"email", "phone"} & set(mapping)):
        errors.append("Map an email or phone column before importing.")
    if errors and supplied:
        raise HTTPException(422, " ".join(errors))
    return mapping, suggested, has_person_parts, errors


def _email_key(value):
    return (value or "").strip().casefold()


def _phone_key(value):
    # Keep the dialable number, ignoring a conventional extension for matching.
    base = re.split(r"\s*(?:ext\.?|extension|x|#)\s*\d+\s*$", (value or "").casefold())[0]
    digits = re.sub(r"\D", "", base)
    return "1" + digits if len(digits) == 10 else digits


def _keys(data):
    return [(kind, value) for kind, value in (("email", _email_key(data.get("email"))),
                                             ("phone", _phone_key(data.get("phone")))) if value]


def _visible_keys(db, user):
    # Deliberately never inspect other reps' private records to make a dedupe decision.
    seen = set()
    for email, phone in db.execute(select(Opportunity.email, Opportunity.phone).where(_sales_filter(user))):
        seen.update(_keys({"email": email, "phone": phone}))
    return seen


def _normalize(raw, mapping, automatic_parts=False):
    data = {key: (raw.get(mapping.get(key), "") or "").strip() or None for key in FIELDS}
    if automatic_parts and not data["name"]:
        by_key = {_header_key(key): value.strip() for key, value in raw.items()}
        data["name"] = " ".join(filter(None, (by_key.get("firstname"), by_key.get("lastname")))) or None
    data["name"] = data["name"] or data["customer"]
    data["customer"] = data["customer"] or data["name"]
    if data["email"]:
        data["email"] = _email_key(data["email"])
    errors = []
    if not data["name"]:
        errors.append("A contact name or company/customer is required.")
    if not data["email"] and not data["phone"]:
        errors.append("An email address or phone number is required.")
    for key, limit in LIMITS.items():
        if data[key] and len(data[key]) > limit:
            errors.append(f"{key} exceeds {limit} characters.")
        if data[key] and "\x00" in data[key]:
            errors.append(f"{key} contains a null character.")
    if data["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", data["email"]):
        errors.append("Email address is not valid.")
    if data["phone"] and not (7 <= len(_phone_key(data["phone"])) <= 15):
        errors.append("Phone number must contain 7 to 15 digits, with an optional extension.")
    if data["source_url"]:
        try:
            url = urlsplit(data["source_url"])
            valid_url = url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password
        except ValueError:
            valid_url = False
        if not valid_url:
            errors.append("Source URL must be an http:// or https:// website address.")
    return data, errors


def _parse(content, raw_mapping):
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "This file is not UTF-8. Save or export it as CSV UTF-8 and upload it again.")
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    try:
        columns = [item.strip() for item in next(reader)]
        if not columns or not any(columns):
            raise HTTPException(400, "The CSV needs a header row.")
        if len(columns) > 100:
            raise HTTPException(400, "The CSV has more than 100 columns. Keep the columns needed for your sales list.")
        if any(not column for column in columns) or len(set(column.casefold() for column in columns)) != len(columns):
            raise HTTPException(400, "Each CSV column needs a unique, nonempty header.")
        if any(len(column) > 200 for column in columns):
            raise HTTPException(400, "CSV headers must be 200 characters or fewer.")
        mapping, suggested, automatic_parts, mapping_errors = _mapping(columns, raw_mapping)
        rows = []
        for row_number, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            if len(rows) >= MAX_ROWS:
                raise HTTPException(413, "A CSV import can contain at most 5,000 nonempty rows. Split the list into smaller files.")
            raw = dict(zip(columns, cells))
            data, errors = _normalize(raw, mapping, automatic_parts)
            if len(cells) != len(columns):
                errors.insert(0, f"This row has {len(cells)} values but the header has {len(columns)} columns.")
            rows.append({"row_number": row_number, "data": data, "errors": errors})
        if not rows:
            raise HTTPException(400, "The CSV contains a header but no contact rows.")
        return columns, mapping, suggested, rows, mapping_errors
    except StopIteration:
        raise HTTPException(400, "The CSV is empty. Include a header row and at least one contact.")
    except csv.Error as exc:
        raise HTTPException(400, f"The CSV could not be read near record {reader.line_num}: {exc}. Check quoting and export it as CSV again.")


def _row_out(row):
    return {"row_number": row.row_number, "status": row.status, "data": row.data,
            "errors": row.errors, "duplicate_reason": row.duplicate_reason}


def _preview_out(batch, rows):
    return {"id": batch.id, "status": batch.status, "filename": batch.filename,
            "columns": batch.columns, "headers": batch.columns,
            "mapping": batch.mapping, "suggested_mapping": batch.suggested_mapping,
            "summary": batch.summary, **batch.summary, "rows": [_row_out(row) for row in rows[:PREVIEW_LIMIT]],
            "errors": batch.errors, "preview_limit": PREVIEW_LIMIT, "provenance": PROVENANCE}


def _batch_access(db, user, batch_id):
    _role(user, SALES)
    batch = db.scalar(select(ContactImportBatch).where(ContactImportBatch.id == batch_id,
                      ContactImportBatch.creator_id == user.id).execution_options(populate_existing=True))
    if batch is None:
        raise HTTPException(404, "This import preview is unavailable. Upload your own CSV to create a preview.")
    _check_owner(db, user, batch.owner_id)
    return batch


def _lock_imports(db):
    # Seeded by migration 0010. This SQL write obtains a PostgreSQL row lock or
    # SQLite writer lock BEFORE we re-read duplicates and create any leads.
    result = db.execute(update(ContactImportLock).where(ContactImportLock.id == 1)
                        .values(revision=ContactImportLock.revision + 1))
    if result.rowcount != 1:
        raise HTTPException(503, "Contact imports are not initialized. Apply the workspace database migrations.")


def _scoped_result(db, user, result, replayed):
    ids = result.get("created_ids", [])
    visible = set(db.scalars(select(Opportunity.id).where(Opportunity.id.in_(ids), _sales_filter(user)))) if ids else set()
    return {**result, "created_ids": [item for item in ids if item in visible], "replayed": replayed}


@router.get("/template")
def template(user: User = Depends(current_user)):
    _role(user, SALES)
    return Response(",".join(FIELDS) + "\r\n", media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="mill-city-sales-import.csv"'})


@router.post("/preview")
def preview(file: UploadFile = File(...), mapping: str | None = Form(None),
            visibility: Literal["private", "shared"] = Form("private"),
            owner_id: int | None = Form(None), db: Session = Depends(get_db), user: User = Depends(current_user)):
    _role(user, SALES)
    if owner_id is not None and user.role != "owner":
        raise HTTPException(403, "Only an administrator can choose the sales owner for an import.")
    target_owner = owner_id if owner_id is not None else user.id
    _check_owner(db, user, target_owner)
    content = file.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "The CSV is larger than 5 MiB. Split it into smaller files before uploading.")
    columns, selected_mapping, suggested, parsed, mapping_errors = _parse(content, mapping)
    existing = _visible_keys(db, user)
    within_file = set()
    rows = []
    for record in parsed:
        keys = set(_keys(record["data"]))
        reason = None
        if record["errors"]:
            status = "invalid"
        elif keys & within_file:
            status, reason = "duplicate", "Email or phone repeats an earlier valid row in this file."
        elif keys & existing:
            status, reason = "duplicate", "Email or phone matches an existing lead visible to you."
        else:
            status = "valid"
        if not record["errors"]:
            within_file.update(keys)
        rows.append(ContactImportRow(**record, status=status, duplicate_reason=reason))
    summary = {"total_rows": len(rows), "valid_count": sum(row.status == "valid" for row in rows),
               "duplicate_count": sum(row.status == "duplicate" for row in rows),
               "invalid_count": sum(row.status == "invalid" for row in rows)}
    batch = ContactImportBatch(creator_id=user.id, owner_id=target_owner, visibility=visibility,
        filename=PurePath((file.filename or "contacts.csv").replace("\\", "/")).name[:255],
        file_digest=hashlib.sha256(content).hexdigest(), columns=columns, mapping=selected_mapping,
        suggested_mapping=suggested, summary=summary, errors=mapping_errors, status="preview")
    db.add(batch)
    db.flush()
    for row in rows:
        row.batch_id = batch.id
    db.add_all(rows)
    return _commit(db, _preview_out(batch, rows))


@router.post("/{batch_id}/commit")
def commit_import(batch_id: int, body: ImportCommit, db: Session = Depends(get_db), user: User = Depends(current_user)):
    batch = _batch_access(db, user, batch_id)
    try:
        _lock_imports(db)
        # Re-read and lock actors after waiting: permissions can change between
        # preview and commit, or while a simultaneous import is finishing.
        db.scalars(select(User).where(User.id.in_({user.id, batch.owner_id})).order_by(User.id)
                   .with_for_update().execution_options(populate_existing=True)).all()
        batch = _batch_access(db, user, batch_id)
        receipt, prior = _begin_command(db, user, "contacts.import.commit", batch_id, body,
                                       lambda: _batch_access(db, user, batch_id))
        if prior is not None:
            return _commit(db, _scoped_result(db, user, prior, True))
        if batch.status == "committed":
            return _commit(db, _scoped_result(db, user, batch.result, True), receipt)
        rows = db.scalars(select(ContactImportRow).where(ContactImportRow.batch_id == batch_id)
                          .order_by(ContactImportRow.row_number)).all()
        existing = _visible_keys(db, user)
        created_ids = []
        duplicates = invalid = 0
        for row in rows:
            if row.status == "invalid":
                invalid += 1
                continue
            keys = set(_keys(row.data))
            if row.status == "duplicate" or keys & existing:
                duplicates += 1
                if row.status != "duplicate":
                    row.status = "duplicate"
                    row.duplicate_reason = "Email or phone matched a visible lead when this import was committed."
                continue
            lead = Opportunity(**{key: row.data[key] for key in ("name", "customer", "email", "phone", "address", "notes")},
                               owner_id=batch.owner_id, visibility=batch.visibility, stage="New Lead")
            db.add(lead)
            db.flush()
            row.opportunity_id = lead.id
            row.status = "created"
            created_ids.append(lead.id)
            existing.update(keys)
        result = {"id": batch.id, "status": "committed", "created_count": len(created_ids),
                  "duplicate_count": duplicates, "skipped_count": duplicates + invalid,
                  "invalid_count": invalid, "created_ids": created_ids, "replayed": False}
        batch.status, batch.committed_at, batch.result = "committed", now(), result
        return _commit(db, result, receipt)
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "Another import or update is in progress. Retry this commit with the same command ID.")
