import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from state import state, CAMPAIGN_TAXONOMY
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


async def _process_batch(due: list[dict]):
    from agents import run_agent_graph  # local import avoids a circular import at module load

    semaphore = asyncio.Semaphore(3)

    async def process(contract: dict):
        async with semaphore:
            prior = state.latest_trace_for(contract["contractId"])
            ticket_summary_record = state.ticket_summaries.get(contract["contractId"])
            ticket_summary = ticket_summary_record["data"] if ticket_summary_record and ticket_summary_record["status"] == "done" else None
            customer_summary_record = state.customer_summaries.get(contract["customerId"])
            customer_summary = customer_summary_record["data"] if customer_summary_record and customer_summary_record["status"] == "done" else None
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
