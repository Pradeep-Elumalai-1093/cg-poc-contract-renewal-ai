"""
Throwaway PostgreSQL databases for the checks: each run creates its own empty
database, migrates it with the real Alembic migration, and drops it on exit.

Needs a PostgreSQL server you can create databases on (for local work:
`docker compose up -d db` from the repo root). Point TEST_PG_ADMIN_DSN at it if
yours isn't the default below.
"""
import atexit
import os
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parent.parent
ADMIN_DSN = os.environ.get("TEST_PG_ADMIN_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")


def _drop(name: str) -> None:
    try:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    except Exception:
        pass


def fresh_database() -> str:
    """Creates an empty database and returns its SQLAlchemy URL."""
    name = "uda_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    atexit.register(_drop, name)
    url = make_url(ADMIN_DSN).set(drivername="postgresql+psycopg", database=name)
    return url.render_as_string(hide_password=False)


def migrate(url: str) -> None:
    """Runs `alembic upgrade head` against url, in-process."""
    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def new_database() -> str:
    url = fresh_database()
    migrate(url)
    return url


# --- loading data and querying, for tests that exercise the real tables ---------------------------
def query(url: str, sql: str, *params):
    """Runs SQL against url and returns the rows as dicts (autocommit)."""
    from psycopg.rows import dict_row
    with psycopg.connect(make_url(url).set(drivername="postgresql").render_as_string(hide_password=False),
                         autocommit=True, row_factory=dict_row) as c:
        cur = c.execute(sql, params) if params else c.execute(sql)   # no params: leave a literal % (modulo) alone
        return cur.fetchall() if cur.description else []


def ingest_frames(url: str, contracts, claims=None) -> None:
    """Writes the frames to CSV and runs the real ingest command against url."""
    import contextlib
    import io
    import tempfile

    from ingest import cli

    d = Path(tempfile.mkdtemp())
    contracts.to_csv(d / "contract.csv", index=False, date_format="%Y-%m-%d")
    args = ["--source", "local", "--contracts", str(d / "contract.csv"), "--force"]
    if claims is not None:
        claims.to_csv(d / "claims.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
        args += ["--claims", str(d / "claims.csv")]
    else:
        args += ["--skip-claims"]
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
    finally:
        os.environ["DATABASE_URL"] = previous if previous is not None else ""
        if previous is None:
            os.environ.pop("DATABASE_URL")
    assert code == 0, (out.getvalue(), err.getvalue())


def load_contracts(url: str, rows: list[dict]) -> None:
    """One live contract per dict (keys are product column names, e.g. CONTRACTID, CTXID, CUSTOMERID),
    everything else filled in by the sample generator."""
    from datetime import date, timedelta

    from ingest import sample

    frame, _ = sample.generate(len(rows), seed=11)
    frame = frame.astype(object)
    today = date.today()
    defaults = {"CTXID": "034", "CONTRACT_STATUS": "Live", "IS_ACTIVE_CONTRACT_VERSION": True, "CONTRACT_END_DATE": None,
                "CONTRACT_ORIGINAL_END_DATE": (today + timedelta(days=200)).isoformat(), "ANNUAL_CONTRACT_VALUE": 500,
                "RISK_SCORE": 40, "ACCOUNTID": None}
    for i, row in enumerate(rows):
        for col, val in {**defaults, "COMPANY": f"Customer {row.get('CUSTOMERID', i)}", **row}.items():
            frame.loc[i, col] = val
    ingest_frames(url, frame)
