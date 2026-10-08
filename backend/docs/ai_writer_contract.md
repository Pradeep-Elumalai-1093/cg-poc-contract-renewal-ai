# AI results: writer contract

For the team building the AWS batch job. The database is the interface: the app and the
job never call each other. The job reads what to process, writes results into four
tables, and the app reads them. This page is everything the job needs to write correctly.

**Where:** database `uda_1325`, schema `ai_recommendations` (configurable via `DB_SCHEMA`).
**As whom:** role `ai_writer` (`docs/db_roles.sql`): `INSERT` and `SELECT` only. It cannot
`UPDATE` or `DELETE`, and cannot see users, access assignments or the audit log.
**Tested:** every rule below is enforced by the database and covered by `tests/check_schema.py`.

## The four tables

| Table | One row per | Key column | Text column | Version column |
|---|---|---|---|---|
| `contract_summary` | contract version of the summary | `contractid` | `ai_summary` | `ai_summary_version` |
| `account_summary` | account (customer) | `accountid` | `ai_summary` | `ai_summary_version` |
| `web_claims_summary` | contract (services summary) | `contractid` | `ai_summary` | `ai_summary_version` |
| `contract_recommendation` | contract | `contractid` | `ai_recommendation` | `ai_recommendation_version` |

Every row is a **version**. A new result for the same (`system`, `language`, key) is a new row;
older rows stay as history.

## What you set, and what the database sets

| Column | Set by | Notes |
|---|---|---|
| key (`contractid` / `accountid`) | **you, required** | Source key as text |
| `ctx` | **you, required** | The contract's `CTXID` as a 3-digit zero-padded string (`'034'`). For `account_summary`, the account's CTX. Drives who can see the row |
| `system` | you, default `'ecare'` | `'ecare'` or `'salesforce'` |
| `language` | you, default `'en'` | A translation is a separate row with its own language code |
| `accountid`, `jde_customer_id` | you, optional | `jde_customer_id` is an opaque identifier: the app never uses it for logic |
| the text column | you | |
| `ai_configuration` (jsonb) | you | Model name/version, parameters, prompt version |
| `input_data` (jsonb) | you | The exact inputs sent to the model. **No personal data** (see below) |
| `input_hash` | you | SHA-256 hex of `input_data` serialised with sorted keys and no spaces |
| `created_by` / `modified_by` | default `'pipeline'` | The app writes the user's id here for user edits |
| `user_feedback` (jsonb) | the app | Leave null |
| `id` | **database** | UUIDv7, `DEFAULT uuidv7()`. Do not supply |
| `prev_id`, version column, `is_latest` | **database** | Do not supply. A trigger links the new row to the previous latest, numbers it previous + 1, flips the old row's `is_latest` and serialises concurrent writers per key |
| `created_on`, `modified_on` | database | `timestamptz` |

`contract_recommendation` has extra columns:

| Column | Set by | Notes |
|---|---|---|
| `retention_action_id` | you | The `action_id` of the retention action chosen, from `v_retention_options` (below) |
| `milestone` | you | The expiry bucket this recommendation was made for: `'>90'`, `'90'`, `'60'`, `'45'`, `'30'`, `'10'` or `'Lost'`. Lets the pipeline recommend again only when a contract moves to the next bucket |
| `retention_action_name` | you | Snapshot of the name, so history stays readable if the action is later edited |
| `execution_owner`, `upsell` | you | As returned by the recommendation step |
| `confidence` | you | 0 to 1 |
| `evaluation` (jsonb) | you | `{scores, composite, pass, notes, retries}` from the evaluation step |
| `draft_content` (jsonb) | you | `{summary, recipient_role, email_subject, email_body}` from the drafting step |
| `action_status`, `outcome`, `outcome_note` | **the app / users** | Do not set. New versions start as `'Action required'` with no outcome; older versions keep theirs |

## Rules

1. **INSERT only.** One `INSERT` is one atomic new version. There is nothing to read, flip or retry.
2. **Do not write when nothing changed.** Compare your new `input_hash` with the latest row's
   (`SELECT input_hash FROM ... WHERE is_latest AND ...`) and skip if equal. This keeps the history
   to real changes instead of one row per contract per day.
3. **No personal data in `input_data` or `ai_configuration`.** That means address, postcode, county,
   phone, fax, vehicle id and contact names. These rows are stored as history and read back by the app.
4. **Exclusions and the menu of actions are configured by users in the app and read from here**
   (PostgreSQL, not Snowflake):
   - `v_excluded_contracts (contractid, ctx, set_ids, set_names)`: a contract listed here matches an ACTIVE
     exclusion set of its area. **It must get its summaries but no `contract_recommendation` row.**
   - `v_retention_options (contractid, ctx, action_id, scope, category, sub_category, name, description)`: the
     retention actions the model may choose from for that contract. A global action is replaced in an area by a
     local one with the same category, sub-category and name; an action with conditions appears only for the
     contracts it matches. A live contract with no rows here has nothing to recommend.
   Both are refreshed by the daily load (`python -m ingest` recomputes every rule's matches right after it), so
   the job should run after the load has finished (or accept that it works from the previous load).
5. **Concurrent writers are safe.** Twelve simultaneous inserts for one key become versions 1 to 12
   with an unbroken chain and no errors.

## The daily run: one batch job per result table

| `job_type` | Stores into | Works from |
|---|---|---|
| `contract_summary` | `contract_summary` | `v_ai_work` where `want_contract_summary` |
| `web_claims_summary` | `web_claims_summary` | `v_ai_work` where `want_web_claims_summary` |
| `account_summary` | `account_summary` | `v_ai_work_accounts` |
| `recommendation` | `contract_recommendation` | `v_ai_work` where `want_recommendation`, choosing from `v_retention_options` |
| `evaluation`, `draft` | the `evaluation` and `draft_content` of that recommendation | the output of the earlier pass (`parent_batch_id`) |

**A recommendation row is written once, after its last pass.** The job's role cannot `UPDATE`, and a row is a version,
so do not insert the recommendation and fill its evaluation and draft in later. Keep the earlier passes' results in S3,
chain the next batch from them, and insert the finished row (recommendation, `evaluation`, `draft_content`) after the
final pass.

### What the work-list views say

`v_ai_work` has one row per live contract that has an area. The rules are in the view, once; the job adds only what the
database cannot know: whether its freshly built inputs differ from the stored ones.

| Column | Meaning |
|---|---|
| `in_ai_scope` | Risk score at or above `ai_setting.summary_risk_threshold` (default 50, admin-adjustable), or in an expiry bucket of 90 days or less |
| `want_contract_summary`, `want_web_claims_summary` | In scope (and, for the web-claims summary, the contract has claims) |
| `contract_summary_hash`, `web_claims_summary_hash` | `input_hash` of the latest stored summary, or null. **Build the inputs, hash them, and send the contract to the model only if the hash differs or is null** - that is "regenerate when the inputs change" |
| `milestone_due` | The contract is in a 90/60/45/30/10 bucket and has not been recommended for that bucket (`last_milestone`) |
| `excluded`, `excluded_by` | An active exclusion set of its area matches it. It still gets its summaries, never a recommendation |
| `has_options` | At least one retention action may be chosen for it |
| `want_recommendation` | in scope, not excluded, `milestone_due`, and `has_options` |

`v_ai_work_accounts` has one row per account with at least one in-scope contract, with `account_summary_hash`.
A custom risk-based trigger for recommendations is not defined yet; until it is, a recommendation is made only when a
contract reaches a new expiry bucket.

**Freshness.** If the batch runs before the daily load, the views describe the previous load: contracts, expiry buckets
and the matches of exclusion sets and retention actions are one day old.

### The batch log (`ai_batch`) - how a batch is started, finished and never written twice

Drive it only through these functions (the role has no `INSERT`/`UPDATE` on the table). `run_id` is one uuid per daily
trigger.

**Lambda 1**, per job type:

1. Build the input file; for each contract compute `input_hash` and skip it if unchanged (above).
2. `ai_batch_create(run_id, job_type, parent_batch_id, model_id, prompt_versions, input_s3_uri, output_s3_uri, records_requested)`
   returns the batch id. Calling it again for the same `(run_id, job_type)` returns the **same** id, so a retried Lambda
   does not start a second job.
3. Start the Bedrock job, then `ai_batch_submitted(batch_id, job_arn)`. If anything fails first: `ai_batch_fail(batch_id, error)`.

**Lambda 2**, on the Bedrock completion event:

1. `batch_id = ai_batch_claim(job_arn, <this invocation's id>)`. **If it returns NULL, stop**: this is a duplicate event,
   or the batch is already written or failed. Of any number of simultaneous deliveries exactly one gets the id.
2. Read the S3 output and `INSERT` the results (versioning is automatic). Skip a result whose `input_hash` equals the
   latest stored one, so a re-run never creates duplicate versions.
3. `ai_batch_finish(batch_id, records_returned, records_written, records_skipped, records_failed)`.
4. On a Bedrock `Failed` or `Stopped` event: `ai_batch_fail(job_arn, message)`.

A claim held for more than 15 minutes is treated as a crashed invocation and can be taken over (this is safe because of
step 2). A written batch is never reopened or marked failed. Admins see the log at `GET /api/admin/batches`.

## Example

```sql
-- a new version of a contract summary: only what you know
INSERT INTO ai_recommendations.contract_summary
    (contractid, accountid, jde_customer_id, ctx, ai_summary, ai_configuration, input_data, input_hash)
VALUES
    ('303758', '10242083', 'J-123', '034', 'Contract 303758 expires in ...',
     '{"model": "<model>", "prompt_version": "v3"}', '{"claims_last_90d": 2, "risk_score": 71}', '9f2c...');

-- a recommendation
INSERT INTO ai_recommendations.contract_recommendation
    (contractid, accountid, ctx, ai_recommendation, retention_action_name, execution_owner,
     upsell, confidence, evaluation, draft_content, ai_configuration, input_data, input_hash)
VALUES
    ('303758', '10242083', '034', 'Two repeat compressor faults in the last quarter ...', 'Free service check-in',
     'Direct Sales Rep', 'Not recommended for this account right now', 0.82,
     '{"composite": 8.1, "pass": true}', '{"email_subject": "..."}', '{...}', '{...}', '1b7e...');
```

## What changed from the UDA schema document

| Document | Here | Why |
|---|---|---|
| `BIGSERIAL` ids | UUIDv7 | Decided; no coordination between writers |
| `contractid TEXT (Unique)` | Unique among **latest** rows only, per `system` and `language` | A plain unique key forbids a second version |
| `BLOB` | `jsonb` | Postgres has no BLOB; `jsonb` is queryable |
| `DATE` timestamps | `timestamptz` | Two versions in one day must be orderable |
| no `ctx` | `ctx` required | Access is by area; nothing could be scoped otherwise |
| no `language` | `language`, default `'en'` | Translation is in scope |
| `ai_recommendation TEXT` only | Structured columns, `evaluation`, `draft_content` | The worklist and dashboard group by the chosen action; evaluation and drafting stay in the pipeline |
| (none) | `input_hash` | Skip regenerating unchanged inputs |

## To agree before the job is built

- The custom risk-based trigger for recommendations (deferred).
- The source of `jde_customer_id` (not a column of the Contract data product).
- Translation: `language` exists on every table, but no job or work-list column drives it yet.
