"""
Agent graph: aggregator -> recommendation agent -> evaluation agent ->
(retry up to MAX_RETRIES | pass | escalate). Policy-compliance failure is a
hard fail regardless of composite score, per the confirmed design decision.
"""
import time
import uuid
from datetime import datetime, timezone

from state import CAMPAIGN_TAXONOMY
from llm_client import call_llm, LLMError

MAX_RETRIES = 2
COMPOSITE_PASS = 7
POLICY_FLOOR = 6
COST_PER_M_INPUT = 3.0
COST_PER_M_OUTPUT = 15.0


def build_aggregator_context(contract: dict, prior_trace: dict | None) -> dict:
    return {
        "customer_id": contract["customerId"],
        "customer_name": contract["customerName"],
        "region": contract["region"],
        "channel": contract["channel"],
        "dealer_id": contract["dealerId"],
        "months_on_book": contract["monthsOnBook"],
        "contract_value_usd": contract["contractValue"],
        "margin_usd": contract["margin"],
        "payment_lag_days": contract["paymentLagDays"],
        "complaint_count_last_year": contract["complaintCount"],
        "service_call_trend": contract["serviceTrend"],
        "milestone": contract["bucket"],
        "risk_score": contract["riskScore"],
        "prior_milestone_action": (prior_trace or {}).get("recommendation", {}).get("campaign") if prior_trace else None,
        "prior_milestone_outcome": (prior_trace.get("outcome") or "not yet recorded") if prior_trace else None,
    }


def recommendation_prompt(ctx: dict, prior_feedback: str | None) -> str:
    taxonomy_names = " | ".join(t["name"] for t in CAMPAIGN_TAXONOMY)
    feedback_line = f"Your previous attempt was rejected by QA for this reason, revise accordingly: {prior_feedback}" if prior_feedback else ""
    import json
    return f"""You are a retention recommendation engine for a B2B truck/trailer HVAC service contract renewal system.
Choose exactly ONE action from this taxonomy: {taxonomy_names}.
If channel is "Dealer", execution_owner must be "Dealer". If channel is "Direct", execution_owner must be "Direct Sales Rep".
Do not repeat an action that was already tried at a prior milestone for this same contract if it did not work.
{feedback_line}

Customer-contract context (JSON):
{json.dumps(ctx, indent=2)}

Respond with ONLY valid JSON, no markdown fences, no preamble:
{{"action": "<short action name>", "campaign": "<one taxonomy name exactly>", "execution_owner": "Dealer or Direct Sales Rep", "rationale": "<2-3 sentences, grounded only in the context above>", "confidence": <0 to 1 number>}}"""


def evaluation_prompt(ctx: dict, recommendation: dict) -> str:
    import json
    return f"""You are a QA evaluator scoring a retention recommendation before it reaches a sales rep.
Score each criterion 0-10 based on the context and recommendation below.
- groundedness: does the rationale only reference facts present in the context, no invented details?
- policy_compliance: is the campaign exactly one of the approved taxonomy names, and is execution_owner correct for the channel?
- actionability: concrete enough for a rep to act on today, not vague?
- non_repetition: does it avoid repeating the prior_milestone_action if that action's outcome was not positive?
- tone: professional, no overpromising?

Context:
{json.dumps(ctx, indent=2)}

Recommendation:
{json.dumps(recommendation, indent=2)}

Respond with ONLY valid JSON, no markdown fences, no preamble:
{{"scores": {{"groundedness": <0-10>, "policy_compliance": <0-10>, "actionability": <0-10>, "non_repetition": <0-10>, "tone": <0-10>}}, "notes": "<one short sentence>"}}"""


async def run_agent_graph(contract: dict, prior_trace: dict | None) -> dict:
    ctx = build_aggregator_context(contract, prior_trace)
    retry_count = 0
    prior_feedback = None
    total_input_tokens = total_output_tokens = total_latency = 0
    last_recommendation = last_evaluation = None
    escalated = passed = False

    def base_record(**overrides) -> dict:
        record = {
            "runId": f"RUN-{contract['contractId']}-{contract['bucket']}-{uuid.uuid4().hex[:8]}",
            "contractId": contract["contractId"],
            "customerId": contract["customerId"],
            "milestone": contract["bucket"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": ctx,
            "recommendation": last_recommendation,
            "evaluation": last_evaluation,
            "retryCount": min(retry_count, MAX_RETRIES),
            "escalated": escalated,
            "pass": passed,
            "error": False,
            "errorMessage": None,
            "latencyMs": total_latency,
            "inputTokens": total_input_tokens,
            "outputTokens": total_output_tokens,
            "costUsd": (total_input_tokens / 1e6) * COST_PER_M_INPUT + (total_output_tokens / 1e6) * COST_PER_M_OUTPUT,
            "outcome": None,
            "outcomeNote": "",
        }
        record.update(overrides)
        return record

    try:
        while retry_count <= MAX_RETRIES:
            rec_res = await call_llm(recommendation_prompt(ctx, prior_feedback))
            total_input_tokens += rec_res["inputTokens"]
            total_output_tokens += rec_res["outputTokens"]
            total_latency += rec_res["latencyMs"]
            recommendation = rec_res["parsed"] or {
                "action": "Unparsed", "campaign": "Personal outreach call",
                "execution_owner": "Dealer", "rationale": "Model response could not be parsed.", "confidence": 0,
            }
            last_recommendation = recommendation

            eval_res = await call_llm(evaluation_prompt(ctx, recommendation))
            total_input_tokens += eval_res["inputTokens"]
            total_output_tokens += eval_res["outputTokens"]
            total_latency += eval_res["latencyMs"]
            evaluation = eval_res["parsed"] or {
                "scores": {"groundedness": 0, "policy_compliance": 0, "actionability": 0, "non_repetition": 0, "tone": 0},
                "notes": "Model response could not be parsed.",
            }
            last_evaluation = evaluation

            scores = evaluation.get("scores", {})
            vals = [float(scores.get(k, 0) or 0) for k in ["groundedness", "policy_compliance", "actionability", "non_repetition", "tone"]]
            composite = sum(vals) / len(vals)
            policy_ok = float(scores.get("policy_compliance", 0) or 0) >= POLICY_FLOOR
            passed = policy_ok and composite >= COMPOSITE_PASS
            evaluation["composite"] = round(composite, 1)
            evaluation["pass"] = passed

            if passed:
                break
            retry_count += 1
            prior_feedback = evaluation.get("notes") or "Composite or policy-compliance score too low."
            if retry_count > MAX_RETRIES:
                escalated = True
                break

    except LLMError as err:
        # A failed LLM call still produces a visible trace record (shown as
        # "Error" in the UI) instead of the contract silently vanishing.
        return base_record(**{"error": True, "errorMessage": str(err), "escalated": False, "pass": False})

    return base_record()
