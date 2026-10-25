from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import (COOKIE_NAME, DUMMY_HASH, limit_auth, password_hasher,
                          protect_mutation, require_user, start_session, token_hash)
from backend.database import get_db
from backend.models import User, UserSession

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(protect_mutation)])


class LoginInput(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise ValueError("Введите корректный email.") from None

    @field_validator("password")
    @classmethod
    def valid_password(cls, value):
        if not 1 <= len(value) <= 128:
            raise ValueError("Пароль должен содержать от 1 до 128 символов.")
        return value


class RegisterInput(LoginInput):
    name: str
    password_confirmation: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not 2 <= len(value) <= 100:
            raise ValueError("Имя должно содержать от 2 до 100 символов.")
        return value

    @model_validator(mode="after")
    def matching_passwords(self):
        if len(self.password) < 12:
            raise ValueError("Используйте пароль длиной от 12 до 128 символов.")
        if self.password != self.password_confirmation:
            raise ValueError("Пароли не совпадают.")
        return self


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str


@router.post("/register", response_model=UserOutput, status_code=201, dependencies=[Depends(limit_auth)])
def register(payload: RegisterInput, request: Request, response: Response, db: Session = Depends(get_db)):
    user = User(name=payload.name, email=payload.email, password_hash=password_hasher.hash(payload.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Аккаунт с таким email уже существует. Войдите в него.") from None
    start_session(request, response, db, user)
    return user


@router.post("/login", response_model=UserOutput, dependencies=[Depends(limit_auth)])
def login(payload: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email))
    try:
        password_hasher.verify(user.password_hash if user else DUMMY_HASH, payload.password)
    except (VerificationError, InvalidHashError):
        raise HTTPException(401, "Неверный email или пароль.") from None
    if user is None:
        raise HTTPException(401, "Неверный email или пароль.")
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(payload.password)
    start_session(request, response, db, user)
    return user


@router.get("/me", response_model=UserOutput)
def me(user: User = Depends(require_user)):
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                           secure=request.app.state.settings.cookie_secure, samesite="lax")
