import json

from .util import jsonable


class Store:
    """The few S3 operations the pipeline needs (JSON Lines in, JSON Lines out)."""

    def __init__(self, client, bucket: str):
        self.client, self.bucket = client, bucket

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def put_jsonl(self, key: str, rows) -> int:
        body = "".join(json.dumps(jsonable(r), separators=(",", ":")) + "\n" for r in rows)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
        return body.count("\n")

    def read_jsonl(self, key: str):
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        for line in body.iter_lines():
            if line.strip():
                yield json.loads(line)

    def list_keys(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
            page = self.client.list_objects_v2(**kw)
            keys += [o["Key"] for o in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                return keys
            token = page["NextContinuationToken"]
