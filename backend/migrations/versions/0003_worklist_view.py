"""one saved worklist view per user (which columns, which sort)

Revision ID: 0003
Revises: 0002
"""
import os
import re

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _schema() -> str:
    name = os.environ.get("DB_SCHEMA", "ai_recommendations")
    if not _IDENT.match(name):
        raise RuntimeError(f"DB_SCHEMA={name!r} is not a plain lower-case identifier")
    return name


def upgrade() -> None:
    S = _schema()
    # The primary key IS the "one view per user" rule. A column that later leaves the
    # catalog simply stops being shown - the API filters the saved list on read.
    op.get_bind().exec_driver_sql(f"""
CREATE TABLE "{S}".worklist_view (
    user_id uuid PRIMARY KEY REFERENCES "{S}".users (id) ON DELETE CASCADE,
    columns text[] NOT NULL,
    sort_key text NOT NULL,
    sort_dir text NOT NULL CHECK (sort_dir IN ('asc', 'desc')),
    updated_at timestamptz NOT NULL DEFAULT now()
)""")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(f'DROP TABLE IF EXISTS "{_schema()}".worklist_view')
