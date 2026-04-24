"""SQLAlchemy engine/session setup."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _default_url() -> str:
    db_path = os.getenv("FACELESS_DB",
                        str(Path.cwd() / "data" / "faceless.db"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


class Base(DeclarativeBase):
    pass


engine = create_engine(_default_url(), future=True, echo=False,
                       connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables. Alembic migrations are optional — for a solo user
    SQLite app, auto-create is fine."""
    from . import models  # noqa: F401 — ensure models are imported
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    return SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
