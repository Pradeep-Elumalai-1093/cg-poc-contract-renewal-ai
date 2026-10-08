"""exclusion sets and retention actions, which users configure and the AI pipeline reads

Revision ID: 0004
Revises: 0003
"""
import os
import re

from alembic import op

revision = "0004"
down_revision = "0003"
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
    op.get_bind().exec_driver_sql(f"""
-- ---------------------------------------------------------------------------------------------
-- Exclusion sets. A set belongs to ONE area (CTX), is visible to everyone with that area, and
-- while ACTIVE keeps the contracts its criteria match in exclusion_hit. A contract matching ANY
-- active set in its area gets no LLM recommendation (it still gets its summaries). Each user
-- chooses which sets also hide contracts from their own screens (user_exclusion_pref).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE "{S}".exclusion_set (
    id uuid PRIMARY KEY DEFAULT uuidv7(),
    ctx varchar(3) NOT NULL CHECK (ctx ~ '^[0-9]{{3}}$'),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    description text NOT NULL DEFAULT '',
    criteria jsonb NOT NULL CHECK (criteria <> '{{}}'::jsonb),   -- an empty set would exclude every contract
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active')),
    version integer NOT NULL DEFAULT 1,                           -- optimistic concurrency: a stale edit gets a 409
    match_count integer,
    matched_at timestamptz,
    created_by uuid REFERENCES "{S}".users (id),
    updated_by uuid REFERENCES "{S}".users (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);
CREATE UNIQUE INDEX exclusion_set_name_uq ON "{S}".exclusion_set (ctx, lower(btrim(name))) WHERE deleted_at IS NULL;

-- Keyed by contract id, not a foreign key: the contract table is replaced by every daily load,
-- and the matches are recomputed right after it (python -m ingest does that).
CREATE TABLE "{S}".exclusion_hit (
    set_id uuid NOT NULL REFERENCES "{S}".exclusion_set (id) ON DELETE CASCADE,
    contractid text NOT NULL,
    PRIMARY KEY (set_id, contractid)
);
CREATE INDEX exclusion_hit_contract_idx ON "{S}".exclusion_hit (contractid);

CREATE TABLE "{S}".user_exclusion_pref (
    user_id uuid PRIMARY KEY REFERENCES "{S}".users (id) ON DELETE CASCADE,
    mode text NOT NULL DEFAULT 'all' CHECK (mode IN ('all', 'custom', 'none')),   -- all active sets / only set_ids / none
    set_ids uuid[] NOT NULL DEFAULT '{{}}'
);

-- Which contracts are excluded, and by what - the pipeline reads this to skip recommendations.
CREATE VIEW "{S}".v_excluded_contracts AS
SELECT h.contractid, s.ctx, array_agg(s.id ORDER BY s.name) AS set_ids, array_agg(s.name ORDER BY s.name) AS set_names
FROM "{S}".exclusion_hit h
JOIN "{S}".exclusion_set s ON s.id = h.set_id AND s.status = 'active' AND s.deleted_at IS NULL
GROUP BY h.contractid, s.ctx;

-- ---------------------------------------------------------------------------------------------
-- Retention actions: what the LLM may recommend. GLOBAL ones (ctx NULL) are managed by admins and
-- apply everywhere; LOCAL ones belong to an area. A local action overrides a global one only when
-- category, sub-category and name all match (case-insensitively). Empty criteria = applies to every
-- contract; otherwise the matching contracts are kept in retention_action_match.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE "{S}".retention_action (
    id uuid PRIMARY KEY DEFAULT uuidv7(),
    scope text NOT NULL CHECK (scope IN ('global', 'local')),
    ctx varchar(3) CHECK (ctx ~ '^[0-9]{{3}}$'),
    category text NOT NULL CHECK (length(btrim(category)) BETWEEN 1 AND 100),
    sub_category text NOT NULL DEFAULT '' CHECK (length(sub_category) <= 100),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    description text NOT NULL DEFAULT '',
    criteria jsonb NOT NULL DEFAULT '{{}}',
    version integer NOT NULL DEFAULT 1,
    match_count integer,
    matched_at timestamptz,
    created_by uuid REFERENCES "{S}".users (id),
    updated_by uuid REFERENCES "{S}".users (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CHECK ((scope = 'global') = (ctx IS NULL))
);
CREATE UNIQUE INDEX retention_action_key_uq ON "{S}".retention_action
    (coalesce(ctx, ''), lower(btrim(category)), lower(btrim(sub_category)), lower(btrim(name))) WHERE deleted_at IS NULL;

CREATE TABLE "{S}".retention_action_match (
    action_id uuid NOT NULL REFERENCES "{S}".retention_action (id) ON DELETE CASCADE,
    contractid text NOT NULL,
    PRIMARY KEY (action_id, contractid)
);
CREATE INDEX retention_action_match_contract_idx ON "{S}".retention_action_match (contractid);

-- The pipeline needs to know which milestone (expiry bucket) a recommendation was made for, so it
-- can recommend again only when a contract moves to the next one (e.g. 90 -> 60).
ALTER TABLE "{S}".contract_recommendation
    ADD COLUMN milestone text CHECK (milestone IN ('>90', '90', '60', '45', '30', '10', 'Lost'));
""".replace("%", "%%"))


def downgrade() -> None:
    S = _schema()
    b = op.get_bind()
    b.exec_driver_sql(f'ALTER TABLE "{S}".contract_recommendation DROP COLUMN IF EXISTS milestone')
    b.exec_driver_sql(f'DROP VIEW IF EXISTS "{S}".v_excluded_contracts')
    for t in ("retention_action_match", "retention_action", "user_exclusion_pref", "exclusion_hit", "exclusion_set"):
        b.exec_driver_sql(f'DROP TABLE IF EXISTS "{S}".{t} CASCADE')
