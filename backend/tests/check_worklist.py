"""
Worklist / summary check. Plain asserts, no framework:

    cd backend && python tests/check_worklist.py

Loads a generated book through the real ingest, then compares what the API returns with
independent oracles (plain SQL and plain Python) - paging order, filters, search, and every
figure on the summary.
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
import pg  # noqa: E402

DB = pg.new_database()
os.environ.update(DATABASE_URL=DB, AUTH_MODE="dev", ENV="development", BOOTSTRAP_ADMIN_EMAILS="boss@x.com", SESSION_SECRET="x" * 40)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import worklist  # noqa: E402
from ingest import sample  # noqa: E402

D, S = "app_data", "ai_recommendations"
contracts, claims = sample.generate(1500, seed=3)
contracts.loc[contracts.index[:25], "CTXID"] = None            # contracts with no CTX: admin-only
pg.ingest_frames(DB, contracts, claims)
# recommendations with outcomes for a slice of the book, written the way the AWS job writes them
pg.query(DB, f"""
INSERT INTO {S}.contract_recommendation (contractid, ctx, ai_recommendation, retention_action_name, outcome, action_status)
SELECT contractid, ctxid, 'why', (ARRAY['Personal outreach call', 'Free service check-in', 'Contract restructuring'])[1 + n % 3],
       (ARRAY['Engaged', 'Declined', 'No response', NULL])[1 + n % 4], CASE WHEN n % 5 = 0 THEN 'Action done' ELSE 'Action required' END
FROM (SELECT contractid, ctxid, row_number() OVER (ORDER BY contractid) AS n FROM {D}.contract WHERE in_scope AND ctxid IS NOT NULL) t
WHERE n % 3 <> 0""")

SORTS = ["risk_score", "annual_contract_value", "end_date_effective", "company", "claims_last_90d", "contract_start_date"]


def ctx_sql(ctxs):  # ctxs None = admin (everything, incl. no-CTX)
    return "TRUE" if ctxs is None else "ctxid IN (" + ",".join(f"'{c}'" for c in ctxs) + ")"


def oracle_ids(ctxs, where="TRUE", sort="risk_score", direction="desc"):
    o = direction.upper()
    rows = pg.query(DB, f"SELECT contractid FROM {D}.contract WHERE in_scope AND {ctx_sql(ctxs)} AND {where} "
                        f"ORDER BY {sort} {o} NULLS LAST, contractid {o}")
    return [r["contractid"] for r in rows]


def pages(client, params, limit=37):
    got, cursor, n = [], None, 0
    while True:
        r = client.get("/api/worklist", params={**params, "limit": limit, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        j = r.json()
        got += [x["id"] for x in j["rows"]]
        cursor, n = j["nextCursor"], n + 1
        if not cursor:
            return got
        assert n < 600, "paging did not terminate"


with TestClient(main.app) as boss, TestClient(main.app) as alice, TestClient(main.app) as bob:
    boss.post("/api/auth/dev/login", json={"email": "boss@x.com"})
    uid = {}
    for who, client in (("alice", alice), ("bob", bob)):
        uid[who] = client.post("/api/auth/dev/login", json={"email": f"{who}@x.com"}).json()["id"]
    boss.get("/api/admin/ctx")  # registers the codes the load introduced
    assert boss.put(f"/api/admin/users/{uid['alice']}/ctx", json={"ctxs": ["034", "049"]}).status_code == 200   # two areas
    assert boss.put(f"/api/admin/users/{uid['bob']}/ctx", json={"ctxs": ["045"]}).status_code == 200

    # ---- A. every sort, both directions: pages stitched together == one ordered query ------------------
    for who, client, ctxs in (("admin", boss, None), ("alice", alice, ["034", "049"])):
        for sort in SORTS:
            for direction in ("asc", "desc"):
                got = pages(client, {"sort": sort, "dir": direction})
                want = oracle_ids(ctxs, sort=sort, direction=direction)
                assert got == want, (who, sort, direction, len(got), len(want), [(a, b) for a, b in zip(got, want) if a != b][:2])
    assert pg.query(DB, f"SELECT count(*) AS n FROM {D}.contract WHERE in_scope AND annual_contract_value IS NULL")[0]["n"] > 0
    assert len(oracle_ids(None)) > len(oracle_ids(["034", "049"]))
    first = boss.get("/api/worklist", params={"sort": "contract_start_date", "dir": "asc", "limit": 1}).json()
    one_by_one, cursor = [first["rows"][0]["id"]], first["nextCursor"]
    for _ in range(40):
        r = boss.get("/api/worklist", params={"sort": "contract_start_date", "dir": "asc", "limit": 1, "cursor": cursor}).json()
        one_by_one.append(r["rows"][0]["id"]); cursor = r["nextCursor"]
    assert one_by_one == oracle_ids(None, sort="contract_start_date", direction="asc")[:41]
    print("ok - 24 sort/direction/scope combinations: paged results equal one ordered query (NULLs last, ties stable, several areas, admin incl. no-CTX)")

    # ---- B. filters --------------------------------------------------------------------------------------
    VB = "CASE WHEN value_vs_median IS NULL THEN 4 WHEN value_vs_median < 0.5 THEN 0 WHEN value_vs_median < 1 THEN 1 WHEN value_vs_median < 2 THEN 2 ELSE 3 END"
    cases = [
        ({"channel": ["Direct"]}, "channel = 'Direct'"),
        ({"segment": ["High Risk", "At Risk"]}, "segment IN ('High Risk', 'At Risk')"),
        ({"bucket": "30"}, "expiry_bucket = '30'"),
        ({"bucket": "Lost"}, "expiry_bucket = 'Lost'"),
        ({"rb": 5}, "LEAST(risk_score / 10, 9) = 5"),
        ({"vb": 4}, f"{VB} = 4"),
        ({"channel": ["Dealer"], "segment": ["Standard"], "bucket": ">90", "vb": 2}, f"channel = 'Dealer' AND segment = 'Standard' AND expiry_bucket = '>90' AND {VB} = 2"),
    ]
    for params, where in cases:
        assert pages(alice, params) == oracle_ids(["034", "049"], where), params
        assert pages(boss, params) == oracle_ids(None, where), params
    assert pages(alice, {"area": ["034"]}) == oracle_ids(["034"])
    assert pages(alice, {"area": ["045"]}) == [] and pages(bob, {"area": ["034"]}) == []     # an area outside the scope never widens it
    assert pages(bob, {}) == oracle_ids(["045"])
    assert boss.get("/api/worklist", params={"channel": "Nope"}).status_code == 422 and boss.get("/api/worklist", params={"area": "34"}).status_code == 422
    print(f"ok - {len(cases)} filter combinations (plus area scoping) match the oracle for a two-area user and for the admin")

    # ---- C. search ----------------------------------------------------------------------------------------
    live = pg.query(DB, f"SELECT contractid, company, unitid FROM {D}.contract WHERE in_scope AND ctxid IS NOT NULL ORDER BY contractid LIMIT 1")[0]
    for q in ("fleet 12", "FLEET 12", "Transport"):
        assert set(pages(boss, {"q": q})) == set(oracle_ids(None, f"search_text LIKE '%{q.lower()}%'")), q
    assert live["contractid"] in pages(boss, {"q": live["contractid"]}) and live["contractid"] in pages(boss, {"q": live["unitid"]})
    assert pages(boss, {"q": "%%"}) == [] and pages(boss, {"q": "__"}) == []          # LIKE wildcards in the input are just characters
    assert pages(boss, {"q": "x"}) == pages(boss, {})                               # under 2 characters: no search
    assert pages(alice, {"q": "fleet"}) == [i for i in oracle_ids(["034", "049"], "search_text LIKE '%fleet%'")]
    print("ok - search: case-insensitive name match, id prefix, wildcards treated literally, scope respected")

    # ---- D. the summary agrees with the list and with an independent calculation --------------------------
    def summary(client, **params):
        r = client.get("/api/summary", params=params)
        assert r.status_code == 200, r.text
        return r.json()

    def oracle_rows(ctxs, where="TRUE"):
        return pg.query(DB, f"""SELECT c.ctxid, c.customerid, c.segment, c.expiry_bucket AS bucket, c.risk_score, c.annual_contract_value AS v,
                                       r.retention_action_name AS rec, r.action_status AS astatus, r.outcome
                                FROM {D}.contract c LEFT JOIN {S}.contract_recommendation r ON r.is_latest AND r.contractid = c.contractid
                                WHERE c.in_scope AND {ctx_sql(ctxs).replace('ctxid', 'c.ctxid')} AND {where}""")

    def check_summary(client, ctxs, params, where):
        s = summary(client, **params)
        rows = oracle_rows(ctxs, where)
        k = s["kpis"]
        assert k["contracts"] == len(rows) == len(pages(client, params)), (params, k["contracts"], len(rows))
        assert abs(k["value"] - sum(r["v"] or 0 for r in rows)) < 0.01
        lost = [r for r in rows if r["bucket"] == "Lost" or r["outcome"] == "Declined"]
        rest = [r for r in rows if r not in lost]
        assert k["lostCount"] == len(lost) and abs(k["lostValue"] - sum(r["v"] or 0 for r in lost)) < 0.01
        assert abs(k["atRiskValue"] - sum(r["v"] or 0 for r in rest if r["outcome"] == "No response")) < 0.01
        assert abs(k["convertedValue"] - sum(r["v"] or 0 for r in rest if r["outcome"] == "Engaged")) < 0.01
        assert k["actionsNeeded"] == sum(1 for r in rows if r["astatus"] == "Action required")
        assert k["segments"] == {sg: sum(1 for r in rows if r["segment"] == sg) for sg in worklist.SEGMENTS}
        assert k["customers"] == len({(r["ctxid"], r["customerid"]) for r in rows})
        logged = [r for r in rows if r["outcome"]]
        eng = sum(1 for r in logged if r["outcome"] == "Engaged")
        assert k["responseRate"] == (round(eng / len(logged) * 100) if logged else None)
        names = {r["rec"] for r in rows if r["rec"]}
        assert {c["name"]: c["assigned"] for c in s["campaigns"]} == {n: sum(1 for r in rows if r["rec"] == n) for n in names}
        o = s["outcomeByRisk"]
        for band in range(5):
            in_band = [r for r in logged if r["risk_score"] is not None and min(r["risk_score"] // 20, 4) == band]
            assert o["engaged"][band] == sum(1 for r in in_band if r["outcome"] == "Engaged")
            assert o["notEngaged"][band] == sum(1 for r in in_band if r["outcome"] != "Engaged")
        assert sum(c["contracts"] for c in s["byCtx"]) == len(rows)
        return s

    check_summary(boss, None, {}, "TRUE")
    check_summary(alice, ["034", "049"], {"channel": ["Dealer"]}, "c.channel = 'Dealer'")
    check_summary(alice, ["034", "049"], {"bucket": "30", "segment": ["At Risk", "High Risk"]}, "c.expiry_bucket = '30' AND c.segment IN ('At Risk', 'High Risk')")
    # a figure leaves out its own selection: choosing a bucket doesn't zero the other bucket cards, a heatmap cell doesn't blank the heatmap
    no_bucket = summary(alice, channel=["Dealer"], rb=4, vb=1)
    assert sum(no_bucket["buckets"].values()) == no_bucket["kpis"]["contracts"] > 0       # no bucket picked: the cards add up to the total
    with_bucket = summary(alice, channel=["Dealer"], rb=4, vb=1, bucket="30")
    assert with_bucket["buckets"] == no_bucket["buckets"]                                  # a bucket's own selection doesn't change the cards...
    assert with_bucket["kpis"]["contracts"] == no_bucket["buckets"]["30"]                  # ...but the KPIs follow it
    norm = lambda h: sorted(h, key=lambda c: (c["rb"], c["vb"]))  # noqa: E731
    cell, no_cell = summary(alice, channel=["Dealer"], bucket="30", rb=4, vb=1), summary(alice, channel=["Dealer"], bucket="30")
    assert norm(cell["heat"]) == norm(no_cell["heat"]) and cell["kpis"]["contracts"] < no_cell["kpis"]["contracts"]   # a picked cell doesn't blank the heatmap
    assert sum(c["n"] for c in no_cell["heat"]) <= no_cell["kpis"]["contracts"]            # (contracts with no risk score are not on the heatmap)
    assert summary(bob, area=["034"])["kpis"]["contracts"] == 0
    print("ok - summary: every KPI, segment, customer, campaign and risk-bucket figure equals an independent calculation; facets ignore their own selection")

    # ---- E. the saved view --------------------------------------------------------------------------------
    v0 = alice.get("/api/me/worklist-view").json()
    assert v0["columns"] == worklist.DEFAULT_COLUMNS and v0["sort"] == {"key": "risk_score", "dir": "desc"} and v0["maxColumns"] == 12
    assert {a["key"] for a in v0["available"]} == set(worklist.AVAILABLE) and "contractid" not in {a["key"] for a in v0["available"]}
    r = alice.put("/api/me/worklist-view", json={"columns": ["company", "ctxid", "end_date_effective"], "sort": {"key": "company", "dir": "asc"}})
    assert r.json()["columns"] == ["ctxid", "company", "end_date_effective"], r.text          # stored in catalog order
    rows = alice.get("/api/worklist").json()["rows"]                                             # no sort param: the saved sort applies
    assert [x["company"] for x in rows] == sorted(x["company"] for x in rows)
    assert set(rows[0]) == {"id", "rec", "excl", "contractid", "risk_score", "segment", "ctxid", "company", "end_date_effective"}, set(rows[0])
    assert bob.get("/api/me/worklist-view").json()["columns"] == worklist.DEFAULT_COLUMNS       # one view per user
    assert alice.put("/api/me/worklist-view", json={"columns": worklist.AVAILABLE[:13]}).status_code == 400
    assert alice.put("/api/me/worklist-view", json={"columns": ["company"], "sort": {"key": "vehicleid", "dir": "asc"}}).status_code == 400
    assert alice.put("/api/me/worklist-view", json={"columns": []}).json()["columns"] == worklist.DEFAULT_COLUMNS   # empty = back to the default
    print("ok - saved view: per user, validated, kept in catalog order, drives the columns and the default sort")

    # ---- F. cursors ---------------------------------------------------------------------------------------
    assert boss.get("/api/worklist", params={"cursor": "not-a-cursor"}).status_code == 400
    stale = worklist._enc({"dv": 999, "v": 1, "i": "x", "p": 0})
    r = boss.get("/api/worklist", params={"cursor": stale})
    assert r.status_code == 409 and r.json()["detail"] == "data_changed"
    print("ok - a tampered cursor is refused, and a cursor from before a new load answers 409")

    # ---- G. one contract ----------------------------------------------------------------------------------
    cid = pg.query(DB, f"SELECT r.contractid, c.accountid FROM {S}.contract_recommendation r JOIN {D}.contract c USING (contractid) "
                       f"WHERE c.ctxid = '034' AND r.is_latest LIMIT 1")[0]
    pg.query(DB, f"INSERT INTO {S}.contract_recommendation (contractid, ctx, ai_recommendation, retention_action_name, evaluation, draft_content, "
                 f"confidence) VALUES (%s, '034', 'newer', 'Free service check-in', %s::jsonb, %s::jsonb, 0.8)", cid["contractid"],
             '{"pass": false, "scores": {"groundedness": 3}, "retries": 2}', '{"email_subject": "Hi"}')
    pg.query(DB, f"INSERT INTO {S}.account_summary (accountid, ctx, ai_summary) VALUES (%s, '034', 'account text')", cid["accountid"])
    d = alice.get(f"/api/contracts/{cid['contractid']}").json()
    t = d["trace"]
    assert t["recommendation"]["rationale"] == "newer", "the latest version of the recommendation is the one shown"
    assert t["escalated"] and not t["pass"] and t["retryCount"] == 2 and t["content"] == {"email_subject": "Hi"}
    assert any("Verify the rationale" in a for a in t["suggestedActions"]) and t["recommendation"]["confidence"] == 0.8
    assert d["summaries"]["account"] == {"status": "done", "data": "account text"} and d["summaries"]["contract"] is None
    n_claims = pg.query(DB, f"SELECT count(*) AS n FROM {D}.claim WHERE contractid = %s", cid["contractid"])[0]["n"]
    assert d["contract"]["claimsTotal"] == n_claims and len(d["contract"]["claims"]) == min(25, n_claims)
    assert sum(d["contract"]["riskFactors"].values()) == d["contract"]["riskScore"] or d["contract"]["riskScore"] >= 100
    assert d["contract"]["customerFeedback"] == {"recent12Months": [], "historical": []}
    print("ok - contract detail: latest recommendation mapped to the drawer's shape (escalation, draft, confidence), summaries, claims")

print("ALL WORKLIST CHECKS PASSED")
