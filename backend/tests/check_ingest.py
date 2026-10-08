"""
Ingest check. Plain asserts, no framework:

    cd backend && python tests/check_ingest.py

Needs PostgreSQL (see tests/pg.py). The Snowflake path is exercised against a stand-in
connector (needs pyarrow); real Snowflake connectivity can only be proven with real credentials.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
import pg  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

TODAY = date.today()
AS_OF = TODAY.isoformat()
TMP = Path(tempfile.mkdtemp())
D = "app_data"


def dsn(url):
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def use(url):
    os.environ["DATABASE_URL"] = url
    return url


def q(url, sql, *params):
    with psycopg.connect(dsn(url), autocommit=True, row_factory=dict_row) as c:
        return c.execute(sql, params).fetchall()


def one(url, sql, *params):
    rows = q(url, sql, *params)
    return list(rows[0].values())[0] if rows else None


def run_cli(*args):
    from ingest import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def write_extract(directory: Path, contracts: pd.DataFrame, claims: pd.DataFrame | None):
    directory.mkdir(parents=True, exist_ok=True)
    contracts.to_csv(directory / "contract.csv", index=False, date_format="%Y-%m-%d")
    if claims is not None:
        claims.to_csv(directory / "claims.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
    return directory / "contract.csv", directory / "claims.csv"


def ingest(url, c_path, k_path=None, *extra):
    use(url)
    args = ["--source", "local", "--contracts", str(c_path), "--as-of", AS_OF, *extra]
    args += ["--claims", str(k_path)] if k_path else ["--skip-claims"]
    return run_cli(*args)


from ingest import sample  # noqa: E402

# ======================================================================== A. a realistic load
A = use(pg.new_database())
c1, k1 = sample.generate(1500, seed=3)
cp, kp = write_extract(TMP / "a1", c1, k1)

# independent oracle, straight from the raw file, using its own simple code
raw = pd.read_csv(cp, dtype=str)
end = pd.to_datetime(raw["CONTRACT_END_DATE"].fillna(raw["CONTRACT_ORIGINAL_END_DATE"]))
latest = raw["IS_ACTIVE_CONTRACT_VERSION"] == "True"
live = raw["CONTRACT_STATUS"].str.lower() == "live"
oracle_scope = latest & (live | (end >= pd.Timestamp(TODAY) - pd.Timedelta(days=90)))
days = (end - pd.Timestamp(TODAY)).dt.days


def oracle_bucket(d):
    return ">90" if pd.isna(d) else "Lost" if d < 0 else "10" if d <= 10 else "30" if d <= 30 else "45" if d <= 45 else "60" if d <= 60 else "90" if d <= 90 else ">90"


code, out, err = ingest(A, cp, kp)
assert code == 0, (out, err)
assert "ISCURRENT__2" in out, "a repeated ISCURRENT header should be reported"
assert one(A, f"SELECT count(*) FROM {D}.contract") == 1500 and one(A, f"SELECT count(*) FROM {D}.claim") == len(k1)
assert one(A, f"SELECT count(*) FROM {D}.contract WHERE in_scope") == int(oracle_scope.sum()), "live-contract rule disagrees with the oracle"
got = {r["expiry_bucket"]: r["n"] for r in q(A, f"SELECT expiry_bucket, count(*) AS n FROM {D}.contract WHERE in_scope GROUP BY 1")}
want = pd.Series([oracle_bucket(d) for d in days[oracle_scope]]).value_counts().to_dict()
assert got == want, (got, want)
assert one(A, f"SELECT count(*) FROM {D}.contract WHERE segment IS NOT NULL AND NOT in_scope") == 0
assert one(A, f"SELECT count(*) FROM pg_inherits WHERE inhparent = '{D}.contract'::regclass") == 1  # only the live partition
v1 = one(A, f"SELECT max(data_version) FROM {D}.ingest_run WHERE status = 'succeeded'")
print("ok - realistic load: live-contract rule and expiry buckets match an independent oracle; one live partition")

# the personal data a contract manager needs is loaded, and every catalog column exists
from ingest.catalog import index_definitions, load_catalog  # noqa: E402

assert one(A, f"SELECT address FROM {D}.contract LIMIT 1").startswith("Teststrasse")
have = {r["column_name"] for r in q(A, "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'contract'", D)}
cols = load_catalog()
assert {c.name for c in cols} <= have and {"data_version", "in_scope"} <= have
idx = {r["indexname"] for r in q(A, "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename LIKE 'contract_v%%'", D)}
for name, _ in index_definitions(cols):
    assert any(i.endswith("_" + name) for i in idx), f"catalog index {name} was not built"
assert len(index_definitions(cols)) >= 14
print("ok - personal columns loaded; every catalog column and every catalog-requested index exists")

# ======================================================================== B. a second load replaces the first
c2, k2 = sample.generate(1800, seed=4)
cp2, kp2 = write_extract(TMP / "a2", c2, k2)
code, out, err = ingest(A, cp2, kp2)
assert code == 0, (out, err)
v2 = one(A, f"SELECT max(data_version) FROM {D}.ingest_run WHERE status = 'succeeded'")
assert v2 > v1 and one(A, f"SELECT count(*) FROM {D}.contract") == 1800
parts = {r["relname"] for r in q(A, "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid")}
assert parts == {f"contract_v{v2}", f"claim_v{v2}"}, parts  # the old partitions are gone
assert one(A, f"SELECT count(*) FROM pg_tables WHERE schemaname = '{D}' AND tablename LIKE '%%_load_%%'") == 0
print("ok - a second load replaces the first: new version live, old partitions dropped, no leftovers")

# ======================================================================== C. readers never see a half-loaded table
c3, k3 = sample.generate(6000, seed=5)
cp3, kp3 = write_extract(TMP / "a3", c3, k3)
before = (one(A, f"SELECT count(*) FROM {D}.contract"), one(A, f"SELECT count(*) FROM {D}.claim"))
seen, errors, stop = set(), [], threading.Event()


def reader():
    try:
        with psycopg.connect(dsn(A), autocommit=True) as c:
            while not stop.is_set():
                seen.add((c.execute(f"SELECT count(*) FROM {D}.contract").fetchone()[0], c.execute(f"SELECT count(*) FROM {D}.claim").fetchone()[0]))
                time.sleep(0.003)
    except Exception as e:  # noqa: BLE001
        errors.append(repr(e))


t = threading.Thread(target=reader)
t.start()
code, out, err = ingest(A, cp3, kp3)
time.sleep(0.1)
stop.set(); t.join()
after = (6000, len(k3))
assert code == 0 and not errors, (code, errors, err)
assert seen <= {before, after} and len(seen) >= 1, f"a reader saw a partial table: {seen - {before, after}}"
assert after in seen or one(A, f"SELECT count(*) FROM {D}.contract") == 6000
print(f"ok - {len(seen)} distinct state(s) seen by a concurrent reader during a 6,000-contract load, all either the old or the new data")
live_before_failure = one(A, f"SELECT max(data_version) FROM {D}.ingest_run WHERE status = 'succeeded'")

# ======================================================================== D. a failed load changes nothing
from ingest import load  # noqa: E402

real_swap = load._swap


def exploding_swap(*a, **k):
    raise load.IngestError("simulated failure at the switch")


load._swap = exploding_swap
code, out, err = ingest(A, cp3, kp3)  # same size as the live data, so the guard lets it reach the switch
load._swap = real_swap
assert code == 1 and "simulated" in err
assert one(A, f"SELECT max(data_version) FROM {D}.ingest_run WHERE status = 'succeeded'") == live_before_failure
assert one(A, f"SELECT count(*) FROM {D}.contract") == 6000, "live data changed after a failed load"
assert one(A, f"SELECT count(*) FROM pg_tables WHERE schemaname = '{D}' AND tablename LIKE '%%_load_%%'") == 0, "private tables left behind"
last = q(A, f"SELECT status, error FROM {D}.ingest_run ORDER BY data_version DESC LIMIT 1")[0]
assert last["status"] == "failed" and "simulated" in last["error"]
print("ok - a load that fails at the switch leaves live data untouched, removes its private tables and logs the failure")

# ======================================================================== E. refusing a suspicious extract
tiny, ktiny = sample.generate(300, seed=9)
tp, tk = write_extract(TMP / "tiny", tiny, ktiny)
code, out, err = ingest(A, tp, tk)
assert code == 2 and "REFUSED" in err and "--force" in err, (code, err)
assert one(A, f"SELECT count(*) FROM {D}.contract") == 6000
assert q(A, f"SELECT status FROM {D}.ingest_run ORDER BY data_version DESC LIMIT 1")[0]["status"] == "aborted"
code, out, err = ingest(A, tp, tk, "--force")
assert code == 0 and one(A, f"SELECT count(*) FROM {D}.contract") == 300
print("ok - an extract with far fewer live contracts is refused (exit 2), and --force overrides it")

# ======================================================================== F. one ingest at a time
with psycopg.connect(dsn(A), autocommit=True) as holder:
    holder.execute("SELECT pg_advisory_lock(7351001)")
    code, out, err = ingest(A, cp, kp)
    assert code == 1 and "already running" in err, (code, err)
print("ok - a second ingest while one is running is refused")

# ======================================================================== G. bad extracts
no_risk = c1.drop(columns=["RISK_SCORE"])
rp, rk = write_extract(TMP / "norisk", no_risk, k1)
count_before = one(A, f"SELECT count(*) FROM {D}.contract")
code, out, err = ingest(A, rp, rk, "--force")
assert code == 1 and "RISK_SCORE" in err and "unchanged" in err
assert one(A, f"SELECT count(*) FROM {D}.contract") == count_before
no_date = c1.drop(columns=["CONTRACT_END_DATE"])
dp, dk = write_extract(TMP / "nodate", no_date, k1)
code, out, err = ingest(A, dp, dk, "--force")
assert code == 0 and "CONTRACT_END_DATE" in out, (code, err)
bad_claims = k1.drop(columns=["CLAIMDATE"])
bp, bk = write_extract(TMP / "badclaims", c1, bad_claims)
code, out, err = ingest(A, bp, bk, "--force")
assert code == 1 and "CLAIMDATE" in err
print("ok - a missing REQUIRED column stops the load (nothing changes); a missing optional one loads with a warning")

# ======================================================================== H. the Snowflake path, against a stand-in connector
try:
    import pyarrow as pa
except ImportError:
    pa = None
    print("skip - Snowflake path (pyarrow not installed: pip install -r requirements-ingest.txt)")
if pa:
    tables = {"DB.S.CONTRACT": pd.read_csv(cp, header=None, dtype=str, keep_default_na=False, na_values=[""]),
              "DB.S.CLAIMS": pd.read_csv(kp, header=None, dtype=str, keep_default_na=False, na_values=[""])}

    class FakeCursor:
        def execute(self, query):
            assert query.startswith("SELECT * FROM ") and ";" not in query, query
            self.frame = tables[query.split()[-1]]
            self.names = self.frame.iloc[0].tolist()  # includes the repeated ISCURRENT, like the real product
            self.description = [(n, None) for n in self.names]
            self.body = self.frame.iloc[1:]

        def fetch_arrow_batches(self):
            half = len(self.body) // 2
            for part in (self.body.iloc[:half], self.body.iloc[half:]):
                yield pa.Table.from_arrays(
                    [pa.array([None if pd.isna(x) else x for x in part.iloc[:, i]], type=pa.string()) for i in range(part.shape[1])],
                    names=self.names)

    class FakeConn:
        def cursor(self): return FakeCursor()
        def close(self): pass

    connected = {}
    mod = types.ModuleType("snowflake.connector")
    mod.connect = lambda **kw: connected.update(kw) or FakeConn()
    pkg = types.ModuleType("snowflake")
    pkg.connector = mod
    sys.modules.update({"snowflake": pkg, "snowflake.connector": mod})
    os.environ.update(SNOWFLAKE_ACCOUNT="acct", SNOWFLAKE_USER="u", SNOWFLAKE_WAREHOUSE="wh", SNOWFLAKE_PASSWORD="pw",
                      SNOWFLAKE_CONTRACT_TABLE="DB.S.CONTRACT", SNOWFLAKE_CLAIMS_TABLE="DB.S.CLAIMS")
    S = use(pg.new_database())
    code, out, err = run_cli("--source", "snowflake", "--as-of", AS_OF)
    assert code == 0, (out, err)
    assert connected["account"] == "acct" and connected["password"] == "pw" and connected["warehouse"] == "wh"
    assert one(S, f"SELECT count(*) FROM {D}.contract") == 1500 and one(S, f"SELECT count(*) FROM {D}.claim") == len(k1)
    assert one(S, f"SELECT count(*) FROM {D}.contract WHERE in_scope") == int(oracle_scope.sum()), "Snowflake path disagrees with the local path"
    assert "ISCURRENT__2" in out
    os.environ["SNOWFLAKE_CONTRACT_TABLE"] = "DB.S.CONTRACT; DROP TABLE x"
    code, out, err = run_cli("--source", "snowflake", "--as-of", AS_OF)
    assert code == 1 and "valid Snowflake table name" in err
    for k in ("SNOWFLAKE_PASSWORD",):
        os.environ.pop(k)
    os.environ["SNOWFLAKE_CONTRACT_TABLE"] = "DB.S.CONTRACT"
    code, out, err = run_cli("--source", "snowflake", "--as-of", AS_OF)
    assert code == 1 and "SNOWFLAKE_PRIVATE_KEY_PATH" in err
    del sys.modules["snowflake"], sys.modules["snowflake.connector"]
    print("ok - Snowflake source (stand-in connector): same result as the file source, repeated header handled, table name validated, auth required")

# ======================================================================== I. every rule, on hand-built rows
B = use(pg.new_database())
rows = []   # (contract id, overrides, expected in_scope)


def add(rid, scope=True, **ov):
    rows.append((rid, ov, scope))


def off(days):
    return (TODAY + timedelta(days=days)).isoformat()


for i, (d, bucket) in enumerate([(5, "10"), (10, "10"), (11, "30"), (45, "45"), (46, "60"), (90, "90"), (91, ">90"), (-1, "Lost"), (None, ">90")]):
    add(f"B{i}", CONTRACT_ORIGINAL_END_DATE=off(d) if d is not None else None, EXPECT_BUCKET=bucket)
add("S1", CONTRACT_STATUS="Inactive", CONTRACT_ORIGINAL_END_DATE=off(-30))                                   # recently lost: kept
add("S2", False, CONTRACT_STATUS="Inactive", CONTRACT_ORIGINAL_END_DATE=off(-120))                           # lost long ago: out
add("S3", False, IS_ACTIVE_CONTRACT_VERSION=False)                                                           # superseded version: out
add("S4", False, CONTRACT_STATUS="Inactive", IS_ACTIVE_CONTRACT_VERSION=False, CONTRACT_ORIGINAL_END_DATE=off(10))
add("S5", CONTRACT_STATUS=" LIVE ")                                                                          # case and spaces
for rid, raw_ctx, want in [("C1", "34", "034"), ("C2", "49.0", "049"), ("C3", "7", "007"), ("C4", "ABC", None), ("C5", "1234", None), ("C6", None, None)]:
    add(rid, CTXID=raw_ctx, EXPECT_CTX=want)
add("DUP1", False, IS_ACTIVE_CONTRACT_VERSION=False, CONTRACT_START_DATE="2010-01-01", ANNUAL_CONTRACT_VALUE=111)   # older duplicate: dropped
add("DUP1", CONTRACT_START_DATE="2020-01-01", ANNUAL_CONTRACT_VALUE=222)                                     # kept
for k in range(40):  # CTX 100: 40 valued contracts -> its own median, 119.5
    add(f"M{k:02d}", CTXID="100", ANNUAL_CONTRACT_VALUE=100 + k, RISK_SCORE=55 if k < 30 else 35)
for k in range(5):   # CTX 200: only 5 valued -> falls back to the all-CTX median
    add(f"N{k}", CTXID="200", ANNUAL_CONTRACT_VALUE=1000 + k, RISK_SCORE=55)
add("Q1", MODEL_CATEGORY=" lcvmt ", EXPECT_EQ="Reefer Unit")
add("Q2", MODEL_CATEGORY="ZZZ", EXPECT_EQ="Unclassified")
add("H1", IS_DIRECT_CONTRACT=True, EXPECT_CHANNEL="Direct")
add("H2", IS_DIRECT_CONTRACT=False, EXPECT_CHANNEL="Dealer")

base, _ = sample.generate(len(rows), seed=11)
base = base.astype(object)  # the overrides below put text ids and None into numeric columns
defaults = {"CTXID": "034", "CONTRACT_STATUS": "Live", "IS_ACTIVE_CONTRACT_VERSION": True, "CONTRACT_END_DATE": None,
            "CONTRACT_ORIGINAL_END_DATE": off(400), "ANNUAL_CONTRACT_VALUE": 500, "RISK_SCORE": 10, "IS_DIRECT_CONTRACT": False,
            "MODEL_CATEGORY": "LCVMT", "CONTRACT_START_DATE": off(-1000), "ACCOUNTID": None, "COMPANY": "Edge Co"}
for i, (rid, ov, _) in enumerate(rows):
    base.loc[i, "CONTRACTID"] = rid
    for col, val in {**defaults, **{k: v for k, v in ov.items() if not k.startswith("EXPECT")}}.items():
        base.loc[i, col] = val
ep, _ = write_extract(TMP / "edge", base, None)
code, out, err = ingest(B, ep, None)
assert code == 0, (out, err)


def row(rid, col):
    return one(B, f"SELECT {col} FROM {D}.contract WHERE contractid = %s", rid)


for rid, ov, scope in rows:
    if rid == "DUP1" and ov.get("ANNUAL_CONTRACT_VALUE") == 111:
        continue
    assert row(rid, "in_scope") is scope, (rid, "in_scope", row(rid, "in_scope"), scope)
    if "EXPECT_BUCKET" in ov:
        assert row(rid, "expiry_bucket") == ov["EXPECT_BUCKET"], (rid, row(rid, "expiry_bucket"), ov["EXPECT_BUCKET"])
    if "EXPECT_CTX" in ov:
        assert row(rid, "ctxid") == ov["EXPECT_CTX"], (rid, row(rid, "ctxid"))
    if "EXPECT_EQ" in ov:
        assert row(rid, "equipment_type") == ov["EXPECT_EQ"], (rid, row(rid, "equipment_type"))
    if "EXPECT_CHANNEL" in ov:
        assert row(rid, "channel") == ov["EXPECT_CHANNEL"], (rid, row(rid, "channel"))
assert one(B, f"SELECT count(*) FROM {D}.contract WHERE contractid = 'DUP1'") == 1 and row("DUP1", "annual_contract_value") == 222
assert one(B, f"SELECT count(*) FROM {D}.contract") == len(rows) - 1
assert "ZZZ" in out and "duplicate CONTRACTID" in out and "no valid CTX" in out
print("ok - every rule on hand-built rows: expiry boundaries, live-contract rule, CTX clean-up, duplicate ids, crosswalk, channel")

# per-CTX median, the fallback, and the segment each contract gets
from rules import compute_segment  # noqa: E402

in_scope_values = [ov.get("ANNUAL_CONTRACT_VALUE", 500) for rid, ov, scope in rows if scope]
global_median = float(np.median(in_scope_values))
s100 = q(B, f"SELECT median_value, valued_contracts, used_global_median FROM {D}.ctx_stats WHERE ctx = '100'")[0]
s200 = q(B, f"SELECT median_value, valued_contracts, used_global_median FROM {D}.ctx_stats WHERE ctx = '200'")[0]
assert s100 == {"median_value": 119.5, "valued_contracts": 40, "used_global_median": False}, s100
assert s200["used_global_median"] is True and s200["valued_contracts"] == 5 and abs(s200["median_value"] - global_median) < 0.01, (s200, global_median)
assert row("M00", "segment") == "At Risk"      # value 100, below its CTX median 119.5, score 55
assert row("M25", "segment") == "High Risk"    # value 125, above the median, score 55
assert row("M35", "segment") == "Healthy"      # value 135, above the median, score 35
for k in range(5):
    assert row(f"N{k}", "segment") == compute_segment(55, 1000 + k, global_median)  # 5 valued contracts: the all-CTX median applies
assert abs(row("M25", "value_vs_median") - 125 / 119.5) < 1e-6
print("ok - per-CTX median (119.5 for 40 valued contracts), fallback to the all-CTX median for a CTX with 5, and the segments that follow")

# ======================================================================== J. the app can read what was loaded
env = {**os.environ, "DATABASE_URL": A, "DATA_SOURCE": "postgres", "STATE_MAX_ROWS": "50"}
r = subprocess.run([sys.executable, "-c", """
import json, state
cs = state.state.contracts
json.dumps(cs, allow_nan=False)
assert len(cs) == 50 and all(isinstance(c['contractId'], str) for c in cs)
assert all(sum(c['riskFactors'].values()) == c['riskScore'] for c in cs if c['riskScore'] < 100)
assert cs[0]['riskScore'] >= cs[-1]['riskScore'], 'highest risk first'
print('bridge ok', len(cs), state.state.ctx_codes())"""], capture_output=True, text=True, env=env, cwd=BACKEND)
assert r.returncode == 0 and "bridge ok" in r.stdout, r.stderr[-600:]
empty = pg.new_database()
r = subprocess.run([sys.executable, "-c", "import state"], capture_output=True, text=True, env={**env, "DATABASE_URL": empty}, cwd=BACKEND)
assert r.returncode != 0 and "python -m ingest" in r.stderr, r.stderr[-400:]
print("ok - the app reads the ingested data (strict JSON, riskFactors sum to the score) and says how to load data when there is none")

print("ALL INGEST CHECKS PASSED")
