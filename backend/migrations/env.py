"""
Alembic environment. The migrations are hand-written SQL (partial unique
indexes, triggers and PostgreSQL functions are exactly what autogenerate can't
express), so there is no target_metadata - the ORM models in db.py only
describe the tables for querying.
"""
import os
import re

from alembic import context
from sqlalchemy import create_engine, pool, text

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def schema_name(var: str, default: str) -> str:
    name = os.environ.get(var, default)
    if not _IDENT.match(name):
        raise RuntimeError(f"{var}={name!r} is not a plain lower-case identifier")
    return name


SCHEMA = schema_name("DB_SCHEMA", "ai_recommendations")
URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/uda_1325")
if not URL:
    raise RuntimeError("DATABASE_URL is not set")


def run_migrations_online() -> None:
    engine = create_engine(URL, poolclass=pool.NullPool, connect_args={"options": "-c timezone=UTC"})
    with engine.connect() as conn:
        # Alembic keeps its bookkeeping table in the app schema, which has to exist first.
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        conn.commit()
        context.configure(connection=conn, target_metadata=None, version_table_schema=SCHEMA)
        with context.begin_transaction():
            context.run_migrations()
        conn.commit()


run_migrations_online()
