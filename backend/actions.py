"""
Retention actions: the menu the LLM recommends from.

  GET/POST /api/retention-actions                  list what the caller can see / create
  PUT /api/retention-actions/{id}                  edit (needs the version you read)
  DELETE /api/retention-actions/{id}?version=      soft delete;  POST .../restore
  POST /api/retention-actions/{id}/clone           copy into an area you own ("clone, then own")
  POST /api/retention-actions/preview              which contracts a criteria would apply to

GLOBAL actions apply to every area and are managed by admins only. LOCAL actions belong to one area
and are managed by anyone with that area. A local action overrides a global one only when category,
sub-category and name all match exactly (ignoring case and surrounding spaces) - so cloning a global
action without renaming it is how an area customises it, and renaming the clone makes it a separate
action instead. Empty criteria = the action applies to every contract.
"""
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import db
from auth import Scope, User, active_user, get_scope
from matching import bump, count_matches, recompute_action, run, validated

router = APIRouter()
S = db.SCHEMA


def _key(r: dict) -> tuple:
    return (r["category"].strip().lower(), r["sub_category"].strip().lower(), r["name"].strip().lower())


def _out(r: dict, overrides_global: bool = False, overridden_in: list | None = None) -> dict:
    return {"id": str(r["id"]), "scope": r["scope"], "ctx": r["ctx"], "category": r["category"], "subCategory": r["sub_category"],
            "name": r["name"], "description": r["description"], "criteria": r["criteria"], "version": r["version"],
            "matchCount": r["match_count"], "updatedAt": db.iso(r["updated_at"]), "updatedBy": r.get("updated_by_email"),
            "deleted": r["deleted_at"] is not None, "overridesGlobal": overrides_global, "overriddenIn": overridden_in or []}


def _can_manage(user: User, scope: Scope, a_scope: str, ctx: str | None) -> bool:
    return user.role == "admin" if a_scope == "global" else (ctx is not None and scope.allows(ctx))


def _get(session: Session, user: User, scope: Scope, action_id: UUID, deleted: bool = False) -> dict:
    row = run(session, f"""SELECT a.*, u.email AS updated_by_email FROM "{S}".retention_action a LEFT JOIN "{S}".users u ON u.id = a.updated_by
                           WHERE a.id = CAST(:id AS uuid)""", {"id": str(action_id)}).mappings().first()
    visible = row is not None and (row["scope"] == "global" or scope.allows(row["ctx"]))
    if not visible or (row["deleted_at"] is not None) != deleted:
        raise HTTPException(status_code=404, detail="Retention action not found.")
    return dict(row)


def _must_manage(user: User, scope: Scope, row: dict) -> None:
    if not _can_manage(user, scope, row["scope"], row["ctx"]):
        raise HTTPException(status_code=403, detail="admin_required" if row["scope"] == "global" else "not_your_area")


def _taken(err: IntegrityError) -> HTTPException:
    if "retention_action_key_uq" in str(err.orig):
        return HTTPException(status_code=409, detail="already_exists")
    raise err


def _shown(session: Session, user: User, scope: Scope, action_id: UUID) -> dict:
    """One action as the list shows it, including whether it overrides a global action."""
    r = _get(session, user, scope, action_id)
    if r["scope"] != "local":
        return _out(r)
    hit = run(session, f"""SELECT 1 FROM "{S}".retention_action g WHERE g.scope = 'global' AND g.deleted_at IS NULL
                           AND lower(btrim(g.category)) = :c AND lower(btrim(g.sub_category)) = :s AND lower(btrim(g.name)) = :n LIMIT 1""",
              dict(zip("csn", _key(r)))).first()
    return _out(r, overrides_global=hit is not None)


@router.get("/api/retention-actions")
def list_actions(deleted: bool = False, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    where, p = ("a.scope = 'global' OR TRUE", {}) if scope.ctxs is None else ("a.scope = 'global' OR a.ctx = ANY(:ctxs)", {"ctxs": sorted(scope.ctxs)})
    rows = [dict(r) for r in run(session, f"""SELECT a.*, u.email AS updated_by_email FROM "{S}".retention_action a
        LEFT JOIN "{S}".users u ON u.id = a.updated_by WHERE ({where}) AND a.deleted_at IS {"NOT NULL" if deleted else "NULL"}
        ORDER BY a.scope, lower(a.category), lower(a.sub_category), lower(a.name), a.ctx""", p).mappings()]
    if deleted:
        return [_out(r) for r in rows]
    globals_ = {_key(r) for r in rows if r["scope"] == "global"}
    local_by_key: dict = {}
    for r in rows:
        if r["scope"] == "local":
            local_by_key.setdefault(_key(r), []).append(r["ctx"])
    return [_out(r, overrides_global=r["scope"] == "local" and _key(r) in globals_,
                 overridden_in=sorted(local_by_key.get(_key(r), [])) if r["scope"] == "global" else None) for r in rows]


class ActionIn(BaseModel):
    scope: Literal["global", "local"]
    ctx: str | None = Field(default=None, pattern=r"^\d{3}$")
    category: str = Field(min_length=1, max_length=100)
    subCategory: str = Field(default="", max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=4000)
    criteria: dict = {}


@router.post("/api/retention-actions")
def create_action(body: ActionIn, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    if (body.scope == "global") != (body.ctx is None):
        raise HTTPException(status_code=422, detail="A global action has no area; a local action needs one.")
    if not _can_manage(user, scope, body.scope, body.ctx):
        raise HTTPException(status_code=403, detail="admin_required" if body.scope == "global" else "not_your_area")
    crit = validated(body.criteria, allow_empty=True)
    try:
        new = run(session, f"""INSERT INTO "{S}".retention_action (scope, ctx, category, sub_category, name, description, criteria, created_by, updated_by)
                               VALUES (:sc, :ctx, btrim(:cat), btrim(:sub), btrim(:name), :d, CAST(:crit AS jsonb), CAST(:me AS uuid), CAST(:me AS uuid))
                               RETURNING id""", {"sc": body.scope, "ctx": body.ctx, "cat": body.category, "sub": body.subCategory,
                                                 "name": body.name, "d": body.description, "crit": json.dumps(crit), "me": str(user.id)}).scalar()
    except IntegrityError as err:
        session.rollback()
        raise _taken(err) from err
    n = recompute_action(session, str(new), body.ctx, crit)
    db.audit(session, user, "retention_action.created", "retention_action", new, ctx_code=body.ctx,
             detail={"scope": body.scope, "category": body.category, "name": body.name, "criteria": crit, "matches": n})
    session.commit()
    return _shown(session, user, scope, new)


class ActionUpdate(BaseModel):
    version: int
    category: str | None = Field(default=None, min_length=1, max_length=100)
    subCategory: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    criteria: dict | None = None


@router.put("/api/retention-actions/{action_id}")
def update_action(action_id: UUID, body: ActionUpdate, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, user, scope, action_id)
    _must_manage(user, scope, row)
    sets, params = [], {}
    for field, column, expr in (("category", "category", "btrim(:category)"), ("subCategory", "sub_category", "btrim(:sub_category)"),
                                ("name", "name", "btrim(:name)"), ("description", "description", ":description")):
        value = getattr(body, field)
        if value is not None:
            sets.append(f"{column} = {expr}"); params[column] = value
    crit = row["criteria"]
    if body.criteria is not None:
        crit = validated(body.criteria, allow_empty=True)
        sets.append("criteria = CAST(:crit AS jsonb)"); params["crit"] = json.dumps(crit)
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    try:
        bump(session, "retention_action", str(action_id), body.version, ", ".join(sets), params, user.id)
    except IntegrityError as err:
        session.rollback()
        raise _taken(err) from err
    n = recompute_action(session, str(action_id), row["ctx"], crit) if crit != row["criteria"] else row["match_count"]
    db.audit(session, user, "retention_action.updated", "retention_action", action_id, ctx_code=row["ctx"], detail={
        "before": {k: row[k] for k in ("category", "sub_category", "name", "criteria")}, "after": {**params, "criteria": crit}, "matches": n})
    session.commit()
    return _shown(session, user, scope, action_id)


@router.delete("/api/retention-actions/{action_id}")
def delete_action(action_id: UUID, version: int, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, user, scope, action_id)
    _must_manage(user, scope, row)   # a global action can only be deleted by an admin
    bump(session, "retention_action", str(action_id), version, "deleted_at = now(), match_count = NULL", {}, user.id)
    run(session, f'DELETE FROM "{S}".retention_action_match WHERE action_id = CAST(:id AS uuid)', {"id": str(action_id)})
    db.audit(session, user, "retention_action.deleted", "retention_action", action_id, ctx_code=row["ctx"],
             detail={"scope": row["scope"], "name": row["name"], "criteria": row["criteria"]})
    session.commit()
    return {"ok": True}


@router.post("/api/retention-actions/{action_id}/restore")
def restore_action(action_id: UUID, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    row = _get(session, user, scope, action_id, deleted=True)
    _must_manage(user, scope, row)
    try:
        run(session, f"""UPDATE "{S}".retention_action SET deleted_at = NULL, version = version + 1, updated_at = now(), updated_by = CAST(:me AS uuid)
                         WHERE id = CAST(:id AS uuid)""", {"me": str(user.id), "id": str(action_id)})
    except IntegrityError as err:
        session.rollback()
        raise _taken(err) from err
    recompute_action(session, str(action_id), row["ctx"], row["criteria"])
    db.audit(session, user, "retention_action.restored", "retention_action", action_id, ctx_code=row["ctx"], detail={"name": row["name"]})
    session.commit()
    return _shown(session, user, scope, action_id)


class CloneIn(BaseModel):
    ctx: str = Field(pattern=r"^\d{3}$")
    category: str | None = Field(default=None, min_length=1, max_length=100)
    subCategory: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=100)


@router.post("/api/retention-actions/{action_id}/clone")
def clone_action(action_id: UUID, body: CloneIn, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    src = _get(session, user, scope, action_id)
    if not scope.allows(body.ctx):
        raise HTTPException(status_code=404, detail="Area not found.")
    new = ActionIn(scope="local", ctx=body.ctx, category=body.category or src["category"], subCategory=src["sub_category"] if body.subCategory is None else body.subCategory,
                   name=body.name or src["name"], description=src["description"], criteria=src["criteria"])
    return create_action(new, user, scope, session)


class PreviewIn(BaseModel):
    ctx: str | None = Field(default=None, pattern=r"^\d{3}$")
    criteria: dict = {}


@router.post("/api/retention-actions/preview")
def preview_action(body: PreviewIn, user: User = Depends(active_user), scope: Scope = Depends(get_scope), session: Session = Depends(db.get_db)):
    if body.ctx is None and user.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")   # across every area: only an admin may look
    if body.ctx is not None and not scope.allows(body.ctx):
        raise HTTPException(status_code=404, detail="Area not found.")
    crit = validated(body.criteria, allow_empty=True)
    n, live = count_matches(session, crit, body.ctx)
    return {"matches": n, "live": live, "share": round(n / live, 3) if live else 0}
