"""ingest support: trigram search, the ingest run log, per-CTX statistics

The contract and claim tables themselves are NOT created here. They are a rebuildable
copy of the Snowflake data product whose columns follow the reviewed column catalog,
so the ingest command creates and maintains them (idempotently, from the catalog).
Only the stable pieces live in Alembic.

Revision ID: 0002
Revises: 0001
"""
import os
import re

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _data_schema() -> str:
    name = os.environ.get("DB_SCHEMA_DATA", "app_data")
    if not _IDENT.match(name):
        raise RuntimeError(f"DB_SCHEMA_DATA={name!r} is not a plain lower-case identifier")
    return name


def upgrade() -> None:
    D = _data_schema()
    bind = op.get_bind()
    # Trigram indexes make "contains" search on customer/account names fast. pg_trgm is a
    # trusted extension (PostgreSQL 13+), so the database owner may create it.
    bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    bind.exec_driver_sql(f"""
CREATE TABLE "{D}".ingest_run (
    id uuid PRIMARY KEY DEFAULT uuidv7(),
    data_version integer GENERATED ALWAYS AS IDENTITY UNIQUE,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'aborted')),
    source text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    contracts_loaded integer,
    in_scope integer,
    claims_loaded integer,
    report jsonb,
    error text
);
CREATE INDEX ingest_run_status_idx ON "{D}".ingest_run (status, data_version DESC);

-- Each CTX's own median contract value (the basis of segmentation) and how it was reached.
CREATE TABLE "{D}".ctx_stats (
    ctx varchar(3) PRIMARY KEY,
    median_value double precision,
    valued_contracts integer NOT NULL,
    contracts integer NOT NULL,
    used_global_median boolean NOT NULL,
    data_version integer NOT NULL
);
""".replace("%", "%%"))


def downgrade() -> None:
    D = _data_schema()
    bind = op.get_bind()
    bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{D}".ctx_stats')
    bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{D}".ingest_run')
    # pg_trgm is left installed: other objects may use it.
