# Contract Renewal POC — FastAPI + React

## Architecture
- **backend/** (FastAPI, Python) — owns all data (in-memory, no database),
  runs the agent graph (aggregator → recommendation → evaluation → retry/escalate)
  as a background task, and serves REST endpoints.
- **frontend/** (React, Vite) — a thin display/trigger layer. It fetches
  contracts/trace/metrics/campaigns, triggers the daily batch, polls its
  status, and logs rep feedback. It contains no simulation logic and no
  LLM calls of its own.

## 1. Backend setup
    cd backend
    python3 -m venv .venv && source .venv/bin/activate   # or your preferred env tool
    pip install -r requirements.txt
    cp .env.example .env

Edit `.env`:
- **vLLM (local model):** `LLM_PROVIDER=vllm`, `VLLM_MODEL=<exact model name>`,
  `VLLM_BASE_URL=http://localhost:8000` (or wherever vLLM is running). Launch
  vLLM separately first:

      python -m vllm.entrypoints.openai.api_server --model <your-model> --port 8000

- **Claude API:** `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=<your key>`

Start the backend (note: port 8080, not 8000 — that's usually vLLM's port):

    uvicorn main:app --reload --port 8080

## 2. Frontend setup (separate terminal)
    cd frontend
    npm install
    npm run dev

Open **http://localhost:5173**. The Vite dev server proxies `/api/*` to the
FastAPI backend on port 8080.

## 3. (Optional) Run as a single process
For a demo-ready single process instead of two dev servers:

    cd frontend && npm run build     # outputs to frontend/dist
    cd ../backend && uvicorn main:app --port 8080

FastAPI detects `frontend/dist` and serves the built app directly from the
same process at http://localhost:8080 — no Vite dev server needed.

## How the pieces fit together
- `POST /api/batch/run` starts the agent graph for all due contract-milestones
  as an `asyncio` background task (concurrency-limited to 3) and returns
  immediately.
- The frontend polls `GET /api/batch/status` every 700ms while running, then
  refetches contracts/trace/metrics/campaigns once it finishes.
- A failed LLM call produces a trace record with `error: true` rather than
  silently disappearing — visible in the Trace tab, and the contract stays in
  the due queue for the next run.
- Switching LLM providers is a `.env` change only (`LLM_PROVIDER`) — no code
  changes on either side.

## Notes
- All state resets when the backend process restarts (`POST /api/reset` does
  this on demand too, e.g. for a clean demo run).
- Smaller/local models may not follow the "return ONLY JSON" instruction as
  reliably as Claude — check the Trace tab for "Unparsed" recommendations or
  a high error rate if you switch to vLLM with a small model.
- Reasoning models served through vLLM (e.g. Qwen3-Instruct) emit a
  `<think>...</think>` block before their actual answer, even when told to
  respond with only JSON — that instruction governs the final answer, not
  the reasoning trace. `llm_client.py` strips `<think>` blocks and falls
  back to extracting the outermost `{...}` object if needed, so this is
  handled automatically.
