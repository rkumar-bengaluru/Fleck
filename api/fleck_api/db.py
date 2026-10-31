from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fleck_api.config import get_settings


def sa_url(url: str) -> str:
    """Plain postgresql:// URLs (used by psycopg directly) -> SQLAlchemy's psycopg3 driver."""
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def make_engine(url: str | None = None):
    return create_engine(sa_url(url or get_settings().database_url), pool_pre_ping=True)


_sessionmaker: sessionmaker[Session] | None = None


def session_factory() -> sessionmaker[Session]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(make_engine(), expire_on_commit=False)
    return _sessionmaker


def get_session():
    """FastAPI dependency: one session per request."""
    with session_factory()() as s:
        yield s
