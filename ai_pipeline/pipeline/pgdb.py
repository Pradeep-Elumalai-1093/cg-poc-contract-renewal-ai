"""
PostgreSQL (Aurora): the work lists to read, the results to write, and the batch log.

The job connects as the `ai_writer` role: it can SELECT the views and result tables, INSERT new versions, and call the
ai_batch_* functions - it cannot UPDATE or DELETE anything (see backend/docs/db_roles.sql). Versioning of results is done
by a trigger, so every write here is a plain INSERT.
"""
import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .util import jsonable

SUMMARY_TABLES = {"contract_summary": "contractid", "web_claims_summary": "contractid", "account_summary": "accountid"}
_WANT = {"contract_summary": "want_contract_summary", "web_claims_summary": "want_web_claims_summary", "recommendation": "want_recommendation"}
_ORDER = {
    # never-summarised first, so a backlog drains and a changed-input check can't starve the rest
    "contract_summary": "contract_summary_hash IS NOT NULL, risk_score DESC NULLS LAST, contractid",
    "web_claims_summary": "web_claims_summary_hash IS NOT NULL, risk_score DESC NULLS LAST, contractid",
    # most urgent bucket first
    "recommendation": "CASE expiry_bucket WHEN '10' THEN 1 WHEN '30' THEN 2 WHEN '45' THEN 3 WHEN '60' THEN 4 ELSE 5 END, risk_score DESC NULLS LAST, contractid",
}


class Db:
    def __init__(self, conn, schema: str, language: str = "en"):
        self.conn, self.s, self.lang = conn, schema, language

    @classmethod
    def connect(cls, secrets_client, settings) -> "Db":
        secret = json.loads(secrets_client.get_secret_value(SecretId=settings.pg_secret_arn)["SecretString"])
        conn = psycopg.connect(
            host=secret["host"], port=int(secret.get("port", 5432)), dbname=secret.get("dbname", "postgres"),
            user=secret["username"], password=secret["password"], connect_timeout=10, autocommit=True, row_factory=dict_row,
            options="-c timezone=UTC", sslmode=secret.get("sslmode", "require"))
        return cls(conn, settings.db_schema, settings.language)

    def rows(self, sql: str, params=()) -> list[dict]:
        return self.conn.execute(sql, params).fetchall()

    def value(self, sql: str, params=()):
        r = self.conn.execute(sql, params).fetchone()
        return None if r is None else next(iter(r.values()))

    # ---- what to process -----------------------------------------------------------------------------------
    def candidates(self, job_type: str) -> list[dict]:
        return self.rows(f"""SELECT contractid, accountid, ctx, expiry_bucket, risk_score, last_milestone, contract_summary_hash, web_claims_summary_hash
                             FROM {self.s}.v_ai_work WHERE {_WANT[job_type]} ORDER BY {_ORDER[job_type]}""")

    def account_candidates(self) -> list[dict]:
        return self.rows(f"""SELECT accountid, ctx, contracts_in_scope, account_summary_hash FROM {self.s}.v_ai_work_accounts
                             ORDER BY account_summary_hash IS NOT NULL, contracts_in_scope DESC, accountid""")

    def account_contracts(self, account_ids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.rows(f"SELECT accountid, contractid FROM {self.s}.v_ai_work WHERE accountid = ANY(%s) ORDER BY contractid", (account_ids,)):
            out.setdefault(r["accountid"], []).append(r["contractid"])
        return out

    def options(self, contract_ids: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for r in self.rows(f"""SELECT contractid, action_id, category, sub_category, name, description FROM {self.s}.v_retention_options
                               WHERE contractid = ANY(%s) ORDER BY contractid, category, sub_category, name""", (contract_ids,)):
            out.setdefault(r["contractid"], []).append({**r, "action_id": str(r["action_id"])})
        return out

    def latest_texts(self, contract_ids: list[str], account_ids: list[str]) -> dict:
        """The most recent stored summaries - the recommendation prompt uses what already exists (a summary created in
        this same daily run is not yet available to it)."""
        def fetch(table, key, ids):
            return {r[key]: r["ai_summary"] for r in self.rows(
                f"SELECT {key}, ai_summary FROM {self.s}.{table} WHERE is_latest AND system = 'ecare' AND language = %s AND {key} = ANY(%s)",
                (self.lang, ids))}
        return {"contract": fetch("contract_summary", "contractid", contract_ids), "claims": fetch("web_claims_summary", "contractid", contract_ids),
                "account": fetch("account_summary", "accountid", account_ids)}

    def prior_recommendations(self, contract_ids: list[str]) -> dict[str, dict]:
        return {r["contractid"]: {"action": r["retention_action_name"], "outcome": r["outcome"], "note": r["outcome_note"], "milestone": r["milestone"]}
                for r in self.rows(f"""SELECT contractid, retention_action_name, outcome, outcome_note, milestone FROM {self.s}.contract_recommendation
                                       WHERE is_latest AND system = 'ecare' AND language = %s AND contractid = ANY(%s)""", (self.lang, contract_ids))}

    # ---- storing results (plain INSERTs; the trigger numbers the versions) -----------------------------------------
    def _latest(self, table: str, key_col: str, key: str):
        return self.conn.execute(f"SELECT input_hash, {'milestone' if table == 'contract_recommendation' else 'NULL AS milestone'} FROM {self.s}.{table} "
                                 f"WHERE is_latest AND system = 'ecare' AND language = %s AND {key_col} = %s", (self.lang, key)).fetchone()

    def write_summary(self, table: str, meta: dict, text: str, config: dict) -> bool:
        key_col = SUMMARY_TABLES[table]
        cur = self._latest(table, key_col, meta["key"])
        if cur and cur["input_hash"] == meta["input_hash"]:
            return False                                   # same inputs as the stored version: nothing new to say
        cols = [key_col, "ctx", "language", "ai_summary", "ai_configuration", "input_data", "input_hash"]
        vals = [meta["key"], meta["ctx"], self.lang, text, Jsonb(jsonable(config)), Jsonb(jsonable(meta["input_data"])), meta["input_hash"]]
        if table != "account_summary":
            cols.append("accountid"); vals.append(meta.get("accountid"))
        self.conn.execute(f"INSERT INTO {self.s}.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals)
        return True

    def write_recommendation(self, meta: dict, row: dict) -> bool:
        cur = self._latest("contract_recommendation", "contractid", meta["key"])
        if cur and cur["input_hash"] == meta["input_hash"] and cur["milestone"] == meta["milestone"]:
            return False
        self.conn.execute(f"""INSERT INTO {self.s}.contract_recommendation
            (contractid, accountid, ctx, language, ai_configuration, input_data, input_hash, ai_recommendation, retention_action_id,
             retention_action_name, execution_owner, upsell, confidence, evaluation, draft_content, milestone)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS uuid), %s, %s, %s, %s, %s, %s, %s)""",
            (meta["key"], meta.get("accountid"), meta["ctx"], self.lang, Jsonb(jsonable(row["config"])), Jsonb(jsonable(meta["input_data"])),
             meta["input_hash"], row["rationale"], row["action_id"], row["action_name"], row["execution_owner"], row["upsell"], row["confidence"],
             Jsonb(jsonable(row["evaluation"])), Jsonb(jsonable(row["draft"])) if row["draft"] is not None else None, meta["milestone"]))
        return True

    # ---- the batch log ----------------------------------------------------------------------------------------------
    def batch_create(self, run_id, job_type, parent_id, model_id, prompts, in_uri, out_uri, requested):
        return self.value(f"SELECT {self.s}.ai_batch_create(CAST(%s AS uuid), %s, CAST(%s AS uuid), %s, CAST(%s AS jsonb), %s, %s, %s)",
                          (str(run_id), job_type, str(parent_id) if parent_id else None, model_id, json.dumps(prompts), in_uri, out_uri, requested))

    def batch_submitted(self, batch_id, arn) -> bool:
        return self.value(f"SELECT {self.s}.ai_batch_submitted(CAST(%s AS uuid), %s)", (str(batch_id), arn))

    def batch_claim(self, arn, worker):
        return self.value(f"SELECT {self.s}.ai_batch_claim(%s, %s)", (arn, worker))

    def batch_finish(self, batch_id, returned, written, skipped, failed) -> bool:
        return self.value(f"SELECT {self.s}.ai_batch_finish(CAST(%s AS uuid), %s, %s, %s, %s)", (str(batch_id), returned, written, skipped, failed))

    def batch_fail_id(self, batch_id, error) -> bool:
        return self.value(f"SELECT {self.s}.ai_batch_fail(CAST(%s AS uuid), %s)", (str(batch_id), error))

    def batch_fail_arn(self, arn, error) -> bool:
        return self.value(f"SELECT {self.s}.ai_batch_fail(CAST(%s AS text), %s)", (arn, error))

    def batch(self, batch_id) -> dict | None:
        r = self.rows(f"SELECT * FROM {self.s}.ai_batch WHERE id = CAST(%s AS uuid)", (str(batch_id),))
        return r[0] if r else None

    def open_batches(self) -> list[dict]:
        return self.rows(f"""SELECT id, run_id, job_type, status, bedrock_job_arn, created_at, claimed_at,
                                    extract(epoch FROM (now() - created_at)) AS age_s
                             FROM {self.s}.ai_batch WHERE status IN ('created', 'submitted', 'writing') ORDER BY created_at""")

    def recent_batches(self, n: int = 20) -> list[dict]:
        return self.rows(f"""SELECT id, run_id, job_type, status, records_requested, records_returned, records_written, records_skipped,
                                    records_failed, error, created_at, finished_at FROM {self.s}.ai_batch ORDER BY created_at DESC, id DESC LIMIT %s""", (n,))
