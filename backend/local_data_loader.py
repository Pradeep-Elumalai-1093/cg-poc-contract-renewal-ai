"""
Local-file data source - reads real contract/claim/invoice exports from
disk and assembles them into the exact same contract dict shape
state.py's synthetic generate_contracts() produces, so rules.py and
agents.py don't need to know or care where the data came from.

Selected via DATA_SOURCE=local in .env (see state.py). Expects three
files inside DATA_DIR (default "./data"), configurable individually if
your real export filenames differ:
  - CONTRACTS_FILE  (.xlsx or .csv) - one row per contract
  - CLAIMS_FILE     (.csv)          - one row per service claim
  - INVOICES_FILE   (.csv)          - one row per billed installment

Column names below match the real exports already reviewed
(100_records_of_contract_information.xlsx, web_claim_3_contracts.csv,
invoice_of_234276.csv). If your actual export uses different column
names, update the *_COL constants at the top rather than the logic below.
"""
import logging
import os
import re
from datetime import date, datetime

import pandas as pd

log = logging.getLogger("local_data_loader")

DATA_DIR = os.environ.get("DATA_DIR", "../data")
CONTRACTS_FILE = os.environ.get("CONTRACTS_FILE", "contracts.csv")
CLAIMS_FILE = os.environ.get("CLAIMS_FILE", "claims.csv")
INVOICES_FILE = os.environ.get("INVOICES_FILE", "invoices.csv")

# The CTX (3-digit area code) comes from the ACCOUNT table and is carried on
# the contract table. The exact column name isn't confirmed, so set CTX_COLUMN
# in .env if yours differs; otherwise these common spellings are tried in order.
CTX_COLUMN_CANDIDATES = [c for c in (os.environ.get("CTX_COLUMN"), "CTX", "CTXID", "CTX_ID", "CTX_CODE") if c]
_CTX_RE = re.compile(r"^\d{1,3}$", re.ASCII)


def _normalize_ctx(value) -> str | None:
    """Area codes are 3-digit strings. A spreadsheet/pandas read turns "034"
    into the integer 34 (or the float 34.0 when the column has blanks), so
    restore the leading zeros. Anything that isn't a 1-3 digit number is
    treated as 'no CTX' rather than guessed at."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text.zfill(3) if _CTX_RE.match(text) else None


def _to_id(value) -> str | None:
    """IDs arrive as ints (or floats like 83121.0 when a column has blanks),
    but the API models declare them as strings and the frontend echoes them
    back - one canonical string form keeps every lookup consistent."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def _clean_row(row: pd.Series) -> dict:
    """Converts one pandas row into a plain, JSON-safe dict: NaN/NaT become
    None (not a truthy NaN float - Python treats NaN as truthy, which was
    silently breaking every 'row.get(X) or default' fallback in this file),
    and numpy scalar types (int64, float64, bool_) become native Python
    int/float/bool, since neither NaN nor numpy scalars survive standard
    JSON serialization. Valid pd.Timestamp values pass through unchanged -
    every date field here is only used for calculation, never written to
    the output dict without an explicit .isoformat() first, so there's no
    need to stringify them at this stage."""
    cleaned = {}
    for key, value in row.items():
        if pd.isna(value):
            cleaned[key] = None
        elif hasattr(value, "item"):  # numpy scalar (int64/float64/bool_) -> native Python type
            cleaned[key] = value.item()
        else:
            cleaned[key] = value
    return cleaned


# TODO: placeholder crosswalk - needs to be built with the client against
# real MODEL_CATEGORY/MODEL_TYPE/MODELGROUP values, not guessed. Falls
# back to "Reefer Unit" for anything unrecognized rather than crashing.
_EQUIPMENT_TYPE_CROSSWALK = {
    "LCVMT": "Reefer Unit",
    "IND": "Rooftop AC Unit",
    "POWER GENERATION UNIT": "APU (Auxiliary Power Unit)",
}


def _map_equipment_type(model_category) -> str:
    return _EQUIPMENT_TYPE_CROSSWALK.get(str(model_category or "").upper(), "Reefer Unit")


# TODO: placeholder crosswalk - COUNTRYID vs COUNTRY were seen to
# *disagree* on a real sample row during discovery. Needs validation
# against a larger sample before trusting this.
_COUNTRY_TO_REGION = {
    "GB": "ETT", "DE": "ETT", "FR": "ETT", "NO": "ETT", "SE": "ETT",
    "IT": "ETT", "ES": "ETT", "NL": "ETT", "PL": "ETT",
}


def _map_region(country_id) -> str:
    return _COUNTRY_TO_REGION.get(str(country_id or "").upper(), "ETT")


def _bucket_for(end_date, today: date) -> str:
    if end_date is None or pd.isna(end_date):
        return ">90"  # unknown expiry - don't silently drop it into "Lost"
    if isinstance(end_date, str):
        end_date = datetime.fromisoformat(end_date).date()
    elif hasattr(end_date, "date"):
        end_date = end_date.date()
    days_to_expiry = (end_date - today).days
    if days_to_expiry < 0:
        return "Lost"
    if days_to_expiry <= 10:
        return "10"
    if days_to_expiry <= 30:
        return "30"
    if days_to_expiry <= 45:
        return "45"
    if days_to_expiry <= 60:
        return "60"
    if days_to_expiry <= 90:
        return "90"
    return ">90"


def _read_contracts_file(path: str) -> pd.DataFrame:
    if path.lower().endswith(".xlsx"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def _read_csv_with_fallback_encoding(path: str) -> pd.DataFrame:
    # Real claim/invoice exports have shown up in both utf-8-sig and
    # latin1 - fall back rather than crash on whichever wasn't used.
    for enc in ("utf-8-sig", "latin1"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode {path} as utf-8-sig or latin1")


def load_contracts_from_local() -> list[dict]:
    today = datetime.now().date()

    contracts_path = os.path.join(DATA_DIR, CONTRACTS_FILE)
    claims_path = os.path.join(DATA_DIR, CLAIMS_FILE)
    invoices_path = os.path.join(DATA_DIR, INVOICES_FILE)

    contracts_df = _read_contracts_file(contracts_path)

    claims_by_contract: dict[str, list[dict]] = {}
    if os.path.exists(claims_path):
        claims_df = _read_csv_with_fallback_encoding(claims_path)
        for _, raw_row in claims_df.iterrows():
            row = _clean_row(raw_row)
            cid = _to_id(row.get("CONTRACTID"))
            if cid is None:
                continue
            claim_date = row.get("CLAIMDATE")
            claims_by_contract.setdefault(cid, []).append({
                "date": pd.to_datetime(claim_date).date().isoformat() if claim_date is not None else None,
                "faultId": row.get("FAULTID"),
                "issue": row.get("FAULT_DESCRIPTION") or "Unspecified issue",
                # JOBWRITTENOFF lives on INT_Service, not in any claims
                # export reviewed so far - defaults to False (neutral)
                # until a matching service-job export is available.
                "jobWrittenOff": False,
            })

    price_trend_by_contract: dict[str, dict] = {}
    if os.path.exists(invoices_path):
        invoices_df = _read_csv_with_fallback_encoding(invoices_path)
        invoices_df["DATEDUE"] = pd.to_datetime(invoices_df["DATEDUE"])
        for cid, group in invoices_df.groupby("CONTRACTID"):
            ordered = group.sort_values("DATEDUE")
            first_amount = _clean_row(ordered.iloc[0]).get("AMOUNT")
            latest_amount = _clean_row(ordered.iloc[-1]).get("AMOUNT")
            if first_amount is None or latest_amount is None:
                continue  # no usable amount to build a price trend from
            price_trend_by_contract[_to_id(cid)] = {
                "first_amount": float(first_amount),
                "latest_amount": float(latest_amount),
                "price_increase_pct": round((latest_amount - first_amount) / first_amount, 3) if first_amount else None,
            }

    ctx_column = next((c for c in CTX_COLUMN_CANDIDATES if c in contracts_df.columns), None)
    if ctx_column is None:
        log.warning(
            "No CTX column found in %s (tried %s). Every contract will have ctx=None and be visible to "
            "admins only - set CTX_COLUMN in .env if the column has a different name.",
            contracts_path, ", ".join(CTX_COLUMN_CANDIDATES),
        )

    contracts = []
    for _, raw_row in contracts_df.iterrows():
        row = _clean_row(raw_row)
        contract_id = _to_id(row["CONTRACTID"])
        claims = claims_by_contract.get(contract_id, [])

        trend = price_trend_by_contract.get(contract_id)
        contract_price = row.get("CONTRACT_PRICE")
        if trend:
            monthly_amount = trend["latest_amount"]
            price_increase_pct = trend["price_increase_pct"]
        else:
            # No invoice history for this contract - fall back to the
            # point-in-time CONTRACT_PRICE (confirmed to be a monthly
            # rate), with no price-increase signal available.
            monthly_amount = float(contract_price) if contract_price is not None else 0.0
            price_increase_pct = None
        annual_contract_value = round(monthly_amount * 12)

        manufacture_date = row.get("MANUFACTUREDATE")
        avg_age_years = (
            round((today - pd.to_datetime(manufacture_date).date()).days / 365, 1)
            if manufacture_date is not None else 0
        )

        end_date = row.get("CONTRACT_END_DATE")
        if end_date is None:
            end_date = row.get("CONTRACT_ORIGINAL_END_DATE")

        contracts.append({
            "contractId": contract_id,
            "customerId": _to_id(row.get("CUSTOMERID")),
            "ctx": _normalize_ctx(row.get(ctx_column)) if ctx_column else None,
            "customerName": row.get("COMPANY") or row.get("ACCOUNT") or "Unknown",
            "region": _map_region(row.get("COUNTRYID")),
            "channel": "Direct" if bool(row.get("IS_DIRECT_CONTRACT")) else "Dealer",
            "dealerId": None,  # TODO: no direct FK on the contract export - needs the inferred join via a service/claim export's DEALERID
            "monthsOnBook": int(row.get("CONTRACT_DURATION_MONTHS") or 0),
            "durationMonths": int(row.get("CONTRACT_DURATION_MONTHS") or 0),
            "contractValue": annual_contract_value,
            "monthlyAmount": monthly_amount,
            "priceIncreasePct": price_increase_pct,
            "serviceGeneral": bool(row.get("IS_GENERAL_SERVICE_INCLUDE")),
            "equipment": {
                "type": _map_equipment_type(row.get("MODEL_CATEGORY")),
                "count": 1,  # TODO: confirm 1-contract-1-unit cardinality assumption
                "avgAgeYears": avg_age_years,
            },
            "claims": claims,
            "warrantyStart": None,  # TODO: not present in this contract export - needs a warranty-specific export
            "warrantyEnd": None,
            "customerFeedback": {"recent12Months": [], "historical": []},  # no real source confirmed - see rules.py
            "feedbackTrend": None,
            "bucket": _bucket_for(end_date, today),
            "lastMilestoneProcessed": None,
            "riskScore": None,
            "riskFactors": None,
            "segment": None,
            "lostReasons": None,
        })

    bad = sum(1 for c in contracts if c["ctx"] is None)
    if ctx_column and bad:
        log.warning("%d of %d contracts have a blank or invalid %s value - visible to admins only.",
                    bad, len(contracts), ctx_column)
    return contracts
