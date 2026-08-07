# Contract Renewal POC — run locally

## 1. Install dependencies
    npm install

## 2. Add your Anthropic API key
    cp .env.example .env
    # then edit .env and paste your real key (get one at console.anthropic.com)

## 3. Start both the proxy server and the app
    npm run dev

This runs:
- the Express proxy on http://localhost:3001 (holds your API key, never exposed to the browser)
- the Vite dev server on http://localhost:5173 (the actual app)

## 4. Open the app
Visit http://localhost:5173 in your browser and click "Run daily batch."

## Notes
- All data is in-memory only, per the POC design — refreshing the page resets everything.
- If you see an error about ANTHROPIC_API_KEY, double check step 2.
- Model used is claude-sonnet-4-6; change it in src/App.jsx (callClaude function) if you want a different model.
