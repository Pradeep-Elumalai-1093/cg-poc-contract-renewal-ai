"""
In-memory data layer. No database, per the POC design — everything lives in
process memory and resets when the server restarts.
"""
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from rules import PRODUCT_CATALOG, compute_risk, compute_segment

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


def _weighted_bucket() -> str:
    pool = [">90", ">90", "90", "90", "60", "60", "45", "45", "30", "30", "10", "Lost"]
    return random.choice(pool)


def _contract_count_for_customer() -> int:
    # Weighted so most customers have 1 contract, some have 2, fewer have 3.
    return random.choice([1, 1, 1, 2, 2, 3])


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
                    "pmCompletionRate": round(random.uniform(0.40, 0.95), 2),
                    "latePaymentsCount": round(random.uniform(0, 5)) if random.random() < 0.5 else 0,
                    "outstandingBalance": round(contract_value * random.uniform(0, 0.15)) if random.random() < 0.4 else 0,
                    "competitorBidReceived": random.random() < 0.35,
                    "npsScore": random.randint(0, 10),
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

    def reset(self):
        self.contracts = generate_contracts()
        self.trace = []
        self.batch_status = {"running": False, "done": 0, "total": 0, "lastError": None}
        self.ticket_summaries = {}
        self.customer_summaries = {}

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
