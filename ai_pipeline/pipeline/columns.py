"""
What a prompt may contain. Contact details (address, phone, ...) are loaded for the contract manager to use,
but must never reach a model or be stored with its results, so the contract context is built from an explicit
list of business columns AND anything on the personal list is stripped regardless (defence in depth).
"""
from .util import jsonable

# Must match backend/ingest/catalog.py PERSONAL_COLUMNS (a test compares them).
PERSONAL = {"address", "town", "county", "postcode", "phoneopen", "phoneclosed", "fax", "vehicleid", "administrator",
            "prodsupport", "controller", "manager", "contactid", "contract_comments"}

CONTEXT_COLUMNS = [
    "contractid", "contract_status", "contract_type_desc", "contract_service_interval", "contract_interval_desc",
    "includes_travel_charges", "is_general_service_include", "contract_start_date", "contract_end_date",
    "contract_original_end_date", "contract_duration_months", "currency", "annual_contract_value", "price_increase_pct",
    "model_name", "model_category", "model_type", "modelgroup", "vehiclebrand", "manufacturedate", "runninghours", "total_hours",
    "total_claims", "claims_last_90d", "repeat_faults", "written_off_jobs", "total_service_jobs", "risk_score",
]
RISK_FACTORS = {  # label shown to the model -> column holding that factor's points
    "Repeat issue": "repeat_issue_points", "Claim frequency": "claim_frequency_points", "Claim recency": "claim_recency_points",
    "Written-off ratio": "written_off_ratio_points", "Coverage gap": "coverage_gap_points", "Equipment age": "equipment_age_points",
    "Warranty status": "warranty_status_points", "Price increase": "price_increase_points",
}
ACCOUNT_COLUMNS = ["contractid", "accountid", "company", "risk_score", "annual_contract_value", "currency", "total_claims",
                   "claims_last_90d", "contract_status", "contract_original_end_date", "contract_end_date", "model_category", "is_direct_contract"]


def contract_context(row: dict, milestone: str | None) -> dict:
    """The facts about one contract that go into a prompt. `milestone` (the expiry bucket) is included so a contract
    moving to a new bucket counts as changed input; days-to-expiry is NOT, because it changes every day."""
    ctx = {c: jsonable(row[c]) for c in CONTEXT_COLUMNS if row.get(c) is not None and c not in PERSONAL}
    ctx["contractid"] = str(row["contractid"])
    ctx["customer_name"] = row.get("company") or "the customer"
    ctx["channel"] = "Direct" if row.get("is_direct_contract") else "Dealer"
    ctx["risk_factors"] = {label: row[col] for label, col in RISK_FACTORS.items() if row.get(col) is not None}
    ctx["milestone"] = milestone
    return ctx


def assert_no_personal_data(obj, where: str = "") -> None:
    """Raises if a personal column name appears as a key anywhere in what is about to be sent or stored."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            bad = PERSONAL.intersection(str(k).lower() for k in cur)
            if bad:
                raise ValueError(f"Personal data columns {sorted(bad)} must not be sent to a model or stored {where}")
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
