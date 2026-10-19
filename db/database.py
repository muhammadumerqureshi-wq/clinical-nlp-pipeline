"""
Database engine/session setup.

Uses SQLite by default (zero-setup, file-based) for this MVP -- swap to
Postgres in production by setting the DATABASE_URL environment variable,
e.g. postgresql://user:pass@host/dbname. No code changes needed elsewhere
since all access goes through SQLAlchemy's ORM, not raw SQL.
"""

import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clinical.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

if DATABASE_URL.startswith("sqlite"):
    # SQLite does not enforce foreign keys unless asked to, per connection.
    # Turning it on makes referential integrity behave like Postgres/MySQL.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency -- yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
