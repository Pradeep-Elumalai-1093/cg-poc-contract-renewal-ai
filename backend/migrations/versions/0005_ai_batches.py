"""the AI batch log: one row per Bedrock batch job, with an atomic claim so a duplicate
completion event can never make the results be written twice

Revision ID: 0005
Revises: 0004
"""
import os
import re

from alembic import op

revision = "0005"
down_revision = "0004"
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
-- Settings the work-queue views read. An admin changes them; the pipeline needs no deployment.
CREATE TABLE "{S}".ai_setting (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid REFERENCES "{S}".users (id)
);
-- A contract is in AI scope when its risk score reaches this, or it is within 90 days of expiry.
INSERT INTO "{S}".ai_setting (key, value) VALUES ('summary_risk_threshold', '50');

-- ---------------------------------------------------------------------------------------------
-- One row per Bedrock batch job. A daily run starts one job per job_type (the four typed result
-- tables, plus follow-on passes such as evaluation or drafting that read an earlier job's output -
-- parent_batch_id). Lifecycle:  created -> submitted -> writing -> written   (or failed).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE "{S}".ai_batch (
    id uuid PRIMARY KEY DEFAULT uuidv7(),
    run_id uuid NOT NULL,                              -- groups the jobs started by one daily trigger
    job_type text NOT NULL CHECK (job_type IN ('contract_summary', 'account_summary', 'web_claims_summary',
                                               'recommendation', 'evaluation', 'draft')),
    parent_batch_id uuid REFERENCES "{S}".ai_batch (id),
    status text NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'submitted', 'writing', 'written', 'failed')),
    bedrock_job_arn text UNIQUE,
    input_s3_uri text,
    output_s3_uri text,
    model_id text,
    prompt_versions jsonb,
    records_requested integer,
    records_returned integer,
    records_written integer,
    records_skipped integer,                           -- input_hash unchanged: nothing new to store
    records_failed integer,
    error text,
    claimed_by text,                                   -- which Lambda invocation is writing, and since when (a lease)
    claimed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    finished_at timestamptz,
    UNIQUE (run_id, job_type)                          -- a retried "start this job" finds the existing one
);
CREATE INDEX ai_batch_status_idx ON "{S}".ai_batch (status, created_at DESC);

-- The lifecycle goes through these functions (SECURITY DEFINER, pinned search_path), so the job's role
-- needs no UPDATE right on the log and cannot move a batch through the states in the wrong order.

-- Start a job. Calling it again for the same (run, job_type) returns the same batch id.
CREATE FUNCTION "{S}".ai_batch_create(p_run uuid, p_job_type text, p_parent uuid, p_model text, p_prompts jsonb,
                                      p_input_uri text, p_output_uri text, p_requested integer) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    INSERT INTO "{S}".ai_batch (run_id, job_type, parent_batch_id, model_id, prompt_versions, input_s3_uri, output_s3_uri, records_requested)
    VALUES (p_run, p_job_type, p_parent, p_model, p_prompts, p_input_uri, p_output_uri, p_requested)
    ON CONFLICT (run_id, job_type) DO UPDATE SET run_id = EXCLUDED.run_id
    RETURNING id
$fn$;

-- Bedrock accepted the job. True only the first time.
CREATE FUNCTION "{S}".ai_batch_submitted(p_id uuid, p_arn text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    WITH u AS (UPDATE "{S}".ai_batch SET status = 'submitted', bedrock_job_arn = p_arn, submitted_at = now()
               WHERE id = p_id AND status = 'created' RETURNING 1)
    SELECT EXISTS (SELECT 1 FROM u)
$fn$;

-- The completion handler's first call. Returns the batch id to the ONE invocation that wins, and NULL to
-- every other (a duplicate event, an already written batch, a failed one). A claim that has been held longer
-- than the lease belongs to a crashed invocation and can be taken over - which is safe because writing
-- skips results whose input_hash is unchanged.
CREATE FUNCTION "{S}".ai_batch_claim(p_arn text, p_worker text, p_lease interval DEFAULT interval '15 minutes') RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    UPDATE "{S}".ai_batch SET status = 'writing', claimed_by = p_worker, claimed_at = now()
    WHERE bedrock_job_arn = p_arn AND (status = 'submitted' OR (status = 'writing' AND claimed_at < now() - p_lease))
    RETURNING id
$fn$;

-- All results stored. True only for the invocation that holds the claim.
CREATE FUNCTION "{S}".ai_batch_finish(p_id uuid, p_returned integer, p_written integer, p_skipped integer, p_failed integer) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    WITH u AS (UPDATE "{S}".ai_batch SET status = 'written', finished_at = now(), records_returned = p_returned,
                      records_written = p_written, records_skipped = p_skipped, records_failed = p_failed
               WHERE id = p_id AND status = 'writing' RETURNING 1)
    SELECT EXISTS (SELECT 1 FROM u)
$fn$;

-- The job failed (by our id, or by Bedrock's job ARN from the failure event). A written batch is never un-written.
CREATE FUNCTION "{S}".ai_batch_fail(p_id uuid, p_error text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    WITH u AS (UPDATE "{S}".ai_batch SET status = 'failed', error = left(p_error, 2000), finished_at = now()
               WHERE id = p_id AND status IN ('created', 'submitted', 'writing') RETURNING 1)
    SELECT EXISTS (SELECT 1 FROM u)
$fn$;

CREATE FUNCTION "{S}".ai_batch_fail(p_arn text, p_error text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    WITH u AS (UPDATE "{S}".ai_batch SET status = 'failed', error = left(p_error, 2000), finished_at = now()
               WHERE bedrock_job_arn = p_arn AND status IN ('submitted', 'writing') RETURNING 1)
    SELECT EXISTS (SELECT 1 FROM u)
$fn$;

REVOKE ALL ON FUNCTION "{S}".ai_batch_create(uuid, text, uuid, text, jsonb, text, text, integer), "{S}".ai_batch_submitted(uuid, text),
    "{S}".ai_batch_claim(text, text, interval), "{S}".ai_batch_finish(uuid, integer, integer, integer, integer),
    "{S}".ai_batch_fail(uuid, text), "{S}".ai_batch_fail(text, text) FROM PUBLIC;
""".replace("%", "%%"))


def downgrade() -> None:
    S = _schema()
    b = op.get_bind()
    for fn in ("ai_batch_create(uuid, text, uuid, text, jsonb, text, text, integer)", "ai_batch_submitted(uuid, text)",
               "ai_batch_claim(text, text, interval)", "ai_batch_finish(uuid, integer, integer, integer, integer)",
               "ai_batch_fail(uuid, text)", "ai_batch_fail(text, text)"):
        b.exec_driver_sql(f'DROP FUNCTION IF EXISTS "{S}".{fn}')
    b.exec_driver_sql(f'DROP TABLE IF EXISTS "{S}".ai_batch')
    b.exec_driver_sql(f'DROP TABLE IF EXISTS "{S}".ai_setting')
