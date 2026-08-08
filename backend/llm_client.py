"""
Thin, swappable LLM client. Same idea as the earlier Node proxy, now living
directly in the Python backend since the agent graph runs here too.
Switch providers via LLM_PROVIDER in .env: "anthropic" or "vllm".
"""
import os
import time
import json
import re
import httpx

LLM_TIMEOUT_SECONDS = 60.0


class LLMError(Exception):
    pass


def _clean_json(raw: str) -> dict | None:
    # Reasoning models (Qwen3-Instruct and similar served through vLLM) emit
    # a <think>...</think> block ahead of the actual answer even when told to
    # respond with only JSON — that instruction governs the final answer, not
    # the reasoning trace. Strip it before attempting to parse.
    without_think = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"```json|```", "", without_think).strip()

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: some models still wrap the JSON in extra prose despite
    # instructions not to. Extract the outermost {...} block and try that.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


async def call_llm(prompt_text: str) -> dict:
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    started = time.perf_counter()

    try:
        if provider == "vllm":
            raw, input_tokens, output_tokens = await _call_vllm(prompt_text)
        else:
            raw, input_tokens, output_tokens = await _call_anthropic(prompt_text)
    except httpx.TimeoutException:
        raise LLMError(
            f"LLM call timed out after {LLM_TIMEOUT_SECONDS:.0f}s. "
            "If you're using vLLM, check the model is loaded and VLLM_BASE_URL is reachable."
        )
    except httpx.RequestError as err:
        raise LLMError(f"Could not reach the LLM provider ({provider}): {err}")

    latency_ms = round((time.perf_counter() - started) * 1000)
    if not raw:
        raise LLMError(
            "LLM response had no text content — check VLLM_MODEL exactly matches "
            "the model vLLM was launched with, or check your Anthropic API key."
        )

    return {
        "parsed": _clean_json(raw),
        "raw": raw,
        "latencyMs": latency_ms,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
    }


async def _call_anthropic(prompt_text: str) -> tuple[str, int, int]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, or set LLM_PROVIDER=vllm.")

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt_text}],
            },
        )
    if response.status_code >= 400:
        raise LLMError(f"Anthropic API error (HTTP {response.status_code}): {response.text[:300]}")
    data = response.json()
    text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
    raw = text_block["text"] if text_block else ""
    usage = data.get("usage", {})
    return raw, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


async def _call_vllm(prompt_text: str) -> tuple[str, int, int]:
    base_url = os.environ.get("VLLM_BASE_URL", "http://localhost:8000")
    model = os.environ.get("VLLM_MODEL", "")
    if not model:
        raise LLMError("VLLM_MODEL is not set in .env — set it to the model name you launched vLLM with.")
    api_key = os.environ.get("VLLM_API_KEY", "not-needed")

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt_text}],
            },
        )
    if response.status_code >= 400:
        raise LLMError(f"vLLM server error (HTTP {response.status_code}): {response.text[:300]}")
    data = response.json()
    choices = data.get("choices", [])
    text = choices[0]["message"]["content"] if choices else ""
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
