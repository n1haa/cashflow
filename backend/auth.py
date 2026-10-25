"""Opaque, revocable server sessions and request protection."""
import hashlib
import secrets
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from threading import Lock
from types import SimpleNamespace

from argon2 import PasswordHasher, Type
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import User, UserSession

COOKIE_NAME = "cashflow_session"
password_hasher = PasswordHasher(type=Type.ID)
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def protect_mutation(request: Request):
    """Non-simple header + no CORS prevents cross-origin form/fetch CSRF."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if (request.headers.get("x-requested-with") != "CashFlow"
            or (origin and origin != request.app.state.settings.app_origin)):
        raise HTTPException(403, "Запрос отклонён. Обновите страницу и повторите действие.")


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        return None
    session = db.get(UserSession, token_hash(token))
    if session is None or session.expires_at <= datetime.utcnow():
        return None
    return db.get(User, session.user_id)


def new_workspace():
    return SimpleNamespace(transactions=(), categorizations={}, balance_info=None,
                           planned_events=[], next_event_id=1, last_forecast=None,
                           last_gaps=None, embedding_cache={})


def require_user(request: Request, user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(401, "Войдите в аккаунт, чтобы продолжить.")
    request.state.workspace = request.app.state.workspaces.setdefault(user.id, new_workspace())
    return user


def start_session(request: Request, response: Response, db: Session, user: User):
    old_token = request.cookies.get(COOKIE_NAME)
    if old_token:
        db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(old_token)))
    db.execute(delete(UserSession).where(UserSession.expires_at <= datetime.utcnow()))
    token = secrets.token_urlsafe(32)
    max_age = request.app.state.settings.session_days * 86400
    db.add(UserSession(token_hash=token_hash(token), user_id=user.id,
                       expires_at=datetime.utcnow() + timedelta(seconds=max_age)))
    db.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=max_age, httponly=True,
                        secure=request.app.state.settings.cookie_secure, samesite="lax", path="/")


class AuthRateLimiter:
    """Bounded per-process limiter. Deploy this in-memory application with one worker."""
    def __init__(self):
        self.attempts = OrderedDict()
        self.lock = Lock()

    def check(self, request: Request):
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.attempts.pop(key, []) if now - t < 60]
            self.attempts[key] = attempts
            while len(self.attempts) > 10000:
                self.attempts.popitem(last=False)
            if len(attempts) >= 10:
                raise HTTPException(429, "Слишком много попыток. Подождите минуту.",
                                    headers={"Retry-After": "60"})
            attempts.append(now)


def limit_auth(request: Request):
    request.app.state.auth_limiter.check(request)
