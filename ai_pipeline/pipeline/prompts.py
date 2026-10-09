"""
Prompts live in Amazon Bedrock Prompt Management, so wording can be changed (and versioned) without a deployment.
A prompt is a text (or chat) template with {{variable}} placeholders. This module fetches one, fills it in, and
shapes the request for the model; and turns the model's reply back into text or JSON.
"""
import json
import re
from dataclasses import dataclass, field

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class Prompt:
    job_type: str
    ref: dict                                  # {"id": ..., "version": ...} - recorded with every result
    user: str
    system: str | None = None
    model_id: str | None = None
    inference: dict = field(default_factory=dict)

    def render(self, variables: dict) -> tuple[str | None, str]:
        def fill(text):
            def sub(m):
                if m.group(1) not in variables:
                    raise KeyError(f"prompt {self.job_type!r} needs the variable {m.group(1)!r}")
                v = variables[m.group(1)]
                return v if isinstance(v, str) else json.dumps(v, indent=2)
            return _VAR.sub(sub, text)
        return (fill(self.system) if self.system else None), fill(self.user)

    def model_input(self, variables: dict) -> dict:
        """The request body for an Anthropic model on Bedrock (Messages API), the same for a batch record and a direct call."""
        system, user = self.render(variables)
        body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": int(self.inference.get("maxTokens", 1024)),
                "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}]}
        if system:
            body["system"] = system
        if "temperature" in self.inference:
            body["temperature"] = float(self.inference["temperature"])
        if "topP" in self.inference:
            body["top_p"] = float(self.inference["topP"])
        if self.inference.get("stopSequences"):
            body["stop_sequences"] = list(self.inference["stopSequences"])
        return body


class PromptBook:
    def __init__(self, agent_client, settings):
        self.agent, self.settings, self._cache = agent_client, settings, {}

    def get(self, job_type: str) -> Prompt:
        if job_type not in self._cache:
            ref = self.settings.prompts.get(job_type)
            if not ref or not ref.get("id"):
                raise KeyError(f"No prompt configured for {job_type!r} (the PROMPTS setting)")
            kw = {"promptIdentifier": ref["id"], **({"promptVersion": str(ref["version"])} if ref.get("version") else {})}
            resp = self.agent.get_prompt(**kw)
            variants = resp["variants"]
            variant = next((v for v in variants if v["name"] == resp.get("defaultVariant")), variants[0])
            cfg = variant["templateConfiguration"]
            system = None
            if "text" in cfg:
                user = cfg["text"]["text"]
            else:   # a chat template: system text plus the user message(s)
                chat = cfg["chat"]
                system = "\n".join(b["text"] for b in chat.get("system", []) if "text" in b) or None
                user = "\n".join(b["text"] for m in chat.get("messages", []) if m["role"] == "user" for b in m["content"] if "text" in b)
            self._cache[job_type] = Prompt(job_type, {"id": ref["id"], "version": resp.get("version") or ref.get("version")}, user, system,
                                           variant.get("modelId"), variant.get("inferenceConfiguration", {}).get("text", {}))
        return self._cache[job_type]


def output_text(model_output: dict) -> str:
    """The text of an Anthropic reply (any <think> block removed)."""
    text = "".join(b.get("text", "") for b in model_output.get("content", []) if b.get("type", "text") == "text")
    return _THINK.sub("", text).strip()


def output_json(model_output: dict) -> dict | None:
    """The JSON object in a reply, tolerating a code fence or a stray sentence around it; None if there isn't one."""
    text = output_text(model_output)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
