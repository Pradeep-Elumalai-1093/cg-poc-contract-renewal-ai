"""
Phase 1 access-control check. Plain asserts, no framework:

    cd backend && python tests/check_access.py

Drives the real FastAPI app (dev auth mode, throwaway SQLite database,
synthetic contracts replaced by a small hand-built set) through the whole
lifecycle: first login -> pending -> admin assigns CTX -> scoped data ->
admin-only locks -> last-admin protection -> disable/enable -> audit.
The LLM agents are stubbed; nothing here calls a model.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{tmp}/check.db",
    DATA_SOURCE="synthetic",
    AUTH_MODE="dev",
    ENV="development",
    BOOTSTRAP_ADMIN_EMAILS="boss@x.com",
    SESSION_SECRET="x" * 40,
)

from fastapi.testclient import TestClient  # noqa: E402

import agents  # noqa: E402
import main  # noqa: E402
from state import state  # noqa: E402


def contract(cid, cust, ctx, segment="Standard"):
    return {
        "contractId": cid, "customerId": cust, "customerName": f"Customer {cust}", "ctx": ctx,
        "region": "ETT", "channel": "Direct", "bucket": ">90", "segment": segment, "riskScore": 40,
        "contractValue": 1000, "lastMilestoneProcessed": None, "equipment": {"type": "Reefer Unit", "count": 1, "avgAgeYears": 1},
    }


def trace_row(cid):
    return {"contractId": cid, "retryCount": 0, "pass": True, "escalated": False, "latencyMs": 10, "costUsd": 0.01,
            "recommendation": {"campaign": "Free service check-in"}, "outcome": "Engaged", "context": {"risk_score": 40}}


# C1 spans two areas on purpose: that's the case where one shared customer summary would leak.
state.contracts = [
    contract("A1", "C1", "034"), contract("A2", "C2", "034"),
    contract("B1", "C1", "049"), contract("B2", "C3", "049"),
    contract("N1", "C4", None),  # no CTX -> admin-only
]
state._index()
state.trace = [trace_row("A1"), trace_row("B1")]
state.ticket_summaries, state.customer_summaries = {}, {}


async def stub_ticket(c):
    return {"status": "done", "data": f"ticket summary {c['contractId']}"}


async def stub_customer(customer_id, name, contracts):
    return {"status": "done", "data": "customer summary over " + ",".join(c["contractId"] for c in contracts)}


agents.run_ticket_summary_agent = stub_ticket
agents.run_customer_summary_agent = stub_customer


def ids(resp):
    return {c["contractId"] for c in resp.json()}


def detail(resp):
    return resp.json().get("detail")


with TestClient(main.app) as boss, TestClient(main.app) as alice, TestClient(main.app) as anon, TestClient(main.app) as mallory:
    # 1. nobody is logged in
    assert anon.get("/api/contracts").status_code == 401
    assert anon.get("/api/auth/me").status_code == 401
    assert anon.get("/api/auth/config").json()["mode"] == "dev"

    # 2. a new user lands as pending and sees no data
    r = alice.post("/api/auth/dev/login", json={"email": "Alice@X.com"})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["email"] == "alice@x.com", r.text
    assert alice.get("/api/auth/me").json()["status"] == "pending"
    for path in ("/api/contracts", "/api/trace", "/api/metrics", "/api/model-info", "/api/region-summary"):
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

    # 4. CTX codes were auto-discovered from the contracts and can be named
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
    assert ids(alice.get("/api/contracts")) == {"A1", "A2"}
    assert ids(boss.get("/api/contracts")) == {"A1", "A2", "B1", "B2", "N1"}

    # 7. aggregates and trace are computed over the caller's scope only
    assert {t["contractId"] for t in alice.get("/api/trace").json()} == {"A1"}
    assert alice.get("/api/metrics").json()["totalRuns"] == 1 and boss.get("/api/metrics").json()["totalRuns"] == 2
    assert alice.get("/api/region-summary").json()["global"]["contractCount"] == 2
    assert boss.get("/api/region-summary").json()["global"]["contractCount"] == 5
    assert alice.get("/api/campaigns").json()["Free service check-in"]["assigned"] == 1
    assert sum(alice.get("/api/outcome-by-risk-bucket").json()["engaged"]) == 1

    # 8. writes on another area's records look like "not found", not "forbidden"
    for path, body in (("/api/feedback", {"contractId": "B1", "outcome": "Declined"}),
                       ("/api/action-status", {"contractId": "B1", "actionStatus": "Action done"})):
        r = alice.post(path, json=body)
        assert r.status_code == 404, (path, r.status_code)
        assert state.latest_trace_for("B1").get("outcome") == "Engaged"  # untouched
    assert alice.post("/api/feedback", json={"contractId": "A1", "outcome": "Declined"}).status_code == 200
    assert alice.post("/api/action-status", json={"contractId": "A1", "actionStatus": "Action done"}).status_code == 200
    assert alice.post("/api/ticket-summaries/run", json={"contractId": "B1"}).status_code == 404
    assert alice.post("/api/ticket-summaries/run", json={"contractId": "N1"}).status_code == 404
    assert alice.post("/api/ticket-summaries/run", json={"contractId": "A1"}).status_code == 200

    # 9. customer summaries: per (customer, CTX), built only from that CTX's contracts
    s034 = boss.post("/api/customer-summaries/run", json={"customerId": "C1", "ctx": "034"}).json()["data"]
    s049 = boss.post("/api/customer-summaries/run", json={"customerId": "C1", "ctx": "049"}).json()["data"]
    assert s034 == "customer summary over A1" and s049 == "customer summary over B1", (s034, s049)
    assert set(boss.get("/api/customer-summaries").json()) == {"C1|034", "C1|049"}
    assert set(alice.get("/api/customer-summaries").json()) == {"C1|034"}  # the 049 summary never reaches her
    assert alice.post("/api/customer-summaries/run", json={"customerId": "C1", "ctx": "049"}).status_code == 404
    assert alice.post("/api/customer-summaries/run", json={"customerId": "C4"}).status_code == 404  # no-CTX customer
    assert alice.post("/api/customer-summaries/run", json={"customerId": "C1", "ctx": "034"}).status_code == 200

    # 10. pipeline / reset / admin routes are admin-only
    for method, path in (("get", "/api/batch/status"), ("post", "/api/batch/run"), ("post", "/api/reset"),
                         ("get", "/api/admin/users"), ("get", "/api/admin/audit"), ("get", "/api/admin/ctx")):
        resp = getattr(alice, method)(path)
        assert resp.status_code == 403 and detail(resp) == "admin_required", (path, resp.status_code, resp.text)

    # 11. a regional user holds several CTXs and sees exactly their union
    boss.put(f"/api/admin/users/{uid['mallory@x.com']}/ctx", json={"ctxs": ["034", "049"]})
    assert ids(mallory.get("/api/contracts")) == {"A1", "A2", "B1", "B2"}

    # 12. removing every CTX puts a user back to pending immediately (no stale session)
    r = boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": []})
    assert r.json()["status"] == "pending"
    assert detail(alice.get("/api/contracts")) == "access_pending"
    boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": ["034"]})

    # 13. disable / re-enable
    assert boss.put(f"/api/admin/users/{uid['alice@x.com']}/status", json={"disabled": True}).json()["status"] == "disabled"
    assert detail(alice.get("/api/contracts")) == "account_disabled"
    # assigning a CTX must not silently re-enable a disabled user
    assert boss.put(f"/api/admin/users/{uid['alice@x.com']}/ctx", json={"ctxs": ["034", "049"]}).json()["status"] == "disabled"
    assert boss.put(f"/api/admin/users/{uid['alice@x.com']}/status", json={"disabled": False}).json()["status"] == "active"
    assert ids(alice.get("/api/contracts")) == {"A1", "A2", "B1", "B2"}

    # 14. the last active admin can't be demoted or disabled
    boss_id = uid["boss@x.com"]
    assert boss.put(f"/api/admin/users/{boss_id}/role", json={"role": "user"}).status_code == 400
    assert boss.put(f"/api/admin/users/{boss_id}/status", json={"disabled": True}).status_code == 400
    boss.put(f"/api/admin/users/{uid['alice@x.com']}/role", json={"role": "admin"})
    assert ids(alice.get("/api/contracts")) == {"A1", "A2", "B1", "B2", "N1"}  # admin sees everything, incl. no-CTX
    assert boss.put(f"/api/admin/users/{boss_id}/role", json={"role": "user"}).status_code == 200  # now allowed
    assert alice.put(f"/api/admin/users/{uid['alice@x.com']}/role", json={"role": "user"}).status_code == 400  # alice is last

    # 15. logging out ends the session
    assert alice.post("/api/auth/logout").status_code == 200
    assert alice.get("/api/contracts").status_code == 401

    # 16. everything above left an audit trail
    carol = TestClient(main.app)
    carol.post("/api/auth/dev/login", json={"email": "boss@x.com"})  # boss was demoted -> no admin route access
    assert carol.get("/api/admin/audit").status_code == 403
    alice.post("/api/auth/dev/login", json={"email": "alice@x.com"})
    audit = alice.get("/api/admin/audit").json()
    seen = {a["action"] for a in audit}
    assert {"user.first_login", "user.bootstrap_admin", "user.ctx_assigned", "user.role_changed",
            "user.disabled", "user.enabled", "ctx.renamed"} <= seen, seen
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

# --- loader: CTX normalization, string ids, strict-JSON-safe output ------------------
os.environ.update(DATA_DIR=tmp, CONTRACTS_FILE="contracts.csv", CLAIMS_FILE="none.csv", INVOICES_FILE="none.csv")
import local_data_loader as ldl  # noqa: E402

assert [ldl._normalize_ctx(v) for v in (34, 34.0, "034", "34", " 7 ", "1234", "AB", None, "")] == \
       ["034", "034", "034", "034", "007", None, None, None, None]
assert ldl._to_id(83121.0) == "83121" and ldl._to_id(5) == "5" and ldl._to_id(None) is None

Path(tmp, "contracts.csv").write_text(
    "CONTRACTID,CUSTOMERID,CTX,COMPANY,CONTRACT_PRICE,CONTRACT_DURATION_MONTHS,IS_DIRECT_CONTRACT,"
    "IS_GENERAL_SERVICE_INCLUDE,MANUFACTUREDATE,CONTRACT_END_DATE\n"
    "100,7,34,Acme,50,24,True,False,2020-01-01,2099-01-01\n"
    "101,7,,Acme,50,24,False,True,,\n"
    "102,8,49,Beta,,12,False,True,2021-01-01,2000-01-01\n"
)
loaded = ldl.load_contracts_from_local()
assert [c["ctx"] for c in loaded] == ["034", None, "049"], [c["ctx"] for c in loaded]
assert all(isinstance(c["contractId"], str) and isinstance(c["customerId"], str) for c in loaded)
json.dumps(loaded, allow_nan=False)  # no NaN / numpy values leaked through

print("ALL CHECKS PASSED")
