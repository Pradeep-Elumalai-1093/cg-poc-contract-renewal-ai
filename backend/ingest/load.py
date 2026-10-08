"""
Load prepared rows into PostgreSQL and switch them live atomically.

The contract and claim tables are LIST-partitioned by data_version and only ever have
ONE partition attached - the live one. A load never touches it:

  1. create a private table next to the live one and COPY the new rows into it,
  2. add the primary key and the catalog's indexes, ANALYZE (all off to the side),
  3. in ONE short transaction: detach and drop the old partition, attach the new one,
     replace ctx_stats, mark the run succeeded.

Readers (queries on the parent table, which never changes name) see the old data until
step 3 commits and the new data after - never half of either. If anything fails before
step 3, the live data is exactly as it was and the private tables are removed.
"""
import json
import os
import re
import time

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from . import IngestError
from .catalog import CLAIM_COLUMNS, app_schema, data_schema, index_definitions, load_catalog, table_columns
from .transform import for_copy

ADVISORY_LOCK_KEY = 7351001           # one ingest at a time, whoever starts it
MAX_DROP_PERCENT = int(os.environ.get("INGEST_MAX_DROP_PERCENT", "50"))
CHUNK_ROWS = 25_000
SWAP_LOCK_TIMEOUT = os.environ.get("INGEST_SWAP_LOCK_TIMEOUT", "20s")


class IngestAborted(IngestError):
    """The extract looks wrong (far fewer live contracts than the current data); nothing was changed."""


_BASE = {"timestamp": "timestamp without time zone"}


def _base(sql_type: str) -> str:
    t = re.split(r"\s+(?:NOT|DEFAULT)\b", sql_type, maxsplit=1)[0].strip()
    return _BASE.get(t, t)


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise IngestError("DATABASE_URL is not set")
    dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    conn = psycopg.connect(dsn, options="-c timezone=UTC", autocommit=True)
    return conn


def ensure_parent(cur, D: str, table: str, columns: list[tuple[str, str]], rebuild: bool) -> list[str]:
    """Create the partitioned parent from the catalog, or bring an existing one in line with
    it (new catalog columns are added). A changed column TYPE needs --rebuild-schema, because
    the table is a rebuildable copy and silently converting 300K rows isn't worth the risk."""
    notes: list[str] = []
    cur.execute("SELECT to_regclass(%s)", (f"{D}.{table}",))
    exists = cur.fetchone()[0] is not None
    if exists and rebuild:
        cur.execute(sql.SQL("DROP TABLE {} CASCADE").format(sql.Identifier(D, table)))
        notes.append(f"rebuilt {table}")
        exists = False
    if not exists:
        cols = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(n), sql.SQL(t)) for n, t in columns)
        cur.execute(sql.SQL("CREATE TABLE {} ({}) PARTITION BY LIST (data_version)").format(sql.Identifier(D, table), cols))
        return notes
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s", (D, table))
    have = dict(cur.fetchall())
    for name, sql_type in columns:
        if name not in have:
            cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                sql.Identifier(D, table), sql.Identifier(name), sql.SQL(re.sub(r"\s+NOT NULL", "", sql_type))))
            notes.append(f"{table}: added column {name}")
        elif have[name] != _base(sql_type):
            raise IngestError(f"{table}.{name} is {have[name]} but the catalog now says {_base(sql_type)}. "
                              "Run with --rebuild-schema (the table is a rebuildable copy; live data is unavailable until the load finishes).")
    stale = sorted(set(have) - {n for n, _ in columns})
    if stale:
        notes.append(f"{table}: columns no longer in the catalog are left in place: {', '.join(stale)}")
    return notes


def ensure_views(cur, D: str, S: str) -> None:
    """What the AI pipeline reads: for every live contract, the retention actions it may be given.
    A global action applies everywhere unless the contract's area has a local action with the same
    category, sub-category and name; an action with criteria applies only to the contracts it matched.
    It joins the contract table, so it lives here (created after the table exists, kept by every load)."""
    cur.execute(f"""
CREATE OR REPLACE VIEW "{S}".v_retention_options AS
SELECT c.contractid, c.ctxid AS ctx, a.id AS action_id, a.scope, a.category, a.sub_category, a.name, a.description
FROM "{D}".contract c
JOIN "{S}".retention_action a ON a.deleted_at IS NULL AND (a.scope = 'global' OR a.ctx = c.ctxid)
WHERE c.in_scope AND c.ctxid IS NOT NULL
  AND (a.criteria = '{{}}'::jsonb OR EXISTS (SELECT 1 FROM "{S}".retention_action_match m WHERE m.action_id = a.id AND m.contractid = c.contractid))
  AND NOT (a.scope = 'global' AND EXISTS (
        SELECT 1 FROM "{S}".retention_action l WHERE l.scope = 'local' AND l.ctx = c.ctxid AND l.deleted_at IS NULL
          AND lower(btrim(l.category)) = lower(btrim(a.category)) AND lower(btrim(l.sub_category)) = lower(btrim(a.sub_category))
          AND lower(btrim(l.name)) = lower(btrim(a.name))))""")


def _copy(cur, D: str, table: str, df, columns: list[str]) -> None:
    stmt = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')").format(
        sql.Identifier(D, table), sql.SQL(", ").join(sql.Identifier(c) for c in columns))
    with cur.copy(stmt) as cp:
        for i in range(0, len(df), CHUNK_ROWS):
            cp.write(df.iloc[i:i + CHUNK_ROWS][columns].to_csv(index=False, header=False, na_rep=""))


def _swap(conn, D: str, v: int, loads: dict[str, str], ctx_stats: list[dict], summary: dict) -> None:
    with conn.transaction():
        cur = conn.cursor()
        cur.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(SWAP_LOCK_TIMEOUT)))
        for parent, load in loads.items():
            cur.execute("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                        "WHERE i.inhparent = to_regclass(%s)", (f"{D}.{parent}",))
            for (old,) in cur.fetchall():
                cur.execute(sql.SQL("ALTER TABLE {} DETACH PARTITION {}").format(sql.Identifier(D, parent), sql.Identifier(D, old)))
                cur.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(D, old)))
            cur.execute(sql.SQL("ALTER TABLE {} ATTACH PARTITION {} FOR VALUES IN ({})").format(
                sql.Identifier(D, parent), sql.Identifier(D, load), sql.Literal(v)))
            cur.execute(sql.SQL("ALTER TABLE {} RENAME TO {}").format(sql.Identifier(D, load), sql.Identifier(f"{parent}_v{v}")))
        cur.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(D, "ctx_stats")))
        for s in ctx_stats:
            cur.execute(sql.SQL("INSERT INTO {} (ctx, median_value, valued_contracts, contracts, used_global_median, data_version) "
                                "VALUES (%s, %s, %s, %s, %s, %s)").format(sql.Identifier(D, "ctx_stats")),
                        (s["ctx"], s["median_value"], s["valued_contracts"], s["contracts"], s["used_global_median"], v))
        cur.execute(sql.SQL("UPDATE {} SET status = 'succeeded', finished_at = now(), contracts_loaded = %s, in_scope = %s, "
                            "claims_loaded = %s, report = %s WHERE data_version = %s").format(sql.Identifier(D, "ingest_run")),
                    (summary["contracts"], summary["in_scope"], summary.get("claims"), json.dumps(summary["report"], default=str), v))


def run(contracts, contracts_report: dict, claims, claims_report: dict | None, *, source: str,
        rebuild: bool = False, force: bool = False) -> dict:
    """Load contracts (and claims, unless claims is None - then the live claims stay as they are)."""
    D = data_schema()
    cols = load_catalog()
    contract_cols = table_columns(cols)
    claim_cols = [("data_version", "integer NOT NULL")] + [(n, t) for n, t, _ in CLAIM_COLUMNS]
    timings: dict[str, float] = {}
    conn = connect()
    v = None
    loads: dict[str, str] = {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        if not cur.fetchone()[0]:
            raise IngestError("Another ingest is already running.")
        cur.execute("SELECT to_regclass(%s)", (f"{D}.ingest_run",))
        if cur.fetchone()[0] is None:
            raise IngestError("The database has not been migrated. Run `alembic upgrade head` from the backend folder, then try again.")
        notes = ensure_parent(cur, D, "contract", contract_cols, rebuild) + ensure_parent(cur, D, "claim", claim_cols, rebuild)
        ensure_views(cur, D, app_schema())
        cur.execute(sql.SQL("INSERT INTO {} (status, source) VALUES ('running', %s) RETURNING data_version").format(
            sql.Identifier(D, "ingest_run")), (source,))
        v = cur.fetchone()[0]

        # --- sanity gate: a bad extract must not replace good data -----------------------------
        cur.execute(sql.SQL("SELECT in_scope FROM {} WHERE status = 'succeeded' AND data_version < %s "
                            "ORDER BY data_version DESC LIMIT 1").format(sql.Identifier(D, "ingest_run")), (v,))
        prev = cur.fetchone()
        new_live = contracts_report["in_scope"]
        if prev and prev[0] and new_live < prev[0] * (1 - MAX_DROP_PERCENT / 100) and not force:
            raise IngestAborted(
                f"The extract has {new_live:,} live contracts but the current data has {prev[0]:,} "
                f"(more than a {MAX_DROP_PERCENT}% drop). Nothing was changed. If this is right, run again with --force.")

        # --- 1+2: load and index off to the side -------------------------------------------------
        t0 = time.perf_counter()
        name = f"contract_load_{v}"
        cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING DEFAULTS)").format(sql.Identifier(D, name), sql.Identifier(D, "contract")))
        loads["contract"] = name
        frame = for_copy(contracts.assign(data_version=v))
        _copy(cur, D, name, frame, [n for n, _ in contract_cols])
        timings["copy_contracts_s"] = round(time.perf_counter() - t0, 1)

        t0 = time.perf_counter()
        cur.execute(sql.SQL("ALTER TABLE {} ADD PRIMARY KEY (contractid)").format(sql.Identifier(D, name)))
        for idx, definition in index_definitions(cols):
            cur.execute(sql.SQL("CREATE INDEX {} ON {} ").format(sql.Identifier(f"{name}_{idx}"), sql.Identifier(D, name)) + sql.SQL(definition))
        cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(D, name)))
        timings["index_contracts_s"] = round(time.perf_counter() - t0, 1)

        if claims is not None:
            t0 = time.perf_counter()
            cname = f"claim_load_{v}"
            cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING DEFAULTS)").format(sql.Identifier(D, cname), sql.Identifier(D, "claim")))
            loads["claim"] = cname
            _copy(cur, D, cname, for_copy(claims.assign(data_version=v)), [n for n, _ in claim_cols])
            cur.execute(sql.SQL("CREATE INDEX {} ON {} (contractid, claimdate)").format(sql.Identifier(f"{cname}_contract"), sql.Identifier(D, cname)))
            cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(D, cname)))
            timings["load_claims_s"] = round(time.perf_counter() - t0, 1)

        for parent, load in loads.items():  # lets ATTACH skip re-checking every row against the partition bound
            cur.execute(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK (data_version = {})").format(
                sql.Identifier(D, load), sql.Identifier(f"{load}_version"), sql.Literal(v)))

        # --- 3: the switch -------------------------------------------------------------------------
        t0 = time.perf_counter()
        report = {"contracts": contracts_report, "claims": claims_report, "schema_notes": notes, "timings": timings}
        summary = {"contracts": contracts_report["contracts"], "in_scope": new_live,
                   "claims": None if claims is None else len(claims), "report": report}
        _swap(conn, D, v, loads, contracts_report["ctx"], summary)
        loads = {}  # consumed by the swap
        timings["swap_s"] = round(time.perf_counter() - t0, 2)
        return {"data_version": v, **{k: summary[k] for k in ("contracts", "in_scope", "claims")}, "timings": timings, "schema_notes": notes}
    except Exception as err:
        if v is not None:
            status = "aborted" if isinstance(err, IngestAborted) else "failed"
            try:
                cur.execute(sql.SQL("UPDATE {} SET status = %s, finished_at = now(), error = %s WHERE data_version = %s").format(
                    sql.Identifier(D, "ingest_run")), (status, str(err)[:2000], v))
            except Exception:  # noqa: BLE001 - never mask the original error
                pass
        raise
    finally:
        try:
            for table in loads.values():  # a failed load leaves no private tables behind
                conn.cursor().execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(D, table)))
        finally:
            conn.close()  # also releases the advisory lock
