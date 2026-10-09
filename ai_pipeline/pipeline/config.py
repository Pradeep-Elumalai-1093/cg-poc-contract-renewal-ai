"""
Configuration, all from environment variables (set by the CloudFormation template), so the same code runs
in every account without edits. Nothing secret lives here: credentials are read from Secrets Manager.
"""
import json
import os
import re
from dataclasses import dataclass, field

# One Bedrock batch job per result table. The recommendation is followed by two passes that read the
# previous pass's output: a QA evaluation, then the outreach draft. The finished recommendation row is
# written ONCE, after the last pass (the job's database role cannot update rows, and a row is a version).
FIRST_STAGE = ("contract_summary", "web_claims_summary", "account_summary", "recommendation")
NEXT_PASS = {"recommendation": "evaluation", "evaluation": "draft"}
ALL_JOBS = FIRST_STAGE + ("evaluation", "draft")
EVAL_CRITERIA = ("groundedness", "policy_compliance", "actionability", "non_repetition", "tone", "upsell_relevance")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class Settings:
    bucket: str = ""
    input_prefix: str = "PCR/AI_Input"
    output_prefix: str = "PCR/AI_Output"
    batch_role_arn: str = ""
    pg_secret_arn: str = ""
    snowflake_secret_arn: str = ""
    db_schema: str = "ai_recommendations"
    contract_table: str = ""
    claims_table: str = ""
    model_id: str = ""
    model_overrides: dict = field(default_factory=dict)       # job type -> model id
    prompts: dict = field(default_factory=dict)               # job type -> {"id": ..., "version": ...}
    completion_function: str = ""                             # the Lambda that handles a finished batch
    language: str = "en"
    min_batch_records: int = 100                              # Bedrock batch jobs need at least this many records
    max_records_per_job: int = 50000                          # ...and accept at most this many
    sync_concurrency: int = 8
    max_claims: int = 25
    timeout_hours: int = 24
    eval_composite_pass: float = 7.0
    eval_policy_floor: float = 6.0
    contact_email_direct: str = "d2d@carrier.com"
    contact_email_dealer: str = "dealer@carrier.com"
    stale_created_hours: int = 6

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        s = cls(
            bucket=e("BUCKET", ""), input_prefix=e("INPUT_PREFIX", "PCR/AI_Input").strip("/"), output_prefix=e("OUTPUT_PREFIX", "PCR/AI_Output").strip("/"),
            batch_role_arn=e("BATCH_ROLE_ARN", ""), pg_secret_arn=e("PG_SECRET_ARN", ""), snowflake_secret_arn=e("SNOWFLAKE_SECRET_ARN", ""),
            db_schema=e("DB_SCHEMA", "ai_recommendations"), contract_table=e("SNOWFLAKE_CONTRACT_TABLE", ""), claims_table=e("SNOWFLAKE_CLAIMS_TABLE", ""),
            model_id=e("MODEL_ID", ""), model_overrides=json.loads(e("MODEL_ID_OVERRIDES", "{}")), prompts=json.loads(e("PROMPTS", "{}")),
            completion_function=e("COMPLETION_FUNCTION", ""), language=e("LANGUAGE", "en"),
            min_batch_records=_int("MIN_BATCH_RECORDS", 100), max_records_per_job=_int("MAX_RECORDS_PER_JOB", 50000),
            sync_concurrency=_int("SYNC_CONCURRENCY", 8), max_claims=_int("MAX_CLAIMS", 25), timeout_hours=_int("JOB_TIMEOUT_HOURS", 24),
            eval_composite_pass=float(e("EVAL_COMPOSITE_PASS", 7)), eval_policy_floor=float(e("EVAL_POLICY_FLOOR", 6)),
            contact_email_direct=e("CONTACT_EMAIL_DIRECT", "d2d@carrier.com"), contact_email_dealer=e("CONTACT_EMAIL_DEALER", "dealer@carrier.com"),
        )
        if not _IDENT.match(s.db_schema):
            raise ValueError(f"DB_SCHEMA={s.db_schema!r} is not a plain lower-case identifier")
        for t in (s.contract_table, s.claims_table):
            if t and not _TABLE.match(t):
                raise ValueError(f"Not a valid Snowflake table name: {t!r}")
        return s

    def model_for(self, job_type: str, prompt_model: str | None) -> str:
        """An explicit per-job override wins, then the model the prompt was authored for, then the default."""
        model = self.model_overrides.get(job_type) or prompt_model or self.model_id
        if not model:
            raise ValueError(f"No model configured for {job_type}: set MODEL_ID (or give the prompt a model)")
        return model

    # S3 layout: everything about one job lives under .../<job type>/<run id>/
    def input_key(self, job_type: str, run_id: str, name: str) -> str:
        return f"{self.input_prefix}/{job_type}/{run_id}/{name}"

    def output_prefix_for(self, job_type: str, run_id: str) -> str:
        return f"{self.output_prefix}/{job_type}/{run_id}/"
