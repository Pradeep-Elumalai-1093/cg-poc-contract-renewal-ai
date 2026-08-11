"""
Agent graph: aggregator -> recommendation agent -> evaluation agent ->
(retry up to MAX_RETRIES | pass | escalate) -> content agent.

Two additional agents (ticket summary, customer summary) are NOT part of
this per-milestone graph — they run on demand, cached by contract/customer
id, and their cached output (if present) is fed into the aggregator context
here. This keeps their cost out of the daily batch entirely.
"""
import json
import uuid
from datetime import datetime, timezone

from state import CAMPAIGN_TAXONOMY
from rules import PRODUCT_CATALOG
from llm_client import call_llm, LLMError, remove_think

MAX_RETRIES = 0
COMPOSITE_PASS = 7
POLICY_FLOOR = 6
COST_PER_M_INPUT = 3.0
COST_PER_M_OUTPUT = 15.0

EVAL_CRITERIA = ["groundedness", "policy_compliance", "actionability", "non_repetition", "tone", "upsell_relevance"]


def build_aggregator_context(
    contract: dict,
    prior_trace: dict | None,
    ticket_summary: str | None,
    customer_summary: str | None,
    suggested_terms: dict,
) -> dict:
    catalog_entry = PRODUCT_CATALOG.get(contract["equipment"]["type"])
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
        "milestone": contract["bucket"],
        "risk_score": contract["riskScore"],
        "risk_factors": contract["riskFactors"],
        "segment": contract["segment"],
        "equipment": contract["equipment"],
        "product_catalog_entry": catalog_entry,
        "suggested_renewal_terms": suggested_terms,
        "ticket_summary": ticket_summary or "Not yet generated.",
        "customer_summary": customer_summary or "Not yet generated.",
        "customer_feedback": _condense_feedback(contract),
        "prior_milestone_action": (prior_trace or {}).get("recommendation", {}).get("campaign") if prior_trace else None,
        "prior_milestone_outcome": (prior_trace.get("outcome") or "not yet recorded") if prior_trace else None,
    }


def _condense_feedback(contract: dict) -> dict:
    """Trims raw feedback entries down to what the recommendation/content
    prompts actually need \u2014 sentiment, category, and the verbatim comment
    (the part that makes a reply feel personalized) \u2014 plus a one-word trend
    and a lightweight historical summary, rather than the full dated record."""
    feedback = contract.get("customerFeedback") or {"recent12Months": [], "historical": []}
    recent = feedback.get("recent12Months", [])
    historical = feedback.get("historical", [])
    return {
        "trend": contract.get("feedbackTrend", "No feedback on record"),
        "recent_12_months": [
            {"sentiment": e["sentiment"], "category": e["category"], "comment": e["comment"]}
            for e in recent
        ],
        "historical_summary": (
            f"{len(historical)} feedback entries older than 12 months on record"
            if historical else "No feedback older than 12 months on record"
        ),
    }


def recommendation_prompt(ctx: dict, prior_feedback: str | None) -> str:
    taxonomy_names = " | ".join(t["name"] for t in CAMPAIGN_TAXONOMY)
    feedback_line = f"Your previous attempt was rejected by QA for this reason, revise accordingly: {prior_feedback}" if prior_feedback else ""
    catalog = ctx.get("product_catalog_entry") or {}
    upgrade_path = catalog.get("upgradePath", "none available")
    return f"""You are the Deal Guidance Agent for a B2B truck/trailer HVAC service contract renewal system.
Choose exactly ONE retention action from this taxonomy: {taxonomy_names}.
If channel is "Dealer", execution_owner must be "Dealer". If channel is "Direct", execution_owner must be "Direct Sales Rep".
Do not repeat an action that was already tried at a prior milestone for this same contract if it did not work.
Use the ticket_summary and customer_summary in the context (if present) to ground your rationale in the account's actual history, not just the raw numbers.
The context also includes customer_feedback (recent_12_months verbatim comments, a sentiment trend, and a historical_summary) \u2014 this is customer sentiment data, separate from any QA retry feedback below. If a customer explicitly praised or complained about something specific, let that shape your rationale naturally rather than writing something generic \u2014 e.g. don't recommend a pricing conversation if their recent feedback was specifically about responsiveness.
Only if genuinely relevant to the equipment and risk profile, suggest ONE upsell tied to this product catalog upgrade path: "{upgrade_path}". If it doesn't fit, say so plainly rather than forcing one.
{feedback_line}

Customer-contract context (JSON):
{json.dumps(ctx, indent=2)}

Respond with ONLY valid JSON, no markdown fences, no preamble:
{{"action": "<short action name>", "campaign": "<one taxonomy name exactly>", "execution_owner": "Dealer or Direct Sales Rep", "rationale": "<2-3 sentences, grounded only in the context above>", "upsell": "<one sentence tied to the catalog upgrade path, or 'Not recommended for this account right now'>", "confidence": <0 to 1 number>}}"""


def evaluation_prompt(ctx: dict, recommendation: dict) -> str:
    return f"""You are a QA evaluator scoring a retention recommendation before it reaches a sales rep.
Score each criterion 0-10 based on the context and recommendation below.
- groundedness: does the rationale only reference facts present in the context (including ticket_summary/customer_summary if present), no invented details?
- policy_compliance: is the campaign exactly one of the approved taxonomy names, and is execution_owner correct for the channel?
- actionability: concrete enough for a rep to act on today, not vague?
- non_repetition: does it avoid repeating the prior_milestone_action if that action's outcome was not positive?
- tone: professional, no overpromising?
- upsell_relevance: is the upsell suggestion (if any) actually grounded in the product_catalog_entry, or a reasonable "not recommended" if it doesn't fit?

Context:
{json.dumps(ctx, indent=2)}

Recommendation:
{json.dumps(recommendation, indent=2)}

Respond with ONLY valid JSON, no markdown fences, no preamble:
{{"scores": {{"groundedness": <0-10>, "policy_compliance": <0-10>, "actionability": <0-10>, "non_repetition": <0-10>, "tone": <0-10>, "upsell_relevance": <0-10>}}, "notes": "<one short sentence>"}}"""


def content_prompt(ctx: dict, recommendation: dict) -> str:
    if ctx["channel"] == "Direct":
        recipient_guidance = (
            'This is a "Direct" channel customer — the email is addressed directly to the fleet operator/customer contact. '
            'recipient_role should be "Customer".'
        )
    else:
        recipient_guidance = (
            'This is a "Dealer" channel customer — the sales rep does not have a direct relationship with the end customer. '
            'The email is addressed to the dealer contact, asking them to reach out to their end customer with this recommendation. '
            'recipient_role should be "Dealer".'
        )
    terms = ctx.get("suggested_renewal_terms", {})
    feedback = ctx.get("customer_feedback", {})
    recent_comments = feedback.get("recent_12_months", [])
    feedback_guidance = (
        "The context includes customer_feedback.recent_12_months \u2014 actual verbatim comments from this customer. "
        "Weave in a natural acknowledgment of the most relevant one if it fits (e.g. thank them for positive feedback, "
        "or acknowledge a specific complaint before pivoting to the offer) so the email reads as written for this "
        "customer specifically, not a template. Don't quote a comment word-for-word or reference the survey mechanics \u2014 "
        "paraphrase naturally, the way a rep who actually read the feedback would."
        if recent_comments else
        "No recent customer feedback is on record for this contract \u2014 don't reference feedback that doesn't exist."
    )
    return f"""You are the Renewal Document Agent, drafting outreach content for a sales rep based on an approved retention recommendation.
{recipient_guidance}
Reference the suggested renewal terms naturally in the email: a {terms.get('priceMovePct', 0)}% price move and a {terms.get('term', '12-month')} term.
{feedback_guidance}
Keep the email concise (under 150 words), professional, and specific to this contract — reference real details from the context, do not invent any.
Match tone to urgency: a >45-day milestone should read as a routine check-in; a <=30-day milestone should convey more urgency without being alarmist.

Context:
{json.dumps(ctx, indent=2)}

Approved recommendation:
{json.dumps(recommendation, indent=2)}

Respond with ONLY valid JSON, no markdown fences, no preamble:
{{"summary": "<2-3 sentence internal summary of this customer situation for the rep, not customer-facing>", "recipient_role": "Customer or Dealer", "email_subject": "<short subject line>", "email_body": "<the full email body, plain text, no markdown>"}}"""


def ticket_summary_prompt(contract: dict) -> str:
    tickets = contract.get("serviceTickets", [])
    return f"""You are the Service Ticket Summary Agent. Given this customer-contract's service ticket history, write a concise 2-3 sentence summary covering: overall pattern (recurring issues, SLA performance), and anything notable an account manager should know before deciding on a retention action. Plain prose, no headers, no bullet points, no markdown.

Equipment: {contract['equipment']['type']}, {contract['equipment']['count']} units, average age {contract['equipment']['avgAgeYears']} years.

Service tickets (JSON, most recent first):
{json.dumps(tickets, indent=2)}

Respond with plain text only — the summary itself, nothing else."""


def customer_summary_prompt(customer_id: str, customer_name: str, contracts: list[dict]) -> str:
    summary_input = [{
        "contract_id": c["contractId"],
        "region": c["region"],
        "channel": c["channel"],
        "months_on_book": c["monthsOnBook"],
        "contract_value_usd": c["contractValue"],
        "margin_usd": c["margin"],
        "risk_score": c["riskScore"],
        "segment": c["segment"],
        "milestone": c["bucket"],
        "equipment_type": c["equipment"]["type"],
    } for c in contracts]
    return f"""You are the Customer Summary Agent. Given all of this customer's contracts, write a concise 2-3 sentence executive summary of the overall relationship: total scope, general health across contracts, and anything an account manager should keep in mind across the whole relationship (not just one contract). Plain prose, no headers, no bullet points, no markdown.

Customer: {customer_name} ({customer_id}), {len(contracts)} contract(s).

Contracts (JSON):
{json.dumps(summary_input, indent=2)}

Respond with plain text only — the summary itself, nothing else."""


async def run_ticket_summary_agent(contract: dict) -> dict:
    prompt = ticket_summary_prompt(contract)
    try:
        res = await call_llm(prompt)
        text = (res["raw"] or "").strip()
        if not text:
            return {"status": "error", "error": "Empty response from model.", "prompt": prompt}
        return {"status": "done", "data": remove_think(text), "prompt": prompt, "raw": res["raw"], "latencyMs": res["latencyMs"]}
    except LLMError as err:
        return {"status": "error", "error": str(err), "prompt": prompt}


async def run_customer_summary_agent(customer_id: str, customer_name: str, contracts: list[dict]) -> dict:
    prompt = customer_summary_prompt(customer_id, customer_name, contracts)
    try:
        res = await call_llm(prompt)
        text = (res["raw"] or "").strip()
        if not text:
            return {"status": "error", "error": "Empty response from model.", "prompt": prompt}
        return {"status": "done", "data": remove_think(text), "prompt": prompt, "raw": res["raw"], "latencyMs": res["latencyMs"]}
    except LLMError as err:
        return {"status": "error", "error": str(err), "prompt": prompt}


async def run_agent_graph(
    contract: dict,
    prior_trace: dict | None,
    ticket_summary: str | None,
    customer_summary: str | None,
    suggested_terms: dict,
) -> dict:
    ctx = build_aggregator_context(contract, prior_trace, ticket_summary, customer_summary, suggested_terms)
    retry_count = 0
    prior_feedback = None
    total_input_tokens = total_output_tokens = total_latency = 0
    last_recommendation = last_evaluation = None
    escalated = passed = False
    attempts: list[dict] = []

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
            "content": None,
            "attempts": attempts,
            "retryCount": min(retry_count, MAX_RETRIES),
            "escalated": escalated,
            "pass": passed,
            "error": False,
            "errorMessage": None,
            "actionStatus": "Action required",
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
            rec_prompt = recommendation_prompt(ctx, prior_feedback)
            rec_res = await call_llm(rec_prompt)
            total_input_tokens += rec_res["inputTokens"]
            total_output_tokens += rec_res["outputTokens"]
            total_latency += rec_res["latencyMs"]
            recommendation = rec_res["parsed"] or {
                "action": "Unparsed", "campaign": "Personal outreach call",
                "execution_owner": "Dealer", "rationale": "Model response could not be parsed.",
                "upsell": "Not recommended for this account right now", "confidence": 0,
            }
            last_recommendation = recommendation

            eval_prompt = evaluation_prompt(ctx, recommendation)
            eval_res = await call_llm(eval_prompt)
            total_input_tokens += eval_res["inputTokens"]
            total_output_tokens += eval_res["outputTokens"]
            total_latency += eval_res["latencyMs"]
            evaluation = eval_res["parsed"] or {
                "scores": {k: 0 for k in EVAL_CRITERIA},
                "notes": "Model response could not be parsed.",
            }
            last_evaluation = evaluation

            scores = evaluation.get("scores", {})
            vals = [float(scores.get(k, 0) or 0) for k in EVAL_CRITERIA]
            composite = sum(vals) / len(vals)
            policy_ok = float(scores.get("policy_compliance", 0) or 0) >= POLICY_FLOOR
            passed = policy_ok and composite >= COMPOSITE_PASS
            evaluation["composite"] = round(composite, 1)
            evaluation["pass"] = passed

            attempts.append({
                "attemptNumber": retry_count + 1,
                "recommendationPrompt": rec_prompt,
                "recommendationRaw": rec_res["raw"],
                "recommendationLatencyMs": rec_res["latencyMs"],
                "evaluationPrompt": eval_prompt,
                "evaluationRaw": eval_res["raw"],
                "evaluationLatencyMs": eval_res["latencyMs"],
                "passed": passed,
            })

            if passed:
                break
            retry_count += 1
            prior_feedback = evaluation.get("notes") or "Composite or policy-compliance score too low."
            if retry_count > MAX_RETRIES:
                escalated = True
                break

    except LLMError as err:
        return base_record(**{"error": True, "errorMessage": str(err), "escalated": False, "pass": False})

    content = None
    content_error = None
    if last_recommendation:
        try:
            c_prompt = content_prompt(ctx, last_recommendation)
            c_res = await call_llm(c_prompt)
            total_input_tokens += c_res["inputTokens"]
            total_output_tokens += c_res["outputTokens"]
            total_latency += c_res["latencyMs"]
            content = c_res["parsed"]
            if content:
                content["prompt"] = c_prompt
                content["raw"] = c_res["raw"]
        except LLMError as err:
            content_error = str(err)

    suggested_actions = None
    if escalated:
        suggested_actions = _build_suggested_actions(last_evaluation)

    return base_record(
        content=content,
        contentError=content_error,
        suggestedActions=suggested_actions,
        inputTokens=total_input_tokens,
        outputTokens=total_output_tokens,
        latencyMs=total_latency,
    )


def _build_suggested_actions(evaluation: dict | None) -> list[str]:
    actions = ["Manually review the draft content below before sending \u2014 automated evaluation could not reach a passing score after the retry limit."]
    scores = (evaluation or {}).get("scores", {})
    if float(scores.get("policy_compliance", 10) or 0) < POLICY_FLOOR:
        actions.append("Check the recommended action against policy manually \u2014 the automated check flagged a compliance concern.")
    if float(scores.get("groundedness", 10) or 0) < 6:
        actions.append("Verify the rationale against the customer's actual contract data before relying on it.")
    if float(scores.get("actionability", 10) or 0) < 6:
        actions.append("Add concrete next steps yourself \u2014 the recommendation may be too vague to act on directly.")
    actions.append("If still uncertain, escalate to the account manager per the standard action taxonomy.")
    return actions
