import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from state import state, CAMPAIGN_TAXONOMY, REGIONS, BUCKETS
from rules import MODEL_INFO, suggested_renewal_terms

app = FastAPI(title="Contract Renewal POC")

# Only needed while running the Vite dev server separately (port 5173).
# When the frontend is built and served from this same process, CORS is moot.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class FeedbackIn(BaseModel):
    contractId: str
    outcome: str | None = None
    note: str | None = None


class ActionStatusIn(BaseModel):
    contractId: str
    actionStatus: str  # "Action required" or "Action done"


class ContractIdIn(BaseModel):
    contractId: str


class CustomerIdIn(BaseModel):
    customerId: str


@app.get("/api/contracts")
def get_contracts():
    return state.contracts


@app.get("/api/trace")
def get_trace():
    return state.trace


@app.get("/api/campaigns")
def get_campaigns():
    summary = {t["name"]: {"assigned": 0, "engaged": 0, "declined": 0, "noResponse": 0} for t in CAMPAIGN_TAXONOMY}
    for record in state.trace:
        name = (record.get("recommendation") or {}).get("campaign")
        if name not in summary:
            continue
        summary[name]["assigned"] += 1
        outcome = record.get("outcome")
        if outcome == "Engaged":
            summary[name]["engaged"] += 1
        elif outcome == "Declined":
            summary[name]["declined"] += 1
        elif outcome == "No response":
            summary[name]["noResponse"] += 1
    return summary


@app.get("/api/metrics")
def get_metrics():
    runs = state.trace
    n = len(runs) or 1
    first_pass = sum(1 for r in runs if r["retryCount"] == 0 and r["pass"])
    avg_retries = sum(r["retryCount"] for r in runs) / n
    escalations = sum(1 for r in runs if r["escalated"])
    avg_latency = sum(r["latencyMs"] for r in runs) / n
    total_cost = sum(r["costUsd"] for r in runs)
    with_outcome = [r for r in runs if r.get("outcome")]
    engaged = sum(1 for r in with_outcome if r["outcome"] == "Engaged")

    return {
        "firstPassRate": round((first_pass / len(runs)) * 100) if runs else 0,
        "avgRetries": round(avg_retries, 2) if runs else 0.0,
        "escalationRate": round((escalations / len(runs)) * 100) if runs else 0,
        "avgLatency": round(avg_latency) if runs else 0,
        "totalCost": total_cost,
        "responseRate": round((engaged / len(with_outcome)) * 100) if with_outcome else None,
        "totalRuns": len(runs),
    }


@app.get("/api/batch/status")
def get_batch_status():
    return state.batch_status


@app.post("/api/batch/run")
async def run_batch():
    if state.batch_status["running"]:
        raise HTTPException(status_code=409, detail="A batch is already running.")
    due = state.due_contracts()
    state.batch_status = {"running": True, "done": 0, "total": len(due), "lastError": None}
    asyncio.create_task(_process_batch(due))
    return {"started": True, "due": len(due)}


# Per-entity locks so, if two contracts for the same customer are processed
# concurrently in the same batch, only one of them actually generates the
# shared customer summary instead of duplicating the LLM call.
_ticket_locks: dict[str, asyncio.Lock] = {}
_customer_locks: dict[str, asyncio.Lock] = {}


def _lock_for(store: dict, key: str) -> asyncio.Lock:
    if key not in store:
        store[key] = asyncio.Lock()
    return store[key]


async def _ensure_ticket_summary(contract: dict) -> str | None:
    """Returns the cached summary text, generating it first if missing.
    A prior manual 'Generate' click in the UI means this is a no-op here."""
    from agents import run_ticket_summary_agent

    async with _lock_for(_ticket_locks, contract["contractId"]):
        existing = state.ticket_summaries.get(contract["contractId"])
        if not existing or existing.get("status") != "done":
            state.ticket_summaries[contract["contractId"]] = {"status": "loading"}
            result = await run_ticket_summary_agent(contract)
            state.ticket_summaries[contract["contractId"]] = result
    record = state.ticket_summaries.get(contract["contractId"])
    return record["data"] if record and record.get("status") == "done" else None


async def _ensure_customer_summary(contract: dict) -> str | None:
    from agents import run_customer_summary_agent

    customer_id = contract["customerId"]
    async with _lock_for(_customer_locks, customer_id):
        existing = state.customer_summaries.get(customer_id)
        if not existing or existing.get("status") != "done":
            state.customer_summaries[customer_id] = {"status": "loading"}
            contracts = state.contracts_for_customer(customer_id)
            result = await run_customer_summary_agent(customer_id, contract["customerName"], contracts)
            state.customer_summaries[customer_id] = result
    record = state.customer_summaries.get(customer_id)
    return record["data"] if record and record.get("status") == "done" else None


async def _process_batch(due: list[dict]):
    from agents import run_agent_graph  # local import avoids a circular import at module load

    semaphore = asyncio.Semaphore(3)

    async def process(contract: dict):
        async with semaphore:
            prior = state.latest_trace_for(contract["contractId"])
            # Generate ticket/customer summaries now if they weren't already
            # produced ahead of time via the manual buttons — the batch
            # should never silently proceed with "Not yet generated."
            ticket_summary = await _ensure_ticket_summary(contract)
            customer_summary = await _ensure_customer_summary(contract)
            terms = suggested_renewal_terms(contract)

            result = await run_agent_graph(contract, prior, ticket_summary, customer_summary, terms)
            state.trace.append(result)
            if result["error"]:
                # Leave lastMilestoneProcessed untouched so this contract is
                # retried on the next batch run instead of being marked done.
                state.batch_status["lastError"] = result["errorMessage"]
            else:
                contract["lastMilestoneProcessed"] = contract["bucket"]
            state.batch_status["done"] += 1

    await asyncio.gather(*(process(c) for c in due))
    state.batch_status["running"] = False


@app.post("/api/feedback")
def post_feedback(body: FeedbackIn):
    record = state.latest_trace_for(body.contractId)
    if not record:
        raise HTTPException(status_code=404, detail="No trace record found for this contract yet.")
    if body.outcome is not None:
        record["outcome"] = body.outcome
    if body.note is not None:
        record["outcomeNote"] = body.note
    return record


@app.post("/api/action-status")
def post_action_status(body: ActionStatusIn):
    record = state.latest_trace_for(body.contractId)
    if not record:
        raise HTTPException(status_code=404, detail="No trace record found for this contract yet.")
    if body.actionStatus not in ("Action required", "Action done"):
        raise HTTPException(status_code=400, detail="actionStatus must be 'Action required' or 'Action done'.")
    record["actionStatus"] = body.actionStatus
    return record


@app.post("/api/reset")
def reset_state():
    state.reset()
    return {"reset": True, "contracts": len(state.contracts)}


@app.get("/api/model-info")
def get_model_info():
    return MODEL_INFO


def _aggregate(contracts: list[dict]) -> dict:
    n = len(contracts) or 1
    customer_ids = {c["customerId"] for c in contracts}
    segment_counts = {"High Risk": 0, "At Risk": 0, "Healthy": 0, "Standard": 0}
    bucket_counts = {b: 0 for b in BUCKETS}
    for c in contracts:
        segment_counts[c["segment"]] = segment_counts.get(c["segment"], 0) + 1
        bucket_counts[c["bucket"]] = bucket_counts.get(c["bucket"], 0) + 1
    return {
        "customerCount": len(customer_ids),
        "contractCount": len(contracts),
        "totalValue": sum(c["contractValue"] for c in contracts),
        "totalMargin": sum(c["margin"] for c in contracts),
        "avgRiskScore": round(sum(c["riskScore"] for c in contracts) / n, 1) if contracts else 0,
        "segmentCounts": segment_counts,
        "bucketCounts": bucket_counts,
    }


@app.get("/api/region-summary")
def get_region_summary():
    global_summary = _aggregate(state.contracts)
    region_summaries = {}
    for region_id, meta in REGIONS.items():
        region_contracts = [c for c in state.contracts if c["region"] == region_id]
        region_summaries[region_id] = {"label": meta["label"], "channels": meta["channels"], **_aggregate(region_contracts)}
    return {"global": global_summary, "regions": region_summaries}


@app.get("/api/ticket-summaries")
def get_ticket_summaries():
    return state.ticket_summaries


@app.post("/api/ticket-summaries/run")
async def run_ticket_summary(body: ContractIdIn):
    from agents import run_ticket_summary_agent

    contract = state.contract_by_id(body.contractId)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found.")
    state.ticket_summaries[body.contractId] = {"status": "loading"}
    result = await run_ticket_summary_agent(contract)
    state.ticket_summaries[body.contractId] = result
    return result


@app.get("/api/customer-summaries")
def get_customer_summaries():
    return state.customer_summaries


@app.post("/api/customer-summaries/run")
async def run_customer_summary(body: CustomerIdIn):
    from agents import run_customer_summary_agent

    contracts = state.contracts_for_customer(body.customerId)
    if not contracts:
        raise HTTPException(status_code=404, detail="Customer not found.")
    customer_name = contracts[0]["customerName"]
    state.customer_summaries[body.customerId] = {"status": "loading"}
    result = await run_customer_summary_agent(body.customerId, customer_name, contracts)
    state.customer_summaries[body.customerId] = result
    return result


@app.get("/api/outcome-by-risk-bucket")
def get_outcome_by_risk_bucket():
    """Live proxy for a backtest: buckets logged campaign outcomes by the
    risk score at the time of the recommendation. This is NOT a validated
    renewal-outcome backtest (we don't have historical renewal ground truth)
    — it's an honest, live signal of whether risk ranking correlates with
    campaign engagement, and it's labeled as such in the UI."""
    bucket_edges = [(0, 19), (20, 39), (40, 59), (60, 79), (80, 100)]
    labels = ["0-19", "20-39", "40-59", "60-79", "80-100"]
    engaged = [0] * 5
    not_engaged = [0] * 5  # Declined + No response
    total_with_outcome = 0

    for record in state.trace:
        outcome = record.get("outcome")
        if not outcome:
            continue
        score = (record.get("context") or {}).get("risk_score")
        if score is None:
            continue
        total_with_outcome += 1
        for i, (lo, hi) in enumerate(bucket_edges):
            if lo <= score <= hi:
                if outcome == "Engaged":
                    engaged[i] += 1
                else:
                    not_engaged[i] += 1
                break

    return {
        "buckets": labels,
        "engaged": engaged,
        "notEngaged": not_engaged,
        "totalWithOutcome": total_with_outcome,
    }


# Serve the built frontend (npm run build in /frontend -> /frontend/dist) if
# present, so the whole app can run as this single FastAPI process. During
# development, run the Vite dev server separately instead (see README).
_dist_dir = Path(__file__).parent.parent / "frontend" / "dist"
if _dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="static")
