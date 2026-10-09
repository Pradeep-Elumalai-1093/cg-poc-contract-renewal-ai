"""
Phase 1 access-control check. Plain asserts, no framework:

    cd backend && python tests/check_access.py

Drives the real FastAPI app (dev auth mode, throwaway PostgreSQL database
migrated by Alembic, synthetic contracts replaced by a small hand-built set) through the whole
lifecycle: first login -> pending -> admin assigns CTX -> scoped data ->
admin-only locks -> last-admin protection -> disable/enable -> audit.
The LLM agents are stubbed; nothing here calls a model.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import pg  # noqa: E402  (tests/pg.py)

DB = pg.new_database()
os.environ.update(
    DATABASE_URL=DB,
    AUTH_MODE="dev",
    ENV="development",
    BOOTSTRAP_ADMIN_EMAILS="boss@x.com",
    SESSION_SECRET="x" * 40,
)

# C1 spans two areas on purpose: that's the case where one shared customer view would leak.
pg.load_contracts(DB, [
    dict(CONTRACTID="A1", CUSTOMERID="C1", CTXID="034"), dict(CONTRACTID="A2", CUSTOMERID="C2", CTXID="034"),
    dict(CONTRACTID="B1", CUSTOMERID="C1", CTXID="049"), dict(CONTRACTID="B2", CUSTOMERID="C3", CTXID="049"),
    dict(CONTRACTID="N1", CUSTOMERID="C4", CTXID=None),  # no CTX -> admin-only
])
for cid, ctx in (("A1", "034"), ("B1", "049")):
    pg.query(DB, "INSERT INTO ai_recommendations.contract_recommendation (contractid, ctx, ai_recommendation, retention_action_name, outcome) "
                 "VALUES (%s, %s, 'why', 'Free service check-in', 'Engaged')", cid, ctx)


def outcome_of(cid):
    return pg.query(DB, "SELECT outcome FROM ai_recommendations.contract_recommendation WHERE contractid = %s AND is_latest", cid)[0]["outcome"]


from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


def ids(resp):
    assert resp.status_code == 200, resp.text
    return {r["id"] for r in resp.json()["rows"]}


def detail(resp):
    return resp.json().get("detail")


with TestClient(main.app) as boss, TestClient(main.app) as alice, TestClient(main.app) as anon, TestClient(main.app) as mallory:
    # 1. nobody is logged in
    assert anon.get("/api/worklist").status_code == 401
    assert anon.get("/api/auth/me").status_code == 401
    assert anon.get("/api/auth/config").json()["mode"] == "dev"

    # 2. a new user lands as pending and sees no data
    r = alice.post("/api/auth/dev/login", json={"email": "Alice@X.com"})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["email"] == "alice@x.com", r.text
    assert alice.get("/api/auth/me").json()["status"] == "pending"
    for path in ("/api/worklist", "/api/summary", "/api/contracts/A1", "/api/me/worklist-view", "/api/model-info"):
        resp = alice.get(path)
        assert resp.status_code == 403 and detail(resp) == "access_pending", (path, resp.status_code, resp.text)
    assert alice.post("/api/auth/dev/login", json={"email": "not-an-email"}).status_code == 400

    # 3. only a bootstrap email becomes admin; anyone else stays pending
    r = boss.post("/api/auth/dev/login", json={"email": "boss@x.com"})
    assert r.json()["role"] == "admin" and r.json()["status"] == "active", r.text
    assert mallory.post("/api/auth/dev/login", json={"email": "mallory@x.com"}).json()["status"] == "pending"
    assert alice.get("/api/admin/users").status_code == 403  # pending user can't use admin routes

    users = boss.get("/api/admin/users").json()
    assert [u["status"] for u in users][:2] == ["pending", "pending"], users  # pending first
    uid = {u["email"]: u["id"] for u in users}

    # 4. CTX codes were discovered from the loaded contracts and can be named
    ctx = boss.get("/api/admin/ctx").json()
    assert {c["code"] for c in ctx["ctxs"]} == {"034", "049"} and ctx["contractsWithoutCtx"] == 1, ctx
    assert boss.put("/api/admin/ctx/034", json={"name": "Spain"}).json()["name"] == "Spain"
    assert boss.put("/api/admin/ctx/999", json={"name": "Nowhere"}).status_code == 404

    # 5. assigning a CTX activates the user; unknown codes are rejected
    assert boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": ["999"]}).status_code == 400
    r = boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": ["034"]})
    assert r.json()["status"] == "active" and r.json()["ctxs"] == ["034"], r.text
    me = alice.get("/api/auth/me").json()
    assert me["status"] == "active" and me["ctxs"] == [{"code": "034", "name": "Spain"}] and me["allCtx"] is False

    # 6. alice sees only CTX 034 - not other areas, not the no-CTX contract
    assert ids(alice.get("/api/worklist")) == {"A1", "A2"}
    assert ids(boss.get("/api/worklist")) == {"A1", "A2", "B1", "B2", "N1"}
    assert ids(alice.get("/api/worklist?area=049")) == set()  # asking for another area's code doesn't widen the scope

    # 7. the summary is computed over the caller's scope only
    sa, sb = alice.get("/api/summary").json(), boss.get("/api/summary").json()
    assert sa["kpis"]["contracts"] == 2 and sb["kpis"]["contracts"] == 5, (sa["kpis"], sb["kpis"])
    assert [c["assigned"] for c in sa["campaigns"]] == [1] and [c["assigned"] for c in sb["campaigns"]] == [2]
    assert sum(sa["outcomeByRisk"]["engaged"]) == 1 and sum(sb["outcomeByRisk"]["engaged"]) == 2
    assert {r["ctx"] for r in sa["byCtx"]} == {"034"}

    # 8. writes on another area's records look like "not found", not "forbidden"
    for path, body in (("/api/feedback", {"contractId": "B1", "outcome": "Declined"}),
                       ("/api/action-status", {"contractId": "B1", "actionStatus": "Action done"})):
        r = alice.post(path, json=body)
        assert r.status_code == 404, (path, r.status_code)
    assert outcome_of("B1") == "Engaged"  # untouched
    assert alice.post("/api/feedback", json={"contractId": "A1", "outcome": "Declined"}).status_code == 200
    assert outcome_of("A1") == "Declined"
    assert alice.post("/api/action-status", json={"contractId": "A1", "actionStatus": "Action done"}).status_code == 200
    assert alice.post("/api/action-status", json={"contractId": "A1", "actionStatus": "Maybe"}).status_code == 422
    assert alice.post("/api/feedback", json={"contractId": "A2", "outcome": "Engaged"}).status_code == 404  # no recommendation yet

    # 9. a contract's detail, the customer portfolio and contact data respect the scope
    assert alice.get("/api/contracts/B1").status_code == 404 and alice.get("/api/contracts/N1").status_code == 404
    d = alice.get("/api/contracts/A1").json()
    assert d["contract"]["contractId"] == "A1" and d["trace"]["recommendation"]["campaign"] == "Free service check-in"
    assert [p["contractId"] for p in d["portfolio"]] == ["A1"], d["portfolio"]  # C1 also holds B1 in 049: it must not appear
    assert not any(i["key"] in ("address", "phoneopen", "postcode") for g in d["details"] for i in g["items"]), "personal data in the detail"
    assert alice.post("/api/contracts/B1/contact").status_code == 404
    c = alice.post("/api/contracts/A1/contact").json()["contact"]
    assert {"address", "phoneopen"} <= {i["key"] for i in c}

    # 10. each user has their own worklist view; bad requests are refused
    assert alice.put("/api/me/worklist-view", json={"columns": ["ctxid", "company"]}).json()["columns"] == ["ctxid", "company"]
    assert boss.get("/api/me/worklist-view").json()["columns"] != ["ctxid", "company"]
    assert alice.put("/api/me/worklist-view", json={"columns": ["no_such_column"]}).status_code == 400

    # 11. admin routes are admin-only
    for method, path in (("get", "/api/admin/users"), ("get", "/api/admin/audit"), ("get", "/api/admin/ctx")):
        resp = getattr(alice, method)(path)
        assert resp.status_code == 403 and detail(resp) == "admin_required", (path, resp.status_code, resp.text)

    # 12. a regional user holds several CTXs and sees exactly their union
    boss.put(f"/api/admin/users/{uid['mallory@x.com']}/ctx", json={"ctxs": ["034", "049"]})
    assert ids(mallory.get("/api/worklist")) == {"A1", "A2", "B1", "B2"}

    # 13. removing every CTX puts a user back to pending immediately (no stale session)
    r = boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": []})
    assert r.json()["status"] == "pending"
    assert detail(alice.get("/api/worklist")) == "access_pending"
    boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": ["034"]})

    # 14. disable / re-enable
    assert boss.put(f"/api/admin/users/{uid['alice@x.com']}/status", json={"disabled": True}).json()["status"] == "disabled"
    assert detail(alice.get("/api/worklist")) == "account_disabled"
    # assigning a CTX must not silently re-enable a disabled user
    assert boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": ["034", "049"]}).json()["status"] == "disabled"
    assert boss.put(f"/api/admin/users/{uid['alice@x.com']}/status", json={"disabled": False}).json()["status"] == "active"
    assert ids(alice.get("/api/worklist")) == {"A1", "A2", "B1", "B2"}

    # 15. the last active admin can't be demoted or disabled
    boss_id = uid["boss@x.com"]
    assert boss.put(f"/api/admin/users/{boss_id}/role", json={"role": "user"}).status_code == 400
    assert boss.put(f"/api/admin/users/{boss_id}/status", json={"disabled": True}).status_code == 400
    boss.put(f"/api/admin/users/{uid['alice@x.com']}/role", json={"role": "admin"})
    assert ids(alice.get("/api/worklist")) == {"A1", "A2", "B1", "B2", "N1"}  # admin sees everything, incl. no-CTX
    assert boss.put(f"/api/admin/users/{boss_id}/role", json={"role": "user"}).status_code == 200  # now allowed
    assert alice.put(f"/api/admin/users/{uid['alice@x.com']}/role", json={"role": "user"}).status_code == 400  # alice is last

    # 16. logging out ends the session
    assert alice.post("/api/auth/logout").status_code == 200
    assert alice.get("/api/worklist").status_code == 401

    # 17. everything above left an audit trail
    carol = TestClient(main.app)
    carol.post("/api/auth/dev/login", json={"email": "boss@x.com"})  # boss was demoted -> no admin route access
    assert carol.get("/api/admin/audit").status_code == 403
    alice.post("/api/auth/dev/login", json={"email": "alice@x.com"})
    audit = alice.get("/api/admin/audit").json()
    seen = {a["action"] for a in audit}
    assert {"user.first_login", "user.bootstrap_admin", "user.ctx_assigned", "user.role_changed",
            "user.disabled", "user.enabled", "ctx.renamed", "contact.viewed"} <= seen, seen
    assert any(a["action"] == "user.ctx_assigned" and a["detail"]["after"] == ["034"] for a in audit)


# --- startup guards (separate processes, because config is read at import) ----------
def boot(**env):
    code = "import auth; auth.validate_config(); print('started')"
    full = {**os.environ, **env}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full, cwd=BACKEND)


# an unset AUTH_MODE must fail closed (sso, with no Microsoft config), never fall back to dev
unset = {k: v for k, v in os.environ.items() if k not in ("AUTH_MODE", "MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "MS_REDIRECT_URI")}
r = subprocess.run([sys.executable, "-c", "import auth; auth.validate_config()"], capture_output=True, text=True, env=unset, cwd=BACKEND)
assert r.returncode != 0 and "MS_TENANT_ID" in r.stderr and "AUTH_MODE=dev" in r.stderr, r.stderr
r = boot(ENV="production", AUTH_MODE="dev")
assert r.returncode != 0 and "AUTH_MODE=dev is not allowed" in r.stderr, r.stderr
r = boot(ENV="production", AUTH_MODE="sso", MS_TENANT_ID="t", MS_CLIENT_ID="c", MS_CLIENT_SECRET="s",
         MS_REDIRECT_URI="https://x/cb", SESSION_SECRET="short")
assert r.returncode != 0 and "SESSION_SECRET" in r.stderr, r.stderr
r = boot(AUTH_MODE="sso", MS_TENANT_ID="", MS_CLIENT_ID="", MS_CLIENT_SECRET="", MS_REDIRECT_URI="")
assert r.returncode != 0 and "MS_TENANT_ID" in r.stderr, r.stderr
r = boot(ENV="production", AUTH_MODE="sso", MS_TENANT_ID="t", MS_CLIENT_ID="c", MS_CLIENT_SECRET="s",
         MS_REDIRECT_URI="https://x/cb", SESSION_SECRET="y" * 40)
assert r.returncode == 0, r.stderr
r = boot(AUTH_MODE="sso", MS_TENANT_ID="t", MS_CLIENT_ID="c", MS_CLIENT_SECRET="s", MS_REDIRECT_URI="https://x/cb")
assert r.returncode == 0, r.stderr  # sso in development is fine

print("ALL CHECKS PASSED")
