"""
The column catalog, as code.

contract_columns.json is generated from the reviewed catalog workbook
(contract_column_catalog.xlsx) - the workbook is where people decide which
columns are loaded, sortable, searchable and so on; this module turns those
decisions into physical columns, indexes and checks. The ingest and the API
both read it, so the table, the indexes and the worklist's column whitelist
can't drift apart.

Rows with origin "Data product" come from Snowflake; "Derived at load" are
computed by the ingest; "App state" (recommendations, status, outcome) are
written by the app and are not columns of the contract table.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

COLUMNS_FILE = Path(__file__).with_name("contract_columns.json")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def data_schema() -> str:
    name = os.environ.get("DB_SCHEMA_DATA", "app_data")
    if not _IDENT.match(name):
        raise RuntimeError(f"DB_SCHEMA_DATA={name!r} is not a plain lower-case identifier")
    return name


# Catalog "Type" -> PostgreSQL type. The catalog doesn't distinguish whole numbers
# from decimals, so counts and scores are listed here.
SQL_TYPE = {"ID": "text", "Text": "text", "Yes/No": "boolean", "Date": "date",
            "Number": "double precision", "Percent": "double precision"}
INTEGER_COLUMNS = {
    "TOTAL_CLAIMS", "CLAIMS_LAST_90D", "REPEAT_FAULTS", "WRITTEN_OFF_JOBS", "TOTAL_SERVICE_JOBS", "RISK_SCORE",
    "REPEAT_ISSUE_POINTS", "CLAIM_FREQUENCY_POINTS", "CLAIM_RECENCY_POINTS", "WRITTEN_OFF_RATIO_POINTS",
    "COVERAGE_GAP_POINTS", "EQUIPMENT_AGE_POINTS", "WARRANTY_STATUS_POINTS", "PRICE_INCREASE_POINTS",
    "CONTRACT_DURATION_MONTHS", "DURATION", "SPONSORED_DURATION", "DAYS_TO_EXPIRY",
}
# Columns computed during load, with their physical types (the catalog lists them for
# filter/sort/search decisions; the type is a property of the computation).
DERIVED_SQL_TYPE = {
    "END_DATE_EFFECTIVE": "date", "DAYS_TO_EXPIRY": "integer", "EXPIRY_BUCKET": "text", "SEGMENT": "text",
    "VALUE_VS_MEDIAN": "double precision", "CHANNEL": "text", "EQUIPMENT_TYPE": "text", "SEARCH_TEXT": "text",
}
# Without these the app cannot work, so a load stops rather than guessing. Every other
# product column may be missing from an extract (it loads as NULL, with a warning).
REQUIRED_SOURCE = {
    "CONTRACTID", "CTXID", "COMPANY", "CONTRACT_STATUS", "IS_ACTIVE_CONTRACT_VERSION",
    "CONTRACT_ORIGINAL_END_DATE", "IS_DIRECT_CONTRACT", "ANNUAL_CONTRACT_VALUE", "RISK_SCORE",
}


@dataclass(frozen=True)
class Col:
    name: str          # physical column name (lower case)
    source: str        # name in the Snowflake product / extract (upper case); derived columns have none
    sql_type: str
    kind: str          # id | text | bool | date | number | int
    origin: str
    load: str          # Yes | Detail only
    filter: str
    placement: str
    sortable: bool
    search: str        # "" | Exact | Contains
    personal: bool = False


# Columns whose values identify a person or vehicle. They are loaded (the contract manager
# needs them to make contact) but only ever returned by the contract-detail call, and they must
# never reach an LLM prompt. Kept here so the API and the AI work-queue view share one list.
PERSONAL_COLUMNS = {"ADDRESS", "TOWN", "COUNTY", "POSTCODE", "PHONEOPEN", "PHONECLOSED", "FAX", "VEHICLEID",
                    "ADMINISTRATOR", "PRODSUPPORT", "CONTROLLER", "MANAGER", "CONTACTID", "CONTRACT_COMMENTS"}


def _physical(row: dict) -> tuple[str, str]:
    name = row["name"]
    if row["origin"] == "Derived at load":
        t = DERIVED_SQL_TYPE[name]
        return t, {"date": "date", "integer": "int", "double precision": "number"}.get(t, "text")
    if name in INTEGER_COLUMNS:
        return "integer", "int"
    t = SQL_TYPE[row["type"]]
    return t, {"boolean": "bool", "date": "date", "double precision": "number"}.get(t, "id" if row["type"] == "ID" else "text")


def _source_name(label: str) -> str:
    # "ISCURRENT (2nd occurrence)" -> "ISCURRENT__2": the extract may carry the same header twice,
    # and the second one is read as NAME__2 (see sources.dedupe_headers).
    m = re.match(r"^(\w+) \((\d+)(?:st|nd|rd|th) occurrence\)$", label)
    return f"{m.group(1)}__{m.group(2)}" if m else label


def load_catalog() -> list[Col]:
    cols = []
    for row in json.loads(COLUMNS_FILE.read_text(encoding="utf-8")):
        if row["origin"] == "App state":
            continue
        sql_type, kind = _physical(row)
        src = _source_name(row["name"]) if row["origin"] == "Data product" else ""
        phys = (src or row["name"]).lower()
        cols.append(Col(
            name=phys, source=src, sql_type=sql_type, kind=kind, origin=row["origin"], load=row["load"],
            filter=row["filter"], placement=row["placement"], sortable=row["sortable"] == "Yes", search=row["search"],
            personal=src in PERSONAL_COLUMNS,
        ))
    return cols


def source_columns(cols: list[Col]) -> list[Col]:
    return [c for c in cols if c.origin == "Data product"]


def table_columns(cols: list[Col]) -> list[tuple[str, str]]:
    """(name, sql type) of every physical column of the contract table, in order.
    data_version leads: it is the partition key. in_scope is a system column."""
    out = [("data_version", "integer NOT NULL")]
    for c in cols:
        out.append((c.name, "text NOT NULL" if c.name == "contractid" else c.sql_type))
    out.append(("in_scope", "boolean NOT NULL DEFAULT false"))
    return out


def index_definitions(cols: list[Col]) -> list[tuple[str, str]]:
    """(name, 'USING ... (...) [WHERE ...]') for the indexes the catalog asks for.

    Every worklist query is scoped to a CTX and to live contracts, so each index leads with
    ctxid and is partial on in_scope. Sort indexes end in contractid so keyset paging has a
    unique tiebreak; they are plain ascending because Postgres scans one backwards for DESC
    (the API must keep the tiebreak in the same direction as the sort)."""
    names = {c.name for c in cols}
    defs = []
    for c in cols:
        if c.sortable and c.name in names:
            defs.append((f"sort_{c.name}", f"USING btree (ctxid, {c.name}, contractid) WHERE in_scope"))
    for c in cols:
        if c.search == "Exact":
            defs.append((f"exact_{c.name}", f"USING btree ({c.name} text_pattern_ops)"))  # equality and LIKE 'abc%'
        elif c.filter == "In list" and c.search != "Exact":
            defs.append((f"inlist_{c.name}", f"USING btree ({c.name})"))
        elif c.search == "Contains" and c.name == "search_text":
            defs.append(("search_text", "USING gin (search_text gin_trgm_ops) WHERE in_scope"))
    if "contractid_old" in names:
        defs.append(("contractid_old", "USING btree (contractid_old)"))  # for building the renewal chain later
    seen, out = set(), []
    for n, d in defs:
        if n not in seen:
            seen.add(n); out.append((n, d))
    return out


# --- the claims table: a fixed, small shape (claims are only listed in the contract drawer) -----------
CLAIM_COLUMNS = [  # (physical name, sql type, accepted source headers)
    ("contractid", "text NOT NULL", ["CONTRACTID"]),
    ("claimno", "text", ["CLAIMNO"]),
    ("claimdate", "timestamp", ["CLAIMDATE"]),
    ("faultid", "text", ["FAULTID"]),
    ("fault_description", "text", ["FAULT_DESCRIPTION"]),
    ("cause_description", "text", ["CAUSE_DESCRIPTION"]),
    ("job_type_description", "text", ["JOB_TYPE_DESCRIPTION"]),
    ("status_description", "text", ["STATUS_DESCRIPTION", "STAUTS_DESCRIPTION"]),  # the source spells it STAUTS
    ("jobstart", "timestamp", ["JOBSTART"]),
    ("jobend", "timestamp", ["JOBEND"]),
    ("runninghours", "double precision", ["RUNNINGHOURS"]),
]
CLAIM_REQUIRED = {"contractid", "claimdate"}
