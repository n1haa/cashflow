from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.auth import COOKIE_NAME, password_hasher, token_hash
from backend.models import User, UserSession

PASSWORD = "a long test password 123"


def register(client, email="alice@example.com", **changes):
    payload = dict(name="Алиса", email=email, password=PASSWORD, password_confirmation=PASSWORD)
    payload.update(changes)
    return client.post("/api/auth/register", json=payload)


def test_registration_hash_session_and_dashboard(anonymous_client):
    client = anonymous_client
    response = register(client, email="  Alice@EXAMPLE.COM  ")
    assert response.status_code == 201
    assert response.json() == {"id": 1, "name": "Алиса", "email": "alice@example.com"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=" in cookie
    assert response.headers["cache-control"] == "no-store"
    with client.app.state.session_factory() as db:
        user = db.get(User, 1)
        assert user.password_hash.startswith("$argon2id$")
        assert password_hasher.verify(user.password_hash, PASSWORD)
        session = db.scalar(select(UserSession))
        assert session.token_hash == token_hash(client.cookies[COOKIE_NAME])
        assert session.token_hash != client.cookies[COOKIE_NAME]
    assert client.get("/api/auth/me").json()["name"] == "Алиса"
    assert client.get("/dashboard").status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/dashboard"


def test_duplicate_email_is_case_insensitive(anonymous_client):
    assert register(anonymous_client).status_code == 201
    response = register(anonymous_client, email="ALICE@example.com")
    assert response.status_code == 409
    with anonymous_client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("changes", [
    {"email": "invalid"}, {"name": " "}, {"name": "a" * 101},
    {"password": "short", "password_confirmation": "short"},
    {"password": "p" * 129}, {"password_confirmation": "different"}, {"password": None},
])
def test_validation_does_not_echo_passwords(anonymous_client, changes):
    response = register(anonymous_client, **changes)
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert "input" not in response.text
    with anonymous_client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_login_logout_and_session_revocation(anonymous_client):
    client = anonymous_client
    register(client)
    registration_token = client.cookies[COOKIE_NAME]
    response = client.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert response.status_code == 200
    token = client.cookies[COOKIE_NAME]
    assert token != registration_token
    with TestClient(client.app) as attacker:
        attacker.cookies.set(COOKIE_NAME, registration_token)
        assert attacker.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 204
    assert COOKIE_NAME not in client.cookies
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD}).status_code == 200
    assert client.get("/dashboard").status_code == 200


def test_wrong_password_and_unknown_user_have_same_error(anonymous_client):
    register(anonymous_client)
    anonymous_client.post("/api/auth/logout")
    results = [anonymous_client.post("/api/auth/login", json={"email": email, "password": "wrong"})
               for email in ("alice@example.com", "missing@example.com")]
    assert all(r.status_code == 401 for r in results)
    assert results[0].json() == results[1].json()
    assert COOKIE_NAME not in anonymous_client.cookies


@pytest.mark.parametrize("path", ["/", "/dashboard", "/assets/index.html"])
def test_cabinet_requires_login(anonymous_client, path):
    response = anonymous_client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/auth/me"), ("GET", "/api/transactions"),
    ("POST", "/api/transactions/upload"), ("POST", "/api/transactions/categorize"),
    ("GET", "/api/transactions/recurring"), ("GET", "/api/forecast"),
    ("GET", "/api/forecast/cash-gaps"), ("GET", "/api/events"),
    ("POST", "/api/events"), ("DELETE", "/api/events/1"), ("POST", "/api/chat"),
])
def test_api_requires_login(anonymous_client, method, path):
    assert anonymous_client.request(method, path).status_code == 401


def test_expired_and_forged_sessions(anonymous_client):
    client = anonymous_client
    register(client)
    with client.app.state.session_factory() as db:
        session = db.scalar(select(UserSession))
        session.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, "forged")
    assert client.get("/api/transactions").status_code == 401


def test_csrf_protection(anonymous_client):
    client = anonymous_client
    assert register(client).status_code == 201
    assert client.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    client.headers.pop("X-Requested-With")
    assert client.post("/api/auth/logout").status_code == 403
    assert register(client, email="other@example.com").status_code == 403
    assert client.post("/api/events", json={}).status_code == 403


def test_users_have_separate_workspaces(anonymous_client, csv_content):
    alice = anonymous_client
    register(alice)
    assert alice.post("/api/transactions/upload", files={"file": ("demo.csv", csv_content)}).status_code == 200
    assert alice.post("/api/transactions/categorize").status_code == 200
    event = {"date": "2026-10-28", "direction": "income", "amount": "100", "category": "other", "description": "Личная оплата"}
    assert alice.post("/api/events", json=event).status_code == 201
    with TestClient(alice.app, headers={"X-Requested-With": "CashFlow"}) as bob:
        assert register(bob, email="bob@example.com").status_code == 201
        assert bob.get("/api/transactions").json() == []
        assert bob.get("/api/events").json() == []
        assert bob.delete("/api/events/1").status_code == 404
        assert bob.get("/api/transactions/recurring").json()["total_groups"] == 0
    assert len(alice.get("/api/transactions").json()) == 1
    assert len(alice.get("/api/events").json()) == 1
    alice.post("/api/auth/logout")
    alice.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert len(alice.get("/api/transactions").json()) == 1


def test_rate_limit(anonymous_client):
    for _ in range(10):
        assert anonymous_client.post("/api/auth/login", json={"email": "missing@example.com", "password": "wrong"}).status_code == 401
    response = anonymous_client.post("/api/auth/login", json={"email": "missing@example.com", "password": "wrong"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_database_failure_is_sanitized(anonymous_client, monkeypatch):
    from sqlalchemy.exc import OperationalError
    def unavailable(*args, **kwargs):
        raise OperationalError("sensitive SQL", {}, Exception("database password"))
    monkeypatch.setattr(anonymous_client.app.state, "session_factory", unavailable)
    response = register(anonymous_client)
    assert response.status_code == 503
    assert "database password" not in response.text


def test_secure_cookie_option(auth_app):
    from dataclasses import replace
    auth_app.state.settings = replace(auth_app.state.settings, cookie_secure=True)
    with TestClient(auth_app, base_url="https://testserver", headers={"X-Requested-With": "CashFlow"}) as client:
        response = register(client)
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/auth/me").status_code == 200


def test_session_survives_application_restart(anonymous_client):
    from backend.main import create_app
    register(anonymous_client)
    token = anonymous_client.cookies[COOKIE_NAME]
    restarted = create_app(anonymous_client.app.state.settings)
    with TestClient(restarted) as client:
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/api/auth/me").json()["email"] == "alice@example.com"
        assert client.get("/api/transactions").json() == []
