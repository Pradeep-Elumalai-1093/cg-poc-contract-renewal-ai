"""
Criteria -> SQL, and the match tables built from them.

A criteria object is {"column": condition, ...}. Different columns are AND-ed (like stacked
Excel filters); within one column the values are OR-ed. A condition is either

    ["a", "b"]            the value is one of these
    {"gte": 50}           exactly one operator: eq ne gt gte lt lte between in contains starts_with is_null

Columns are whitelisted from the column catalog (every loaded column may be used), operators are
limited per column type, every value is coerced to the column's type in Python and sent as a bound
parameter - nothing a user types is ever put into SQL text.

Used by the exclusion sets and the retention actions (and by `python -m ingest`, which recomputes the
matches right after each load, because the contract table has just been replaced).
"""
import math
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

import db
from ingest.catalog import load_catalog

S, D = db.SCHEMA, db.DATA_SCHEMA
MAX_COLUMNS, MAX_VALUES, MAX_TEXT = 20, 500, 200


class CriteriaError(ValueError):
    """The criteria are not valid; the message is written for the person who typed them."""


class NoData(Exception):
    """The contract data hasn't been loaded yet (python -m ingest)."""


class VersionConflict(Exception):
    """Someone else changed the record since the caller read it."""


COLUMNS = {c.name: c for c in load_catalog() if c.name != "search_text"}
_TEXT_OPS = {"in", "eq", "ne", "contains", "starts_with", "is_null"}
_ORDERED_OPS = {"in", "eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null"}
OPS = {"id": _TEXT_OPS, "text": _TEXT_OPS, "bool": {"eq", "is_null"}, "date": _ORDERED_OPS, "number": _ORDERED_OPS, "int": _ORDERED_OPS}
_CMP = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _coerce(col, v):
    kind = col.kind
    try:
        if kind in ("id", "text"):
            s = str(v).strip()
            if not s or len(s) > MAX_TEXT:
                raise ValueError
            return s
        if kind == "bool":
            if isinstance(v, bool):
                return v
            s = str(v).strip().lower()
            if s in ("true", "yes", "y", "1"):
                return True
            if s in ("false", "no", "n", "0"):
                return False
            raise ValueError
        if kind == "date":
            return date.fromisoformat(str(v).strip()[:10])
        f = float(v)
        if not math.isfinite(f):
            raise ValueError
        if kind == "int":
            if f != int(f):
                raise ValueError
            return int(f)
        return f
    except (ValueError, TypeError, OverflowError):
        raise CriteriaError(f"{col.name}: {v!r} is not a valid {'whole number' if kind == 'int' else kind}") from None


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def compile_criteria(criteria, alias: str = "c") -> tuple[str, dict]:
    """(SQL predicate over `alias`, bound parameters). An empty criteria object compiles to TRUE."""
    if not isinstance(criteria, dict):
        raise CriteriaError("The criteria must be an object of column conditions.")
    if len(criteria) > MAX_COLUMNS:
        raise CriteriaError(f"At most {MAX_COLUMNS} columns per rule.")
    parts, params = [], {}

    def bind(v) -> str:
        name = f"cp{len(params)}"
        params[name] = v
        return f":{name}"

    for key, cond in criteria.items():
        col = COLUMNS.get(str(key).strip().lower())
        if col is None:
            raise CriteriaError(f"Unknown column: {key}")
        ref, t = f'{alias}."{col.name}"', col.sql_type
        if isinstance(cond, list):
            vals = [_coerce(col, v) for v in cond]
            if not vals or len(vals) > MAX_VALUES:
                raise CriteriaError(f"{col.name}: give between 1 and {MAX_VALUES} values.")
            parts.append(f"{ref} = ANY(CAST({bind(vals)} AS {t}[]))")
        elif isinstance(cond, dict) and len(cond) == 1:
            ((op, v),) = cond.items()
            if op not in OPS[col.kind]:
                raise CriteriaError(f"{col.name}: the operator {op!r} can't be used on a {col.kind} column.")
            if op == "is_null":
                parts.append(f"{ref} IS {'' if bool(v) else 'NOT '}NULL")
            elif op == "in":
                vals = [_coerce(col, x) for x in (v if isinstance(v, list) else [v])]
                if not vals or len(vals) > MAX_VALUES:
                    raise CriteriaError(f"{col.name}: give between 1 and {MAX_VALUES} values.")
                parts.append(f"{ref} = ANY(CAST({bind(vals)} AS {t}[]))")
            elif op == "between":
                if not isinstance(v, list) or len(v) != 2:
                    raise CriteriaError(f"{col.name}: 'between' needs two values.")
                a, b = _coerce(col, v[0]), _coerce(col, v[1])
                parts.append(f"{ref} BETWEEN CAST({bind(a)} AS {t}) AND CAST({bind(b)} AS {t})")
            elif op in ("contains", "starts_with"):
                s = _coerce(col, v)
                pat = f"%{_like(s)}%" if op == "contains" else f"{_like(s)}%"
                parts.append(f"{ref} ILIKE {bind(pat)} ESCAPE '\\'")
            else:
                parts.append(f"{ref} {_CMP[op]} CAST({bind(_coerce(col, v))} AS {t})")
        else:
            raise CriteriaError(f"{col.name}: use a list of values or exactly one operator.")
    return (" AND ".join(parts) or "TRUE"), params


def normalize(criteria) -> dict:
    """Lower-cased column names, so the same rule is stored one way. Validity is checked by compile_criteria."""
    if not isinstance(criteria, dict):
        raise CriteriaError("The criteria must be an object of column conditions.")
    return {str(k).strip().lower(): v for k, v in criteria.items()}


def validated(raw, allow_empty: bool) -> dict:
    """Normalised criteria, or a CriteriaError saying what is wrong. An empty exclusion would exclude every contract."""
    crit = normalize(raw)
    if not crit and not allow_empty:
        raise CriteriaError("An exclusion set needs at least one condition - an empty one would exclude every contract.")
    compile_criteria(crit)
    return crit


def run(session: Session, sql: str, params: dict | None = None):
    try:
        return session.execute(text(sql), params or {})
    except ProgrammingError as err:
        if "does not exist" in str(err.orig) and f"{D}" in str(err.orig):
            raise NoData("No contract data has been loaded yet. Run `python -m ingest`.") from err
        raise


def bump(session: Session, table: str, record_id: str, version: int, sets: str, params: dict, user_id) -> dict:
    """UPDATE one live record, but only if it is still at `version` - otherwise someone else changed it first."""
    row = run(session, f"""UPDATE "{S}".{table} SET {sets}, version = version + 1, updated_at = now(), updated_by = CAST(:_me AS uuid)
                           WHERE id = CAST(:_id AS uuid) AND version = :_v AND deleted_at IS NULL RETURNING *""",
              {**params, "_me": str(user_id), "_id": record_id, "_v": version}).mappings().first()
    if row is None:
        raise VersionConflict()
    return dict(row)


# ---- the match tables ---------------------------------------------------------------------------
def count_matches(session: Session, criteria: dict, ctx: str | None) -> tuple[int, int]:
    """(contracts the criteria match, live contracts in scope) - for a single area, or every area when ctx is None."""
    pred, params = compile_criteria(criteria)
    where = "c.in_scope AND c.ctxid = :ctx" if ctx else "c.in_scope AND c.ctxid IS NOT NULL"
    p = {**params, **({"ctx": ctx} if ctx else {})}
    row = run(session, f'SELECT count(*) FILTER (WHERE {pred}), count(*) FROM "{D}".contract c WHERE {where}', p).one()
    return row[0], row[1]


def recompute_exclusion(session: Session, set_id: str, ctx: str, criteria: dict) -> int:
    pred, params = compile_criteria(criteria)
    run(session, f'DELETE FROM "{S}".exclusion_hit WHERE set_id = CAST(:sid AS uuid)', {"sid": set_id})
    n = run(session, f"""INSERT INTO "{S}".exclusion_hit (set_id, contractid)
                         SELECT CAST(:sid AS uuid), c.contractid FROM "{D}".contract c
                         WHERE c.in_scope AND c.ctxid = :ctx AND {pred}""", {**params, "sid": set_id, "ctx": ctx}).rowcount
    run(session, f'UPDATE "{S}".exclusion_set SET match_count = :n, matched_at = now() WHERE id = CAST(:sid AS uuid)', {"n": n, "sid": set_id})
    return n


def recompute_action(session: Session, action_id: str, ctx: str | None, criteria: dict) -> int | None:
    """Empty criteria = the action applies to every contract, so there is nothing to store (None)."""
    run(session, f'DELETE FROM "{S}".retention_action_match WHERE action_id = CAST(:aid AS uuid)', {"aid": action_id})
    if not criteria:
        run(session, f'UPDATE "{S}".retention_action SET match_count = NULL, matched_at = now() WHERE id = CAST(:aid AS uuid)', {"aid": action_id})
        return None
    pred, params = compile_criteria(criteria)
    where = "c.ctxid = :ctx" if ctx else "c.ctxid IS NOT NULL"   # a global action matches in every area
    n = run(session, f"""INSERT INTO "{S}".retention_action_match (action_id, contractid)
                         SELECT CAST(:aid AS uuid), c.contractid FROM "{D}".contract c
                         WHERE c.in_scope AND {where} AND {pred}""", {**params, "aid": action_id, **({"ctx": ctx} if ctx else {})}).rowcount
    run(session, f'UPDATE "{S}".retention_action SET match_count = :n, matched_at = now() WHERE id = CAST(:aid AS uuid)', {"n": n, "aid": action_id})
    return n


def recompute_all(session: Session) -> dict:
    """After every data load: the contracts changed, so every active rule's matches are rebuilt.
    ponytail: one INSERT..SELECT per rule over the live contracts - fine for tens to low hundreds of
    rules at 300K contracts; past that, evaluate rules in one pass or only those touching changed columns."""
    sets = run(session, f"SELECT id, ctx, criteria FROM \"{S}\".exclusion_set WHERE status = 'active' AND deleted_at IS NULL").all()
    for sid, ctx, crit in sets:
        recompute_exclusion(session, str(sid), ctx, crit)
    acts = run(session, f"SELECT id, ctx, criteria FROM \"{S}\".retention_action WHERE deleted_at IS NULL").all()
    for aid, ctx, crit in acts:
        recompute_action(session, str(aid), ctx, crit)
    return {"exclusion_sets": len(sets), "actions": len(acts)}
