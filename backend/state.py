"""
In-memory data layer. No database, per the POC design - everything lives in
process memory and resets when the server restarts.

v3 update: contract shape now mirrors confirmed real eCare fields (see
contract_risk_score_v1.sql and the field-mapping spreadsheet), not the
earlier synthetic 11-factor shape. Fields with no confirmed real source
after multiple real data pulls (payment behavior, outstanding balance,
competitor bid, NPS score, portal engagement, exec touchpoint gap,
cost-to-serve/margin) have been REMOVED rather than kept as fabricated
placeholders - generating fake data for a field that doesn't exist in
production would silently break the moment real data replaces this file,
in a way that's much harder to spot than a field simply not being here.

Still synthetic: this generator produces demo data shaped like the real
schema (real field names, real value ranges observed in the sample
extracts), not a live Snowflake connection - swapping this module for a
real loader is the next step, not done here.
"""
import os
import random
from datetime import datetime, timedelta, date, timezone
from typing import Optional

from rules import PRODUCT_CATALOG, compute_risk, compute_segment, top_loss_reasons, feedback_sentiment_trend

# "synthetic" (default) generates demo data in-process; "local" reads real
# exported files from disk via local_data_loader.py. Same switch pattern as
# llm_client.py's LLM_PROVIDER - one env var, no code change to flip it.
DATA_SOURCE = os.environ.get("DATA_SOURCE", "local")

# Synthetic-only stand-ins for the real 3-digit area codes (the real ones
# come from the contract table's CTX column - see local_data_loader.py).
SYNTHETIC_CTX_CODES = ["034", "049", "043"]

BUCKETS = [">90", "90", "60", "45", "30", "10", "Lost"]
DUE_BUCKETS = ["90", "60", "45", "30", "10"]

CAMPAIGN_TAXONOMY = [
    {"id": "outreach_call", "name": "Personal outreach call"},
    {"id": "loyalty_pricing", "name": "Discount / loyalty pricing offer"},
    {"id": "service_checkin", "name": "Free service check-in"},
    {"id": "restructure", "name": "Contract restructuring"},
    {"id": "escalate_am", "name": "Escalation to account manager"},
]

# NATT/APAC_TT kept in the lookup for label/filter compatibility with the
# frontend, but MVP focus is Europe only per client scope - see
# region_counts below, which only generates ETT contracts.
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
]

# No real fault-repeat pool exists per equipment type yet (that crosswalk
# still needs building against the real MODEL_CATEGORY/TYPE/GROUP fields) -
# reusing the existing failure-mode text per equipment type as a stand-in,
# tagged with a synthetic faultId so repeat-fault logic has something real
# to compute against.
def _generate_claims(eq_type: str, contract_start: date, today: date) -> list[dict]:
    failure_modes = PRODUCT_CATALOG[eq_type]["failureModes"]
    n_claims = random.randint(0, 12)  # real data showed contracts ranging from 0 to 10+ claims
    claims = []
    span_days = max(1, (today - contract_start).days)
    for _ in range(n_claims):
        days_ago = random.randint(0, min(span_days, 730))
        fault_idx = random.randint(0, len(failure_modes) - 1)
        claims.append({
            "date": (today - timedelta(days=days_ago)).isoformat(),
            "faultId": f"{eq_type[:3].upper()}-{fault_idx}",  # stand-in for a real FAULTID code
            "issue": failure_modes[fault_idx],
            "jobWrittenOff": random.random() < 0.12,
        })
    claims.sort(key=lambda c: c["date"], reverse=True)
    return claims


def _generate_invoice_trend(months_on_book: int) -> tuple[float, float, float]:
    """Stand-in for INT_INVOICE's first-vs-latest installment amount.
    Returns (monthly_amount, annual_contract_value, price_increase_pct)."""
    first_amount = round(random.uniform(80, 1800), 2)
    # Most contracts never see a price change; some do, mid-term.
    had_increase = random.random() < 0.3
    latest_amount = round(first_amount * random.uniform(1.05, 1.20), 2) if had_increase else first_amount
    price_increase_pct = round((latest_amount - first_amount) / first_amount, 3) if first_amount else 0.0
    annual_contract_value = round(latest_amount * 12)
    return latest_amount, annual_contract_value, price_increase_pct


def _weighted_bucket() -> str:
    pool = [">90", ">90", "90", "90", "60", "60", "45", "45", "30", "30", "10", "Lost"]
    return random.choice(pool)


def _contract_count_for_customer() -> int:
    return random.choice([1, 1, 1, 2, 2, 3])


def generate_contracts() -> list[dict]:
    contracts = []
    seq = 1
    cust_seq = 1
    name_idx = 0
    region_counts = {"ETT": 5}  # Europe-only for MVP scope
    eq_types = list(PRODUCT_CATALOG.keys())
    today = datetime.now(timezone.utc).date()

    for region_id, customer_count in region_counts.items():
        channels = REGIONS[region_id]["channels"]
        for _ in range(customer_count):
            customer_id = f"CU-{cust_seq:04d}"
            customer_name = FLEET_NAMES[name_idx % len(FLEET_NAMES)]
            channel = random.choice(channels)
            dealer_id = f"DLR-{100 + (cust_seq % 12)}" if channel == "Dealer" else None

            for _ in range(_contract_count_for_customer()):
                months_on_book = round(random.uniform(3, 96))  # real samples showed durations up to 120mo
                duration_months = months_on_book + random.randint(0, 36)
                contract_start = today - timedelta(days=months_on_book * 30)
                bucket = _weighted_bucket()

                eq_type = random.choice(eq_types)
                manufacture_date = contract_start - timedelta(days=random.randint(0, 365))
                equipment = {
                    "type": eq_type,
                    "count": random.randint(1, 12),
                    "avgAgeYears": round((today - manufacture_date).days / 365, 1),
                }
                claims = _generate_claims(eq_type, contract_start, today)

                # Warranty: short relative to contract duration, matching what
                # real data suggests (most mature contracts are out of
                # warranty) - typically 12-24 months from manufacture.
                warranty_start = manufacture_date
                warranty_end = manufacture_date + timedelta(days=random.choice([365, 545, 730]))

                monthly_amount, annual_contract_value, price_increase_pct = _generate_invoice_trend(months_on_book)
                service_general = random.random() < 0.55

                contracts.append({
                    "contractId": f"CT-{seq:04d}",
                    "customerId": customer_id,
                    "customerName": customer_name,
                    "ctx": SYNTHETIC_CTX_CODES[cust_seq % len(SYNTHETIC_CTX_CODES)],
                    "region": region_id,
                    "channel": channel,
                    "dealerId": dealer_id,
                    "monthsOnBook": months_on_book,
                    "durationMonths": duration_months,
                    "contractValue": annual_contract_value,
                    "monthlyAmount": monthly_amount,
                    "priceIncreasePct": price_increase_pct,
                    "serviceGeneral": service_general,
                    "equipment": equipment,
                    "claims": claims,
                    "warrantyStart": warranty_start.isoformat(),
                    "warrantyEnd": warranty_end.isoformat(),
                    # No real feedback/NPS source confirmed anywhere in eCare -
                    # kept as an always-empty structure rather than fabricated,
                    # so feedback_sentiment_trend degrades honestly instead of
                    # lying. See rules.py's docstring.
                    "customerFeedback": {"recent12Months": [], "historical": []},
                    "feedbackTrend": None,  # set below, after generation
                    "bucket": bucket,
                    "lastMilestoneProcessed": None,
                    # riskScore / riskFactors / segment are assigned in a
                    # second pass below, once the book's median contract
                    # value is known.
                    "riskScore": None,
                    "riskFactors": None,
                    "segment": None,
                    "lostReasons": None,  # computed uniformly in _finalize_contracts below
                })
                seq += 1
            cust_seq += 1
            name_idx += 1

    return _finalize_contracts(contracts)


def _finalize_contracts(contracts: list[dict]) -> list[dict]:
    """Shared second pass for both data sources (synthetic and local-file):
    fills in feedback trend, lost-reason ranking, and the risk score /
    segment, which all need either per-contract derivation or the book-
    wide median contract value. Kept as one function so the two loaders
    can't silently drift into scoring contracts differently."""
    for c in contracts:
        c["feedbackTrend"] = feedback_sentiment_trend(c["customerFeedback"])
        if c["bucket"] == "Lost" and c["lostReasons"] is None:
            c["lostReasons"] = top_loss_reasons(c.get("claims", []))

    median_contract_value = sorted(c["contractValue"] for c in contracts)[len(contracts) // 2] if contracts else 0
    for c in contracts:
        score, factors = compute_risk(c)
        c["riskScore"] = score
        c["riskFactors"] = factors
        c["segment"] = compute_segment(score, c["contractValue"], median_contract_value)

    return contracts


def load_contracts() -> list[dict]:
    """Single entry point AppState uses to get contracts, regardless of
    source. Add a new DATA_SOURCE branch here (e.g. "snowflake") without
    touching AppState itself when that becomes relevant."""
    if DATA_SOURCE == "local":
        from local_data_loader import load_contracts_from_local
        return _finalize_contracts(load_contracts_from_local())
    return generate_contracts()


_ANY = object()  # sentinel: "don't filter by ctx" (None is a real value: contracts with no CTX)


def customer_summary_key(customer_id, ctx) -> str:
    """Customer summaries are cached per (customer, CTX), never per customer
    alone: a customer with contracts in two areas would otherwise get ONE
    summary narrating both, shown to a manager who may only see one."""
    return f"{customer_id}|{ctx or ''}"


class AppState:
    """Single in-process store. Not thread-safe by design - this runs on
    asyncio's single event loop, which is sufficient for a POC."""

    def __init__(self):
        self.contracts: list[dict] = load_contracts()
        self._index()
        self.trace: list[dict] = []
        self.batch_status: dict = {"running": False, "done": 0, "total": 0, "lastError": None}
        self.ticket_summaries: dict = {}   # contractId -> {status, data, error}
        self.customer_summaries: dict = {}  # customerId -> {status, data, error}

    def reset(self):
        self.contracts = load_contracts()
        self._index()
        self.trace = []
        self.batch_status = {"running": False, "done": 0, "total": 0, "lastError": None}
        self.ticket_summaries = {}
        self.customer_summaries = {}

    def _index(self) -> None:
        """Rebuilt whenever contracts change, so scope checks (called on
        every request, and once per trace row) are dictionary lookups
        instead of scans over the whole book."""
        self._by_id = {c["contractId"]: c for c in self.contracts}
        self._by_ctx: dict = {}
        for c in self.contracts:
            self._by_ctx.setdefault(c.get("ctx"), []).append(c)

    def contract_by_id(self, contract_id: str) -> Optional[dict]:
        return self._by_id.get(contract_id)

    def ctx_of(self, contract_id: str) -> Optional[str]:
        c = self._by_id.get(contract_id)
        return c.get("ctx") if c else None

    def contracts_in(self, ctxs) -> list[dict]:
        """ctxs=None -> every contract (admin). Otherwise only contracts whose
        CTX is in the given set; contracts with no CTX are never included."""
        if ctxs is None:
            return self.contracts
        return [c for code in sorted(ctxs) for c in self._by_ctx.get(code, [])]

    def ctx_codes(self) -> list[str]:
        return sorted(code for code in self._by_ctx if code)

    def ctx_counts(self) -> dict:
        return {code: len(rows) for code, rows in self._by_ctx.items() if code}

    def count_without_ctx(self) -> int:
        return len(self._by_ctx.get(None, []))

    def contracts_for_customer(self, customer_id: str, ctx=_ANY) -> list[dict]:
        return [c for c in self.contracts
                if c["customerId"] == customer_id and (ctx is _ANY or c.get("ctx") == ctx)]

    def latest_trace_for(self, contract_id: str) -> Optional[dict]:
        for record in reversed(self.trace):
            if record["contractId"] == contract_id:
                return record
        return None

    def due_contracts(self) -> list[dict]:
        return [c for c in self.contracts if c["bucket"] in DUE_BUCKETS and c["lastMilestoneProcessed"] != c["bucket"]]


state = AppState()
