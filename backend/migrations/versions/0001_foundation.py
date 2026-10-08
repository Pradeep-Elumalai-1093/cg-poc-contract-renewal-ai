"""foundation: schemas, UUIDv7, app configuration tables, versioned AI result tables

Revision ID: 0001
Revises:
"""
import os
import re

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _schema(var: str, default: str) -> str:
    name = os.environ.get(var, default)
    if not _IDENT.match(name):
        raise RuntimeError(f"{var}={name!r} is not a plain lower-case identifier")
    return name


def _run(sql: str) -> None:
    # Raw driver execution: the SQL below contains :=, $1 and $$ quoting that
    # SQLAlchemy's text() parsing would mangle. psycopg still scans for its own
    # %s placeholders, so the literal % in format()'s %I must be doubled.
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


# PostgreSQL 18 ships uuidv7(). On an older server this adds an equivalent
# (RFC 9562 version 7: 48-bit unix-ms timestamp, then random), and ONLY when the
# built-in is absent - so on 18+ nothing is created and the built-in is used.
UUIDV7_COMPAT = """
DO $do$
BEGIN
  IF to_regprocedure('uuidv7()') IS NULL THEN
    CREATE FUNCTION public.uuidv7() RETURNS uuid LANGUAGE sql VOLATILE AS $f$
      SELECT encode(
        set_bit(set_bit(
          overlay(uuid_send(gen_random_uuid())
                  placing substring(int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
                  FROM 1 FOR 6),
          52, 1), 53, 1),
        'hex')::uuid;
    $f$;
  END IF;
END $do$;
"""


def _app_tables(S: str) -> str:
    return f"""
CREATE TABLE "{S}".ctx (
    code varchar(3) PRIMARY KEY CHECK (code ~ '^[0-9]{{3}}$'),
    name varchar(100) NOT NULL
);

CREATE TABLE "{S}".users (
    id uuid PRIMARY KEY DEFAULT uuidv7(),
    oid varchar(200) NOT NULL UNIQUE,
    email varchar(320) NOT NULL,
    name varchar(200) NOT NULL DEFAULT '',
    role varchar(10) NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    status varchar(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);
CREATE INDEX users_email_idx ON "{S}".users (email);

CREATE TABLE "{S}".user_ctx (
    user_id uuid NOT NULL REFERENCES "{S}".users (id) ON DELETE CASCADE,
    ctx_code varchar(3) NOT NULL REFERENCES "{S}".ctx (code),
    PRIMARY KEY (user_id, ctx_code)
);

CREATE TABLE "{S}".audit_log (
    id uuid PRIMARY KEY DEFAULT uuidv7(),
    at timestamptz NOT NULL DEFAULT now(),
    actor_id uuid,
    actor_email varchar(320),
    action varchar(50) NOT NULL,
    entity varchar(50) NOT NULL,
    entity_id varchar(100),
    ctx_code varchar(3),
    detail jsonb
);
CREATE INDEX audit_log_at_idx ON "{S}".audit_log (at DESC);
"""


# Writers (the AWS job, this app) only ever INSERT a new row for a new version.
# This trigger does the bookkeeping, so it cannot be done wrong from outside:
#   - serialises writers per (table, system, language, key) for the transaction,
#   - finds the current latest row, flips its is_latest to false,
#   - links the new row to it (prev_id) and numbers it previous + 1,
#   - marks the new row as the latest.
# The key column and version column are passed per table as trigger arguments.
# SECURITY DEFINER: the flip of the previous row's is_latest runs with the table
# owner's rights, so a writer needs only INSERT (and SELECT) - it can never UPDATE
# a row itself, i.e. cannot rewrite history or hide the current version by
# clearing is_latest. search_path is pinned, as a definer function must.
VERSION_FUNCTIONS = """
CREATE FUNCTION "{S}".ai_version_before_insert() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE
  key_col text := TG_ARGV[0];
  ver_col text := TG_ARGV[1];
  key_val text := to_jsonb(NEW) ->> TG_ARGV[0];
  prev_row_id uuid;
  prev_ver integer;
BEGIN
  PERFORM pg_advisory_xact_lock(
    hashtextextended(TG_TABLE_NAME || '|' || NEW.system || '|' || NEW.language || '|' || key_val, 0));
  EXECUTE format(
    'SELECT id, %I FROM %I.%I WHERE is_latest AND system = $1 AND language = $2 AND %I = $3',
    ver_col, TG_TABLE_SCHEMA, TG_TABLE_NAME, key_col)
    INTO prev_row_id, prev_ver USING NEW.system, NEW.language, key_val;
  IF prev_row_id IS NOT NULL THEN
    EXECUTE format('UPDATE %I.%I SET is_latest = false, modified_on = now() WHERE id = $1',
                   TG_TABLE_SCHEMA, TG_TABLE_NAME) USING prev_row_id;
    NEW.prev_id := prev_row_id;
    NEW := jsonb_populate_record(NEW, jsonb_build_object(ver_col, prev_ver + 1));
  END IF;
  NEW.is_latest := true;
  RETURN NEW;
END $fn$;

CREATE FUNCTION "{S}".ai_touch_modified() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
  NEW.modified_on := now();
  RETURN NEW;
END $fn$;
"""


def _ai_table(S: str, name: str, key_col: str, ver_col: str, key_cols_sql: str, payload_sql: str, extra_sql: str = "") -> str:
    return f"""
CREATE TABLE "{S}"."{name}" (
    id uuid PRIMARY KEY DEFAULT uuidv7(),
    prev_id uuid REFERENCES "{S}"."{name}" (id),
{key_cols_sql}
    ctx varchar(3) NOT NULL CHECK (ctx ~ '^[0-9]{{3}}$'),
    system text NOT NULL DEFAULT 'ecare' CHECK (system IN ('ecare', 'salesforce')),
    language text NOT NULL DEFAULT 'en',
    ai_configuration jsonb,
    input_data jsonb,
    input_hash text,
    user_feedback jsonb,
{payload_sql}
{extra_sql}
    {ver_col} integer NOT NULL DEFAULT 1 CHECK ({ver_col} >= 1),
    is_latest boolean NOT NULL DEFAULT true,
    created_on timestamptz NOT NULL DEFAULT now(),
    modified_on timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL DEFAULT 'pipeline',
    modified_by text NOT NULL DEFAULT 'pipeline'
);
CREATE UNIQUE INDEX "{name}_latest_uq" ON "{S}"."{name}" (system, language, {key_col}) WHERE is_latest;
CREATE UNIQUE INDEX "{name}_version_uq" ON "{S}"."{name}" (system, language, {key_col}, {ver_col});
CREATE INDEX "{name}_ctx_idx" ON "{S}"."{name}" (ctx) WHERE is_latest;
CREATE TRIGGER "{name}_version" BEFORE INSERT ON "{S}"."{name}"
    FOR EACH ROW EXECUTE FUNCTION "{S}".ai_version_before_insert('{key_col}', '{ver_col}');
CREATE TRIGGER "{name}_touch" BEFORE UPDATE ON "{S}"."{name}"
    FOR EACH ROW EXECUTE FUNCTION "{S}".ai_touch_modified();
"""


CONTRACT_KEYS = "    contractid text NOT NULL,\n    accountid text,\n    jde_customer_id text,"
ACCOUNT_KEYS = "    accountid text NOT NULL,\n    jde_customer_id text,"

RECOMMENDATION_EXTRAS = """
    retention_action_id uuid,
    retention_action_name text,
    execution_owner text,
    upsell text,
    confidence numeric(4, 3) CHECK (confidence BETWEEN 0 AND 1),
    action_status text NOT NULL DEFAULT 'Action required' CHECK (action_status IN ('Action required', 'Action done')),
    outcome text CHECK (outcome IN ('Engaged', 'Declined', 'No response')),
    outcome_note text,
    evaluation jsonb,
    draft_content jsonb,"""


def upgrade() -> None:
    S = _schema("DB_SCHEMA", "ai_recommendations")
    D = _schema("DB_SCHEMA_DATA", "app_data")
    _run(f'CREATE SCHEMA IF NOT EXISTS "{S}"')
    _run(f'CREATE SCHEMA IF NOT EXISTS "{D}"')
    _run(UUIDV7_COMPAT)
    _run(_app_tables(S))
    _run(VERSION_FUNCTIONS.replace("{S}", S))
    _run(_ai_table(S, "contract_summary", "contractid", "ai_summary_version", CONTRACT_KEYS, "    ai_summary text,"))
    _run(_ai_table(S, "account_summary", "accountid", "ai_summary_version", ACCOUNT_KEYS, "    ai_summary text,"))
    _run(_ai_table(S, "web_claims_summary", "contractid", "ai_summary_version", CONTRACT_KEYS, "    ai_summary text,"))
    _run(_ai_table(S, "contract_recommendation", "contractid", "ai_recommendation_version", CONTRACT_KEYS,
                   "    ai_recommendation text,", RECOMMENDATION_EXTRAS))


def downgrade() -> None:
    S = _schema("DB_SCHEMA", "ai_recommendations")
    for t in ("contract_recommendation", "web_claims_summary", "account_summary", "contract_summary",
              "audit_log", "user_ctx", "users", "ctx"):
        _run(f'DROP TABLE IF EXISTS "{S}"."{t}" CASCADE')
    _run(f'DROP FUNCTION IF EXISTS "{S}".ai_version_before_insert()')
    _run(f'DROP FUNCTION IF EXISTS "{S}".ai_touch_modified()')
    # The schemas and the compatibility uuidv7() are left in place: they may
    # hold other objects, and it can't be known whether this migration created the function.
