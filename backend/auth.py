"""
Authentication and authorization.

Two modes, chosen by AUTH_MODE (never both at once - the routes of the
unused mode are not registered at all):

  sso  Microsoft Entra ID, OpenID Connect authorization-code flow with PKCE,
       via Authlib (state, nonce, PKCE and id_token signature/issuer/
       audience/expiry validation are Authlib's job, not hand-rolled here).
  dev  A local login that accepts any email, for development without
       Microsoft credentials. Refuses to start when ENV=production.

Either way a successful login does the same thing: look the user up by
their stable identity, insert them as 'pending' if new, and start a signed
session cookie holding ONLY the user id. Role, status and CTX are re-read
from the database on every request, so an admin deactivating someone or
changing their areas takes effect immediately instead of when a cookie
expires.

Dependency ladder used by the endpoints:
  current_user  -> logged in (401 otherwise); any status
  active_user   -> status active (403 access_pending / account_disabled)
  get_scope     -> active + the CTX set they may see
  admin_user    -> active admin (403 admin_required)
"""
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from db import SessionLocal, User, audit, get_db, reconcile_status, utcnow

log = logging.getLogger("auth")

ENV = os.environ.get("ENV", "development").lower()
# Secure by default: with nothing configured the app demands SSO settings and
# refuses to start, rather than silently accepting any email as a login.
# Local development opts in explicitly with AUTH_MODE=dev in .env.
AUTH_MODE = os.environ.get("AUTH_MODE", "sso").lower()
APP_URL = os.environ.get("APP_URL", "/")  # where to land after a successful SSO login
SESSION_MAX_AGE = int(float(os.environ.get("SESSION_MAX_AGE_HOURS", "12")) * 3600)

_secret_from_env = os.environ.get("SESSION_SECRET")
# Without an explicit secret a random one is used, which only works for a
# single dev process (sessions die on restart) - production refuses it below.
SESSION_SECRET = _secret_from_env or secrets.token_urlsafe(32)

# Emails that become the first admin(s). Only honored while NO active admin
# exists, so this can't be used to take over an established install. Email
# is acceptable here only because SSO is single-tenant (our own Entra
# tenant): in a multi-tenant app the email claim must never gate access.
BOOTSTRAP_ADMIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("BOOTSTRAP_ADMIN_EMAILS", "").split(",") if e.strip()
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def validate_config() -> None:
    """Called once at startup - fail loudly rather than run insecurely."""
    if AUTH_MODE not in ("dev", "sso"):
        raise RuntimeError(f"AUTH_MODE must be 'dev' or 'sso', got {AUTH_MODE!r}")
    if ENV == "production":
        if AUTH_MODE == "dev":
            raise RuntimeError("AUTH_MODE=dev is not allowed when ENV=production (it accepts any email).")
        if not _secret_from_env or len(_secret_from_env) < 32:
            raise RuntimeError("SESSION_SECRET must be set to a random value of at least 32 characters in production.")
    if AUTH_MODE == "sso":
        missing = [k for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "MS_REDIRECT_URI")
                   if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                f"AUTH_MODE=sso requires: {', '.join(missing)}. "
                "(For local development without Microsoft credentials, set AUTH_MODE=dev in .env.)"
            )


# --------------------------------------------------------------------------
# Users and scope
# --------------------------------------------------------------------------

def _active_admin_exists(db: Session) -> bool:
    n = db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.status == "active"))
    return bool(n)


def upsert_user(db: Session, oid: str, email: str, name: str) -> User:
    email = (email or "").strip().lower()
    user = db.scalar(select(User).where(User.oid == oid))
    if user is None:
        user = User(oid=oid, email=email, name=name or "", role="user", status="pending", last_login_at=utcnow())
        db.add(user)
        db.flush()
        audit(db, user, "user.first_login", "user", user.id, detail={"email": email})
    else:
        user.email = email or user.email
        user.name = name or user.name
        user.last_login_at = utcnow()

    if email in BOOTSTRAP_ADMIN_EMAILS and user.role != "admin" and not _active_admin_exists(db):
        user.role = "admin"
        reconcile_status(user)
        audit(db, user, "user.bootstrap_admin", "user", user.id, detail={"email": email})

    db.commit()
    return user


def _login_user(oid: str, email: str, name: str) -> uuid.UUID:
    with SessionLocal() as db:
        return upsert_user(db, oid, email, name).id


def start_session(request: Request, user_id: uuid.UUID) -> None:
    # Clear first so nothing from before login (SSO state, an earlier
    # user's session) survives into the authenticated session.
    request.session.clear()
    request.session["uid"] = str(user_id)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = None
    try:
        user = db.get(User, uuid.UUID(request.session.get("uid", "")))
    except (ValueError, AttributeError, TypeError):
        pass  # no session, or a cookie holding something that isn't a user id
    if user is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return user


def active_user(user: User = Depends(current_user)) -> User:
    if user.status == "disabled":
        raise HTTPException(status_code=403, detail="account_disabled")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="access_pending")
    return user


def admin_user(user: User = Depends(active_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return user


@dataclass(frozen=True)
class Scope:
    """Which CTXs the caller may see. ctxs=None means every CTX (admins),
    including contracts that have no CTX at all - those are visible to
    admins only, so a data gap fails closed rather than open."""
    ctxs: Optional[frozenset]

    def allows(self, ctx: Optional[str]) -> bool:
        return self.ctxs is None or (ctx is not None and ctx in self.ctxs)


def get_scope(user: User = Depends(active_user)) -> Scope:
    if user.role == "admin":
        return Scope(None)
    codes = frozenset(link.ctx_code for link in user.ctx_links)
    if not codes:  # defensive: status says active but nothing is assigned
        raise HTTPException(status_code=403, detail="access_pending")
    return Scope(codes)


def user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "status": user.status,
        "allCtx": user.role == "admin",
        "ctxs": [{"code": l.ctx_code, "name": l.ctx.name} for l in sorted(user.ctx_links, key=lambda l: l.ctx_code)],
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/config")
def auth_config():
    """Public: tells the UI which login screen to show."""
    return {"mode": AUTH_MODE, "loginUrl": "/api/auth/login" if AUTH_MODE == "sso" else None}


@router.get("/me")
def me(user: User = Depends(current_user)):
    # Deliberately current_user, not active_user: a pending or disabled user
    # must be able to ask "what is my state?" so the UI can show the right screen.
    return user_payload(user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


if AUTH_MODE == "dev":
    class DevLoginIn(BaseModel):
        email: str
        name: str | None = None

    @router.post("/dev/login")
    def dev_login(body: DevLoginIn, request: Request):
        email = body.email.strip().lower()
        if not EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="Enter a valid email address.")
        uid = _login_user(f"dev:{email}", email, body.name or email.split("@")[0])
        start_session(request, uid)
        with SessionLocal() as db:
            return user_payload(db.get(User, uid))


if AUTH_MODE == "sso":
    import httpx
    from authlib.integrations.starlette_client import OAuth, OAuthError
    from joserfc.errors import JoseError  # raised for a bad signature/issuer/audience/expiry/nonce

    _oauth = OAuth()

    def _microsoft():
        client = _oauth.create_client("microsoft")
        if client is None:
            tenant = os.environ["MS_TENANT_ID"]
            _oauth.register(
                name="microsoft",
                client_id=os.environ["MS_CLIENT_ID"],
                client_secret=os.environ["MS_CLIENT_SECRET"],
                # Override for sovereign clouds (login.microsoftonline.us, ...)
                # and for tests against a fake identity provider.
                server_metadata_url=os.environ.get("OIDC_METADATA_URL")
                or f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
                client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
            )
            client = _oauth.create_client("microsoft")
        return client

    @router.get("/login")
    async def sso_login(request: Request):
        return await _microsoft().authorize_redirect(request, os.environ["MS_REDIRECT_URI"])

    @router.get("/callback")
    async def sso_callback(request: Request):
        # The specific reason is logged for operators; the caller only learns
        # that sign-in failed, so a forged token gets no hints about which check tripped.
        client = _microsoft()
        try:
            # Passing claims_options REPLACES Authlib's default issuer check, so
            # issuer and audience are both stated explicitly here. Leaving
            # audience to Authlib's defaults only rejects a foreign token as a
            # side effect of its 'azp' rule - this makes it a deliberate check.
            metadata = await client.load_server_metadata()
            claims_options = {
                "iss": {"essential": True, "values": [metadata["issuer"]]},
                "aud": {"essential": True, "values": [os.environ["MS_CLIENT_ID"]]},
            }
            token = await client.authorize_access_token(request, claims_options=claims_options)
        except OAuthError as err:  # state mismatch, rejected code, PKCE failure, provider error
            log.warning("SSO callback rejected: %s %s", err.error, err.description)
            raise HTTPException(status_code=400, detail=f"SSO sign-in failed: {err.error}")
        except JoseError as err:  # id_token failed validation
            log.warning("SSO id_token rejected: %s", err)
            raise HTTPException(status_code=400, detail="SSO sign-in failed: identity token could not be validated")
        except httpx.HTTPError as err:  # couldn't reach the identity provider
            log.error("SSO identity provider unreachable: %s", err)
            raise HTTPException(status_code=502, detail="SSO sign-in failed: identity provider unreachable")

        # Authlib only fills "userinfo" after validating the id_token's
        # signature, issuer, audience, expiry and nonce. If it's missing,
        # validation didn't happen - never fall back to reading the token.
        claims = token.get("userinfo")
        if not claims:
            raise HTTPException(status_code=400, detail="SSO sign-in failed: identity token could not be validated")

        oid = claims.get("oid") or claims.get("sub")
        email = claims.get("email") or claims.get("preferred_username") or claims.get("upn") or ""
        if not oid:
            raise HTTPException(status_code=400, detail="SSO sign-in failed: no user identifier in token")

        uid = await run_in_threadpool(_login_user, f"entra:{oid}", email, claims.get("name") or email)
        start_session(request, uid)
        return RedirectResponse(APP_URL, status_code=302)
