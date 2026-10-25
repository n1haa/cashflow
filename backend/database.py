from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(url):
    if str(url).startswith("sqlite"):
        # SQLite is only used by isolated tests, never as a production fallback.
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800,
                         connect_args={"connect_timeout": 5}, hide_parameters=True)


def get_db(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
