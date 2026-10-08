"""
Exclusion sets: which contracts must NOT get an LLM recommendation.

  GET/POST /api/exclusions                     list the sets of the caller's areas / create one
  PUT /api/exclusions/{id}                     edit (needs the version you read - a stale edit gets a 409)
  POST /api/exclusions/{id}/activate|deactivate
  DELETE /api/exclusions/{id}?version=         soft delete;  POST .../restore brings it back as a draft
  POST /api/exclusions/preview                 what a set would exclude, before saving it
  GET/PUT /api/me/exclusion-pref               which sets hide contracts from MY screens
  GET /api/rules/columns[/{column}/values]     what the criteria builder offers

A set belongs to one area and is visible to everyone with that area. While active, the contracts it
matches are kept in exclusion_hit; the AI pipeline skips the recommendation for any contract matching
ANY active set of its area, whatever individual users have chosen to hide on their own screens.
A contract matching several sets is still one contract: every count here is of distinct contracts.
"""
import json
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import db
import matching
from auth import Scope, User, active_user, get_scope
from ingest.catalog import column_label
from matching import COLUMNS, OPS, bump, compile_criteria, count_matches, recompute_exclusion, run, validated

router = APIRouter()
S, D = db.SCHEMA, db.DATA_SCHEMA
SHARE_CONFIRM = float(os.environ.get("EXCLUSION_CONFIRM_SHARE", "0.3"))   # a set excluding more than this share of an area needs a confirmation
NIL = "00000000-0000-0000-0000-000000000000"


def _vis(scope: Scope) -> tuple[str, dict]:
    return ("TRUE", {}) if scope.ctxs is None else ("s.ctx = ANY(:ctxs)", {"ctxs": sorted(scope.ctxs)})


def _out(r: dict, only_here: int | None = None) -> dict:
    return {"id": str(r["id"]), "ctx": r["ctx"], "name": r["name"], "description": r["description"], "criteria": r["criteria"],
            "status": r["status"], "version": r["version"], "matchCount": r["match_count"], "matchedAt": db.iso(r["matched_at"]),
            "updatedAt": db.iso(r["updated_at"]), "updatedBy": r.get("updated_by_email"), "deleted": r["deleted_at"] is not None,
            "onlyThisSet": only_here}


def _get(session: Session, scope: Scope, set_id: UUID, deleted: bool = False) -> dict:
    row = run(session, f"""SELECT s.*, u.email AS updated_by_email FROM "{S}".exclusion_set s LEFT JOIN "{S}".users u ON u.id = s.updated_by
                           WHERE s.id = CAST(:id AS uuid)""", {"id": str(set_id)}).mappings().first()
    if row is None or not scope.allows(row["ctx"]) or (row["deleted_at"] is not None) != deleted:
        raise HTTPException(status_code=404, detail="Exclusion set not found.")
    return dict(row)


def _guard(session: Session, criteria: dict, ctx: str, confirm: bool) -> None:
    n, live = count_matches(session, criteria, ctx)
    if live and n / live > SHARE_CONFIRM and not confirm:
        raise HTTPException(status_code=409, detail={"code": "confirm_required", "matches": n, "live": live, "share": round(n / live, 3)})


def _name_taken(err: IntegrityError) -> HTTPException:
    if "exclusion_set_name_uq" in str(err.orig):
        return HTTPException(status_code=409, detail="name_taken")
    raise err


@router.get("/api/exclusions")
def list_sets(deleted: bool = False, scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    vis, p = _vis(scope)
    rows = run(session, f"""SELECT s.*, u.email AS updated_by_email FROM "{S}".exclusion_set s LEFT JOIN "{S}".users u ON u.id = s.updated_by
                            WHERE {vis} AND s.deleted_at IS {"NOT NULL" if deleted else "NULL"} ORDER BY s.ctx, lower(s.name)""", p).mappings().all()
    # How many contracts ONLY this set excludes (the rest are also excluded by another set) - shows redundancy.
    only = dict(run(session, f"""
        WITH h AS (SELECT x.set_id, count(*) OVER (PARTITION BY x.contractid) AS n FROM "{S}".exclusion_hit x
                   JOIN "{S}".exclusion_set s ON s.id = x.set_id AND s.status = 'active' AND s.deleted_at IS NULL WHERE {vis})
        SELECT set_id, count(*) FILTER (WHERE n = 1) FROM h GROUP BY set_id""", p).all())
    combined = dict(run(session, f"""SELECT s.ctx, count(DISTINCT x.contractid) FROM "{S}".exclusion_hit x
                                     JOIN "{S}".exclusion_set s ON s.id = x.set_id AND s.status = 'active' AND s.deleted_at IS NULL
                                     WHERE {vis} GROUP BY s.ctx""", p).all())
    return {"sets": [_out(dict(r), only.get(r["id"], 0) if r["status"] == "active" else None) for r in rows], "combined": combined}


class SetIn(BaseModel):
    ctx: str = Field(pattern=r"^\d{3}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    criteria: dict
    activate: bool = False
    confirm: bool = False


@router.post("/api/exclusions")
def create_set(body: SetIn, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    if not scope.allows(body.ctx):
        raise HTTPException(status_code=404, detail="Area not found.")
    crit = validated(body.criteria, allow_empty=False)
    if body.activate:
        _guard(session, crit, body.ctx, body.confirm)
    try:
        new = run(session, f"""INSERT INTO "{S}".exclusion_set (ctx, name, description, criteria, status, created_by, updated_by)
                               VALUES (:ctx, btrim(:name), :d, CAST(:crit AS jsonb), :st, CAST(:me AS uuid), CAST(:me AS uuid)) RETURNING id""",
                  {"ctx": body.ctx, "name": body.name, "d": body.description, "crit": json.dumps(crit),
                   "st": "active" if body.activate else "draft", "me": str(user.id)}).scalar()
    except IntegrityError as err:
        session.rollback()
        raise _name_taken(err) from err
    n = recompute_exclusion(session, str(new), body.ctx, crit) if body.activate else None
    db.audit(session, user, "exclusion.created", "exclusion_set", new, ctx_code=body.ctx,
             detail={"name": body.name.strip(), "criteria": crit, "active": body.activate, "matches": n})
    session.commit()
    return _out(_get(session, scope, new))


class SetUpdate(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    criteria: dict | None = None
    confirm: bool = False


@router.put("/api/exclusions/{set_id}")
def update_set(set_id: UUID, body: SetUpdate, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, scope, set_id)
    sets, params = [], {}
    if body.name is not None:
        sets.append("name = btrim(:name)"); params["name"] = body.name
    if body.description is not None:
        sets.append("description = :d"); params["d"] = body.description
    crit = row["criteria"]
    if body.criteria is not None:
        crit = validated(body.criteria, allow_empty=False)
        sets.append("criteria = CAST(:crit AS jsonb)"); params["crit"] = json.dumps(crit)
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    changed = crit != row["criteria"]
    if changed and row["status"] == "active":
        _guard(session, crit, row["ctx"], body.confirm)
    try:
        bump(session, "exclusion_set", str(set_id), body.version, ", ".join(sets), params, user.id)
    except IntegrityError as err:
        session.rollback()
        raise _name_taken(err) from err
    n = recompute_exclusion(session, str(set_id), row["ctx"], crit) if changed and row["status"] == "active" else None
    db.audit(session, user, "exclusion.updated", "exclusion_set", set_id, ctx_code=row["ctx"], detail={
        "name": [row["name"], body.name], "criteria": {"before": row["criteria"], "after": crit} if changed else None, "matches": n})
    session.commit()
    return _out(_get(session, scope, set_id))


class Transition(BaseModel):
    version: int
    confirm: bool = False


@router.post("/api/exclusions/{set_id}/activate")
def activate_set(set_id: UUID, body: Transition, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, scope, set_id)
    if row["status"] == "active":
        return _out(row)
    _guard(session, row["criteria"], row["ctx"], body.confirm)
    bump(session, "exclusion_set", str(set_id), body.version, "status = 'active'", {}, user.id)
    n = recompute_exclusion(session, str(set_id), row["ctx"], row["criteria"])
    db.audit(session, user, "exclusion.activated", "exclusion_set", set_id, ctx_code=row["ctx"], detail={"name": row["name"], "matches": n})
    session.commit()
    return _out(_get(session, scope, set_id))


@router.post("/api/exclusions/{set_id}/deactivate")
def deactivate_set(set_id: UUID, body: Transition, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, scope, set_id)
    bump(session, "exclusion_set", str(set_id), body.version, "status = 'draft', match_count = NULL", {}, user.id)
    run(session, f'DELETE FROM "{S}".exclusion_hit WHERE set_id = CAST(:id AS uuid)', {"id": str(set_id)})
    db.audit(session, user, "exclusion.deactivated", "exclusion_set", set_id, ctx_code=row["ctx"], detail={"name": row["name"]})
    session.commit()
    return _out(_get(session, scope, set_id))


@router.delete("/api/exclusions/{set_id}")
def delete_set(set_id: UUID, version: int, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, scope, set_id)
    bump(session, "exclusion_set", str(set_id), version, "deleted_at = now(), status = 'draft', match_count = NULL", {}, user.id)
    run(session, f'DELETE FROM "{S}".exclusion_hit WHERE set_id = CAST(:id AS uuid)', {"id": str(set_id)})
    db.audit(session, user, "exclusion.deleted", "exclusion_set", set_id, ctx_code=row["ctx"], detail={"name": row["name"], "criteria": row["criteria"]})
    session.commit()
    return {"ok": True}


@router.post("/api/exclusions/{set_id}/restore")
def restore_set(set_id: UUID, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, scope, set_id, deleted=True)
    try:
        run(session, f"""UPDATE "{S}".exclusion_set SET deleted_at = NULL, status = 'draft', version = version + 1, updated_at = now(),
                         updated_by = CAST(:me AS uuid) WHERE id = CAST(:id AS uuid)""", {"me": str(user.id), "id": str(set_id)})
    except IntegrityError as err:
        session.rollback()
        raise _name_taken(err) from err
    db.audit(session, user, "exclusion.restored", "exclusion_set", set_id, ctx_code=row["ctx"], detail={"name": row["name"]})
    session.commit()
    return _out(_get(session, scope, set_id))


class PreviewIn(BaseModel):
    ctx: str = Field(pattern=r"^\d{3}$")
    criteria: dict
    setId: UUID | None = None   # when editing: leave this set out of "newly excluded"


@router.post("/api/exclusions/preview")
def preview(body: PreviewIn, scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    if not scope.allows(body.ctx):
        raise HTTPException(status_code=404, detail="Area not found.")
    crit = validated(body.criteria, allow_empty=False)
    n, live = count_matches(session, crit, body.ctx)
    pred, params = compile_criteria(crit)
    newly = run(session, f"""SELECT count(*) FROM "{D}".contract c WHERE c.in_scope AND c.ctxid = :ctx AND {pred} AND NOT EXISTS (
        SELECT 1 FROM "{S}".exclusion_hit h JOIN "{S}".exclusion_set s ON s.id = h.set_id AND s.status = 'active' AND s.deleted_at IS NULL
        AND s.ctx = :ctx AND s.id <> CAST(:ex AS uuid) WHERE h.contractid = c.contractid)""",
        {**params, "ctx": body.ctx, "ex": str(body.setId or NIL)}).scalar()
    return {"matches": n, "live": live, "share": round(n / live, 3) if live else 0, "newlyExcluded": newly,
            "needsConfirm": bool(live and n / live > SHARE_CONFIRM)}


# ---- each user's own choice of which sets hide contracts from their screens ---------------------------
class PrefIn(BaseModel):
    mode: Literal["all", "custom", "none"]
    setIds: list[UUID] = []


def _pref(session: Session, user: User) -> dict:
    r = run(session, f'SELECT mode, set_ids FROM "{S}".user_exclusion_pref WHERE user_id = CAST(:u AS uuid)', {"u": str(user.id)}).first()
    return {"mode": r[0], "setIds": [str(i) for i in r[1]]} if r else {"mode": "all", "setIds": []}


@router.get("/api/me/exclusion-pref")
def get_pref(user: User = Depends(active_user), session: Session = Depends(db.get_db)):
    return _pref(session, user)


@router.put("/api/me/exclusion-pref")
def put_pref(body: PrefIn, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    ids = [str(i) for i in dict.fromkeys(body.setIds)] if body.mode == "custom" else []
    if ids:
        vis, p = _vis(scope)
        found = run(session, f'SELECT count(*) FROM "{S}".exclusion_set s WHERE s.id = ANY(CAST(:ids AS uuid[])) AND s.deleted_at IS NULL AND {vis}',
                    {**p, "ids": ids}).scalar()
        if found != len(ids):
            raise HTTPException(status_code=400, detail="One or more exclusion sets were not found.")
    run(session, f"""INSERT INTO "{S}".user_exclusion_pref (user_id, mode, set_ids) VALUES (CAST(:u AS uuid), :m, CAST(:ids AS uuid[]))
                     ON CONFLICT (user_id) DO UPDATE SET mode = EXCLUDED.mode, set_ids = EXCLUDED.set_ids""", {"u": str(user.id), "m": body.mode, "ids": ids})
    session.commit()
    return _pref(session, user)


# ---- what the criteria builder offers --------------------------------------------------------------------
@router.get("/api/rules/columns")
def rule_columns(_: User = Depends(active_user)):
    return [{"key": c.name, "label": column_label(c.name), "kind": c.kind, "picker": c.filter == "Value picker" or c.kind == "bool",
             "ops": sorted(OPS[c.kind]), "personal": c.personal} for c in COLUMNS.values()]


@router.get("/api/rules/columns/{column}/values")
def rule_column_values(column: str, scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    col = COLUMNS.get(column)
    if col is None or not (col.filter == "Value picker" or col.kind == "bool"):
        raise HTTPException(status_code=404, detail="No value list for this column.")
    where, p = ("TRUE", {}) if scope.ctxs is None else ("c.ctxid = ANY(:ctxs)", {"ctxs": sorted(scope.ctxs)})
    rows = run(session, f"""SELECT CAST(c."{col.name}" AS text), count(*) FROM "{D}".contract c WHERE c.in_scope AND {where}
                            AND c."{col.name}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 200""", p).all()
    return [{"value": v, "count": n} for v, n in rows]
