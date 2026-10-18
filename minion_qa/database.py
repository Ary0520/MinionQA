"""
MinionQA Database Module.

Configures SQLAlchemy engine, session factory, declarative base,
and database initialization helpers. Supports SQLite by default for zero-config
local development and PostgreSQL for production deployments.
"""

from __future__ import annotations

import logging
import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from minion_qa.config import default_config

logger = logging.getLogger("minion_qa.database")

DATABASE_URL = os.getenv("DATABASE_URL", getattr(default_config, "database_url", "sqlite:///./minionqa.db"))

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Dependency generator yielding an active database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize all registered database tables."""
    try:
        from minion_qa import models  # noqa: F401
    except ImportError:
        pass
    Base.metadata.create_all(bind=engine)
    logger.info("MinionQA database initialized successfully (%s)", DATABASE_URL)
