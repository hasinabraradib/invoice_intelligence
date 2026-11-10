"""Database plumbing: the SQLAlchemy async engine and the declarative Base for models.

No tables yet. Future models will subclass `Base`, and Alembic reads `Base.metadata`
to autogenerate migrations.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def create_engine(database_url: str) -> AsyncEngine:
    # pool_pre_ping: test a pooled connection before using it, so a Postgres restart
    # does not hand us a dead connection. Creating the engine does NOT connect yet.
    return create_async_engine(database_url, pool_pre_ping=True)
