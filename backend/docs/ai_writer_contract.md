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
   the job must run after the load has finished (check `app_data.ingest_run` for today's `succeeded` run).
5. **Concurrent writers are safe.** Twelve simultaneous inserts for one key become versions 1 to 12
   with an unbroken chain and no errors.

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

- Which contracts need a summary, and which need a recommendation (the work-queue view `v_ai_work`): this depends on
  the rules below being settled.

- The exact columns of `app_data.v_ai_work` (proposed: system, contractid, accountid, ctx, needs_summary,
  needs_recommendation, excluded_by, input hash of the current source data).
- The source of `jde_customer_id` (not a column of the Contract data product).
- Whether `retention_action_id` stays null until retention actions are configurable.
