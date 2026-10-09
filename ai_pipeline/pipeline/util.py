import hashlib
import json
from datetime import date, datetime
from decimal import Decimal


def jsonable(v):
    """Snowflake hands back Decimal, date and datetime; JSON (and the input hash) need plain values."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def canonical(obj) -> str:
    return json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"))


def input_hash(obj) -> str:
    """SHA-256 of the input serialised with sorted keys and no spaces - the same rule the result tables document, so
    a result is regenerated when (and only when) what went into the model changed."""
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def pretty(obj) -> str:
    return json.dumps(jsonable(obj), indent=2, sort_keys=True)


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def split_s3(uri: str) -> tuple[str, str]:
    assert uri.startswith("s3://"), uri
    bucket, _, key = uri[5:].partition("/")
    return bucket, key
