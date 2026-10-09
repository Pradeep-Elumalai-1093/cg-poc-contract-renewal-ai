"""Contract and claim details from the Snowflake data product (read only)."""
import json

from .util import chunks, jsonable


class Snowflake:
    def __init__(self, conn, settings, cursor_factory=None):
        self.conn, self.settings = conn, settings
        self._dict_cursor = cursor_factory

    @classmethod
    def connect(cls, secrets_client, settings) -> "Snowflake":
        import snowflake.connector  # a layer dependency, imported here so tests and tools don't need it
        from cryptography.hazmat.primitives import serialization

        sec = json.loads(secrets_client.get_secret_value(SecretId=settings.snowflake_secret_arn)["SecretString"])
        kw = {k: sec[k] for k in ("account", "user", "warehouse", "role", "database", "schema") if sec.get(k)}
        if sec.get("private_key"):    # key-pair authentication (preferred for a service)
            key = serialization.load_pem_private_key(
                sec["private_key"].encode(), password=sec["private_key_passphrase"].encode() if sec.get("private_key_passphrase") else None)
            kw["private_key"] = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        else:
            kw["password"] = sec["password"]
        return cls(snowflake.connector.connect(**kw), settings, snowflake.connector.DictCursor)

    def _query(self, sql: str, params: list) -> list[dict]:
        cur = self.conn.cursor(self._dict_cursor) if self._dict_cursor else self.conn.cursor()
        try:
            cur.execute(sql, params)
            return [{k.lower(): v for k, v in row.items()} for row in cur.fetchall()]
        finally:
            cur.close()

    def contracts(self, ids: list[str], columns: list[str] | None = None) -> dict[str, dict]:
        """contract id -> row (lower-case keys). If a contract appears in several versions, the active one wins."""
        out: dict[str, dict] = {}
        select = "*" if columns is None else ", ".join(sorted({c.upper() for c in columns} | {"CONTRACTID", "IS_ACTIVE_CONTRACT_VERSION"}))
        for chunk in chunks(list(dict.fromkeys(ids)), 1000):
            marks = ", ".join(["%s"] * len(chunk))
            for row in self._query(f"SELECT {select} FROM {self.settings.contract_table} WHERE CAST(CONTRACTID AS VARCHAR) IN ({marks})", chunk):
                cid = str(row["contractid"])
                if cid not in out or (row.get("is_active_contract_version") and not out[cid].get("is_active_contract_version")):
                    out[cid] = row
        return out

    def claims(self, ids: list[str], per_contract: int) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for chunk in chunks(list(dict.fromkeys(ids)), 1000):
            marks = ", ".join(["%s"] * len(chunk))
            rows = self._query(f"""SELECT CONTRACTID, CLAIMDATE, FAULTID, FAULT_DESCRIPTION, JOB_TYPE_DESCRIPTION FROM {self.settings.claims_table}
                                   WHERE CAST(CONTRACTID AS VARCHAR) IN ({marks}) ORDER BY CLAIMDATE DESC""", chunk)
            for r in rows:
                lst = out.setdefault(str(r["contractid"]), [])
                if len(lst) < per_contract:
                    lst.append(jsonable({"date": r["claimdate"], "fault_id": r["faultid"], "fault": r["fault_description"], "job_type": r["job_type_description"]}))
        return out
