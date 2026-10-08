"""
Admin-only endpoints behind the Access page: who has signed in, which CTXs
(areas) they may see, role changes, deactivation, and the audit trail.

Every route here requires an active admin (router-level dependency), and
every change writes an audit row in the same transaction as the change.
"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import admin_user
from db import AuditLog, Ctx, User, UserCtx, audit, get_db, iso, live_contract_counts, reconcile_status, sync_ctx_codes

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_user)])

_STATUS_ORDER = {"pending": 0, "active": 1, "disabled": 2}


def _user_row(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "status": user.status,
        "lastLoginAt": iso(user.last_login_at),
        "ctxs": sorted(l.ctx_code for l in user.ctx_links),
    }


def _get_user(db: Session, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def _ensure_another_active_admin(db: Session, user: User) -> None:
    """Locking everyone out of the admin page would need a database edit to
    recover from, so the last active admin can't be demoted or disabled."""
    others = db.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.status == "active", User.id != user.id)
    )
    if not others:
        raise HTTPException(status_code=400, detail="last_admin: at least one active admin must remain.")


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = list(db.scalars(select(User)))
    # Pending users first - they're the ones waiting on an admin.
    users.sort(key=lambda u: (_STATUS_ORDER.get(u.status, 3), u.email))
    return [_user_row(u) for u in users]


class CtxAssignIn(BaseModel):
    ctxs: list[str]


@router.put("/users/{user_id}/ctx")
def assign_ctx(user_id: UUID, body: CtxAssignIn, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    wanted = sorted({c.strip() for c in body.ctxs if c.strip()})
    known = set(db.scalars(select(Ctx.code)))
    unknown = [c for c in wanted if c not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown CTX code(s): {', '.join(unknown)}")

    before = sorted(l.ctx_code for l in user.ctx_links)
    previous_status = user.status
    current = {l.ctx_code: l for l in user.ctx_links}
    for code, link in current.items():
        if code not in wanted:
            user.ctx_links.remove(link)
    for code in wanted:
        if code not in current:
            user.ctx_links.append(UserCtx(ctx_code=code))
    reconcile_status(user)

    audit(db, actor, "user.ctx_assigned", "user", user.id,
          detail={"email": user.email, "before": before, "after": wanted, "status": [previous_status, user.status]})
    db.commit()
    return _user_row(user)


class RoleIn(BaseModel):
    role: Literal["admin", "user"]


@router.put("/users/{user_id}/role")
def set_role(user_id: UUID, body: RoleIn, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    if user.role == "admin" and body.role == "user" and user.status == "active":
        _ensure_another_active_admin(db, user)
    previous = (user.role, user.status)
    user.role = body.role
    reconcile_status(user)
    audit(db, actor, "user.role_changed", "user", user.id,
          detail={"email": user.email, "role": [previous[0], user.role], "status": [previous[1], user.status]})
    db.commit()
    return _user_row(user)


class DisableIn(BaseModel):
    disabled: bool


@router.put("/users/{user_id}/status")
def set_disabled(user_id: UUID, body: DisableIn, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    previous = user.status
    if body.disabled:
        if user.role == "admin" and user.status == "active":
            _ensure_another_active_admin(db, user)
        user.status = "disabled"
    elif user.status == "disabled":
        user.status = "pending"
        reconcile_status(user)  # back to active if they still hold a CTX / are an admin
    audit(db, actor, "user.disabled" if body.disabled else "user.enabled", "user", user.id,
          detail={"email": user.email, "status": [previous, user.status]})
    db.commit()
    return _user_row(user)


@router.get("/ctx")
def list_ctx(db: Session = Depends(get_db)):
    counts = live_contract_counts(db)
    # A load can introduce an area nobody has seen yet; registering it here lets an admin assign it right away.
    if sync_ctx_codes(db, [c for c in counts if c]):
        db.commit()
    rows = [
        {"code": c.code, "name": c.name, "contractCount": counts.get(c.code, 0)}
        for c in db.scalars(select(Ctx).order_by(Ctx.code))
    ]
    # Contracts with no (or an unparseable) CTX are visible to admins only -
    # surfacing the count tells you when the source data is missing the column.
    return {"ctxs": rows, "contractsWithoutCtx": counts.get(None, 0)}


class CtxNameIn(BaseModel):
    name: str


@router.put("/ctx/{code}")
def rename_ctx(code: str, body: CtxNameIn, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
    ctx = db.get(Ctx, code)
    if ctx is None:
        raise HTTPException(status_code=404, detail="CTX not found.")
    name = body.name.strip()
    if not name or len(name) > 100:
        raise HTTPException(status_code=400, detail="Name must be 1-100 characters.")
    previous, ctx.name = ctx.name, name
    audit(db, actor, "ctx.renamed", "ctx", code, ctx_code=code, detail={"name": [previous, name]})
    db.commit()
    return {"code": ctx.code, "name": ctx.name}


@router.get("/audit")
def list_audit(limit: int = 100, db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 500)
    # UUIDv7 ids are time-ordered, but two rows in the same millisecond have no
    # guaranteed order between them - the timestamp leads, the id breaks ties.
    rows = db.scalars(select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit))
    return [
        {
            "id": str(r.id), "at": iso(r.at), "actorEmail": r.actor_email, "action": r.action,
            "entity": r.entity, "entityId": r.entity_id, "ctxCode": r.ctx_code,
            "detail": r.detail,
        }
        for r in rows
    ]
