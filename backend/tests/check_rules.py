"""
Exclusion sets and retention actions check. Plain asserts, no framework:

    cd backend && python tests/check_rules.py

Loads a generated book through the real ingest, then compares every match, count and hidden contract
with an independent calculation in pandas.
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
import pg  # noqa: E402

DB = pg.new_database()
os.environ.update(DATABASE_URL=DB, AUTH_MODE="dev", ENV="development", BOOTSTRAP_ADMIN_EMAILS="boss@x.com", SESSION_SECRET="x" * 40,
                  EXCLUSION_CONFIRM_SHARE="0.5")   # the generated book is ~35% Direct / ~65% Dealer: 0.5 lets the first pass and trips the second

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from ingest import sample  # noqa: E402

D, S = "app_data", "ai_recommendations"
contracts, claims = sample.generate(1500, seed=3)
pg.ingest_frames(DB, contracts, claims)


def live_frame():
    d = pd.DataFrame(pg.query(DB, f"SELECT * FROM {D}.contract WHERE in_scope"))
    for c in ("risk_score", "annual_contract_value", "claims_last_90d", "total_claims"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["end"] = pd.to_datetime(d["end_date_effective"])
    return d


LIVE = live_frame()


def oracle(ctx, pred, d=None):
    d = LIVE if d is None else d
    return set(d[(d.ctxid == ctx) & pred(d)].contractid)


def hits_of(set_id):
    return {r["contractid"] for r in pg.query(DB, f"SELECT contractid FROM {S}.exclusion_hit WHERE set_id = CAST(%s AS uuid)", set_id)}


soon = (date.today() + timedelta(days=60)).isoformat()
a_customer = LIVE[LIVE.ctxid == "034"].customerid.iloc[0], LIVE[LIVE.ctxid == "034"].customerid.iloc[1]
CASES = [  # (criteria, the same condition in plain pandas)
    ({"channel": ["Direct"]}, lambda d: d.channel == "Direct"),
    ({"segment": ["High Risk", "At Risk"], "channel": ["Dealer"]}, lambda d: d.segment.isin(["High Risk", "At Risk"]) & (d.channel == "Dealer")),
    ({"risk_score": {"gte": 50}}, lambda d: d.risk_score >= 50),
    ({"annual_contract_value": {"between": [1000, 3000]}}, lambda d: d.annual_contract_value.between(1000, 3000)),
    ({"end_date_effective": {"lt": soon}}, lambda d: d.end < pd.Timestamp(soon)),
    ({"company": {"contains": "FLEET 1"}}, lambda d: d.company.fillna("").str.lower().str.contains("fleet 1", regex=False)),
    ({"company": {"starts_with": "fleet 12"}}, lambda d: d.company.fillna("").str.lower().str.startswith("fleet 12")),
    ({"annual_contract_value": {"is_null": True}}, lambda d: d.annual_contract_value.isna()),
    ({"is_general_service_include": {"eq": False}}, lambda d: d.is_general_service_include == False),  # noqa: E712
    ({"customerid": list(a_customer)}, lambda d: d.customerid.isin(a_customer)),
    ({"claims_last_90d": {"in": [0, 2]}}, lambda d: d.claims_last_90d.isin([0, 2])),
    ({"RISK_SCORE": {"lt": 20}, "Channel": ["Direct"]}, lambda d: (d.risk_score < 20) & (d.channel == "Direct")),   # column names: any case
    ({"company": {"contains": "%"}}, lambda d: d.company.fillna("").str.contains("%", regex=False)),               # wildcards are plain characters
    ({"company": ["x'); DROP TABLE users;--"]}, lambda d: d.company == "x'); DROP TABLE users;--"),             # hostile text is only ever a value
]

with TestClient(main.app) as boss, TestClient(main.app) as alice, TestClient(main.app) as bob, TestClient(main.app) as carol:
    boss.post("/api/auth/dev/login", json={"email": "boss@x.com"})
    uid = {w: c.post("/api/auth/dev/login", json={"email": f"{w}@x.com"}).json()["id"] for w, c in (("alice", alice), ("bob", bob), ("carol", carol))}
    boss.get("/api/admin/ctx")
    for who, ctxs in (("alice", ["034", "049"]), ("bob", ["045"]), ("carol", ["034"])):
        assert boss.put(f"/api/admin/users/{uid[who]}/ctx", json={"ctxs": ctxs}).status_code == 200

    def preview(client, ctx, criteria):
        r = client.post("/api/exclusions/preview", json={"ctx": ctx, "criteria": criteria})
        assert r.status_code == 200, (criteria, r.text)
        return r.json()

    def mk(client, ctx, name, criteria, **kw):
        r = client.post("/api/exclusions", json={"ctx": ctx, "name": name, "criteria": criteria, **kw})
        assert r.status_code == 200, (name, r.status_code, r.text)
        return r.json()

    # ---- A. the criteria compiler against plain pandas, and against hostile input --------------------------------------
    for crit, pred in CASES:
        for ctx in ("034", "049"):
            got = preview(alice, ctx, crit)
            assert got["matches"] == len(oracle(ctx, pred)), (crit, ctx, got["matches"], len(oracle(ctx, pred)))
    assert pg.query(DB, "SELECT count(*) AS n FROM ai_recommendations.users")[0]["n"] >= 4   # the injection attempt did nothing
    bad = [({"no_such_column": ["a"]}, "Unknown column"), ({"company": {"gte": 5}}, "operator"), ({"risk_score": {"gte": "abc"}}, "not a valid"),
           ({"risk_score": {"gte": 1, "lte": 9}}, "exactly one operator"), ({"risk_score": 5}, "exactly one operator"), ({"channel": []}, "between 1 and"),
           ({"annual_contract_value": {"between": [1]}}, "two values"), ({"end_date_effective": {"lt": "31/12/2026"}}, "not a valid"),
           ({"claims_last_90d": {"eq": 1.5}}, "whole number"), ({}, "at least one condition"), ({f"c{i}": ["a"] for i in range(21)}, "At most 20 columns")]
    for crit, why in bad:
        r = alice.post("/api/exclusions/preview", json={"ctx": "034", "criteria": crit})
        assert r.status_code == 422 and why in r.json()["detail"], (crit, r.status_code, r.text)
    print(f"ok - criteria: {len(CASES)} conditions match plain pandas in two areas; {len(bad)} kinds of bad input are refused with a clear message; hostile text is only a value")

    # ---- B. exclusion sets: scope, names, activation, overlap, guard, concurrency, soft delete ----------------------------
    A = mk(alice, "034", "  Direct customers ", {"channel": ["Direct"]})
    assert A["status"] == "draft" and A["name"] == "Direct customers" and A["matchCount"] is None and hits_of(A["id"]) == set()
    assert alice.post("/api/exclusions", json={"ctx": "045", "name": "x", "criteria": {"channel": ["Direct"]}}).status_code == 404   # not her area
    assert alice.post("/api/exclusions", json={"ctx": "034", "name": "DIRECT CUSTOMERS", "criteria": {"channel": ["Dealer"]}}).json()["detail"] == "name_taken"
    assert mk(alice, "049", "Direct customers", {"channel": ["Direct"]})["ctx"] == "049"      # the same name in another area is fine
    assert alice.post("/api/exclusions", json={"ctx": "034", "name": "e", "criteria": {}}).status_code == 422
    assert {s["name"] for s in carol.get("/api/exclusions").json()["sets"]} == {"Direct customers"}      # visible to everyone with the area...
    assert bob.get("/api/exclusions").json()["sets"] == []                                              # ...and to nobody else
    for call in (lambda: bob.put(f"/api/exclusions/{A['id']}", json={"version": 1, "name": "z"}), lambda: bob.delete(f"/api/exclusions/{A['id']}?version=1"),
                 lambda: bob.post(f"/api/exclusions/{A['id']}/activate", json={"version": 1})):
        assert call().status_code == 404

    r = carol.post(f"/api/exclusions/{A['id']}/activate", json={"version": A["version"]})
    assert r.status_code == 200 and r.json()["status"] == "active", r.text
    want_a = oracle("034", lambda d: d.channel == "Direct")
    assert r.json()["matchCount"] == len(want_a) and hits_of(A["id"]) == want_a
    B = mk(alice, "034", "High risk", {"risk_score": {"gte": 50}}, activate=True)
    want_b = oracle("034", lambda d: d.risk_score >= 50)
    assert hits_of(B["id"]) == want_b and len(want_a & want_b) > 0
    listing = alice.get("/api/exclusions").json()
    sets = {s["name"]: s for s in listing["sets"] if s["ctx"] == "034"}
    assert listing["combined"]["034"] == len(want_a | want_b), "a contract matching two sets is ONE excluded contract"
    assert sets["Direct customers"]["onlyThisSet"] == len(want_a - want_b) and sets["High risk"]["onlyThisSet"] == len(want_b - want_a)
    in_view = {r["contractid"] for r in pg.query(DB, f"SELECT contractid FROM {S}.v_excluded_contracts WHERE ctx = '034'")}
    assert in_view == want_a | want_b
    p = preview(alice, "034", {"channel": ["Dealer"]})
    want_c = oracle("034", lambda d: d.channel == "Dealer")
    assert p["newlyExcluded"] == len(want_c - want_a - want_b) and p["needsConfirm"] is True and p["share"] > 0.5
    r = alice.post("/api/exclusions", json={"ctx": "034", "name": "Dealers", "criteria": {"channel": ["Dealer"]}, "activate": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_required" and r.json()["detail"]["matches"] == len(want_c)   # a big exclusion needs a confirmation
    assert pg.query(DB, f"SELECT count(*) AS n FROM {S}.exclusion_set WHERE name = 'Dealers'")[0]["n"] == 0                          # ...and nothing was saved
    C = mk(alice, "034", "Dealers", {"channel": ["Dealer"]}, activate=True, confirm=True)
    assert hits_of(C["id"]) == want_c

    # two people edit the same set: the second, stale save is refused
    seen_by_alice = alice.get("/api/exclusions").json()["sets"]
    v = next(s for s in seen_by_alice if s["id"] == B["id"])["version"]
    ok = carol.put(f"/api/exclusions/{B['id']}", json={"version": v, "description": "carol was here"})
    stale = alice.put(f"/api/exclusions/{B['id']}", json={"version": v, "criteria": {"risk_score": {"gte": 70}}})
    assert ok.status_code == 200 and stale.status_code == 409 and stale.json()["detail"] == "version_conflict"
    assert hits_of(B["id"]) == want_b, "a refused edit must change nothing"
    cur = ok.json()
    r = alice.put(f"/api/exclusions/{B['id']}", json={"version": cur["version"], "criteria": {"risk_score": {"gte": 70}}})
    assert r.status_code == 200 and hits_of(B["id"]) == oracle("034", lambda d: d.risk_score >= 70)   # editing an active set recomputes it
    r = alice.post(f"/api/exclusions/{C['id']}/deactivate", json={"version": C["version"]})
    assert r.json()["status"] == "draft" and hits_of(C["id"]) == set()
    d = alice.get("/api/exclusions").json()["sets"]
    gone = next(s for s in d if s["id"] == B["id"])
    assert alice.delete(f"/api/exclusions/{B['id']}?version={gone['version']}").status_code == 200
    assert hits_of(B["id"]) == set() and B["id"] not in {s["id"] for s in alice.get("/api/exclusions").json()["sets"]}
    assert B["id"] in {s["id"] for s in alice.get("/api/exclusions?deleted=true").json()["sets"]}
    mk(alice, "034", "High risk", {"channel": ["Direct"], "risk_score": {"lt": 10}})                   # the name is free again...
    assert alice.post(f"/api/exclusions/{B['id']}/restore").json()["detail"] == "name_taken"           # ...so the old one can't come back under it
    audit = {a["action"] for a in boss.get("/api/admin/audit?limit=100").json()}
    assert {"exclusion.created", "exclusion.activated", "exclusion.updated", "exclusion.deactivated", "exclusion.deleted"} <= audit
    print("ok - exclusion sets: area scoping, unique names, overlap counted once, big exclusions need a confirmation, stale edits refused, soft delete")

    # ---- C. what each user has chosen to hide ------------------------------------------------------------------------------
    live034_049 = {*LIVE[LIVE.ctxid.isin(["034", "049"])].contractid}
    hidden_all = hits_of(A["id"])           # A is still active (B was deleted, C deactivated)

    def listing_ids(client, **p):
        got, cursor = [], None
        while True:
            r = client.get("/api/worklist", params={**p, "limit": 100, **({"cursor": cursor} if cursor else {})}).json()
            got += [x for x in r["rows"]]
            cursor = r["nextCursor"]
            if not cursor:
                return got

    rows = listing_ids(alice)
    assert {r["id"] for r in rows} == live034_049 - hidden_all, "by default an active set hides its contracts"
    s0 = alice.get("/api/summary").json()
    assert s0["kpis"]["contracts"] == len(live034_049 - hidden_all) and s0["excluded"] == len(hidden_all)
    assert carol.get("/api/summary").json()["excluded"] == len(hidden_all)
    assert alice.put("/api/me/exclusion-pref", json={"mode": "none"}).json() == {"mode": "none", "setIds": []}
    rows = listing_ids(alice)
    assert {r["id"] for r in rows} == live034_049 and alice.get("/api/summary").json()["excluded"] == 0
    shown = {r["id"]: r["excl"] for r in rows if r["excl"]}
    assert set(shown) == hidden_all and all(v == ["Direct customers"] for v in shown.values())          # not hidden, but each row says why it would be
    other = next(s for s in alice.get("/api/exclusions").json()["sets"] if s["ctx"] == "049")
    boss.post(f"/api/exclusions/{other['id']}/activate", json={"version": other["version"]})
    assert alice.put("/api/me/exclusion-pref", json={"mode": "custom", "setIds": [A["id"]]}).json()["mode"] == "custom"
    assert {r["id"] for r in listing_ids(alice)} == live034_049 - hidden_all                              # only the chosen set hides
    assert alice.put("/api/me/exclusion-pref", json={"mode": "custom", "setIds": [other["id"]]}).status_code == 200
    assert {r["id"] for r in listing_ids(alice)} == live034_049 - hits_of(other["id"])
    assert carol.get("/api/me/exclusion-pref").json()["mode"] == "all"                                    # another user's choice is their own
    assert bob.put("/api/me/exclusion-pref", json={"mode": "custom", "setIds": [A["id"]]}).status_code == 400   # a set from an area he doesn't have
    assert alice.put("/api/me/exclusion-pref", json={"mode": "custom", "setIds": ["00000000-0000-0000-0000-000000000000"]}).status_code == 400
    alice.put("/api/me/exclusion-pref", json={"mode": "all"})
    assert {r["id"] for r in listing_ids(alice)} == live034_049 - hidden_all - hits_of(other["id"])
    print("ok - each user's own choice (all / only these / none) hides contracts from their list and figures; rows say why; counts are of distinct contracts")

    # ---- D. retention actions: global vs local, override, clone, permissions -----------------------------------------------
    G1 = boss.post("/api/retention-actions", json={"scope": "global", "category": "Service", "subCategory": "Visit", "name": "Free check-in", "description": "d"})
    assert G1.status_code == 200, G1.text
    G1 = G1.json()
    G2 = boss.post("/api/retention-actions", json={"scope": "global", "category": "Commercial", "name": "Loyalty offer", "criteria": {"channel": ["Direct"]}}).json()
    assert G2["matchCount"] == len(set(LIVE[LIVE.ctxid.notna() & (LIVE.channel == "Direct")].contractid))
    assert alice.post("/api/retention-actions", json={"scope": "global", "category": "x", "name": "y"}).json()["detail"] == "admin_required"
    assert alice.post("/api/retention-actions", json={"scope": "local", "ctx": "045", "category": "x", "name": "y"}).json()["detail"] == "not_your_area"
    L1 = alice.post("/api/retention-actions", json={"scope": "local", "ctx": "034", "category": " service ", "subCategory": "VISIT", "name": "free CHECK-IN",
                                                     "criteria": {"risk_score": {"gte": 50}}})
    assert L1.status_code == 200, L1.text
    L1 = L1.json()
    assert alice.post("/api/retention-actions", json={"scope": "local", "ctx": "034", "category": "Service", "subCategory": "Visit", "name": "Free check-in"}).json()["detail"] == "already_exists"
    seen = {a["id"]: a for a in alice.get("/api/retention-actions").json()}
    assert seen[L1["id"]]["overridesGlobal"] is True and seen[G1["id"]]["overriddenIn"] == ["034"]      # case and spaces don't matter, so this IS an override
    assert seen[G2["id"]]["overriddenIn"] == []
    assert {a["id"] for a in bob.get("/api/retention-actions").json()} == {G1["id"], G2["id"]}           # bob sees the globals, not alice's local one
    cl = bob.post(f"/api/retention-actions/{G1['id']}/clone", json={"ctx": "045"})
    assert cl.status_code == 200 and cl.json()["scope"] == "local" and cl.json()["overridesGlobal"] is True      # clone, keep the name: an override for his area
    assert bob.post(f"/api/retention-actions/{G1['id']}/clone", json={"ctx": "034"}).status_code == 404
    renamed = bob.post(f"/api/retention-actions/{G2['id']}/clone", json={"ctx": "045", "name": "Our own loyalty offer"}).json()
    assert renamed["overridesGlobal"] is False and renamed["criteria"] == G2["criteria"]                    # a different name is a separate action
    assert alice.delete(f"/api/retention-actions/{G1['id']}?version={G1['version']}").json()["detail"] == "admin_required"
    first = alice.put(f"/api/retention-actions/{L1['id']}", json={"version": L1["version"], "description": "first"})
    second = alice.put(f"/api/retention-actions/{L1['id']}", json={"version": L1["version"], "description": "second"})
    assert first.status_code == 200 and second.status_code == 409
    up = alice.put(f"/api/retention-actions/{L1['id']}", json={"version": first.json()["version"], "criteria": {"risk_score": {"gte": 70}}}).json()
    assert up["matchCount"] == len(oracle("034", lambda d: d.risk_score >= 70))
    assert alice.post("/api/retention-actions/preview", json={"criteria": {}}).status_code == 403
    assert boss.post("/api/retention-actions/preview", json={"criteria": {"channel": ["Direct"]}}).json()["matches"] == G2["matchCount"]
    assert boss.post("/api/retention-actions", json={"scope": "local", "ctx": "049", "category": "Service", "name": "Local only"}).status_code == 200

    # what the pipeline sees: an independent model of the rule, compared with the view
    opts = {}
    for r in pg.query(DB, f"SELECT contractid, name FROM {S}.v_retention_options"):
        opts.setdefault(r["contractid"], set()).add(r["name"])
    want = {}
    for _, c in LIVE[LIVE.ctxid.notna()].iterrows():
        names = set()
        if c.ctxid == "034":
            if c.risk_score >= 70:
                names.add("free CHECK-IN")      # alice's local action REPLACES the global one in 034 - even where its own criteria don't match
        else:
            names.add("Free check-in")          # the global one (in 045 it is bob's local copy: same name, no criteria, replaces the global)
        if c.channel == "Direct":
            names.add("Loyalty offer")
            if c.ctxid == "045":
                names.add("Our own loyalty offer")   # a renamed clone is an extra action, not an override
        if c.ctxid == "049":
            names.add("Local only")
        want[c.contractid] = names
    assert set(opts) <= set(want), "options were given to a contract that isn't live"
    for cid, names in want.items():
        assert opts.get(cid, set()) == names, (cid, opts.get(cid, set()), names)
    d_ = alice.delete(f"/api/retention-actions/{L1['id']}?version={up['version']}")
    assert d_.status_code == 200 and pg.query(DB, f"SELECT count(*) AS n FROM {S}.retention_action_match WHERE action_id = CAST(%s AS uuid)", L1["id"])[0]["n"] == 0
    assert alice.post(f"/api/retention-actions/{L1['id']}/restore").status_code == 200
    print("ok - retention actions: admin-only globals, local override only on an exact (case-blind) match, clone-then-own, version conflicts, and the pipeline view equals an independent model")

    # ---- D2. what the AI pipeline is told to do today (v_ai_work), against an independent model of the rules ---------------------
    DUE = ("90", "60", "45", "30", "10")
    live = LIVE[LIVE.ctxid.notna()]
    due_ids = live[live.expiry_bucket.isin(DUE)].contractid.tolist()
    ctx_of = dict(zip(live.contractid, live.ctxid))
    last_ms = {cid: live[live.contractid == cid].expiry_bucket.iloc[0] for cid in due_ids[:20]}      # recommended for the bucket they are in now
    last_ms.update({cid: ">90" for cid in due_ids[20:40]})                                            # recommended earlier, for a previous bucket
    pg.query(DB, f"INSERT INTO {S}.contract_recommendation (contractid, ctx, ai_recommendation, milestone) VALUES " +
             ",".join(f"('{cid}', '{ctx_of[cid]}', 'x', '{ms}')" for cid, ms in last_ms.items()))
    some = live.contractid.tolist()[:10]
    for cid in some:
        pg.query(DB, f"INSERT INTO {S}.contract_summary (contractid, ctx, ai_summary, input_hash) VALUES ('{cid}', '{ctx_of[cid]}', 's', 'h1')")
    for cid in some[:5]:     # a second version: the view shows the LATEST hash
        pg.query(DB, f"INSERT INTO {S}.contract_summary (contractid, ctx, ai_summary, input_hash) VALUES ('{cid}', '{ctx_of[cid]}', 's', 'h2')")
    for cid in some[:3]:
        pg.query(DB, f"INSERT INTO {S}.web_claims_summary (contractid, ctx, ai_summary, input_hash) VALUES ('{cid}', '{ctx_of[cid]}', 's', 'w1')")
    acct = live.accountid.iloc[0]
    pg.query(DB, f"INSERT INTO {S}.account_summary (accountid, ctx, ai_summary, input_hash) VALUES ('{acct}', '{live.ctxid.iloc[0]}', 's', 'a1')")
    active_sets = [r["id"] for r in pg.query(DB, f"SELECT id FROM {S}.exclusion_set WHERE status = 'active' AND deleted_at IS NULL")]
    excluded = set().union(*[hits_of(str(i)) for i in active_sets])
    assert excluded, "the model needs some excluded contracts"

    def expected(threshold):
        out = {}
        for _, c in live.iterrows():
            cid = c.contractid
            scope = bool(c.risk_score >= threshold) or c.expiry_bucket in DUE
            due = c.expiry_bucket in DUE and last_ms.get(cid) != c.expiry_bucket
            out[cid] = {"in_ai_scope": scope, "want_contract_summary": scope, "want_web_claims_summary": scope and c.total_claims > 0,
                        "excluded": cid in excluded, "milestone_due": due, "has_options": bool(want[cid]),
                        "want_recommendation": scope and cid not in excluded and due and bool(want[cid]),
                        "last_milestone": last_ms.get(cid), "contract_summary_hash": {**{x: "h2" for x in some[:5]}, **{x: "h1" for x in some[5:]}}.get(cid),
                        "web_claims_summary_hash": "w1" if cid in some[:3] else None}
        return out

    def check_work(threshold):
        rows = {r["contractid"]: r for r in pg.query(DB, f"SELECT * FROM {S}.v_ai_work")}
        model = expected(threshold)
        assert set(rows) == set(model), "v_ai_work should list exactly the live contracts that have an area"
        for cid, want_ in model.items():
            got = {k: rows[cid][k] for k in want_}
            assert got == want_, (cid, got, want_)
        return model

    model = check_work(50)
    kinds = {"recommend": sum(m["want_recommendation"] for m in model.values()),
             "excluded_but_due": sum(m["excluded"] and m["milestone_due"] and m["in_ai_scope"] for m in model.values()),
             "due_without_options": sum(m["milestone_due"] and m["in_ai_scope"] and not m["has_options"] for m in model.values()),
             "already_recommended_for_this_bucket": sum(m["in_ai_scope"] and not m["milestone_due"] and m["last_milestone"] is not None for m in model.values())}
    assert all(v > 0 for v in kinds.values()), f"every rule should be exercised by the data: {kinds}"
    assert not any(r["excluded"] for r in pg.query(DB, f"SELECT excluded FROM {S}.v_ai_work WHERE want_recommendation"))
    pg.query(DB, f"UPDATE {S}.ai_setting SET value = '70' WHERE key = 'summary_risk_threshold'")        # an admin raises the bar: no deployment needed
    assert sum(m["in_ai_scope"] for m in check_work(70).values()) < sum(m["in_ai_scope"] for m in model.values())
    pg.query(DB, f"UPDATE {S}.ai_setting SET value = '50' WHERE key = 'summary_risk_threshold'")
    accts = {r["accountid"]: r for r in pg.query(DB, f"SELECT * FROM {S}.v_ai_work_accounts")}
    mine = live.assign(s=live.contractid.map(lambda x: model[x]["in_ai_scope"])).groupby("accountid").s.sum()
    assert set(accts) == set(mine[mine > 0].index) and all(accts[a]["contracts_in_scope"] == mine[a] for a in accts)
    assert accts[acct]["account_summary_hash"] == "a1"
    ids_ = [pg.query(DB, f"SELECT {S}.ai_batch_create(gen_random_uuid(), '{t}', NULL, 'm', '{{}}', 's3://i', 's3://o', 5) AS id")[0]["id"] for t in ("contract_summary", "recommendation")]
    shown = boss.get("/api/admin/batches").json()
    assert {b["id"] for b in shown} >= {str(i) for i in ids_} and all(b["status"] == "created" for b in shown)
    assert alice.get("/api/admin/batches").json()["detail"] == "admin_required"
    print(f"ok - AI work list: {len(model)} contracts match an independent model of the rules ({kinds}); hashes show the latest version; the threshold is a setting; the batch log is visible to admins")

    # ---- E. a new data load recomputes every rule's matches --------------------------------------------------------------------
    before = hits_of(A["id"])
    new_contracts, new_claims = sample.generate(1500, seed=9)
    pg.ingest_frames(DB, new_contracts, new_claims)
    LIVE = live_frame()
    assert hits_of(A["id"]) == oracle("034", lambda d: d.channel == "Direct") and hits_of(A["id"]) != before
    assert {r["contractid"] for r in pg.query(DB, f"SELECT action_id, contractid FROM {S}.retention_action_match WHERE action_id = CAST(%s AS uuid)", G2["id"])} == \
        set(LIVE[LIVE.ctxid.notna() & (LIVE.channel == "Direct")].contractid)
    import matching  # noqa: E402
    real = matching.recompute_all
    matching.recompute_all = lambda s: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        pg.ingest_frames(DB, new_contracts, new_claims, expect=3)           # data live, but the matches could not be rebuilt: exit code 3
    finally:
        matching.recompute_all = real
    print("ok - a data load recomputes every active rule's matches; if that step fails the load exits 3 instead of looking fine")

    # ---- F. what the builder offers, and the new milestone column ---------------------------------------------------------------
    cols = {c["key"]: c for c in alice.get("/api/rules/columns").json()}
    assert cols["risk_score"]["kind"] == "int" and "between" in cols["risk_score"]["ops"] and cols["channel"]["picker"] and "search_text" not in cols
    vals = bob.get("/api/rules/columns/channel/values").json()
    assert sum(v["count"] for v in vals) == len(LIVE[LIVE.ctxid == "045"]) and alice.get("/api/rules/columns/company/values").status_code == 404
    pg.query(DB, f"INSERT INTO {S}.contract_recommendation (contractid, ctx, ai_recommendation, milestone) VALUES ('M1', '034', 'x', '90')")
    try:
        pg.query(DB, f"INSERT INTO {S}.contract_recommendation (contractid, ctx, ai_recommendation, milestone) VALUES ('M2', '034', 'x', '75')")
        raise AssertionError("an unknown milestone should be refused")
    except Exception as e:  # noqa: BLE001
        assert "check" in str(e).lower(), e
    print("ok - the criteria builder's column list and value lists are scoped; recommendations record their milestone")

print("ALL RULES CHECKS PASSED")
