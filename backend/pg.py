"""
Throwaway PostgreSQL databases for the checks: each run creates its own empty
database, migrates it with the real Alembic migration, and drops it on exit.

Needs a PostgreSQL server you can create databases on (for local work:
`docker compose up -d db` from the repo root). Point TEST_PG_ADMIN_DSN at it if
yours isn't the default below.
"""
import atexit
import os
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parent.parent
ADMIN_DSN = os.environ.get("TEST_PG_ADMIN_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")


def _drop(name: str) -> None:
    try:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    except Exception:
        pass


def fresh_database() -> str:
    """Creates an empty database and returns its SQLAlchemy URL."""
    name = "uda_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    atexit.register(_drop, name)
    url = make_url(ADMIN_DSN).set(drivername="postgresql+psycopg", database=name)
    return url.render_as_string(hide_password=False)


def migrate(url: str) -> None:
    """Runs `alembic upgrade head` against url, in-process."""
    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def new_database() -> str:
    url = fresh_database()
    migrate(url)
    return url
