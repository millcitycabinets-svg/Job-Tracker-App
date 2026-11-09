import hashlib
import hmac

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def session_fingerprint(user: User, secret: str) -> str:
    """A keyed credential version; the bcrypt hash never enters the cookie."""
    payload = f"{user.id}:{user.password_hash}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if (not user or not user.active or not hmac.compare_digest(
            str(request.session.get("credential_version", "")),
            session_fingerprint(user, request.app.state.session_secret))):
        request.session.clear()
        raise HTTPException(401, "Not logged in")
    expected_user = request.headers.get("X-Workspace-User")
    if expected_user is not None and expected_user != str(user.id):
        # Another tab may have signed in as B while this tab still displays A.
        # Reject the stale tab without clearing B's valid shared browser cookie.
        raise HTTPException(401, "Your signed-in account changed. Sign in again before continuing.")
    db.info["user_id"] = user.id  # read by the audit log
    return user


def require(*roles):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Not allowed")
        return user

    return dep


owner_only = require("owner")
