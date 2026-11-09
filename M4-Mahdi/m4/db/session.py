"""Database engine/session management and initialization."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..config import get_settings
from .models import Base, Scenario

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().resolved_database_url
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def reset_engine() -> None:
    """Used by tests to point the application at a fresh database."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine, _SessionLocal = None, None


def SessionLocal() -> Session:
    get_engine()
    return _SessionLocal()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(seed_scenarios: bool = True) -> None:
    """Create tables (idempotent) and upsert the predefined scenarios."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    if seed_scenarios:
        from ..scenarios.predefined import predefined_scenarios

        with SessionLocal() as db:
            for key, cfg in predefined_scenarios().items():
                row = db.get(Scenario, key)
                data = cfg.model_dump(mode="json")
                if row is None:
                    db.add(Scenario(id=key, name=cfg.name, description=cfg.description, is_predefined=True,
                                    owner_id=None, config=data))
                else:
                    row.name, row.description, row.config, row.is_predefined = cfg.name, cfg.description, data, True
            db.commit()


def predefined_ids(db: Session) -> list[str]:
    return list(db.scalars(select(Scenario.id).where(Scenario.is_predefined.is_(True))))
