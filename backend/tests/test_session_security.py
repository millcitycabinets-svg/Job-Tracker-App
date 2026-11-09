"""Credential changes invalidate every previously issued signed session."""
import base64
import json

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.main import app
from test_workspace import PASSWORD, ok, password_hash, workspace


def test_password_reset_revokes_all_old_sessions_and_new_login_works(workspace):
    first = workspace.client("rep_a")
    second = TestClient(app, base_url="https://testserver")
    try:
        ok(second.post("/api/login", json={"email": "rep_a@workspace.test", "password": PASSWORD}))
        ok(first.get("/api/me"))
        ok(second.get("/api/workspace/opportunities"))
        unaffected = workspace.client("rep_b")
        owner = workspace.client("admin")
        replacement = "new-test-password-only"
        ok(owner.patch(f"/api/users/{workspace.user_ids['rep_a']}", json={"password": replacement}))
        assert first.get("/api/me").status_code == 401
        assert second.get("/api/workspace/opportunities").status_code == 401
        assert first.post("/api/workspace/opportunities", json={"name": "Blocked", "customer": "Blocked"}).status_code == 401
        ok(unaffected.get("/api/me"))
        assert first.post("/api/login", json={"email": "rep_a@workspace.test", "password": PASSWORD}).status_code == 401
        ok(first.post("/api/login", json={"email": "rep_a@workspace.test", "password": replacement}))
        ok(first.get("/api/workspace/opportunities"))
    finally:
        second.close()


def test_legacy_cookie_without_credential_version_is_rejected(workspace):
    # A correctly signed old cookie must not bypass credential-version checks.
    payload = base64.b64encode(json.dumps({"user_id": workspace.user_ids["admin"]}).encode())
    old_cookie = TimestampSigner(app.state.session_secret).sign(payload).decode()
    client = TestClient(app, base_url="https://testserver")
    try:
        client.cookies.set("session", old_cookie)
        assert client.get("/api/users").status_code == 401
        assert client.get("/api/workspace/context").status_code == 401
    finally:
        client.close()


def test_stale_tab_cannot_write_or_logout_under_another_accounts_cookie(workspace):
    stale_tab = workspace.client("rep_a")
    other_tab = workspace.client("rep_b")
    # Browsers share cookies across tabs; the old tab still renders rep A's UI.
    stale_tab.cookies.update(other_tab.cookies)
    stale_headers = {"X-Workspace-User": str(workspace.user_ids["rep_a"])}
    current_headers = {"X-Workspace-User": str(workspace.user_ids["rep_b"])}
    response = stale_tab.post("/api/workspace/opportunities", headers=stale_headers,
                              json={"name": "Must never be saved as B", "customer": "Stale tab"})
    assert response.status_code == 401
    assert stale_tab.get("/api/me", headers=stale_headers).status_code == 401
    assert stale_tab.post("/api/logout", headers=stale_headers).status_code == 401
    assert ok(stale_tab.get("/api/me", headers=current_headers))["id"] == workspace.user_ids["rep_b"]
    assert ok(other_tab.get("/api/me"))["id"] == workspace.user_ids["rep_b"]
    assert ok(other_tab.get("/api/workspace/opportunities")) == []
    # An explicit current-identity action succeeds; the optional header retains
    # compatibility for clients which do not yet send it.
    ok(stale_tab.post("/api/workspace/opportunities", headers=current_headers,
                     json={"name": "B's explicit lead", "customer": "B customer"}))
    ok(stale_tab.post("/api/logout", headers=current_headers))
    assert stale_tab.get("/api/me").status_code == 401
