"""
In-memory data layer. No database, per the POC design — everything lives in
process memory and resets when the server restarts.
"""
import random
from typing import Optional

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


def _weighted_bucket() -> str:
    pool = [">90", ">90", "90", "90", "60", "60", "45", "45", "30", "30", "10", "Lost"]
    return random.choice(pool)


def generate_contracts() -> list[dict]:
    contracts = []
def _contract_count_for_customer() -> int:
    # Weighted so most customers have 1 contract, some have 2, fewer have 3.
    return random.choice([1, 1, 1, 2, 2, 3])


def generate_contracts() -> list[dict]:
    contracts = []
    seq = 1
    cust_seq = 1
    name_idx = 0
    region_counts = {"NATT": 9, "ETT": 9, "APAC_TT": 7}  # customer counts per region

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
                complaint_count = round(random.uniform(1, 4)) if random.random() < 0.3 else 0
                service_trend = random.choice(["declining", "stable", "stable", "increasing"])
                bucket = _weighted_bucket()

                risk_score = (
                    20 + payment_lag_days * 0.9 + complaint_count * 9
                    + (22 if service_trend == "declining" else -8 if service_trend == "increasing" else 0)
                    - (8 if months_on_book > 30 else 0)
                )
                risk_score = max(4, min(97, round(risk_score)))
                risk_tier = "High" if risk_score >= 66 else "Medium" if risk_score >= 38 else "Low"

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
                    "complaintCount": complaint_count,
                    "serviceTrend": service_trend,
                    "bucket": bucket,
                    "lastMilestoneProcessed": None,
                    "riskScore": risk_score,
                    "riskTier": risk_tier,
                })
                seq += 1
            cust_seq += 1
            name_idx += 1
    return contracts


class AppState:
    """Single in-process store. Not thread-safe by design — this runs on
    asyncio's single event loop, which is sufficient for a POC."""

    def __init__(self):
        self.contracts: list[dict] = generate_contracts()
        self.trace: list[dict] = []
        self.batch_status: dict = {"running": False, "done": 0, "total": 0, "lastError": None}

    def reset(self):
        self.contracts = generate_contracts()
        self.trace = []
        self.batch_status = {"running": False, "done": 0, "total": 0, "lastError": None}

    def contract_by_id(self, contract_id: str) -> Optional[dict]:
        return next((c for c in self.contracts if c["contractId"] == contract_id), None)

    def latest_trace_for(self, contract_id: str) -> Optional[dict]:
        for record in reversed(self.trace):
            if record["contractId"] == contract_id:
                return record
        return None

    def due_contracts(self) -> list[dict]:
        return [c for c in self.contracts if c["bucket"] in DUE_BUCKETS and c["lastMilestoneProcessed"] != c["bucket"]]


state = AppState()
