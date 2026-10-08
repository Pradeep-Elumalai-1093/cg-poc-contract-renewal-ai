-- Least-privilege database roles. Run once per environment by someone allowed to
-- CREATE ROLE; take the passwords from your secret store (not from this file).
-- Not run by Alembic: roles belong to the server, not to a database's migrations.
-- If DB_SCHEMA / DB_SCHEMA_DATA differ from the defaults, change the schema names below.

-- ---------------------------------------------------------------------------
-- ai_writer: the AWS Lambda job that stores summaries and recommendations.
-- It can add new versions and read what it needs. It cannot UPDATE or DELETE
-- anything (the version trigger runs as the table owner and does the bookkeeping),
-- so it cannot rewrite history or hide the current version, and it cannot see
-- users, access assignments or the audit log.
-- ---------------------------------------------------------------------------
CREATE ROLE ai_writer LOGIN PASSWORD 'CHANGE-ME';
GRANT USAGE ON SCHEMA ai_recommendations TO ai_writer;
GRANT SELECT, INSERT ON
    ai_recommendations.contract_summary,
    ai_recommendations.account_summary,
    ai_recommendations.web_claims_summary,
    ai_recommendations.contract_recommendation
TO ai_writer;
-- What the job reads to decide what to process (views run with their owner's rights, so no access to app_data is needed):
GRANT SELECT ON ai_recommendations.v_excluded_contracts TO ai_writer;
-- v_retention_options joins the contract table, so it exists only after the FIRST `python -m ingest`. Then run:
--   GRANT SELECT ON ai_recommendations.v_retention_options TO ai_writer;

-- ---------------------------------------------------------------------------
-- app_api: the FastAPI service.
-- ---------------------------------------------------------------------------
CREATE ROLE app_api LOGIN PASSWORD 'CHANGE-ME';
GRANT USAGE ON SCHEMA ai_recommendations, app_data TO app_api;
GRANT SELECT, INSERT, UPDATE ON ai_recommendations.users, ai_recommendations.ctx TO app_api;
GRANT SELECT, INSERT, DELETE ON ai_recommendations.user_ctx TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_recommendations.worklist_view TO app_api;   -- one saved view per user
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_recommendations.user_exclusion_pref TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_recommendations.exclusion_set, ai_recommendations.exclusion_hit,
    ai_recommendations.retention_action, ai_recommendations.retention_action_match TO app_api;   -- users configure these; the matches are rebuilt by the app and the load
GRANT SELECT, INSERT ON ai_recommendations.audit_log TO app_api;            -- append-only
GRANT SELECT, INSERT, UPDATE ON
    ai_recommendations.contract_summary,
    ai_recommendations.account_summary,
    ai_recommendations.web_claims_summary,
    ai_recommendations.contract_recommendation
TO app_api;                                                                  -- user edits, outcomes
GRANT SELECT ON ALL TABLES IN SCHEMA app_data TO app_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA app_data GRANT SELECT ON TABLES TO app_api;
