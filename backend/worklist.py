"""
Everything the UI reads about contracts, straight from PostgreSQL.

  GET  /api/worklist                one page of the worklist (keyset paging, search, filters)
  GET  /api/summary                 KPIs, bucket counts, heatmap, per-area and campaign figures
  GET  /api/contracts/{id}          one contract with everything the drawer shows
  POST /api/contracts/{id}/contact  personal contact data, audited (never part of any list)
  GET/PUT /api/me/worklist-view     the caller's one saved view (columns, sort)
  POST /api/feedback, /api/action-status   what a rep records about a recommendation

Every query is scoped to the caller's CTXs in SQL (never from a request parameter), and a
record outside the scope answers 404 - not 403 - so another area's ids can't be probed.
The columns a caller may select, sort or search are whitelisted from the column catalog;
all values travel as bound parameters.
"""
import base64
import json
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

import db
from auth import Scope, User, active_user, get_scope
from ingest.catalog import PERSONAL_COLUMNS, column_label, load_catalog
from rules import feedback_sentiment_trend, top_loss_reasons

router = APIRouter()
S, D = db.SCHEMA, db.DATA_SCHEMA          # app/AI schema, contract data schema
SYSTEM, LANG = "ecare", "en"
SEGMENTS = ["High Risk", "At Risk", "Healthy", "Standard"]
BUCKETS = [">90", "90", "60", "45", "30", "10", "Lost"]
MAX_COLUMNS = 12
OUTCOMES = ("Engaged", "Declined", "No response")

# ---- the catalog, as the API sees it ---------------------------------------------------------
_CATALOG = load_catalog()
_BY_NAME = {c.name: c for c in _CATALOG}
ALWAYS = ("contractid", "risk_score", "segment")                        # sent with every row
VIRTUAL = ("recommended_action", "action_status")                       # come from the recommendation, not the contract table
AVAILABLE = [c.name for c in _CATALOG if c.worklist and c.name not in ALWAYS] + list(VIRTUAL)
DEFAULT_COLUMNS = [c.name for c in _CATALOG if c.worklist and c.default == "Yes"] + list(VIRTUAL)
SORTABLE = {c.name: c for c in _CATALOG if c.sortable}
DEFAULT_SORT = ("risk_score", "desc")
EXACT_COLUMNS = [c.name for c in _CATALOG if c.search == "Exact"]

RB = "LEAST(c.risk_score / 10, 9)"      # heatmap risk band 0..9
VB = ("CASE WHEN c.value_vs_median IS NULL THEN 4 WHEN c.value_vs_median < 0.5 THEN 0 "
      "WHEN c.value_vs_median < 1 THEN 1 WHEN c.value_vs_median < 2 THEN 2 ELSE 3 END")   # value vs the CTX median
REC_JOIN = (f'LEFT JOIN "{S}".contract_recommendation r ON r.system = \'{SYSTEM}\' AND r.language = \'{LANG}\' '
            "AND r.is_latest AND r.contractid = {c}.contractid")


def _run(session: Session, sql: str, params: dict | None = None):
    try:
        return session.execute(text(sql), params or {})
    except ProgrammingError as err:
        if "does not exist" in str(err.orig):
            raise HTTPException(status_code=503, detail="No contract data has been loaded yet. Run `python -m ingest`.") from err
        raise


def _data_version(session: Session) -> int:
    return _run(session, f"SELECT coalesce(max(data_version), 0) FROM \"{D}\".ingest_run WHERE status = 'succeeded'").scalar()


# ---- filters and scope -----------------------------------------------------------------------
def get_filters(
    area: list[str] = Query(default=[]),
    channel: list[Literal["Direct", "Dealer"]] = Query(default=[]),
    segment: list[Literal["High Risk", "At Risk", "Healthy", "Standard"]] = Query(default=[]),
    bucket: Literal[">90", "90", "60", "45", "30", "10", "Lost"] | None = None,
    rb: int | None = Query(default=None, ge=0, le=9),
    vb: int | None = Query(default=None, ge=0, le=4),
) -> dict:
    if any(not re.fullmatch(r"\d{3}", a) for a in area):
        raise HTTPException(status_code=422, detail="area must be 3-digit codes")
    return {"area": area, "channel": channel, "segment": segment, "bucket": bucket, "rb": rb, "vb": vb}


def _scope_ctxs(session: Session, scope: Scope, areas: list[str]) -> tuple[list[str], bool]:
    """(the CTX codes the caller may read, whether contracts with no CTX are included).
    Only admins see no-CTX contracts, so a gap in the source data fails closed."""
    if scope.ctxs is None:
        if areas:
            return sorted(set(areas)), False
        return [r[0] for r in _run(session, f'SELECT ctx FROM "{D}".ctx_stats ORDER BY ctx')], True
    return ([a for a in sorted(scope.ctxs) if a in areas] if areas else sorted(scope.ctxs)), False


def _static_filters(f: dict) -> tuple[str, dict]:
    sql, p = "", {}
    if f["channel"]:
        sql += " AND c.channel = ANY(:f_channel)"; p["f_channel"] = f["channel"]
    if f["segment"]:
        sql += " AND c.segment = ANY(:f_segment)"; p["f_segment"] = f["segment"]
    return sql, p


def _dynamic_filters(f: dict, expr: dict, skip=()) -> tuple[str, dict]:
    """bucket / risk band / value band - kept apart because each summary figure leaves out
    its own selection (so choosing a bucket doesn't zero the other bucket cards)."""
    sql, p = "", {}
    for k in ("bucket", "rb", "vb"):
        if f[k] is not None and k not in skip:
            sql += f" AND {expr[k]} = :f_{k}"; p[f"f_{k}"] = f[k]
    return sql, p


EXPR_C = {"bucket": "c.expiry_bucket", "rb": RB, "vb": VB}
EXPR_BASE = {"bucket": "bucket", "rb": "rb", "vb": "vb"}


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_sql(q: str) -> tuple[str, dict]:
    q = q.strip()
    if len(q) < 2:
        return "", {}
    exact = " OR ".join(f"c.{n} LIKE :s_pre ESCAPE '\\'" for n in EXACT_COLUMNS)
    return (f" AND (c.search_text LIKE :s_like ESCAPE '\\' OR {exact})",
            {"s_like": f"%{_like_escape(q.lower())}%", "s_pre": f"{_like_escape(q)}%"})


# ---- the caller's saved view -------------------------------------------------------------------
def _view(session: Session, user: User) -> dict:
    row = session.get(db.WorklistView, user.id)
    cols = [c for c in (row.columns if row else DEFAULT_COLUMNS) if c in AVAILABLE] or DEFAULT_COLUMNS
    key, direction = (row.sort_key, row.sort_dir) if row and row.sort_key in SORTABLE else DEFAULT_SORT
    return {"columns": cols, "sort": {"key": key, "dir": direction}}


class SortIn(BaseModel):
    key: str
    dir: Literal["asc", "desc"]


class ViewIn(BaseModel):
    columns: list[str] = []
    sort: SortIn | None = None


@router.get("/api/me/worklist-view")
def get_view(user: User = Depends(active_user), session: Session = Depends(db.get_db)):
    return {
        **_view(session, user),
        "always": list(ALWAYS), "maxColumns": MAX_COLUMNS,
        "available": [{"key": n, "label": column_label(n), "kind": "virtual" if n in VIRTUAL else _BY_NAME[n].kind,
                       "sortable": n in SORTABLE} for n in AVAILABLE],
    }


@router.put("/api/me/worklist-view")
def put_view(body: ViewIn, user: User = Depends(active_user), session: Session = Depends(db.get_db)):
    unknown = [c for c in body.columns if c not in AVAILABLE]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown column(s): {', '.join(unknown)}")
    if len(set(body.columns)) > MAX_COLUMNS:
        raise HTTPException(status_code=400, detail=f"A view can have at most {MAX_COLUMNS} columns.")
    if body.sort and body.sort.key not in SORTABLE:
        raise HTTPException(status_code=400, detail=f"Cannot sort by {body.sort.key}.")
    current = _view(session, user)
    columns = [c for c in AVAILABLE if c in set(body.columns)] or DEFAULT_COLUMNS      # catalog order; empty = back to the default
    sort = body.sort.model_dump() if body.sort else current["sort"]
    session.merge(db.WorklistView(user_id=user.id, columns=columns, sort_key=sort["key"], sort_dir=sort["dir"], updated_at=datetime.now(timezone.utc)))
    session.commit()
    return _view(session, user)


# ---- the worklist --------------------------------------------------------------------------------
def _enc(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, default=str).encode()).decode().rstrip("=")


def _dec(s: str) -> dict:
    try:
        d = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
        assert isinstance(d, dict) and {"dv", "i", "p"} <= set(d)
        return d
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Bad cursor.") from err


def _page_rows(session, ctxs, include_null, where, params, select_sql, order, outer_order, limit):
    """One phase of one page. Each CTX is read through its own index in order (LATERAL),
    so a user with several areas - or an admin - doesn't force a sort of every live
    contract; the per-CTX tops are merged and cut to `limit`."""
    def one(ctx_pred: str) -> str:
        return (f'SELECT {select_sql} FROM "{D}".contract c WHERE c.in_scope AND {ctx_pred} AND {where} '
                f"ORDER BY {order} LIMIT :lim")
    parts = [f"(SELECT t.* FROM unnest(CAST(:ctxs AS text[])) AS k(ctx) CROSS JOIN LATERAL ({one('c.ctxid = k.ctx')}) t)"]
    if include_null:
        parts.append(f"({one('c.ctxid IS NULL')})")
    sql = f"SELECT * FROM ({' UNION ALL '.join(parts)}) u ORDER BY {outer_order} LIMIT :lim"
    return _run(session, sql, {**params, "ctxs": ctxs, "lim": limit}).mappings().all()


@router.get("/api/worklist")
def get_worklist(
    f: dict = Depends(get_filters),
    q: str = Query(default="", max_length=100),
    sort: str | None = None,
    dir: Literal["asc", "desc"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    user: User = Depends(active_user),
    scope: Scope = Depends(get_scope),
    session: Session = Depends(db.get_db),
):
    view = _view(session, user)
    sort = sort or view["sort"]["key"]
    direction = dir or view["sort"]["dir"]
    if sort not in SORTABLE:
        raise HTTPException(status_code=400, detail=f"Cannot sort by {sort}.")
    dv = _data_version(session)
    cur = _dec(cursor) if cursor else None
    if cur and cur["dv"] != dv:
        raise HTTPException(status_code=409, detail="data_changed")   # new data was loaded mid-scroll
    ctxs, include_null = _scope_ctxs(session, scope, f["area"])
    if not ctxs and not include_null:
        return {"rows": [], "nextCursor": None, "dataVersion": dv}

    columns = list(dict.fromkeys(["contractid", "risk_score", "segment", *[c for c in view["columns"] if c not in VIRTUAL]]))
    select_sql = ", ".join([f"c.{sort} AS _sv", *[f"c.{c}" for c in columns]])
    st, sp = _static_filters(f)
    dy, dp = _dynamic_filters(f, EXPR_C)
    sq, qp = _search_sql(q)
    base_params = {**sp, **dp, **qp}
    base_where = f"TRUE{st}{dy}{sq}"

    op, o = (">", "ASC") if direction == "asc" else ("<", "DESC")
    type_ = SORTABLE[sort].sql_type
    want, rows = limit + 1, []
    # NULLs sort last in both directions: the non-null rows first (index order), then the NULL ones by id.
    for phase in ([0] if cur is None or cur["p"] == 0 else []) + [1]:
        if len(rows) >= want:
            break
        params = dict(base_params)
        if phase == 0:
            where, order, outer = f"{base_where} AND c.{sort} IS NOT NULL", f"c.{sort} {o}, c.contractid {o}", f"u._sv {o}, u.contractid {o}"
            if cur and cur["p"] == 0:
                where += f" AND (c.{sort}, c.contractid) {op} (CAST(:cv AS {type_}), :ci)"
                params.update(cv=cur["v"], ci=cur["i"])
        else:
            where, order, outer = f"{base_where} AND c.{sort} IS NULL", f"c.contractid {o}", f"u.contractid {o}"
            if cur and cur["p"] == 1:
                where += f" AND c.contractid {op} :ci"
                params["ci"] = cur["i"]
        rows += _page_rows(session, ctxs, include_null, where, params, select_sql, order, outer, want - len(rows))

    more = len(rows) > limit
    rows = rows[:limit]
    ids = [r["contractid"] for r in rows]
    recs = {r["contractid"]: r for r in _run(session, f"""
        SELECT contractid, retention_action_name, action_status, outcome, (evaluation ->> 'pass') = 'false' AS escalated
        FROM "{S}".contract_recommendation
        WHERE system = '{SYSTEM}' AND language = '{LANG}' AND is_latest AND contractid = ANY(:ids)""", {"ids": ids}).mappings()}
    out = []
    for r in rows:
        rec = recs.get(r["contractid"])
        out.append({"id": r["contractid"], **{c: r[c] for c in columns},
                    "rec": {"name": rec["retention_action_name"], "status": rec["action_status"], "outcome": rec["outcome"],
                            "escalated": bool(rec["escalated"])} if rec else None})
    nxt = None
    if more and rows:
        last = rows[-1]
        nxt = _enc({"dv": dv, "v": last["_sv"], "i": last["contractid"], "p": 0 if last["_sv"] is not None else 1})
    return {"rows": out, "nextCursor": nxt, "dataVersion": dv}


# ---- the summary ----------------------------------------------------------------------------------
_LOST = "(bucket = 'Lost' OR outcome = 'Declined')"


@router.get("/api/summary")
def get_summary(
    f: dict = Depends(get_filters),
    scope: Scope = Depends(get_scope),
    session: Session = Depends(db.get_db),
):
    ctxs, include_null = _scope_ctxs(session, scope, f["area"])
    empty = {"kpis": {"contracts": 0, "customers": 0, "value": 0, "segments": {s: 0 for s in SEGMENTS}, "lostCount": 0, "lostValue": 0,
                      "atRiskValue": 0, "convertedValue": 0, "actionsNeeded": 0, "responseRate": None},
             "buckets": {b: 0 for b in BUCKETS}, "heat": [], "byCtx": [], "campaigns": [],
             "outcomeByRisk": {"buckets": ["0-19", "20-39", "40-59", "60-79", "80-100"], "engaged": [0] * 5, "notEngaged": [0] * 5,
                               "revenueLost": [0] * 5, "revenueConverted": [0] * 5, "totalWithOutcome": 0}}
    if not ctxs and not include_null:
        return {**empty, "dataVersion": _data_version(session)}
    st, sp = _static_filters(f)
    main_dy, mp = _dynamic_filters(f, EXPR_BASE)
    nob_dy, _ = _dynamic_filters(f, EXPR_BASE, skip=("bucket",))
    nohm_dy, _ = _dynamic_filters(f, EXPR_BASE, skip=("rb", "vb"))
    scope_sql = "c.ctxid = ANY(:ctxs)" + (" OR c.ctxid IS NULL" if include_null else "")
    n = lambda cond: f"count(*) FILTER (WHERE {cond})"   # noqa: E731
    v = lambda cond: f"coalesce(sum(v) FILTER (WHERE {cond}), 0)"  # noqa: E731
    sql = f"""
WITH base AS MATERIALIZED (
  SELECT c.ctxid, c.customerid, c.segment, c.expiry_bucket AS bucket, c.risk_score, c.annual_contract_value AS v,
         {RB} AS rb, {VB} AS vb, r.retention_action_name AS rec, r.action_status AS astatus, r.outcome
  FROM "{D}".contract c {REC_JOIN.format(c="c")}
  WHERE c.in_scope AND ({scope_sql}){st}
)
SELECT 'ctx' AS k, jsonb_build_object('ctx', ctxid, 'customers', count(DISTINCT customerid), 'contracts', count(*),
    'value', coalesce(sum(v), 0), 'hr', {n("segment = 'High Risk'")}, 'ar', {n("segment = 'At Risk'")},
    'hl', {n("segment = 'Healthy'")}, 'st', {n("segment = 'Standard'")},
    'lostN', {n(_LOST)}, 'lostV', {v(_LOST)},
    'riskV', {v("bucket <> 'Lost' AND outcome = 'No response'")}, 'convV', {v("bucket <> 'Lost' AND outcome = 'Engaged'")},
    'eng', {n("outcome = 'Engaged'")}, 'dec', {n("outcome = 'Declined'")}, 'nor', {n("outcome = 'No response'")},
    'actions', {n("astatus = 'Action required'")}) AS d
  FROM base WHERE TRUE{main_dy} GROUP BY ctxid
UNION ALL
SELECT 'bucket', jsonb_build_object('b', bucket, 'n', count(*)) FROM base WHERE TRUE{nob_dy} GROUP BY bucket
UNION ALL
SELECT 'heat', jsonb_build_object('rb', rb, 'vb', vb, 'n', count(*), 'value', coalesce(sum(v), 0))
  FROM base WHERE rb IS NOT NULL{nohm_dy} GROUP BY rb, vb
UNION ALL
SELECT 'camp', jsonb_build_object('name', rec, 'assigned', count(*), 'engaged', {n("outcome = 'Engaged'")},
    'declined', {n("outcome = 'Declined'")}, 'noResponse', {n("outcome = 'No response'")}, 'assignedValue', coalesce(sum(v), 0),
    'atRiskValue', {v("outcome = 'No response'")}, 'lostValue', {v("outcome = 'Declined'")}, 'convertedValue', {v("outcome = 'Engaged'")})
  FROM base WHERE rec IS NOT NULL{main_dy} GROUP BY rec
UNION ALL
SELECT 'risk', jsonb_build_object('band', LEAST(risk_score / 20, 4), 'engaged', {n("outcome = 'Engaged'")},
    'notEngaged', {n("outcome <> 'Engaged'")}, 'lost', {v("outcome = 'Declined'")}, 'converted', {v("outcome = 'Engaged'")})
  FROM base WHERE outcome IS NOT NULL AND risk_score IS NOT NULL{main_dy} GROUP BY LEAST(risk_score / 20, 4)"""
    rows = _run(session, sql, {**sp, **mp, "ctxs": ctxs}).all()
    names = {c: nm for c, nm in _run(session, f'SELECT code, name FROM "{S}".ctx').all()}

    by_ctx, buckets, heat, camps = [], {b: 0 for b in BUCKETS}, [], []
    risk = empty["outcomeByRisk"]
    for k, d in rows:
        if k == "ctx":
            by_ctx.append({"ctx": d["ctx"], "name": names.get(d["ctx"]) or d["ctx"], "customers": d["customers"], "contracts": d["contracts"],
                           "value": d["value"], "segments": {"High Risk": d["hr"], "At Risk": d["ar"], "Healthy": d["hl"], "Standard": d["st"]},
                           "lostCount": d["lostN"], "lostValue": d["lostV"], "atRiskValue": d["riskV"], "convertedValue": d["convV"],
                           "engaged": d["eng"], "declined": d["dec"], "noResponse": d["nor"], "actionsNeeded": d["actions"]})
        elif k == "bucket":
            buckets[d["b"]] = d["n"]
        elif k == "heat":
            heat.append(d)
        elif k == "camp":
            camps.append(d)
        else:
            i = d["band"]
            risk["engaged"][i], risk["notEngaged"][i] = d["engaged"], d["notEngaged"]
            risk["revenueLost"][i], risk["revenueConverted"][i] = d["lost"], d["converted"]
            risk["totalWithOutcome"] += d["engaged"] + d["notEngaged"]
    by_ctx.sort(key=lambda r: (r["ctx"] is None, r["ctx"] or ""))
    tot = lambda key: sum(r[key] for r in by_ctx)  # noqa: E731
    logged = tot("engaged") + tot("declined") + tot("noResponse")
    kpis = {"contracts": tot("contracts"), "customers": tot("customers"), "value": tot("value"),
            "segments": {s: sum(r["segments"][s] for r in by_ctx) for s in SEGMENTS},
            "lostCount": tot("lostCount"), "lostValue": tot("lostValue"), "atRiskValue": tot("atRiskValue"),
            "convertedValue": tot("convertedValue"), "actionsNeeded": tot("actionsNeeded"),
            "responseRate": round(tot("engaged") / logged * 100) if logged else None}
    camps.sort(key=lambda c: -c["assigned"])
    return {"kpis": kpis, "buckets": buckets, "heat": heat, "byCtx": by_ctx, "campaigns": camps, "outcomeByRisk": risk,
            "dataVersion": _data_version(session)}


# ---- one contract ---------------------------------------------------------------------------------
def _contract_row(session: Session, scope: Scope, contract_id: str) -> dict:
    row = _run(session, f'SELECT * FROM "{D}".contract WHERE contractid = :id', {"id": contract_id}).mappings().first()
    if row is None or not scope.allows(row["ctxid"]):
        raise HTTPException(status_code=404, detail="Contract not found.")
    return dict(row)


def _suggested_actions(evaluation: dict | None) -> list[str]:
    scores = (evaluation or {}).get("scores", {})
    actions = ["Manually review the draft content below before sending - automated evaluation could not reach a passing score after the retry limit."]
    if float(scores.get("policy_compliance", 10) or 0) < 6:
        actions.append("Check the recommended action against policy manually - the automated check flagged a compliance concern.")
    if float(scores.get("groundedness", 10) or 0) < 6:
        actions.append("Verify the rationale against the customer's actual contract data before relying on it.")
    if float(scores.get("actionability", 10) or 0) < 6:
        actions.append("Add concrete next steps yourself - the recommendation may be too vague to act on directly.")
    actions.append("If still uncertain, escalate to the account manager per the standard action taxonomy.")
    return actions


def _trace(rec: dict) -> dict:
    """A recommendation row from the AI tables, in the shape the drawer already renders."""
    ev = rec["evaluation"] or {}
    passed = ev.get("pass", True) is not False
    return {
        "contractId": rec["contractid"],
        "recommendation": {"campaign": rec["retention_action_name"], "execution_owner": rec["execution_owner"],
                           "rationale": rec["ai_recommendation"], "upsell": rec["upsell"],
                           "confidence": float(rec["confidence"]) if rec["confidence"] is not None else None},
        "outcome": rec["outcome"], "outcomeNote": rec["outcome_note"] or "", "actionStatus": rec["action_status"],
        "pass": passed, "escalated": not passed, "error": False, "retryCount": ev.get("retries", 0),
        "evaluation": ev, "content": rec["draft_content"], "contentError": None,
        "suggestedActions": _suggested_actions(ev) if not passed else [],
        "latencyMs": ev.get("latency_ms", 0), "costUsd": ev.get("cost_usd", 0), "attempts": ev.get("attempts"),
    }


@router.get("/api/contracts/{contract_id}")
def get_contract(contract_id: str, scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _contract_row(session, scope, contract_id)
    today = datetime.now(timezone.utc).date()
    claims = _run(session, f"""SELECT claimdate, faultid, fault_description FROM "{D}".claim WHERE contractid = :id
                               ORDER BY claimdate DESC NULLS LAST LIMIT 25""", {"id": contract_id}).all()
    claims_total = _run(session, f'SELECT count(*) FROM "{D}".claim WHERE contractid = :id', {"id": contract_id}).scalar()
    legacy_claims = [{"date": c[0].date().isoformat() if c[0] else None, "faultId": c[1], "issue": c[2] or "Unspecified issue",
                      "jobWrittenOff": False} for c in claims]
    start, made, value = row["contract_start_date"], row["manufacturedate"], row["annual_contract_value"] or 0
    contract = {
        "contractId": row["contractid"], "customerId": row["customerid"], "customerName": row["company"] or row["account"] or "Unknown",
        "ctx": row["ctxid"], "region": row["ctxid"] or "—", "channel": row["channel"], "bucket": row["expiry_bucket"],
        "segment": row["segment"] or "Standard", "riskScore": row["risk_score"] or 0,
        "riskFactors": {"Repeat issue": row["repeat_issue_points"] or 0, "Claim frequency": row["claim_frequency_points"] or 0,
                        "Claim recency": row["claim_recency_points"] or 0, "Written-off ratio": row["written_off_ratio_points"] or 0,
                        "Coverage gap": row["coverage_gap_points"] or 0, "Equipment age": row["equipment_age_points"] or 0,
                        "Warranty status": row["warranty_status_points"] or 0, "Price increase": row["price_increase_points"] or 0},
        "contractValue": round(value), "monthlyAmount": round(value / 12, 2), "priceIncreasePct": row["price_increase_pct"],
        "monthsOnBook": max(0, (today - start).days // 30) if start else 0, "durationMonths": row["contract_duration_months"] or 0,
        "serviceGeneral": bool(row["is_general_service_include"]),
        "equipment": {"type": row["equipment_type"], "count": 1, "avgAgeYears": round((today - made).days / 365, 1) if made else 0},
        "claims": legacy_claims, "claimsTotal": claims_total,
        "customerFeedback": {"recent12Months": [], "historical": []},
        "feedbackTrend": feedback_sentiment_trend({"recent12Months": [], "historical": []}),
        "lostReasons": top_loss_reasons(legacy_claims) if row["expiry_bucket"] == "Lost" else None,
    }
    groups: dict[str, list] = {}
    for c in _CATALOG:
        val = row.get(c.name)
        if c.personal or val is None or c.origin == "App state":
            continue
        groups.setdefault(c.group or "Other", []).append({"key": c.name, "label": column_label(c.name), "value": val})

    portfolio = _run(session, f"""
        SELECT contractid, ctxid, expiry_bucket, annual_contract_value, risk_score, segment FROM "{D}".contract
        WHERE in_scope AND customerid = :cid AND ctxid IS NOT DISTINCT FROM :ctx ORDER BY risk_score DESC NULLS LAST, contractid LIMIT 50""",
        {"cid": row["customerid"], "ctx": row["ctxid"]}).all()

    rec = _run(session, f"""SELECT * FROM "{S}".contract_recommendation WHERE system = '{SYSTEM}' AND language = '{LANG}'
                            AND is_latest AND contractid = :id""", {"id": contract_id}).mappings().first()

    def summary(table: str, key: str, value):
        if value is None:
            return None
        t = _run(session, f"""SELECT ai_summary FROM "{S}".{table} WHERE system = '{SYSTEM}' AND language = '{LANG}'
                              AND is_latest AND {key} = :v""", {"v": value}).scalar()
        return {"status": "done", "data": t} if t else None

    return {
        "contract": contract,
        "details": [{"group": g, "items": items} for g, items in groups.items()],
        "portfolio": [{"contractId": p[0], "region": p[1] or "—", "bucket": p[2], "contractValue": round(p[3] or 0), "riskScore": p[4] or 0,
                       "segment": p[5] or "Standard"}
                      for p in portfolio],
        "trace": _trace(dict(rec)) if rec else None,
        "summaries": {"contract": summary("contract_summary", "contractid", contract_id),
                      "webClaims": summary("web_claims_summary", "contractid", contract_id),
                      "account": summary("account_summary", "accountid", row["accountid"])},
    }


@router.post("/api/contracts/{contract_id}/contact")
def reveal_contact(contract_id: str, user: User = Depends(active_user), scope: Scope = Depends(get_scope),
                   session: Session = Depends(db.get_db)):
    """Contact details are loaded because the contract manager needs them to reach out, but they
    only leave the server on an explicit request - and every request is on the audit trail."""
    row = _contract_row(session, scope, contract_id)
    db.audit(session, user, "contact.viewed", "contract", contract_id, ctx_code=row["ctxid"])
    session.commit()
    return {"contact": [{"key": c.name, "label": column_label(c.name), "value": row.get(c.name)}
                        for c in _CATALOG if c.personal and row.get(c.name) not in (None, "")]}


# ---- what a rep records ----------------------------------------------------------------------------
class FeedbackIn(BaseModel):
    contractId: str
    outcome: Literal["Engaged", "Declined", "No response"] | None = None
    note: str | None = None


class ActionStatusIn(BaseModel):
    contractId: str
    actionStatus: Literal["Action required", "Action done"]


def _update_recommendation(session, user, scope, contract_id, sets: dict):
    assigns = ", ".join(f"{k} = :{k}" for k in sets)
    scope_sql = "TRUE" if scope.ctxs is None else "c.ctxid = ANY(:ctxs)"
    res = _run(session, f"""
        UPDATE "{S}".contract_recommendation r SET {assigns}, modified_by = :me
        FROM "{D}".contract c
        WHERE r.contractid = c.contractid AND r.contractid = :cid AND r.is_latest AND r.system = '{SYSTEM}' AND r.language = '{LANG}'
          AND {scope_sql} RETURNING r.id""",
        {**sets, "me": str(user.id), "cid": contract_id, "ctxs": sorted(scope.ctxs or [])})
    if res.first() is None:
        raise HTTPException(status_code=404, detail="No recommendation found for this contract yet.")
    session.commit()
    return {"ok": True}


@router.post("/api/feedback")
def post_feedback(body: FeedbackIn, user: User = Depends(active_user), scope: Scope = Depends(get_scope),
                  session: Session = Depends(db.get_db)):
    sets = {}
    if "outcome" in body.model_fields_set:
        sets["outcome"] = body.outcome
    if "note" in body.model_fields_set:
        sets["outcome_note"] = body.note
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    return _update_recommendation(session, user, scope, body.contractId, sets)


@router.post("/api/action-status")
def post_action_status(body: ActionStatusIn, user: User = Depends(active_user), scope: Scope = Depends(get_scope),
                       session: Session = Depends(db.get_db)):
    return _update_recommendation(session, user, scope, body.contractId, {"action_status": body.actionStatus})
