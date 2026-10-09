"""
Turn a raw extract into the rows the app serves.

Decisions applied here (all from the project's agreed rules):
  * Live contract: the latest version of a contract (IS_ACTIVE_CONTRACT_VERSION) that is
    either Live, or whose end date passed within the last LOST_WINDOW_DAYS (so recently
    lost contracts stay visible as "Lost"). Until the renewal chain (CONTRACTID_OLD) is
    built, an expired contract that was renewed cannot be told from a lost one.
  * Segment: the existing rules.compute_segment, using each CTX's OWN median contract value
    (computed here, in Python). A CTX with fewer than MIN_CTX_SAMPLE valued contracts uses
    the all-CTX median instead - a median of a handful of contracts isn't stable.
  * The median is taken over live contracts with a value above zero: contracts with no
    billing history would otherwise drag it towards zero.
"""
import json
import os
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from rules import compute_segment  # the single definition of the segmentation logic

from . import IngestError
from .catalog import CLAIM_COLUMNS, CLAIM_REQUIRED, REQUIRED_SOURCE, load_catalog, source_columns

LIVE_STATUS = os.environ.get("INGEST_LIVE_STATUS", "live").strip().lower()
LOST_WINDOW_DAYS = int(os.environ.get("INGEST_LOST_WINDOW_DAYS", "90"))
MIN_CTX_SAMPLE = int(os.environ.get("INGEST_MIN_CTX_SAMPLE", "30"))

_TRUE = {"true", "t", "yes", "y", "1", "1.0"}
_FALSE = {"false", "f", "no", "n", "0", "0.0"}
_FLOAT_ID = re.compile(r"^(\d+)\.0+$")


def _text(s: pd.Series) -> pd.Series:
    t = s.astype("string").str.strip()
    return t.mask(t == "")


def _id(s: pd.Series) -> pd.Series:
    # 83121.0 (an integer column that picked up blanks) -> "83121"
    return _text(s).str.replace(_FLOAT_ID, r"\1", regex=True)


def _bool(s: pd.Series) -> pd.Series:
    low = _text(s).str.lower()
    out = pd.Series(pd.NA, index=s.index, dtype="boolean")
    out[low.isin(_TRUE).fillna(False)] = True
    out[low.isin(_FALSE).fillna(False)] = False
    return out


def _date(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s, errors="coerce", format="mixed")
    if getattr(d.dt, "tz", None) is not None:
        d = d.dt.tz_localize(None)
    return d.dt.normalize()


def _convert(s: pd.Series, kind: str) -> pd.Series:
    if kind == "id":
        return _id(s)
    if kind == "text":
        return _text(s)
    if kind == "bool":
        return _bool(s)
    if kind == "date":
        return _date(s)
    if kind == "int":
        return pd.to_numeric(s, errors="coerce").round().astype("Int64")
    return pd.to_numeric(s, errors="coerce").astype("float64")


def normalize_ctx(s: pd.Series) -> pd.Series:
    """CTX is a 3-digit text code. A spreadsheet or number column turns "034" into 34 (or
    34.0), so restore the leading zeros; anything that isn't 1-3 digits is 'no CTX'."""
    t = _text(s).str.replace(r"\.0+$", "", regex=True)
    return t.where(t.str.fullmatch(r"\d{1,3}").fillna(False)).str.zfill(3)


def _equipment_map() -> tuple[dict, str]:
    cw = json.loads((Path(__file__).with_name("equipment_crosswalk.json")).read_text(encoding="utf-8"))
    return {k.upper(): v for k, v in cw.get("MODEL_CATEGORY", {}).items()}, cw.get("default", "Unclassified")


def prepare_contracts(raw: pd.DataFrame, today: date) -> tuple[pd.DataFrame, dict]:
    cols = load_catalog()
    src = source_columns(cols)
    report: dict = {"as_of": today.isoformat(), "rows_read": int(len(raw)), "warnings": []}

    missing = sorted(REQUIRED_SOURCE - set(raw.columns))
    if missing:
        raise IngestError(
            "The extract is missing required column(s): " + ", ".join(missing) +
            ". Nothing was loaded and the live data is unchanged.")
    known = {c.source for c in src}
    report["ignored_columns"] = sorted(set(raw.columns) - known)
    report["missing_optional_columns"] = sorted(known - set(raw.columns))
    if report["missing_optional_columns"]:
        report["warnings"].append(f"{len(report['missing_optional_columns'])} optional column(s) are not in the extract and load as empty")
    dup_headers = [c for c in raw.columns if "__" in c]
    if dup_headers:
        report["warnings"].append("repeated column header(s) in the extract: " + ", ".join(dup_headers))

    data = {}
    for c in src:
        # A column missing from the extract still gets its proper type (all NULL), so everything
        # downstream - date arithmetic, boolean logic - behaves the same as with a full extract.
        column = raw[c.source] if c.source in raw.columns else pd.Series(pd.NA, index=raw.index, dtype="object")
        data[c.name] = _convert(column, c.kind)
    df = pd.DataFrame(data, index=raw.index)
    df["ctxid"] = normalize_ctx(raw["CTXID"])

    # --- one row per contract ---------------------------------------------------------
    no_id = df["contractid"].isna()
    report["dropped_no_contractid"] = int(no_id.sum())
    df = df[~no_id]
    before = len(df)
    ranked = df.assign(_act=df["is_active_contract_version"].fillna(False).astype("int8")) \
               .sort_values(["_act", "contract_start_date"], ascending=[False, False], kind="stable", na_position="last")
    df = ranked.drop_duplicates("contractid", keep="first").drop(columns="_act").sort_index()
    report["duplicate_contractid_dropped"] = int(before - len(df))
    if report["duplicate_contractid_dropped"]:
        report["warnings"].append(
            f"{report['duplicate_contractid_dropped']} duplicate CONTRACTID row(s) dropped (kept the latest version)")

    # --- derived columns -----------------------------------------------------------------
    today_ts = pd.Timestamp(today)
    end = df["contract_end_date"].fillna(df["contract_original_end_date"])
    df["end_date_effective"] = end
    days = (end - today_ts).dt.days
    df["days_to_expiry"] = days.astype("Int64")
    d = days.to_numpy(dtype="float64", na_value=np.nan)
    df["expiry_bucket"] = np.select(
        [np.isnan(d), d < 0, d <= 10, d <= 30, d <= 45, d <= 60, d <= 90],
        [">90", "Lost", "10", "30", "45", "60", "90"], default=">90")  # unknown expiry stays out of "Lost"
    df["channel"] = np.where(df["is_direct_contract"].fillna(False).astype(bool), "Direct", "Dealer")

    cmap, default_type = _equipment_map()
    category = _text(df["model_category"]).str.upper()
    df["equipment_type"] = category.map(cmap).fillna(default_type)
    unmapped = category[~category.isin(list(cmap))].fillna("(blank)").value_counts().head(10)
    report["unmapped_equipment_categories"] = {str(k): int(v) for k, v in unmapped.items()}

    parts = [df[c].astype("string").fillna("") for c in ("contractid", "contractrefno", "company", "account")]
    df["search_text"] = (parts[0] + " " + parts[1] + " " + parts[2] + " " + parts[3]).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()

    # --- who is in scope -------------------------------------------------------------------
    latest = df["is_active_contract_version"].fillna(False).astype(bool)
    live = (_text(df["contract_status"]).str.lower() == LIVE_STATUS).fillna(False).astype(bool)
    recent = (end >= today_ts - pd.Timedelta(days=LOST_WINDOW_DAYS)).fillna(False).astype(bool)
    df["in_scope"] = latest & (live | recent)
    in_scope = df["in_scope"]
    report["contracts"] = int(len(df))
    report["in_scope"] = int(in_scope.sum())
    report["out_of_scope"] = int((~in_scope).sum())
    report["in_scope_without_ctx"] = int((in_scope & df["ctxid"].isna()).sum())
    if report["in_scope_without_ctx"]:
        report["warnings"].append(f"{report['in_scope_without_ctx']} live contract(s) have no valid CTX - visible to admins only")
    report["in_scope_without_risk_score"] = int((in_scope & df["risk_score"].isna()).sum())
    if report["in_scope_without_risk_score"]:
        report["warnings"].append(f"{report['in_scope_without_risk_score']} live contract(s) have no RISK_SCORE and get no segment")
    one = df.dropna(subset=["accountid", "ctxid"]).groupby("accountid")["ctxid"].nunique()
    report["accounts_with_multiple_ctx"] = int((one > 1).sum())
    if report["accounts_with_multiple_ctx"]:
        report["warnings"].append(
            f"{report['accounts_with_multiple_ctx']} account(s) appear under more than one CTX - the account summaries assume one")

    # --- per-CTX median and segment ---------------------------------------------------------
    value = df["annual_contract_value"]
    valued = df[in_scope & (value > 0)]
    global_median = float(valued["annual_contract_value"].median()) if len(valued) else np.nan
    stats, median_of = [], {}
    for ctx, grp in df[df["ctxid"].notna()].groupby("ctxid"):
        sample = valued[valued["ctxid"] == ctx]["annual_contract_value"]
        use_global = len(sample) < MIN_CTX_SAMPLE
        med = global_median if use_global else float(sample.median())
        median_of[ctx] = med
        stats.append({"ctx": ctx, "contracts": int(len(grp)), "in_scope": int(grp["in_scope"].sum()),
                      "valued_contracts": int(len(sample)), "median_value": None if np.isnan(med) else round(med, 2),
                      "used_global_median": bool(use_global)})
    report["ctx"] = stats
    report["global_median"] = None if np.isnan(global_median) else round(global_median, 2)

    med = df["ctxid"].map(median_of).astype("float64").fillna(global_median)
    scores = df["risk_score"].to_numpy(dtype="float64", na_value=np.nan)
    vals = value.to_numpy(dtype="float64", na_value=0.0)
    meds = med.to_numpy(dtype="float64", na_value=0.0)
    mask = in_scope.to_numpy() & ~np.isnan(scores)
    df["segment"] = pd.Series(
        [compute_segment(int(s), float(v), float(m)) if ok else None for s, v, m, ok in zip(scores, vals, meds, mask)],
        index=df.index, dtype="object")
    df["value_vs_median"] = (value / med).where((value > 0) & (med > 0)).astype("float64")
    seg = df.loc[in_scope, "segment"].value_counts(dropna=True)
    report["segments"] = {k: int(v) for k, v in seg.items()}
    bk = df.loc[in_scope, "expiry_bucket"].value_counts()
    report["buckets"] = {k: int(v) for k, v in bk.items()}
    return df, report


def prepare_claims(raw: pd.DataFrame, contract_ids: set) -> tuple[pd.DataFrame, dict]:
    report: dict = {"rows_read": int(len(raw)), "warnings": []}
    data = {}
    for name, sql_type, aliases in CLAIM_COLUMNS:
        found = next((a for a in aliases if a in raw.columns), None)
        if found is None:
            if name in CLAIM_REQUIRED:
                raise IngestError(f"The claims extract is missing required column {aliases[0]}. Nothing was loaded.")
            data[name] = pd.Series(pd.NA, index=raw.index, dtype="object")
            continue
        s = raw[found]
        if name in ("contractid", "claimno", "faultid"):
            data[name] = _id(s)
        elif "timestamp" in sql_type:
            t = pd.to_datetime(s, errors="coerce", format="mixed")
            data[name] = t.dt.tz_localize(None) if getattr(t.dt, "tz", None) is not None else t
        elif "double" in sql_type:
            data[name] = pd.to_numeric(s, errors="coerce").astype("float64")
        else:
            data[name] = _text(s)
    df = pd.DataFrame(data, index=raw.index)
    keep = df["contractid"].isin(contract_ids)
    report["orphan_claims_dropped"] = int((~keep).sum())
    if report["orphan_claims_dropped"]:
        report["warnings"].append(f"{report['orphan_claims_dropped']:,} claim(s) belong to contracts not in the contract extract and were dropped")
    df = df[keep].reset_index(drop=True)
    report["claims"] = int(len(df))
    return df, report


def for_copy(df: pd.DataFrame) -> pd.DataFrame:
    """Dates and timestamps become ISO text; NULLs are written as empty fields by to_csv."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            fmt = "%Y-%m-%d" if (out[c].dropna().dt.normalize() == out[c].dropna()).all() else "%Y-%m-%d %H:%M:%S"
            out[c] = out[c].dt.strftime(fmt)
    return out
