"""
Where the data comes from: a local CSV/Excel extract, or the Snowflake data product.
Both return a DataFrame whose headers are UPPER CASE with a repeated header
suffixed (ISCURRENT, ISCURRENT__2), so everything after this point treats the
two sources identically.
"""
import os
import re
from pathlib import Path

import pandas as pd

from . import IngestError


def dedupe_headers(names) -> list[str]:
    """['A', 'a', 'B'] -> ['A', 'A__2', 'B']. The Contract data product currently carries
    ISCURRENT twice; a pandas read would silently rename the second one 'ISCURRENT.1'."""
    seen: dict[str, int] = {}
    out = []
    for n in names:
        n = str(n).strip().upper()
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}__{seen[n]}")
    return out


def read_file(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise IngestError(f"File not found: {path}")
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        raw = pd.read_excel(p, header=None, dtype=object)
    elif ext in (".csv", ".txt"):
        raw = None
        for enc in ("utf-8-sig", "latin1"):  # real exports have shown up in both
            try:
                raw = pd.read_csv(p, header=None, dtype=str, keep_default_na=False, na_values=[""], encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raise IngestError(f"Could not decode {path} as UTF-8 or Latin-1")
    else:
        raise IngestError(f"Unsupported file type {ext!r} (use .csv or .xlsx): {path}")
    if raw.empty:
        raise IngestError(f"{path} is empty")
    df = raw.iloc[1:].reset_index(drop=True)
    df.columns = dedupe_headers(raw.iloc[0].tolist())  # header=None above keeps repeated headers intact
    return df


_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


def _snowflake_params() -> dict:
    env = os.environ
    missing = [k for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE") if not env.get(k)]
    if missing:
        raise IngestError(f"Snowflake source needs: {', '.join(missing)}")
    params = {"account": env["SNOWFLAKE_ACCOUNT"], "user": env["SNOWFLAKE_USER"], "warehouse": env["SNOWFLAKE_WAREHOUSE"]}
    for key, var in (("role", "SNOWFLAKE_ROLE"), ("database", "SNOWFLAKE_DATABASE"), ("schema", "SNOWFLAKE_SCHEMA"),
                     ("authenticator", "SNOWFLAKE_AUTHENTICATOR")):
        if env.get(var):
            params[key] = env[var]
    if env.get("SNOWFLAKE_PRIVATE_KEY_PATH"):  # key-pair auth (preferred for a scheduled job)
        from cryptography.hazmat.primitives import serialization

        pw = env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        key = serialization.load_pem_private_key(
            Path(env["SNOWFLAKE_PRIVATE_KEY_PATH"]).read_bytes(), password=pw.encode() if pw else None)
        params["private_key"] = key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    elif env.get("SNOWFLAKE_PASSWORD"):
        params["password"] = env["SNOWFLAKE_PASSWORD"]
    elif params.get("authenticator") not in ("externalbrowser",):
        raise IngestError("Snowflake source needs SNOWFLAKE_PRIVATE_KEY_PATH, SNOWFLAKE_PASSWORD or SNOWFLAKE_AUTHENTICATOR=externalbrowser")
    return params


def read_snowflake(table: str) -> pd.DataFrame:
    """SELECT * from the table, streamed as Arrow batches. (The connector's own
    fetch_pandas_* needs its [pandas] extra, which pins pandas below 3 - Arrow batches
    converted here keep the project's pandas version.)"""
    if not table or not _TABLE.match(table):
        raise IngestError(f"Not a valid Snowflake table name: {table!r}")
    import snowflake.connector  # imported here so a local-only run doesn't need it installed

    conn = snowflake.connector.connect(**_snowflake_params())
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table}")
        names = dedupe_headers([d[0] for d in cur.description])
        frames = []
        for batch in cur.fetch_arrow_batches():
            f = batch.to_pandas()
            f.columns = names  # by position, so a repeated name can't collide
            frames.append(f)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=names)
    finally:
        conn.close()
