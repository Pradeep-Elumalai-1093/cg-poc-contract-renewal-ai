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
            result = await run_agent_graph(contract, prior)
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


# Serve the built frontend (npm run build in /frontend -> /frontend/dist) if
# present, so the whole app can run as this single FastAPI process. During
# development, run the Vite dev server separately instead (see README).
_dist_dir = Path(__file__).parent.parent / "frontend" / "dist"
if _dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="static")
