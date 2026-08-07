// Local proxy: normalizes between LLM providers so the React app
// never needs to know which one is behind it. It always speaks
// Anthropic's response shape ({content:[{type:"text",text}], usage:{input_tokens,output_tokens}})
// to the browser, regardless of which provider actually served the call.
//
// Switch providers via LLM_PROVIDER in .env: "anthropic" or "vllm".
import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import fetch from "node-fetch";

dotenv.config();
const app = express();
app.use(cors());
app.use(express.json());

const PROVIDER = process.env.LLM_PROVIDER || "anthropic";
const VLLM_BASE_URL = process.env.VLLM_BASE_URL || "http://localhost:8000";
const VLLM_MODEL = process.env.VLLM_MODEL || "";

app.get("/", (req, res) => {
  res.send(`This is the API proxy (port 3001), provider=${PROVIDER} — it only handles /api/messages. Open the actual app at http://localhost:5173`);
});

app.post("/api/messages", async (req, res) => {
  try {
    if (PROVIDER === "vllm") {
      return await handleVllm(req, res);
    }
    return await handleAnthropic(req, res);
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

async function handleAnthropic(req, res) {
  if (!process.env.ANTHROPIC_API_KEY) {
    return res.status(500).json({ error: "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, or set LLM_PROVIDER=vllm." });
  }
  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": process.env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify(req.body),
  });
  const data = await response.json();
  res.status(response.status).json(data);
}

async function handleVllm(req, res) {
  if (!VLLM_MODEL) {
    return res.status(500).json({ error: "VLLM_MODEL is not set in .env — set it to the model name you launched vLLM with." });
  }
  // req.body already arrives as { model, max_tokens, messages } from the
  // client — that shape is compatible with the OpenAI chat/completions API,
  // we just override "model" with the one vLLM actually has loaded.
  const openaiBody = {
    model: VLLM_MODEL,
    max_tokens: req.body.max_tokens,
    messages: req.body.messages,
  };

  const response = await fetch(`${VLLM_BASE_URL}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // vLLM's server ignores this unless you launched it with --api-key,
      // but sending a placeholder keeps the request shape consistent.
      "Authorization": `Bearer ${process.env.VLLM_API_KEY || "not-needed"}`,
    },
    body: JSON.stringify(openaiBody),
  });

  if (!response.ok) {
    const errText = await response.text();
    return res.status(response.status).json({ error: `vLLM server error: ${errText}` });
  }

  const data = await response.json();
  const choice = data.choices?.[0];
  const text = choice?.message?.content ?? "";

  // Normalize OpenAI-shaped response into the Anthropic shape the
  // frontend's callClaude() already expects, so App.jsx needs zero changes.
  res.json({
    content: [{ type: "text", text }],
    usage: {
      input_tokens: data.usage?.prompt_tokens ?? 0,
      output_tokens: data.usage?.completion_tokens ?? 0,
    },
  });
}

app.listen(3001, () => console.log(`Proxy server running on http://localhost:3001 (provider: ${PROVIDER})`));
