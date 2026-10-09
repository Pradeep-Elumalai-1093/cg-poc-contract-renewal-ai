"""
End-to-end check of the AI pipeline. Plain asserts, no framework:

    cd aws && python tests/check_pipeline.py

Real: the pipeline code, PostgreSQL with the real schema / ingest / work-list views, and the restricted `ai_writer` database role.
Faked: S3, Bedrock (batch + direct), Prompt Management, Snowflake and Lambda - so the AWS API shapes themselves are NOT proven
here (the deployment notebook has smoke tests for that). The fake model reads the actual rendered prompts, so the prompt
templates are exercised, and failures are injected at every pass.
"""
import json
import os
import re
import sys
import threading
import uuid
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
os.chdir(BACKEND)
import pg  # noqa: E402  (backend/tests/pg.py)

import pandas as pd  # noqa: E402
import psycopg  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from ingest import sample  # noqa: E402
from ingest.catalog import PERSONAL_COLUMNS  # noqa: E402
from lambdas import on_batch_complete, start_batches  # noqa: E402
from pipeline import bedrock_jobs as bj  # noqa: E402
from pipeline import columns, stages  # noqa: E402
from pipeline.bedrock_jobs import Jobs  # noqa: E402
from pipeline.config import EVAL_CRITERIA, Settings  # noqa: E402
from pipeline.pgdb import Db  # noqa: E402
from pipeline.prompts import PromptBook, output_json, output_text  # noqa: E402
from pipeline.runtime import Runtime, set_runtime  # noqa: E402
from pipeline.s3io import Store  # noqa: E402

S = "ai_recommendations"
PROMPT_FILES = {p.stem: json.loads(p.read_text()) for p in (HERE / "prompts").glob("*.json")}

# ------------------------------------------------------------------ the prompts themselves
for job, p in PROMPT_FILES.items():
    placeholders = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", p["template"]))
    assert placeholders == set(p["variables"]), (job, placeholders ^ set(p["variables"]))
assert set(PROMPT_FILES) == {"contract_summary", "web_claims_summary", "account_summary", "recommendation", "evaluation", "draft"}
assert columns.PERSONAL == {c.lower() for c in PERSONAL_COLUMNS}, "the pipeline's personal-data list drifted from the backend catalog"
try:
    columns.assert_no_personal_data({"a": [{"Phoneopen": "1"}]})
    raise AssertionError("personal data should be refused")
except ValueError:
    pass
print("ok - every prompt declares exactly the variables it uses; the personal-data list matches the backend; personal keys are refused")


# ------------------------------------------------------------------ fakes for AWS
class Body:
    def __init__(self, data: bytes):
        self.data = data

    def iter_lines(self):
        yield from self.data.splitlines()

    def read(self):
        return self.data


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": Body(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}


MIN_BATCH = [100]                                                              # Bedrock's minimum records per batch job, set below from the data
FAIL_OPTION, FAIL_ERROR, LOW_POLICY, BAD_DRAFT = set(), set(), set(), set()   # contract ids, filled in once the work list is known


def fake_model(body: dict) -> dict:
    """Answers by reading the rendered prompt, like a (very obedient) model would."""
    text = body["messages"][0]["content"][0]["text"]
    cid = (re.search(r'"contractid": "([^"]+)"', text) or [None, None])[1]
    assert body["anthropic_version"] and isinstance(body["max_tokens"], int) and body["messages"][0]["role"] == "user"

    def reply(s):
        return {"id": "msg_x", "type": "message", "role": "assistant", "content": [{"type": "text", "text": s}], "stop_reason": "end_turn"}
    if "Contract Summary Agent" in text:
        return reply(f"Summary of contract {cid}.")
    if "Service Ticket Summary Agent" in text:
        return reply("Service summary.")
    if "Customer Summary Agent" in text:
        return reply("Account summary.")
    if "Deal Guidance Agent" in text:
        labels = re.findall(r'"label": "(opt_\d+)"', text)
        assert labels and "{{" not in text, "options must be in the prompt and every variable filled"
        return reply(json.dumps({"option": "opt_99" if cid in FAIL_OPTION else labels[-1], "rationale": f"Because of {cid}.", "upsell": "Not recommended for this account right now", "confidence": 0.8}))
    if "QA evaluator" in text:
        return reply("```json\n" + json.dumps({"scores": {k: 8 for k in ("groundedness", "actionability", "non_repetition", "tone", "upsell_relevance")} |
                                               {"policy_compliance": 3 if cid in LOW_POLICY else 8}, "notes": "ok"}) + "\n```")
    if "Renewal Document Agent" in text:
        if cid in BAD_DRAFT:
            return reply("I cannot do that.")
        return reply(json.dumps({"summary": "s", "recipient_role": "Customer", "email_subject": f"About {cid}", "email_body": "Hi,\n\n- point\n\nThank you,\nx@y.com"}))
    raise AssertionError("an unrecognised prompt: " + text[:80])


class FakeBedrock:
    def __init__(self, s3: FakeS3):
        self.s3, self.jobs, self.tokens = s3, {}, {}

    def create_model_invocation_job(self, jobName, roleArn, modelId, clientRequestToken, inputDataConfig, outputDataConfig, timeoutDurationInHours):
        assert roleArn.startswith("arn:") and re.fullmatch(r"[A-Za-z0-9-]{1,63}", jobName), jobName
        assert inputDataConfig["s3InputDataConfig"]["s3InputFormat"] == "JSONL"
        if clientRequestToken in self.tokens:                       # Bedrock's own idempotency
            return {"jobArn": self.tokens[clientRequestToken]}
        arn = f"arn:aws:bedrock:eu-west-1:123456789012:model-invocation-job/{uuid.uuid4().hex[:12]}"
        self.tokens[clientRequestToken] = arn
        self.jobs[arn] = {"status": "InProgress", "message": "", "in": inputDataConfig["s3InputDataConfig"]["s3Uri"], "out": outputDataConfig["s3OutputDataConfig"]["s3Uri"], "model": modelId}
        return {"jobArn": arn}

    def get_model_invocation_job(self, jobIdentifier):
        return {"status": self.jobs[jobIdentifier]["status"], "message": self.jobs[jobIdentifier]["message"]}

    def complete(self, arn):
        job = self.jobs[arn]
        in_key, out_key = job["in"].split("/", 3)[3], job["out"].split("/", 3)[3]
        job_id = arn.rsplit("/", 1)[-1]
        lines = [json.loads(line) for line in self.s3.objects[in_key].decode().splitlines()]
        assert len(lines) >= MIN_BATCH[0], f"Bedrock batch jobs need at least {MIN_BATCH[0]} records"
        out = "".join(json.dumps({"recordId": r["recordId"], "modelInput": r["modelInput"], "modelOutput": fake_model(r["modelInput"])}) + "\n" for r in lines)
        self.s3.objects[f"{out_key}{job_id}/input.jsonl.out"] = out.encode()
        self.s3.objects[f"{out_key}{job_id}/manifest.json.out"] = b'{"totalRecordCount": %d}' % len(lines)   # must be ignored
        job["status"] = "Completed"


class FakeBedrockRuntime:
    def invoke_model(self, modelId, body, contentType, accept):
        req = json.loads(body)
        text = req["messages"][0]["content"][0]["text"]
        if (m := re.search(r'"contractid": "([^"]+)"', text)) and m.group(1) in FAIL_ERROR:
            raise RuntimeError("ThrottlingException: Too many requests")
        return {"body": Body(json.dumps(fake_model(req)).encode())}


class FakeAgent:
    def get_prompt(self, promptIdentifier, promptVersion=None):
        job = promptIdentifier.removeprefix("P-")
        p = PROMPT_FILES[job]
        return {"id": promptIdentifier, "version": "3", "defaultVariant": "v1", "variants": [
            {"name": "v1", "templateType": "TEXT", "templateConfiguration": {"text": {"text": p["template"]}},
             "modelId": "fake-model-v1", "inferenceConfiguration": {"text": p["inference"]}}]}


class FakeLambda:
    def __init__(self):
        self.queue = []

    def invoke(self, FunctionName, InvocationType, Payload):
        assert FunctionName == "complete-fn" and InvocationType == "Event"
        self.queue.append(json.loads(Payload))


class FakeSecrets:
    def __init__(self, secret):
        self.secret = secret

    def get_secret_value(self, SecretId):
        return {"SecretString": json.dumps(self.secret)}


class Ctx:
    aws_request_id = "req-1"

    def __init__(self, ms=900_000):
        self.ms = ms

    def get_remaining_time_in_millis(self):
        return self.ms


def native(v):
    if v is None or (isinstance(v, float) and v != v) or v is pd.NaT:
        return None
    if hasattr(v, "item") and not isinstance(v, (pd.Timestamp,)):
        v = v.item()
    return v.to_pydatetime() if isinstance(v, pd.Timestamp) else v


class FakeSnowflake:
    def __init__(self, contracts: pd.DataFrame, claims: pd.DataFrame):
        contracts = contracts.loc[:, ~contracts.columns.duplicated()]
        self.rows = {str(r["CONTRACTID"]): {k.lower(): native(v) for k, v in r.items()} for _, r in contracts.iterrows()}
        self.claims_by = {}
        for _, r in claims.sort_values("CLAIMDATE", ascending=False).iterrows():
            self.claims_by.setdefault(str(r["CONTRACTID"]), []).append({"claimdate": native(r["CLAIMDATE"]), "faultid": native(r["FAULTID"]),
                                                                      "fault_description": r["FAULT_DESCRIPTION"], "job_type_description": r["JOB_TYPE_DESCRIPTION"]})

    def contracts(self, ids, columns=None):
        return {i: self.rows[i] for i in dict.fromkeys(ids) if i in self.rows}

    def claims(self, ids, per_contract):
        return {i: [{"date": c["claimdate"].isoformat(), "fault_id": c["faultid"], "fault": c["fault_description"], "job_type": c["job_type_description"]}
                    for c in self.claims_by[i][:per_contract]] for i in dict.fromkeys(ids) if i in self.claims_by}


# ------------------------------------------------------------------ the world: data, database role, runtime
DB = pg.new_database()
os.environ["DATABASE_URL"] = DB
contracts, claims = sample.generate(1500, seed=3)
pg.ingest_frames(DB, contracts, claims)
q = lambda sql, *p: pg.query(DB, sql, *p)  # noqa: E731
for name, crit in (("Global check-in", None), ("Global loyalty", None)):
    q(f"INSERT INTO {S}.retention_action (scope, category, name, description) VALUES ('global', 'Service', '{name}', 'a retention action: {name}')")
q(f"INSERT INTO {S}.exclusion_set (ctx, name, criteria, status) VALUES ('034', 'Direct 034', '{{\"channel\": [\"Direct\"]}}', 'active')")
q(f"INSERT INTO {S}.exclusion_hit SELECT (SELECT id FROM {S}.exclusion_set LIMIT 1), contractid FROM {'app_data'}.contract WHERE in_scope AND ctxid = '034' AND channel = 'Direct'")
EXCLUDED = {r["contractid"] for r in q(f"SELECT contractid FROM {S}.v_excluded_contracts")}
assert EXCLUDED

suffix = uuid.uuid4().hex[:6]
roles_sql = (BACKEND / "docs" / "db_roles.sql").read_text().replace("ai_writer", f"ai_writer_{suffix}").replace("app_api", f"app_api_{suffix}")
q(roles_sql)
q(f"GRANT SELECT ON {S}.v_retention_options, {S}.v_ai_work, {S}.v_ai_work_accounts TO ai_writer_{suffix}")    # the grant db_roles.sql defers until after the first ingest
url = make_url(DB)
secret = {"host": url.host, "port": url.port or 5432, "dbname": url.database, "username": f"ai_writer_{suffix}", "password": "CHANGE-ME", "sslmode": "disable"}

settings = Settings(bucket="test-bucket", batch_role_arn="arn:aws:iam::123456789012:role/batch", db_schema=S, completion_function="complete-fn", model_id="fallback-model",
                    prompts={j: {"id": f"P-{j}", "version": "3"} for j in PROMPT_FILES}, min_batch_records=100)
s3, lam = FakeS3(), FakeLambda()
store = Store(s3, settings.bucket)
bedrock = FakeBedrock(s3)
snow = FakeSnowflake(contracts, claims)
db = Db.connect(FakeSecrets(secret), settings)
rt = Runtime(settings=settings, db=db, store=store, jobs=Jobs(bedrock, FakeBedrockRuntime(), store, settings), prompts=PromptBook(FakeAgent(), settings),
             lambda_client=lam, snowflake_factory=lambda: snow)
set_runtime(rt)


def start(event):
    return start_batches.handler(event, Ctx())


def deliver(payload):
    return on_batch_complete.handler(payload, Ctx())


def drain():
    """Run the completion Lambda for everything queued by direct runs, until the chain (recommend -> evaluate -> draft) is finished."""
    results = []
    while lam.queue:
        results.append(deliver(lam.queue.pop(0)))
    return results


def work(flag):
    return [r["contractid"] for r in q(f"SELECT contractid FROM {S}.v_ai_work WHERE {flag}")]


rec_ids = sorted(work("want_recommendation"))
assert len(rec_ids) > 20, f"the data should give a useful number of recommendations, got {len(rec_ids)}"
FAIL_OPTION.update(rec_ids[0:2]); FAIL_ERROR.add(rec_ids[2]); LOW_POLICY.update(rec_ids[3:5]); BAD_DRAFT.add(rec_ids[5])
want = {"contract_summary": len(work("want_contract_summary")), "web_claims_summary": len(work("want_web_claims_summary")),
        "account_summary": q(f"SELECT count(*) AS n FROM {S}.v_ai_work_accounts")[0]["n"], "recommendation": len(rec_ids)}
assert not set(rec_ids) & EXCLUDED, "an excluded contract must never be planned for a recommendation"
# Put the batch minimum just above the recommendation count: summaries then go through Bedrock batch jobs, while the (smaller) recommendation
# job and its two follow-on passes run directly - so both paths are exercised whatever the generated numbers are.
MIN_BATCH[0] = settings.min_batch_records = want["recommendation"] + 1
assert want["contract_summary"] >= MIN_BATCH[0] + 13, "the data should give a batch-sized summary job"

# ================================================================ A. planning without side effects
dry = start({"id": "evt-dry", "dry_run": True})
assert {j: r["records"] for j, r in dry["jobs"].items()} == want, (dry["jobs"], want)
assert not s3.objects and q(f"SELECT count(*) AS n FROM {S}.ai_batch")[0]["n"] == 0 and not bedrock.jobs
print(f"ok - a dry run plans exactly what the work list says ({want}) and starts nothing")

# ================================================================ B. a real run
run1 = start({"id": "evt-1"})
modes = {j: r["mode"] for j, r in run1["jobs"].items()}
assert modes == {j: ("batch" if n >= MIN_BATCH[0] else "direct") for j, n in want.items()}, (modes, want)
assert modes["contract_summary"] == "batch" and modes["recommendation"] == "direct", "the data should exercise both paths"
batch_jobs = [j for j, m in modes.items() if m == "batch"]
batches = {b["job_type"]: b for b in q(f"SELECT * FROM {S}.ai_batch")}
assert {j: b["status"] for j, b in batches.items()} == {j: "submitted" for j in want} and len(bedrock.jobs) == len(batch_jobs)
assert batches["recommendation"]["bedrock_job_arn"].startswith("sync:") and len(lam.queue) == len(want) - len(batch_jobs)
for job, n in want.items():
    base = f"PCR/AI_Input/{job}/{run1['run_id']}/"
    inputs = [json.loads(x) for x in s3.objects[base + "input.jsonl"].decode().splitlines()]
    metas = [json.loads(x) for x in s3.objects[base + "meta.jsonl"].decode().splitlines()]
    assert len(inputs) == len(metas) == n and [i["recordId"] for i in inputs] == [m["rid"] for m in metas]
    assert all(m["input_hash"] and m["ctx"] and m["input_data"] for m in metas)
    sample_text = inputs[0]["modelInput"]["messages"][0]["content"][0]["text"]
    assert "{{" not in sample_text and inputs[0]["modelInput"]["temperature"] == PROMPT_FILES[job]["inference"]["temperature"]
blob = b" ".join(s3.objects.values()).decode()
assert not any(w in blob for w in ("Teststrasse", "+00 000", "phoneopen", "postcode")), "personal data reached a prompt or the metadata"
print(f"ok - a real run ({modes}; direct below Bedrock's 100-record minimum); inputs, metadata and the model request are well formed; no personal data in S3")

# ================================================================ C. a retry of the same scheduled event does nothing twice
before = (q(f"SELECT count(*) AS n FROM {S}.ai_batch")[0]["n"], len(bedrock.jobs), len(lam.queue))
again = start({"id": "evt-1"})
assert again["run_id"] == run1["run_id"] and all("already" in r for r in again["jobs"].values()), again
assert (q(f"SELECT count(*) AS n FROM {S}.ai_batch")[0]["n"], len(bedrock.jobs), len(lam.queue)) == before
print("ok - the same scheduled event delivered twice starts no second batch, job or hand-over")

# ================================================================ D. completion: duplicates, writing, the three-pass chain
arns = {j: batches[j]["bedrock_job_arn"] for j in batch_jobs}
for arn in arns.values():
    bedrock.complete(arn)
first = {j: deliver({"version": "0", "detail": {"batchJobArn": arn, "status": "Completed"}}) for j, arn in arns.items()}
dupes = {j: deliver({"detail": {"batchJobArn": arn, "status": "Completed"}}) for j, arn in arns.items()}
assert all(r["duplicate"] for r in dupes.values()), dupes
for j, r in first.items():
    assert (r["written"], r["skipped"], r["failed"]) == (want[j], 0, 0), (j, r)
chain = drain()                                  # direct runs hand over by queue: summaries below 100 records, then recommend -> evaluate -> draft
assert q(f"SELECT count(*) AS n FROM {S}.contract_summary")[0]["n"] == want["contract_summary"]
assert q(f"SELECT count(*) AS n FROM {S}.web_claims_summary")[0]["n"] == want["web_claims_summary"]
assert q(f"SELECT count(*) AS n FROM {S}.account_summary")[0]["n"] == want["account_summary"]
cs = q(f"SELECT * FROM {S}.contract_summary LIMIT 50")
assert all(r["is_latest"] and r["language"] == "en" and r["ai_summary"].startswith("Summary of contract") and r["input_hash"] and r["ai_configuration"]["model"] == "fake-model-v1" and
           r["ai_configuration"]["prompt"] == {"id": "P-contract_summary", "version": "3"} for r in cs)
assert not any(w in json.dumps([r["input_data"] for r in q(f"SELECT input_data FROM {S}.contract_summary")]) for w in ("Teststrasse", "address", "postcode"))
print("ok - completion events are delivered twice and written once; stored summaries carry their input_hash, model, prompt version and no personal data")

assert [r["job_type"] for r in chain if r.get("job_type") in ("recommendation", "evaluation", "draft")] == ["recommendation", "evaluation", "draft"], chain
chain = [r for r in chain if r.get("job_type") in ("recommendation", "evaluation", "draft")]
assert chain[0]["chained"]["records"] == len(rec_ids) - 3 and chain[0]["failed"] == 3     # the invalid option and the model error were dropped
final = q(f"SELECT * FROM {S}.contract_recommendation")
assert len(final) == len(rec_ids) - 3 and not ({r["contractid"] for r in final} & (FAIL_OPTION | FAIL_ERROR))
opts = {}
for r in q(f"SELECT contractid, action_id FROM {S}.v_retention_options"):
    opts.setdefault(r["contractid"], set()).add(r["action_id"])
view = {r["contractid"]: r for r in q(f"SELECT * FROM {S}.v_ai_work")}
for r in final:
    cid = r["contractid"]
    assert r["retention_action_id"] in opts[cid], "the model may only choose an action that was on this contract's menu"
    assert r["milestone"] == view[cid]["expiry_bucket"] and r["is_latest"] and r["action_status"] == "Action required" and r["outcome"] is None
    channel = "Direct Sales Rep" if "Direct" == json.loads(json.dumps(r["input_data"]))["context"]["channel"] else "Dealer"
    assert r["execution_owner"] == channel and 0 <= float(r["confidence"]) <= 1
    ev = r["evaluation"]
    assert set(ev["scores"]) == set(EVAL_CRITERIA) and ev["pass"] == (cid not in LOW_POLICY) and ev["retries"] == 0
    assert (r["draft_content"] is None) == (cid in BAD_DRAFT)
    assert r["draft_content"] is None or r["draft_content"]["email_subject"] == f"About {cid}", (cid, r["draft_content"], r["evaluation"])
    assert set(r["ai_configuration"]) == {"recommendation", "evaluation", "draft"} and r["input_hash"]
for job in ("evaluation", "draft"):                  # in every pass, each prompt must sit next to ITS contract's metadata
    for key in [k for k in s3.objects if f"/{job}/" in k and k.endswith("input.jsonl")]:
        inputs = [json.loads(x) for x in s3.objects[key].decode().splitlines()]
        metas = [json.loads(x) for x in s3.objects[key.replace("input.jsonl", "meta.jsonl")].decode().splitlines()]
        assert [i["recordId"] for i in inputs] == [m["rid"] for m in metas]
        for i, m in zip(inputs, metas):
            assert f'"contractid": "{m["key"]}"' in i["modelInput"]["messages"][0]["content"][0]["text"], (job, m["key"])
stages_ = {b["job_type"]: b for b in q(f"SELECT * FROM {S}.ai_batch")}
assert stages_["evaluation"]["parent_batch_id"] == stages_["recommendation"]["id"] and stages_["draft"]["parent_batch_id"] == stages_["evaluation"]["id"]
assert {b["status"] for b in stages_.values()} == {"written"} and stages_["draft"]["records_written"] == len(final) and stages_["draft"]["records_failed"] == 1
print(f"ok - the three-pass chain: {len(rec_ids)} planned, 3 dropped (invalid option, model error), {len(final)} stored once with the evaluation and draft; "
      "only menu actions chosen; owner follows the channel; low policy score escalates; a bad draft keeps the recommendation")

# ================================================================ E. the next day: nothing to redo
nxt = start({"id": "evt-2"})
assert {j: r["records"] for j, r in nxt["jobs"].items() if j != "recommendation"} == {"contract_summary": 0, "web_claims_summary": 0, "account_summary": 0}, nxt
retry = nxt["jobs"]["recommendation"]
assert retry["records"] == 3, "only the contracts that failed yesterday are still due for a recommendation"
drain()
FORCED = MIN_BATCH[0] + 13
forced = start({"id": "evt-3", "force": True, "jobs": ["contract_summary"], "limit": FORCED})
assert forced["jobs"]["contract_summary"]["records"] == FORCED and forced["jobs"]["contract_summary"]["mode"] == "batch"
print("ok - the next day: unchanged summaries are not regenerated; only yesterday's failures are retried; force regenerates")

# ================================================================ F. a missed event, a failed job and a stuck batch: the sweeper
arn = forced["jobs"]["contract_summary"]["arn"]
bedrock.complete(arn)                                             # Bedrock finished, but no event reached us
assert sum(1 for b in stages.sweep(rt, 9e12)["processed"]) == 0, "a fresh batch is given time for its normal event first"
q(f"UPDATE {S}.ai_batch SET created_at = now() - interval '1 hour' WHERE bedrock_job_arn = '{arn}'")
swept = stages.sweep(rt, 9e12)
done = [p for p in swept["processed"] if p["arn"] == arn][0]
assert (done["written"], done["skipped"]) == (0, FORCED), "the same inputs as the stored summaries: nothing new is written"
failing = start({"id": "evt-4", "force": True, "jobs": ["web_claims_summary"], "limit": MIN_BATCH[0] + 3})["jobs"]["web_claims_summary"]
assert failing["mode"] == "batch", "the web-claims job should be big enough for a batch"
bedrock.jobs[failing["arn"]].update(status="Failed", message="ValidationException: bad record")
q(f"UPDATE {S}.ai_batch SET created_at = now() - interval '1 hour' WHERE bedrock_job_arn = '{failing['arn']}'")
q(f"SELECT {S}.ai_batch_create(gen_random_uuid(), 'draft', NULL, 'm', '{{}}', 's3://x', 's3://y', 1)")
q(f"UPDATE {S}.ai_batch SET created_at = now() - interval '7 hours' WHERE status = 'created'")
swept = stages.sweep(rt, 9e12)
assert any(p.get("marked_failed") for p in swept["processed"]) and len(swept["marked_failed"]) == 1, swept
states = {r["status"] for r in q(f"SELECT status FROM {S}.ai_batch")}
assert "failed" in states and "created" not in states and "submitted" not in states
assert deliver({"detail": {"batchJobArn": failing["arn"], "status": "Failed"}})["marked_failed"] is False, "a failure event for an already failed batch is a no-op"
report = deliver({"report": True})
assert report and {"id", "job_type", "status", "records_written"} <= set(report[0])
try:
    deliver({"detail": {"status": "Completed"}})
    raise AssertionError("an event without a job arn should be rejected")
except ValueError:
    pass
print("ok - the sweeper completes a batch whose event never came, marks a failed Bedrock job and a never-submitted batch failed, and a late failure event changes nothing")

# ================================================================ G. the database role can do all of that and nothing more
try:
    db.conn.execute(f"UPDATE {S}.contract_summary SET ai_summary = 'x'")
    raise AssertionError("the job's role must not be able to update results")
except psycopg.errors.InsufficientPrivilege:
    pass
try:
    db.conn.execute(f"DELETE FROM {S}.ai_batch")
    raise AssertionError("the job's role must not be able to delete from the batch log")
except psycopg.errors.InsufficientPrivilege:
    pass
print("ok - everything above ran as the restricted ai_writer role (insert + functions + views), which cannot update or delete")

# cleanup of the cluster-wide test roles
db.conn.close()
for stmt in (f"REVOKE ALL ON ALL TABLES IN SCHEMA {S} FROM ai_writer_{suffix}, app_api_{suffix}", f"REVOKE ALL ON ALL TABLES IN SCHEMA app_data FROM ai_writer_{suffix}, app_api_{suffix}",
             f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {S} FROM ai_writer_{suffix}, app_api_{suffix}", f"REVOKE ALL ON SCHEMA {S}, app_data FROM ai_writer_{suffix}, app_api_{suffix}",
             f"ALTER DEFAULT PRIVILEGES IN SCHEMA app_data REVOKE SELECT ON TABLES FROM app_api_{suffix}", f"DROP ROLE ai_writer_{suffix}", f"DROP ROLE app_api_{suffix}"):
    q(stmt)
print("ALL PIPELINE CHECKS PASSED")
