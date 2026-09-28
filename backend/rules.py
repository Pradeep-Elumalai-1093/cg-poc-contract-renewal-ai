"""
Rule-based logic - deliberately kept separate from agents.py (which holds
LLM calls). Nothing in this file makes a network call; everything here is
deterministic and instant, which is what a risk score and a segmentation
label need to be.

v3 update: the risk model below is now grounded in confirmed real eCare
fields (REF_DB.ECARE_CTE_STG), not the earlier synthetic 11-factor model.
Six of the original factors (payment behavior, outstanding balance,
competitor bid, NPS score, portal engagement, exec touchpoint gap) were
checked against real data pulls from INT_CONTRACT, INT_WEBCLAIM,
INT_Service, INT_INVOICE, and confirmed absent from every source checked -
they are not approximated here, they are dropped, matching the same SQL
model already validated against the real book (106,106 contracts).
See the accompanying contract_risk_score_v1.sql for the reference query
this Python logic mirrors.
"""

import math
from datetime import date


SENTIMENT_SCORE = {"Positive": 1, "Neutral": 0, "Negative": -1}


def feedback_sentiment_trend(feedback: dict) -> str:
    """Rule-based (no LLM call) comparison of recent-12-months sentiment
    against historical sentiment. Kept for forward-compatibility - no real
    feedback/NPS/sentiment source has been found in eCare, so this will
    return "No feedback on record" for every real contract today. If a
    feedback source (e.g. Salesforce) becomes available, this starts
    working without further changes."""
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


def top_loss_reasons(claims: list[dict], limit: int = 3) -> list[str]:
    """Rule-based (no LLM call) prioritization of a lost contract's claim
    history into a short, explainable 'reason for loss' list.

    v3 update: the original version scored by ticket priority and SLA-met
    status - both confirmed absent from every real eCare claim extract
    checked (INT_WEBCLAIM has no PRIORITY or SLA_MET field). Rescored
    instead by how often each fault recurred and how recent it was -
    both real, confirmed fields (FAULTID, CLAIMDATE) - so a fault that
    kept coming back, especially recently, ranks as the more likely
    reason a customer didn't renew."""
    if not claims:
        return []

    scored: dict[str, dict] = {}
    for c in claims:
        issue = c.get("issue", "Unspecified issue")
        entry = scored.setdefault(issue, {"repeatCount": 0, "latestDate": ""})
        entry["repeatCount"] += 1
        if c.get("date", "") > entry["latestDate"]:
            entry["latestDate"] = c.get("date", "")

    ranked = sorted(scored.items(), key=lambda kv: (kv[1]["repeatCount"], kv[1]["latestDate"]), reverse=True)
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
    "Cab Trailer System": {
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

# Weights mirror contract_risk_score_v1.sql (v3) exactly - sum to 100 at max.
FACTOR_MAX = {
    "Repeat issue": 20,
    "Claim frequency": 15,
    "Claim recency": 16,
    "Written-off ratio": 14,
    "Coverage gap": 6,
    "Equipment age": 13,
    "Warranty status": 6,
    "Price increase": 10,
}


def compute_risk(contract: dict, today: date | None = None) -> tuple[int, dict]:
    """Weighted scorecard, grounded in confirmed real eCare fields. Returns
    (score 0-100, per-factor breakdown) so the UI can show driver features,
    not just a number. Mirrors contract_risk_score_v1.sql's CASE logic -
    keep the two in sync if either changes."""
    today = today or date.today()
    claims = contract.get("claims", [])
    duration_months = contract.get("durationMonths") or 1

    # Factor 1: repeat issue - magnitude-scaled, not a flat yes/no. Real
    # sample data showed the same fault recurring up to 10 times on one
    # contract; a flat threshold can't tell that apart from a single extra
    # repeat, which is why this scales linearly instead.
    fault_counts: dict[str, int] = {}
    for c in claims:
        fid = c.get("faultId")
        if fid is not None:
            fault_counts[fid] = fault_counts.get(fid, 0) + 1
    max_repeat = max(fault_counts.values()) if fault_counts else 1
    repeat_issue_points = min(20, max(0, (max_repeat - 1) * 4))

    # Factor 2: claim frequency, normalized by contract duration.
    # PLACEHOLDER BANDS - replace with real percentiles once queried
    # against the full book (see Step 0 in contract_risk_score_v1.sql).
    claims_per_month = len(claims) / duration_months
    if claims_per_month >= 0.6:
        claim_frequency_points = 15
    elif claims_per_month >= 0.3:
        claim_frequency_points = 9
    elif claims_per_month >= 0.1:
        claim_frequency_points = 4
    else:
        claim_frequency_points = 0

    # Factor 3: claim recency - active claims in the last 90 days are a
    # stronger live signal than the same total spread over years.
    claims_last_90d = sum(1 for c in claims if _days_ago(c.get("date"), today) <= 90)
    if claims_last_90d >= 2:
        claim_recency_points = 16
    elif claims_last_90d == 1:
        claim_recency_points = 8
    else:
        claim_recency_points = 0

    # Factor 4: written-off ratio. ASSUMPTION FLAGGED: treats a written-off
    # job as an unresolved issue, not a neutral "wasn't actually needed"
    # outcome - the real business meaning of JOBWRITTENOFF is still
    # unconfirmed with the eCare team.
    total_jobs = len(claims) or 0
    written_off = sum(1 for c in claims if c.get("jobWrittenOff"))
    if total_jobs == 0:
        written_off_points = 0
    else:
        ratio = written_off / total_jobs
        written_off_points = 14 if ratio > 0.25 else (7 if ratio > 0 else 0)

    # Factor 5: coverage gap - contract excludes general service.
    coverage_gap_points = 6 if not contract.get("serviceGeneral", True) else 0

    # Factor 6: equipment age. PLACEHOLDER BANDS.
    age_years = contract.get("equipment", {}).get("avgAgeYears", 0) or 0
    if age_years >= 8:
        equipment_age_points = 13
    elif age_years >= 4:
        equipment_age_points = 7
    else:
        equipment_age_points = 0

    # Factor 7: warranty status. No warranty record at all is treated the
    # same as "not under warranty" (flagged assumption - a genuinely
    # missing record is different from an expired one, and this collapses
    # that distinction).
    warranty_start = contract.get("warrantyStart")
    warranty_end = contract.get("warrantyEnd")
    is_under_warranty = bool(warranty_start and warranty_end and warranty_start <= today.isoformat() <= warranty_end)
    warranty_points = 0 if is_under_warranty else 6

    # Factor 8: price increase magnitude, from invoice history. DIRECTION
    # FLAGGED AS AN ASSUMPTION: scores a larger recent price increase as
    # more risk (sticker-shock churn risk) - a judgment call, not read off
    # any confirmed source. Confirm the direction with the business.
    price_increase_pct = contract.get("priceIncreasePct")
    if price_increase_pct is None:
        price_increase_points = 0
    elif price_increase_pct > 0.10:
        price_increase_points = 10
    elif price_increase_pct > 0.05:
        price_increase_points = 6
    elif price_increase_pct > 0:
        price_increase_points = 3
    else:
        price_increase_points = 0

    factors = {
        "Repeat issue": repeat_issue_points,
        "Claim frequency": claim_frequency_points,
        "Claim recency": claim_recency_points,
        "Written-off ratio": written_off_points,
        "Coverage gap": coverage_gap_points,
        "Equipment age": equipment_age_points,
        "Warranty status": warranty_points,
        "Price increase": price_increase_points,
    }
    score = min(100, max(0, sum(factors.values())))
    return score, factors


def _days_ago(iso_date: str | None, today: date) -> int:
    if not iso_date:
        return 10_000  # effectively "never" - excluded from any recency window
    try:
        return (today - date.fromisoformat(iso_date)).days
    except ValueError:
        return 10_000


def compute_segment(risk_score: int, contract_value: float, median_contract_value: float) -> str:
    """Risk crossed with value - a high-risk, high-value account is a very
    different priority than a high-risk, low-value one.

    Uses contract value (real, from invoice history) as the value axis,
    not margin. Real cost-to-serve data (INT_UNIT.UNITCOST) was 0 in
    every sample checked so far - unreliable, not a real signal - so
    margin isn't computable yet. If a real cost source is confirmed
    later, this should cross on margin instead, matching the original
    design intent."""
    above_median = contract_value > median_contract_value
    if (math.ceil(risk_score) >= 70) or (math.ceil(risk_score) >= 50 and above_median):
        return "High Risk"
    if math.ceil(risk_score) >= 50:
        return "At Risk"
    return "Healthy" if (math.ceil(risk_score) >= 30 and above_median) else "Standard"


MODEL_INFO = {
    "riskModel": {
        "name": "At-Risk Prediction Model",
        "type": "Rule-based Weighted Scorecard",
        "description": (
            "Given a customer-contract's real service claim and contract history from eCare, this "
            "model produces a 0-100 risk score and a ranked list of driver features explaining why. "
            "It is a deterministic weighted scorecard, not a trained ML model - no training data or "
            "model-fitting step is involved. This keeps it fully explainable and instant to compute, "
            "at the cost of not learning patterns beyond what the weights encode. Six factors "
            "considered in an earlier design (payment behavior, outstanding balance, competitor bid, "
            "NPS score, portal engagement, exec touchpoint gap) were checked against real eCare data "
            "and confirmed unavailable from any source checked so far - they are excluded here rather "
            "than approximated, and may be reintroduced if a real source (e.g. Salesforce, JDE) is "
            "confirmed later."
        ),
        "inputFeatures": list(FACTOR_MAX.keys()),
        "output": "risk_score (0-100), factor_breakdown (points contributed per driver)",
    },
    "valueModel": {
        "name": "Customer Value Classification",
        "type": "Rule-based (Risk Score \u00d7 Contract Value vs. Book Median)",
        "description": (
            "Crosses the risk score against contract value relative to the book median to assign "
            "one of four segments (High Risk, At Risk, Healthy, Standard). A high-risk, high-value "
            "account and a high-risk, low-value account are treated as different priorities, not "
            "collapsed into the same 'at risk' bucket. Uses contract value rather than margin, since "
            "real cost-to-serve data has not been confirmed reliable yet - see riskModel notes."
        ),
        "output": "segment (High Risk | At Risk | Healthy | Standard)",
    },
}
