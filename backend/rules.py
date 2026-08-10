"""
Rule-based logic — deliberately kept separate from agents.py (which holds
LLM calls). Nothing in this file makes a network call; everything here is
deterministic and instant, which is what a risk score and a segmentation
label need to be.

Per the agreed framing: this is a rule-based weighted scorecard presented
honestly as such, not a trained ML model. MODEL_INFO below is the copy
shown in the UI to describe it accurately.
"""

PRIORITY_WEIGHT = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
SENTIMENT_SCORE = {"Positive": 1, "Neutral": 0, "Negative": -1}


def feedback_sentiment_trend(feedback: dict) -> str:
    """Rule-based (no LLM call) comparison of recent-12-months sentiment
    against historical sentiment, so both the UI and the agent context get
    a one-word trend instead of raw entries alone."""
    recent = feedback.get("recent12Months", [])
    historical = feedback.get("historical", [])
    if not recent and not historical:
        return "No feedback on record"
    if not historical:
        return "Insufficient history to compare"

    def avg_score(entries):
        return sum(SENTIMENT_SCORE.get(e.get("sentiment"), 0) for e in entries) / len(entries)

    recent_avg = avg_score(recent) if recent else 0
    historical_avg = avg_score(historical)
    delta = recent_avg - historical_avg

    if delta >= 0.4:
        return "Improving"
    if delta <= -0.4:
        return "Declining"
    return "Stable"



def top_loss_reasons(tickets: list[dict], limit: int = 3) -> list[str]:
    """Rule-based (no LLM call) prioritization of a lost contract's service
    history into a short, explainable 'reason for loss' list. Scores each
    distinct issue by severity and SLA performance, then returns the top N
    issue descriptions by aggregate score \u2014 ties broken by most recent date.
    Deliberately deterministic, matching the same design principle as the
    risk scorecard: instant and explainable rather than LLM-generated."""
    if not tickets:
        return []

    scored: dict[str, dict] = {}
    for t in tickets:
        issue = t.get("issue", "Unspecified issue")
        weight = PRIORITY_WEIGHT.get(t.get("priority"), 1)
        if not t.get("slaMet", True):
            weight += 2
        entry = scored.setdefault(issue, {"score": 0, "latestDate": t.get("date", "")})
        entry["score"] += weight
        if t.get("date", "") > entry["latestDate"]:
            entry["latestDate"] = t.get("date", "")

    ranked = sorted(scored.items(), key=lambda kv: (kv[1]["score"], kv[1]["latestDate"]), reverse=True)
    return [issue for issue, _ in ranked[:limit]]


PRODUCT_CATALOG = {
    "Reefer Unit": {
        "model": "ThermoGuard TR-500",
        "priceUsd": 28000,
        "lifeYears": 12,
        "pmFrequency": "Quarterly",
        "failureModes": ["Compressor failure", "Refrigerant leak", "Evaporator fan fault", "Defrost cycle malfunction"],
        "upgradePath": "Telematics-enabled reefer unit with remote temperature monitoring",
        "warranty": True,
    },
    "Cab HVAC System": {
        "model": "CabComfort CC-200",
        "priceUsd": 6500,
        "lifeYears": 8,
        "pmFrequency": "Semi-Annual",
        "failureModes": ["Blower motor failure", "Refrigerant leak", "Thermostat malfunction", "Condenser fouling"],
        "upgradePath": "High-efficiency variable-speed blower retrofit",
        "warranty": True,
    },
    "APU (Auxiliary Power Unit)": {
        "model": "IdleFree APU-100",
        "priceUsd": 15000,
        "lifeYears": 10,
        "pmFrequency": "Quarterly",
        "failureModes": ["Battery degradation", "Compressor wear", "Control board fault"],
        "upgradePath": "Battery-electric APU upgrade for extended idle-free runtime",
        "warranty": True,
    },
    "Bunk Heater": {
        "model": "NightHeat BH-50",
        "priceUsd": 2200,
        "lifeYears": 7,
        "pmFrequency": "Annual",
        "failureModes": ["Ignition failure", "Fuel line clog", "Thermostat fault"],
        "upgradePath": "Diesel-electric hybrid bunk heater upgrade",
        "warranty": False,
    },
    "Rooftop AC Unit": {
        "model": "SkyChill RT-300",
        "priceUsd": 9000,
        "lifeYears": 10,
        "pmFrequency": "Semi-Annual",
        "failureModes": ["Belt wear", "Refrigerant leak", "Condenser fouling", "Sensor malfunction"],
        "upgradePath": "Higher-SEER rooftop unit replacement",
        "warranty": True,
    },
}

FACTOR_MAX = {
    "SLA breaches": 20,
    "Emergency ticket ratio": 12,
    "Repeat issues": 10,
    "PM completion rate": 15,
    "Late payments": 15,
    "Outstanding balance": 6,
    "Competitor bid": 15,
    "NPS score": 15,
    "Portal engagement": 8,
    "Last price increase": 6,
    "Exec touchpoint gap": 8,
}


def compute_risk(contract: dict) -> tuple[int, dict]:
    """Weighted scorecard. Returns (score 0-100, per-factor breakdown) so the
    UI can show driver features, not just a number."""
    tickets = contract.get("serviceTickets", [])
    total_tickets = len(tickets) or 1

    sla_breaches = sum(1 for t in tickets if not t.get("slaMet", True))
    emergency_count = sum(1 for t in tickets if t.get("type") == "Emergency")
    issues = [t.get("issue") for t in tickets]
    repeat_issue = len(issues) != len(set(issues))

    pm_completion = contract.get("pmCompletionRate", 0.7)
    late_payments = contract.get("latePaymentsCount", 0)
    outstanding = contract.get("outstandingBalance", 0)
    contract_value = contract.get("contractValue", 1) or 1
    competitor_bid = contract.get("competitorBidReceived", False)
    nps = contract.get("npsScore", 5)
    portal_logins = contract.get("portalLogins", 0)
    last_price_increase_pct = contract.get("lastPriceIncreasePct", 0.1)
    exec_gap_days = contract.get("lastExecTouchpointDaysAgo", 90)

    factors = {
        "SLA breaches": min(20, sla_breaches * 4),
        "Emergency ticket ratio": round((emergency_count / total_tickets) * 12),
        "Repeat issues": 10 if repeat_issue else 0,
        "PM completion rate": round((1 - pm_completion) * 15),
        "Late payments": min(15, late_payments * 3),
        "Outstanding balance": min(6, round((outstanding / contract_value) * 40)),
        "Competitor bid": 15 if competitor_bid else 0,
        "NPS score": round((10 - nps) / 10 * 15),
        "Portal engagement": min(8, max(0, 8 - portal_logins)),
        "Last price increase": round((1 - min(last_price_increase_pct, 0.20) / 0.20) * 6),
        "Exec touchpoint gap": min(8, round(exec_gap_days / 45)),
    }
    score = min(100, max(0, sum(factors.values())))
    return score, factors


def compute_segment(risk_score: int, margin: float, median_margin: float) -> str:
    """Risk crossed with value — a high-risk, high-margin account is a very
    different priority than a high-risk, low-margin one."""
    above_median = margin > median_margin
    if risk_score >= 50:
        return "High Risk" if above_median else "At Risk"
    if risk_score >= 30:
        return "At Risk"
    return "Healthy" if above_median else "Standard"


def suggested_renewal_terms(contract: dict) -> dict:
    """Rule-based negotiation starting point, fed to the Renewal Document
    Agent so the drafted letter references concrete, consistent numbers
    instead of the LLM inventing a price move."""
    segment = contract.get("segment", "Standard")
    tenure_years = round(contract.get("monthsOnBook", 12) / 12, 1)
    long_tenure = tenure_years >= 4

    if segment == "High Risk":
        return {"priceMovePct": 0, "term": "24-month" if long_tenure else "12-month"}
    if segment == "At Risk":
        return {"priceMovePct": 3, "term": "12-month"}
    if segment == "Healthy":
        return {"priceMovePct": 7, "term": "24-month" if long_tenure else "12-month"}
    return {"priceMovePct": 5, "term": "12-month"}


MODEL_INFO = {
    "riskModel": {
        "name": "At-Risk Prediction Model",
        "type": "Rule-based weighted scorecard",
        "description": (
            "Given a customer-contract's service, financial, and engagement data, this model "
            "produces a 0-100 risk score and a ranked list of driver features explaining why. "
            "It is a deterministic weighted scorecard, not a trained ML model — no training data "
            "or model-fitting step is involved. This keeps it fully explainable and instant to "
            "compute, at the cost of not learning patterns beyond what the weights encode."
        ),
        "inputFeatures": list(FACTOR_MAX.keys()),
        "output": "risk_score (0-100), factor_breakdown (points contributed per driver)",
    },
    "valueModel": {
        "name": "Customer Value Classification",
        "type": "Rule-based (risk score \u00d7 margin vs. book median)",
        "description": (
            "Crosses the risk score against contract margin relative to the book median to assign "
            "one of four segments (High Risk, At Risk, Healthy, Standard). A high-risk, high-margin "
            "account and a high-risk, low-margin account are treated as different priorities, not "
            "collapsed into the same 'at risk' bucket."
        ),
        "output": "segment (High Risk | At Risk | Healthy | Standard)",
    },
}
