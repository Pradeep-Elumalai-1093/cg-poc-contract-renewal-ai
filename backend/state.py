"""
In-memory data layer. No database, per the POC design — everything lives in
process memory and resets when the server restarts.
"""
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from rules import (
    PRODUCT_CATALOG, compute_risk, compute_segment, top_loss_reasons,
    feedback_sentiment_trend, segment_price_percentile, COMPETITOR_TABLE,
)

BUCKETS = [">90", "90", "60", "45", "30", "10", "Lost"]
DUE_BUCKETS = ["90", "60", "45", "30", "10"]

CAMPAIGN_TAXONOMY = [
    {"id": "outreach_call", "name": "Personal outreach call"},
    {"id": "loyalty_pricing", "name": "Discount / loyalty pricing offer"},
    {"id": "service_checkin", "name": "Free service check-in"},
    {"id": "restructure", "name": "Contract restructuring"},
    {"id": "escalate_am", "name": "Escalation to account manager"},
]

REGIONS = {
    "NATT": {"label": "North America Truck & Trailer", "channels": ["Dealer"]},
    "ETT": {"label": "Europe Truck & Trailer", "channels": ["Dealer", "Direct"]},
    "APAC_TT": {"label": "APAC Truck & Trailer", "channels": ["Dealer"]},
}

FLEET_NAMES = [
    "Alder Freight Co", "Boreal Transit", "Cascade Logistics", "Delta Haulage", "Evergreen Fleet Services",
    "Fenwick Cold Chain", "Granite Transport", "Harborline Trucking", "Ironbridge Freight", "Juniper Fleet Corp",
    "Kestrel Logistics", "Lattimer Transport", "Meridian Cold Freight", "Northgate Haulers", "Orchard Fleet Solutions",
    "Palisade Trucking", "Quarrystone Freight", "Ridgeway Logistics", "Sablewood Transport", "Thornfield Fleet",
    "Umberline Haulage", "Vantage Cold Chain", "Westmark Trucking", "Yarrow Freight Services", "Zephyr Logistics",
    "Ashgrove Transit", "Briarcliff Freight", "Cedarline Haulers", "Dunmoor Logistics", "Elmscourt Fleet",
    "Foxglen Transport", "Greywick Freight", "Hollowmere Trucking", "Ivywood Logistics", "Larkspur Fleet Corp",
    "Millbrook Cold Chain", "Nettlewood Haulage",
]

TICKET_TYPE_WEIGHTS = ["Preventive", "Preventive", "Corrective", "Corrective", "Emergency", "Inspection"]
PRIORITY_BY_TYPE = {
    "Emergency": ["Critical", "Critical", "High"],
    "Corrective": ["High", "Medium", "Medium"],
    "Preventive": ["Low"],
    "Inspection": ["Low"],
}

FEEDBACK_SOURCES = ["NPS Survey", "Post-Service Survey", "Account Review Call", "Renewal Conversation", "Support Ticket Follow-up"]
FEEDBACK_POOL = {
    "Positive": [
        ("Service Quality", "Really happy with the response time on our last service call."),
        ("Technician Expertise", "The technician was excellent and explained everything clearly."),
        ("Account Relationship", "Appreciate the proactive check-ins from our account rep."),
        ("Pricing", "Pricing feels fair for the level of service we get."),
        ("Service Quality", "Great experience overall, would recommend to other fleet operators."),
    ],
    "Neutral": [
        ("Service Quality", "Service was fine, nothing stood out either way."),
        ("Account Relationship", "No complaints, but haven't seen much proactive outreach lately."),
        ("Pricing", "Pricing is about what we expected, similar to our last vendor."),
        ("Responsiveness", "Response time is acceptable, could be faster during peak season."),
    ],
    "Negative": [
        ("Responsiveness", "Frustrated with how long it took to resolve our refrigeration issue."),
        ("Pricing", "Feels like pricing has crept up without much explanation."),
        ("Account Relationship", "Wish we heard from our rep more often \u2014 feels like an afterthought."),
        ("Responsiveness", "Had to follow up multiple times to get a technician scheduled."),
        ("Service Quality", "Considering other options given the recent service issues."),
    ],
}


def _weighted_bucket() -> str:
    pool = [">90", ">90", "90", "90", "60", "60", "45", "45", "30", "30", "10", "Lost"]
    return random.choice(pool)


def _contract_count_for_customer() -> int:
    # Weighted so most customers have 1 contract, some have 2, fewer have 3.
    return random.choice([1, 1, 1, 2, 2, 3])


def _generate_feedback_entries(count: int, min_days_ago: int, max_days_ago: int, sentiment_bias: list[str]) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    entries = []
    for _ in range(count):
        sentiment = random.choice(sentiment_bias)
        category, comment = random.choice(FEEDBACK_POOL[sentiment])
        days_ago = random.randint(min_days_ago, max_days_ago)
        entries.append({
            "date": (today - timedelta(days=days_ago)).isoformat(),
            "source": random.choice(FEEDBACK_SOURCES),
            "sentiment": sentiment,
            "category": category,
            "comment": comment,
        })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def _generate_customer_feedback(service_trend_bias: str) -> dict:
    """Historical (12+ months back) and recent-12-months feedback, generated
    with a mild sentiment skew so accounts that are already trending risky
    tend to have more negative recent feedback \u2014 not purely random, so the
    data is at least internally plausible for a demo."""
    if service_trend_bias == "declining":
        recent_bias = ["Negative", "Negative", "Neutral", "Positive"]
    elif service_trend_bias == "increasing":
        recent_bias = ["Positive", "Positive", "Neutral"]
    else:
        recent_bias = ["Positive", "Neutral", "Neutral", "Negative"]
    historical_bias = ["Positive", "Neutral", "Neutral", "Negative"]  # less skewed \u2014 baseline

    recent = _generate_feedback_entries(random.randint(2, 5), 1, 365, recent_bias)
    historical = _generate_feedback_entries(random.randint(1, 3), 366, 900, historical_bias) if random.random() < 0.75 else []
    return {"recent12Months": recent, "historical": historical}


def _generate_price_history(current_value: float, months_on_book: int) -> list[dict]:
    """Works backward from the current price with plausible annual increases,
    so history is internally consistent rather than random noise \u2014 2-3
    points depending on how long the relationship has actually run."""
    today = datetime.now(timezone.utc).date()
    n_points = 3 if months_on_book >= 24 else 2 if months_on_book >= 12 else 1
    history = []
    price = current_value
    for i in range(n_points):
        years_ago = i
        history.append({
            "date": (today.replace(year=today.year - years_ago)).isoformat(),
            "price": round(price),
        })
        price = price / random.uniform(1.03, 1.09)  # each prior year was ~3-9% lower
    history.reverse()
    return history


def _generate_service_tickets(eq_type: str) -> list[dict]:
    failure_modes = PRODUCT_CATALOG[eq_type]["failureModes"]
    n_tickets = random.randint(2, 6)
    today = datetime.now(timezone.utc).date()
    tickets = []
    for _ in range(n_tickets):
        ticket_type = random.choice(TICKET_TYPE_WEIGHTS)
        priority = random.choice(PRIORITY_BY_TYPE[ticket_type])
        days_ago = random.randint(1, 90)
        sla_met = random.random() > (0.35 if priority in ("Critical", "High") else 0.1)
        tickets.append({
            "date": (today - timedelta(days=days_ago)).isoformat(),
            "type": ticket_type,
            "priority": priority,
            "issue": random.choice(failure_modes),
            "slaMet": sla_met,
            "resolutionHours": round(random.uniform(4, 170), 1),
        })
    tickets.sort(key=lambda t: t["date"], reverse=True)
    return tickets


def generate_contracts() -> list[dict]:
    contracts = []
    seq = 1
    cust_seq = 1
    name_idx = 0
    region_counts = {"NATT": 9, "ETT": 9, "APAC_TT": 7}  # customer counts per region
    eq_types = list(PRODUCT_CATALOG.keys())

    for region_id, customer_count in region_counts.items():
        channels = REGIONS[region_id]["channels"]
        for _ in range(customer_count):
            customer_id = f"CU-{cust_seq:04d}"
            customer_name = FLEET_NAMES[name_idx % len(FLEET_NAMES)]
            channel = random.choice(channels)
            dealer_id = f"DLR-{100 + (cust_seq % 12)}" if channel == "Dealer" else None

            for _ in range(_contract_count_for_customer()):
                months_on_book = round(random.uniform(3, 72))
                contract_value = round(random.uniform(8000, 145000) / 500) * 500
                cost_to_serve_ratio = random.uniform(0.45, 0.85)
                cost_to_serve = round(contract_value * cost_to_serve_ratio)
                margin = contract_value - cost_to_serve
                payment_lag_days = round(random.uniform(0, 55))
                bucket = _weighted_bucket()

                eq_type = random.choice(eq_types)
                equipment = {
                    "type": eq_type,
                    "count": random.randint(1, 12),
                    "avgAgeYears": round(random.uniform(1, 16), 1),
                    "critical": random.random() < 0.4,
                }
                service_tickets = _generate_service_tickets(eq_type)
                nps_score = random.randint(0, 10)
                feedback_bias = "declining" if nps_score <= 4 else "increasing" if nps_score >= 8 else "stable"
                customer_feedback = _generate_customer_feedback(feedback_bias)
                price_history = _generate_price_history(contract_value, months_on_book)

                contracts.append({
                    "contractId": f"CT-{seq:04d}",
                    "customerId": customer_id,
                    "customerName": customer_name,
                    "region": region_id,
                    "channel": channel,
                    "dealerId": dealer_id,
                    "monthsOnBook": months_on_book,
                    "contractValue": contract_value,
                    "costToServe": cost_to_serve,
                    "margin": margin,
                    "paymentLagDays": payment_lag_days,
                    "equipment": equipment,
                    "serviceTickets": service_tickets,
                    "customerFeedback": customer_feedback,
                    "priceHistory": price_history,
                    "feedbackTrend": feedback_sentiment_trend(customer_feedback),
                    "pmCompletionRate": round(random.uniform(0.40, 0.95), 2),
                    "latePaymentsCount": round(random.uniform(0, 5)) if random.random() < 0.5 else 0,
                    "outstandingBalance": round(contract_value * random.uniform(0, 0.15)) if random.random() < 0.4 else 0,
                    "competitorBidReceived": random.random() < 0.35,
                    "npsScore": nps_score,
                    "portalLogins": random.randint(0, 14),
                    "lastExecTouchpointDaysAgo": random.randint(10, 400),
                    "lastPriceIncreasePct": round(random.uniform(0.0, 0.20), 3),
                    "bucket": bucket,
                    "lastMilestoneProcessed": None,
                    # riskScore / riskFactors / segment are assigned in a
                    # second pass below, once the book's median margin is known.
                    "riskScore": None,
                    "riskFactors": None,
                    "segment": None,
                    # competitorSnapshot is keyed by segment (see COMPETITOR_TABLE),
                    # so it's filled in the second pass right after segment is known.
                    "competitorSnapshot": None,
                    # pricePercentile needs every contract's segment already
                    # assigned, so it's filled in the third pass.
                    "pricePercentile": None,
                    # Rule-based, not from an agent — top 3 service issues by
                    # severity/SLA performance, shown as "reason for loss" in
                    # the UI. Only meaningful (and only computed) for Lost contracts.
                    "lostReasons": top_loss_reasons(service_tickets) if bucket == "Lost" else None,
                })
                seq += 1
            cust_seq += 1
            name_idx += 1

    # Second pass: risk score is independent per contract, but segment needs
    # the book-wide median margin, so compute that only after all contracts exist.
    median_margin = sorted(c["margin"] for c in contracts)[len(contracts) // 2]
    for c in contracts:
        score, factors = compute_risk(c)
        c["riskScore"] = score
        c["riskFactors"] = factors
        c["segment"] = compute_segment(score, c["margin"], median_margin)
        c["competitorSnapshot"] = COMPETITOR_TABLE[c["segment"]]

    # Third pass: peer/segment price percentile needs every contract's
    # segment already assigned, so it has to run after the second pass.
    for c in contracts:
        c["pricePercentile"] = segment_price_percentile(c, contracts)

    return contracts


class AppState:
    """Single in-process store. Not thread-safe by design — this runs on
    asyncio's single event loop, which is sufficient for a POC."""

    def __init__(self):
        self.contracts: list[dict] = generate_contracts()
        self.trace: list[dict] = []
        self.batch_status: dict = {"running": False, "done": 0, "total": 0, "lastError": None}
        # Pre-computed, cached-on-demand agents — keyed by id, not tied to a
        # milestone run, per the "runs beforehand" design.
        self.ticket_summaries: dict = {}   # contractId -> {status, data, error}
        self.customer_summaries: dict = {}  # customerId -> {status, data, error}
        self.pricing_strategies: dict = {}  # contractId -> {status, data, error}

    def reset(self):
        self.contracts = generate_contracts()
        self.trace = []
        self.batch_status = {"running": False, "done": 0, "total": 0, "lastError": None}
        self.ticket_summaries = {}
        self.customer_summaries = {}
        self.pricing_strategies = {}

    def contract_by_id(self, contract_id: str) -> Optional[dict]:
        return next((c for c in self.contracts if c["contractId"] == contract_id), None)

    def contracts_for_customer(self, customer_id: str) -> list[dict]:
        return [c for c in self.contracts if c["customerId"] == customer_id]

    def latest_trace_for(self, contract_id: str) -> Optional[dict]:
        for record in reversed(self.trace):
            if record["contractId"] == contract_id:
                return record
        return None

    def due_contracts(self) -> list[dict]:
        return [c for c in self.contracts if c["bucket"] in DUE_BUCKETS and c["lastMilestoneProcessed"] != c["bucket"]]


state = AppState()
