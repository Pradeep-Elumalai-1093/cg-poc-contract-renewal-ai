# Contract Renewal POC — run locally

## 1. Install dependencies
    npm install

## 2. Configure your LLM provider
    cp .env.example .env

Edit `.env`:
- For **vLLM (local model)**: set `LLM_PROVIDER=vllm`, and set `VLLM_MODEL` to match
  exactly what you launched vLLM with, e.g.:

      python -m vllm.entrypoints.openai.api_server \
        --model meta-llama/Llama-3.1-8B-Instruct \
        --port 8000

  Make sure that server is running and reachable at `VLLM_BASE_URL` (default
  `http://localhost:8000`) before you start the app.

- For **Claude API**: set `LLM_PROVIDER=anthropic` and fill in `ANTHROPIC_API_KEY`.

## 3. Start the proxy and the app
    npm run dev

This runs:
- the Express proxy on http://localhost:3001 (talks to whichever provider you configured)
- the Vite dev server on http://localhost:5173 (the actual app)

## 4. Open the app
Visit http://localhost:5173 and click "Run daily batch."

## Notes
- All data is in-memory only — refreshing the page resets everything.
- The React app (src/App.jsx) never changes between providers — it always
  talks to the proxy at `/api/messages`, and the proxy normalizes vLLM's
  OpenAI-style response into the same shape the app already expects.
- Smaller/local models may be less reliable at returning strict JSON than
  Claude. If you see a lot of "Unparsed" recommendations or evaluation
  failures in the Trace tab, that's usually the model not following the
  JSON-only instruction — worth checking your model's instruction-following
  strength before trusting the retry/escalation metrics.
- To switch providers later, just edit `.env` and restart `npm run dev` —
  no code changes needed.
