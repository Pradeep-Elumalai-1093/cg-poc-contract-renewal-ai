# Proof of Concept Plan — Proactive Contract Renewal AI
**Client:** Climate Solutions Transportation (CST) — HVAC solutions for NATT / ETT / APAC TT
**Status:** Proposal / discovery stage — not a confirmed build
**Principle throughout:** every component is modular and swappable; unknowns are labeled assumptions, not decisions

---

## 1. Assumptions

These are explicitly flagged for validation with the client — not treated as fact.

| # | Assumption | Why it matters |
|---|---|---|
| A1 | Unit of analysis is customer-**contract**, not customer alone | A customer may hold multiple contracts, each on its own renewal clock |
| A2 | Renewal milestones at 90 / 60 / 45 / 30 / 10 days to expiry | Drives the state machine trigger points |
| A3 | No existing retention playbook — we propose a generic action taxonomy | Real playbook (if any) should replace ours |
| A4 | No existing customer health score — we introduce a new "Renewal Risk Score (0–100)" | Avoids colliding with any legacy scoring |
| A5 | Contract data source is unknown (CRM/ERP/spreadsheets) — POC uses simulated data | Real integration effort unknown until source system named |
| A6 | No labeled churn history confirmed — POC simulates 2–3 years of synthetic contract history | Real model quality depends on real historical depth |
| A7 | Margin captured at contract level (contract value − est. cost to serve) | Minimum granularity needed to prioritize by profitability, not just size |
| A8 | Renewal is modeled as a **new contract record with `parent_contract_id`** linking to the prior term, not an in-place edit | Preserves historical margin/pricing trend per term; matches typical CRM/ERP renewal patterns |
| A9 | Contract term defaults to 1 year (auto-renewing), consistent with commercial HVAC industry norms; "months on book" (relationship tenure) is tracked separately from contract term length | Distinguishes contract-term risk from customer-relationship value |
| A10 | Outcome signal for POC is a simple simulated enum: `No response` / `Engaged` / `Declined`, plus optional rep free-text feedback | Placeholder for real engagement signal (email opens, CRM activity) not yet available |
| A11 | NATT and APAC TT follow a dealer-only channel pattern; ETT has both dealer and direct channels | Confirmed by client; APAC TT is an assumed analog to NATT pending confirmation |
| A12 | Dealer-channel visibility into end-customer data may be limited — "customer" may resolve to the dealer's aggregated book, not the individual fleet operator | Needs explicit validation; affects whether risk scoring happens per fleet operator or per dealer account |
| A13 | Retry limit for the evaluation loop is 2, before escalation to human review | Open decision — proposed default |
| A14 | Policy-compliance failure is treated as an automatic fail regardless of composite evaluation score | Open decision — proposed default, given regulatory/pricing risk |
| A15 | LLM provider is Claude (Sonnet-class) for POC, behind a swappable interface | Provider-agnostic output contract; not a commitment to a single vendor |
| A16 | Rollout scope is pilot team only; deployment architecture kept flexible and out of POC detail | Per client direction — POC proves the concept, not production infra |

---

## 2. Data Catalog

### `customers`
| Field | Type | Notes |
|---|---|---|
| customer_id | string (PK) | |
| customer_name | string | |
| region | enum | NATT / ETT / APAC_TT |
| channel | enum | Dealer / Direct (ETT only supports both) |
| dealer_id | string, nullable | populated when channel = Dealer |
| segment | string | e.g., fleet size tier |
| first_contract_date | date | anchors "months on book" |

### `contracts`
| Field | Type | Notes |
|---|---|---|
| contract_id | string (PK) | |
| customer_id | string (FK) | |
| parent_contract_id | string, nullable | links renewal to prior term (A8) |
| start_date | date | |
| end_date | date | |
| contract_value | float | |
| est_cost_to_serve | float | |
| margin | float | derived: contract_value − est_cost_to_serve |
| term_months | int | |
| status | enum | Active / Renewed / Lost |
| current_milestone | enum | >90 / 90 / 60 / 45 / 30 / 10 / Lost |
| last_milestone_processed | enum, nullable | drives the daily filter logic |

### `service_history`
| Field | Type | Notes |
|---|---|---|
| record_id | string (PK) | |
| contract_id | string (FK) | |
| service_date | date | |
| service_type | string | |
| complaint_flag | bool | |

### `payment_history`
| Field | Type | Notes |
|---|---|---|
| record_id | string (PK) | |
| contract_id | string (FK) | |
| due_date | date | |
| paid_date | date, nullable | |
| lag_days | int | derived |

### `campaign_taxonomy` (config, admin-editable)
| Field | Type | Notes |
|---|---|---|
| campaign_id | string (PK) | |
| campaign_name | string | e.g., "Loyalty pricing offer" |
| region_override | string, nullable | region-specific variant if needed |
| execution_owner_type | enum | Direct Sales Rep / Dealer |

### `campaign_assignments`
| Field | Type | Notes |
|---|---|---|
| assignment_id | string (PK) | |
| contract_id | string (FK) | |
| milestone | enum | |
| campaign_id | string (FK) | |
| assigned_date | date | |
| outcome | enum | No response / Engaged / Declined |
| rep_feedback | string, nullable | |

### `recommendation_trace`
| Field | Type | Notes |
|---|---|---|
| run_id | string (PK) | |
| customer_id | string (FK) | |
| contract_id | string (FK) | |
| milestone | enum | |
| timestamp | datetime | |
| prior_milestone_outcome | string, nullable | fed forward per state machine rule |
| aggregator_output | json | |
| recommendation_output | json | action, campaign, rationale |
| prompt_version | string | |
| evaluation_scores | json | per rubric criterion |
| evaluation_pass | bool | |
| retry_count | int | |
| escalated | bool | |
| model_used | string | |
| latency_ms | int | |
| token_cost_usd | float | |

---

## 3. Table Names (in-memory objects)

Since there is no database for the POC, these are the in-memory data structures (pandas DataFrames or Python dicts, seeded from JSON/CSV at startup):

- `customers`
- `contracts`
- `service_history`
- `payment_history`
- `campaign_taxonomy`
- `campaign_assignments`
- `recommendation_trace`
- `milestone_config` — the 90/60/45/30/10 day thresholds, editable
- `region_channel_config` — region-to-channel mapping (A11)

---

## 4. Modules (Python backend)

| Module | Responsibility |
|---|---|
| `data_simulator` | Generates synthetic customers/contracts/history across NATT, ETT, APAC TT with realistic channel splits |
| `milestone_filter` | Deterministic daily job — identifies contract-milestone pairs due for processing (no LLM cost here) |
| `agents/data_aggregator` | Pulls contract, margin, history, and prior-milestone context into a structured payload |
| `agents/recommendation_agent` | LLM call — produces action, campaign, rationale |
| `agents/evaluation_agent` | LLM call — scores the recommendation against the rubric |
| `agents/orchestrator` | Runs the graph: aggregator → recommend → evaluate → pass/retry/escalate |
| `llm_client` | Thin wrapper around the Claude API; swappable interface for provider changes |
| `trace_logger` | Writes structured records to `recommendation_trace` |
| `metrics_engine` | Computes KPI, LLM, and success metrics from trace + assignment data |
| `campaign_config` | Admin-editable taxonomy and milestone thresholds |
| `api_layer` | FastAPI endpoints serving the frontend (dashboard, customer detail, campaigns, admin, feedback) |

### Frontend modules (React)
- `Dashboard` — bucket cards, value-segmentation scatter, funnel, campaign effectiveness, risk histogram
- `CustomerDetail` — score, why, history, recommended action
- `RecommendedActions` — grouped campaign view
- `FeedbackLogging` — outcome capture
- `AdminConfig` — pilot-scope thresholds and taxonomy editor

---

## 5. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI | REST API serving the React frontend |
| Data handling | pandas / in-memory Python objects | no database — see §6 |
| Agent orchestration | Custom lightweight graph runner | swappable for LangGraph or similar later |
| LLM | Claude API (Sonnet-class), behind `llm_client` interface | swappable provider; Haiku-class as a cheaper alternative for less complex calls |
| Frontend | ReactJS | Recharts (or similar) for scatter/funnel/histogram charts |
| Styling | Tailwind CSS | consistent with modular, swappable front-end approach |

---

## 6. No Database — In-Memory Design

- All "tables" in §3 are held as **pandas DataFrames (or Python dicts) in backend process memory**
- Seeded at startup from static JSON/CSV seed files (the simulated dataset)
- **State does not persist across backend restarts** by default — acceptable for a POC/demo
- Optional convenience: serialize in-memory state to a JSON snapshot file on shutdown / periodic interval, purely so a demo session can be resumed without re-seeding — this is a demo convenience, not a persistence architecture, and should not be read as a production recommendation
- This keeps the POC fast to stand up and avoids committing to a database technology before the real build's data source is known (ties back to A5)

---

## 7. KPI Metrics (business-facing)

| Metric | Purpose |
|---|---|
| Renewal rate by milestone bucket | Where in the funnel are we winning/losing? |
| At-risk resolution rate | % of at-risk contracts that moved to renewed after an action |
| Revenue/value retained vs. lost | Ties directly to the client's stated success metric |
| Campaign response rate by campaign type | Which actions actually work? |
| Risk score distribution shift over time | Is the overall book getting healthier or riskier? |
| Regional / channel breakdown of all the above | Validates the "one engine, many regions" design |

---

## 8. LLM / Agent Metrics (operational)

| Metric | Purpose |
|---|---|
| First-pass evaluation pass rate | Quality signal — low rate means prompts need work |
| Average retries per recommendation | Cost and reliability signal |
| Escalation rate (% to human review) | How often the loop can't converge |
| Evaluation score by rubric criterion (groundedness, policy compliance, actionability, non-repetition, tone) | Diagnoses *which* quality dimension is weak |
| Latency per recommendation | Operational feasibility for daily batch |
| Token cost per recommendation | Ties to the cost estimate (~$0.005–$0.016/call, pre-optimization) |
| Evaluation score drift over time | Detects prompt or model degradation |

---

## 9. Success Metrics (for the POC itself)

Distinct from the KPI/LLM metrics above — these judge whether the **POC accomplished its purpose**, not whether the business outcome improved (which real outcome data can't yet confirm):

- End-to-end pipeline runs unattended across all three regions/channels (NATT, ETT, APAC TT) without manual intervention
- A sample of recommendations is reviewed and judged sensible/grounded by a subject-matter reviewer (qualitative check, since no real outcome data exists yet to measure against)
- Demo clearly demonstrates a **single engine** producing appropriately different, channel-aware recommendations — not three separate systems
- Full traceability is demonstrated: any recommendation can be traced back to its inputs, evaluation scores, and retry history
- Client stakeholders express confidence to proceed to a scoped real-data pilot

---

## 10. Additional Items

### Explicitly out of scope for the POC
- No real system integration (CRM/ERP/Snowflake connection) — simulated data only
- No authentication/security hardening
- No production deployment or infrastructure decisions (kept flexible per client direction)
- No automated campaign execution (email/call automation) — human-in-the-loop only

### Risks & limitations to state plainly
- Simulated data means KPI results in the demo are **illustrative, not predictive** — real model quality depends on real historical depth (A6)
- Using the same LLM family for both the recommendation and evaluation agents carries some risk of shared blind spots; using a different model/provider for evaluation is a future hardening option worth costing separately
- Dealer-channel data visibility (A12) is unresolved and could materially change the data model once real client data is seen

### Suggested build phases (high-level only, not a deployment plan)
1. Data simulator + in-memory schema
2. Milestone filter + agent graph (aggregator → recommend → evaluate) with a single region
3. Trace logging + metrics engine
4. Extend to all three regions/channels
5. Frontend screens wired to live in-memory API
6. Internal demo walkthrough / stakeholder review
