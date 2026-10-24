"""Every create, update and delete on audited tables is written to audit_log automatically.

The acting user is read from session.info["user_id"], which the request dependency sets.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event, insert, inspect
from sqlalchemy.orm import Session

from .models import AuditLog, Base

SECRET_FIELDS = {"password_hash"}


def _plain(value):
    if isinstance(value, bytes):
        return f"<file, {len(value)} bytes>"
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    return value


def _columns(obj):
    return [c.key for c in inspect(obj).mapper.column_attrs]


@event.listens_for(Session, "after_flush")
def _write_audit(session, flush_context):
    rows = []
    user_id = session.info.get("user_id")
    for action, objs in (("create", session.new), ("update", session.dirty), ("delete", session.deleted)):
        for obj in objs:
            if not isinstance(obj, Base) or isinstance(obj, AuditLog):
                continue
            state = inspect(obj)
            changes = {}
            for key in _columns(obj):
                hist = state.attrs[key].history
                if action == "create":
                    new = getattr(obj, key)
                    if new is not None:
                        changes[key] = [None, new]
                elif action == "delete":
                    changes[key] = [getattr(obj, key), None]
                elif hist.has_changes():
                    changes[key] = [hist.deleted[0] if hist.deleted else None, hist.added[0] if hist.added else None]
            if action == "update" and not changes:
                continue
            changes = {
                k: (["***", "***"] if k in SECRET_FIELDS else [_plain(old), _plain(new)]) for k, (old, new) in changes.items()
            }
            rows.append(
                {"user_id": user_id, "table_name": obj.__tablename__, "row_id": getattr(obj, "id", None),
                 "action": action, "changes": changes}
            )
    if rows:
        session.connection().execute(insert(AuditLog), rows)
