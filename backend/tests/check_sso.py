"""
Phase 1 SSO check. Plain asserts, no framework:

    cd backend && python tests/check_sso.py

There are no Microsoft credentials yet, so this stands up a small fake
identity provider (discovery document, authorize, token and key endpoints)
that issues REAL RS256-signed tokens and enforces PKCE, then drives the
app's actual /api/auth/login and /api/auth/callback routes through it.
Signature, issuer, audience, expiry, nonce, state and PKCE checks all
genuinely run - the only thing not covered is Microsoft's own server, so
the first real login still needs a smoke test once keys exist.
"""
import base64
import hashlib
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

CLIENT_ID, CLIENT_SECRET, TENANT = "client-1", "secret-1", "tenant-guid"
REDIRECT = "http://testserver/api/auth/callback"

# ------------------------------------------------------------------ fake IdP
GOOD_KEY = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})
EVIL_KEY = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})  # same kid, different key
idp = FastAPI()
codes: dict = {}
cfg = {"mode": "good", "oid": "oid-123", "email": "boss@x.com"}


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@idp.get("/tenant/v2.0/.well-known/openid-configuration")
def discovery():
    base = idp.state.base
    return {
        "issuer": f"{base}/tenant/v2.0",
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "jwks_uri": f"{base}/keys",
        "response_types_supported": ["code"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
    }


@idp.get("/keys")
def keys():
    return {"keys": [GOOD_KEY.as_dict(private=False)]}


@idp.get("/authorize")
def authorize(request: Request):
    q = request.query_params
    problems = []
    if q.get("response_type") != "code": problems.append("response_type")
    if q.get("client_id") != CLIENT_ID: problems.append("client_id")
    if q.get("redirect_uri") != REDIRECT: problems.append("redirect_uri")
    if not q.get("state"): problems.append("state")
    if not q.get("nonce"): problems.append("nonce")
    if not q.get("code_challenge") or q.get("code_challenge_method") != "S256": problems.append("pkce")
    if "openid" not in q.get("scope", "").split(): problems.append("scope")
    if problems:
        return JSONResponse({"error": "invalid_request", "problems": problems}, status_code=400)
    code = secrets.token_urlsafe(16)
    codes[code] = {"nonce": q["nonce"], "challenge": q["code_challenge"]}
    return RedirectResponse(f"{REDIRECT}?code={code}&state={q['state']}")


@idp.post("/token")
async def token(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    basic = request.headers.get("authorization", "")
    if basic.lower().startswith("basic "):
        cid, _, secret = base64.b64decode(basic[6:]).decode().partition(":")
        form.setdefault("client_id", cid)
        form["client_secret"] = secret
    if form.get("client_secret") != CLIENT_SECRET or form.get("grant_type") != "authorization_code":
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    entry = codes.pop(form.get("code", ""), None)  # single use
    if entry is None or form.get("redirect_uri") != REDIRECT:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    verifier = form.get("code_verifier", "")
    if b64url(hashlib.sha256(verifier.encode()).digest()) != entry["challenge"]:
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

    mode, now = cfg["mode"], int(time.time())
    claims = {
        "iss": f"{idp.state.base}/tenant/v2.0", "aud": CLIENT_ID, "iat": now, "exp": now + 3600,
        "nonce": entry["nonce"], "oid": cfg["oid"], "sub": "pairwise-sub", "tid": TENANT,
        "preferred_username": cfg["email"], "name": "Test User",
    }
    if mode == "bad_nonce": claims["nonce"] = "not-the-nonce"
    if mode == "bad_aud": claims["aud"] = "someone-else"
    if mode == "bad_iss": claims["iss"] = "https://evil.example/tenant/v2.0"
    if mode == "expired": claims["exp"] = now - 3600
    body = {"access_token": "at", "token_type": "Bearer", "expires_in": 3600}
    if mode != "no_id_token":
        signer = EVIL_KEY if mode == "bad_sig" else GOOD_KEY
        body["id_token"] = jwt.encode({"alg": "RS256", "kid": "k1"}, claims, signer)
    return body


with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
idp.state.base = f"http://127.0.0.1:{port}"
server = uvicorn.Server(uvicorn.Config(idp, host="127.0.0.1", port=port, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)

# ------------------------------------------------------------------ the app under test
import pg  # noqa: E402  (tests/pg.py)

os.environ.update(
    DATABASE_URL=pg.new_database(), ENV="development", AUTH_MODE="sso",
    MS_TENANT_ID=TENANT, MS_CLIENT_ID=CLIENT_ID, MS_CLIENT_SECRET=CLIENT_SECRET, MS_REDIRECT_URI=REDIRECT,
    OIDC_METADATA_URL=f"{idp.state.base}/tenant/v2.0/.well-known/openid-configuration",
    BOOTSTRAP_ADMIN_EMAILS="boss@x.com", SESSION_SECRET="s" * 40, APP_URL="/",
)
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402


def start_login(client):
    """Step 1+2: hit /login, then follow the redirect to the fake IdP by hand."""
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307), r.status_code
    loc = r.headers["location"]
    q = parse_qs(urlparse(loc).query)
    assert q["code_challenge_method"] == ["S256"] and "nonce" in q and "state" in q, q
    hop = httpx.get(loc, follow_redirects=False)
    assert hop.status_code in (302, 307), (hop.status_code, hop.text)  # the IdP accepted our request
    return hop.headers["location"].replace("http://testserver", "")


def sso_login(client, **idp_settings):
    cfg.update({"mode": "good", "oid": "oid-123", "email": "boss@x.com", **idp_settings})
    return client.get(start_login(client), follow_redirects=False)


with TestClient(main.app) as c:
    assert c.get("/api/auth/config").json() == {"mode": "sso", "loginUrl": "/api/auth/login"}
    assert c.post("/api/auth/dev/login", json={"email": "a@b.com"}).status_code in (404, 405)  # dev login doesn't exist in sso mode

    # 1. happy path: signed in, bootstrap email becomes the first admin
    r = sso_login(c)
    assert r.status_code == 302 and r.headers["location"] == "/", (r.status_code, r.text)
    me = c.get("/api/auth/me").json()
    assert me["email"] == "boss@x.com" and me["role"] == "admin" and me["status"] == "active", me
    first_id = me["id"]

    # 2. identity is the oid, not the email: same oid + new email is the SAME user, email refreshed
    c.post("/api/auth/logout")
    sso_login(c, email="renamed@x.com")
    me = c.get("/api/auth/me").json()
    assert me["id"] == first_id and me["email"] == "renamed@x.com", me
    c.post("/api/auth/logout")

    # 3. a different person lands as pending, with no data
    other = TestClient(main.app)
    sso_login(other, oid="oid-999", email="newbie@x.com")
    me = other.get("/api/auth/me").json()
    assert me["status"] == "pending" and me["role"] == "user", me
    assert other.get("/api/worklist").json()["detail"] == "access_pending"

    # 4. forged / invalid tokens are all rejected and leave NO session behind
    for bad in ("bad_nonce", "bad_sig", "bad_aud", "bad_iss", "expired", "no_id_token"):
        victim = TestClient(main.app)
        r = sso_login(victim, mode=bad, oid="oid-evil", email="boss@x.com")
        assert r.status_code == 400, (bad, r.status_code, r.text)
        assert victim.get("/api/auth/me").status_code == 401, bad
        assert victim.get("/api/worklist").status_code == 401, bad

    # 5. a tampered state parameter is rejected
    t = TestClient(main.app)
    cfg.update({"mode": "good", "oid": "oid-123", "email": "boss@x.com"})
    callback = start_login(t)
    r = t.get(callback.replace("state=", "state=tampered"), follow_redirects=False)
    assert r.status_code == 400 and t.get("/api/auth/me").status_code == 401

    # 6. replaying a valid callback a second time fails (state and code are single-use)
    t2 = TestClient(main.app)
    callback = start_login(t2)
    assert t2.get(callback, follow_redirects=False).status_code == 302
    t2.post("/api/auth/logout")
    assert t2.get(callback, follow_redirects=False).status_code == 400
    assert t2.get("/api/auth/me").status_code == 401

    # 7. a callback with no prior login (no state in the session) is rejected
    assert TestClient(main.app).get("/api/auth/callback?code=abc&state=xyz", follow_redirects=False).status_code == 400

print("ALL SSO CHECKS PASSED")
