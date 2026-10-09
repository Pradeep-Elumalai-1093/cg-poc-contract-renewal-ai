# AI pipeline (AWS)

Generates the AI summaries and retention recommendations the contract-renewal application shows. Deploy it with
**`deploy.ipynb`**.

```
EventBridge (daily) ─► Lambda 1: start-batches ──► Bedrock batch inference ──► EventBridge (job finished)
                         │  plan from Aurora + Snowflake                                    │
                         │  prompts from Prompt Management                                  ▼
                         └─► S3 PCR/AI_Input/<job>/<run>/                       Lambda 2: on-batch-complete
                                                                                  │  S3 PCR/AI_Output/…
EventBridge (hourly)  ─────► Lambda 2 (sweep: asks Bedrock about open batches) ──┘  ─► Aurora (4 result tables)
```

## What it does each day

1. **Plans.** Reads the work lists in Aurora (`v_ai_work`, `v_ai_work_accounts`, `v_retention_options`): which contracts
   need a summary, which are due a recommendation (reached a new expiry bucket, not excluded, have actions to choose from).
   Builds each contract's facts from the **Snowflake** data product (no personal data - enforced in code, and by a test).
   A summary is only generated when its inputs changed (`input_hash`).
2. **Starts one Bedrock batch job per result table:** contract summary, web-claims summary, account summary, recommendation.
   A job below Bedrock's 100-record minimum is run directly instead - same files, same code path afterwards.
3. **Handles each finished job.** Summaries are written. A recommendation is chained through two more passes -
   **evaluation** (QA scores, pass if composite ≥ 7 and policy ≥ 6) then **draft** (outreach email) - and the finished row is
   inserted **once**, after the last pass (the job's database role cannot update a row).
4. **Never writes twice.** A duplicate completion event, a retried Lambda, or a crashed handler cannot double-write: the batch
   log's atomic claim lets exactly one invocation through, writes skip a result whose `input_hash` is already stored, and
   starting a job is idempotent per run. An hourly sweep (`SweepSchedule`) completes any batch whose event never arrived.

## Files

| | |
|---|---|
| `deploy.ipynb` | Step-by-step deployment: secrets, prompts, packaging, the stack, database role, smoke tests, operations, teardown |
| `template.yaml` | CloudFormation: bucket, two IAM roles, dependency layer, two Lambdas, three EventBridge rules, optional alarms |
| `package.py` | Builds the Lambda layer and code zip - no Docker, works from Windows/macOS |
| `prompts/*.json` | The six prompts, published to Bedrock Prompt Management by the notebook |
| `pipeline/` | The logic (`stages.py` is the heart) · `lambdas/` the two thin handlers |
| `tests/` | `check_pipeline.py` (end to end), `check_api_shapes.py` (every AWS request vs AWS's own definitions) |

## Deploy

Open `deploy.ipynb`, fill in the settings cell, and run the cells in order. Prerequisites are listed at its top (Bedrock model
access, Aurora with the application's migrations and one `python -m ingest`, a Snowflake read user, private networking).

## Operate

Run from the notebook (step 9) or invoke the functions directly:

| | |
|---|---|
| Plan without starting anything | invoke `start-batches` with `{"dry_run": true}` |
| Run now / only some jobs / ignore stored hashes / cap the size | `{"jobs": ["recommendation"]}` · `{"force": true}` · `{"limit": 200}` |
| What happened | invoke `on-batch-complete` with `{"report": true}`, or the admin screen `GET /api/admin/batches` |
| Complete stragglers now | `{"sweep": true}` |
| Pause the daily run | disable the `…-daily` rule (running batches still finish) |
| Change a prompt | edit it in Prompt Management → create a version → update the stack's `PromptsJson` (no code deploy) |

A failed run is safe to run again. Batches stuck `submitted` or `writing` are retried by the sweep; a batch never submitted for
6 hours is marked failed; a failed Bedrock job is recorded with its reason in `ai_batch.error`.

## What is and isn't verified

**Verified by the tests** (run locally against a real PostgreSQL with the real schema, ingest and views, connecting as the
restricted `ai_writer` role): planning matches the work lists; both the batch and the direct path; idempotent starts; duplicate
events written once; the three-pass chain with injected failures (invalid option, model error, low QA score, bad draft); only
menu actions can be chosen; no personal data in S3 or in stored inputs; the sweeper; and that every record is paired with its own
contract in every pass (the tests caught a real bug here). Every request the pipeline and the notebook build is valid against
AWS's service definitions, and the job states the code reacts to are ones Bedrock really returns. The template passes `cfn-lint`,
and the Lambda layer imports from a clean environment.

**Not verified - it needs your AWS account** (the notebook's smoke tests cover the first two):
- Aurora and Snowflake connectivity from the VPC; Prompt Management and the model actually answering.
- The **shape of Bedrock's "job finished" EventBridge event** and **its output file layout**. The code reads `detail.batchJobArn`
  and `detail.status`, and lists `<output>/<job id>/*.jsonl.out`. If either differs, the sweep still completes every batch by
  asking Bedrock directly, and a missing output is recorded as a failed batch (nothing wrong is written).
- That your chosen model supports **batch inference** in your region, and your account's batch quotas.

## Known limits

* At most `MaxRecordsPerJob` (50,000) records per job per day; a bigger backlog drains over several days, never-summarised first.
* Planning scans candidates against Snowflake in chunks and stops 2 minutes before the Lambda timeout; whatever is left waits for the next run.
* Recommendations use the summaries already stored, so a summary created in the same daily run is used the next day.
* The evaluation does not retry a recommendation (the batch pipeline has no loop); a failing one is stored with `pass: false` for a human.
* The model request format is Anthropic's Messages API. Another model family needs `Prompt.model_input` / `output_text` adapted.
* A batch whose handler keeps throwing is retried by the sweep until fixed; the error alarm is how you find out.
* Changing a prompt does not regenerate existing summaries by itself (the hash covers the inputs, not the prompt); use `{"force": true}`.
