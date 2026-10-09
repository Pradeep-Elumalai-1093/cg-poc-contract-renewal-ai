"""
Starting Bedrock batch inference jobs and reading their results.

Bedrock needs at least `min_batch_records` records per batch job (100 by default). A smaller delta - the normal case on
most days for recommendations - is run directly instead, and its results are written to the same S3 location in the same
format, under a synthetic job id `sync:<batch id>`; everything after that point treats the two identically.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

SYNC = "sync:"
DONE = {"Completed", "PartiallyCompleted"}
FAILED = {"Failed", "Stopped", "Expired"}


def job_name(job_type: str, run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", f"pcr-{job_type}-{run_id[:8]}")[:63]


class Jobs:
    def __init__(self, bedrock, runtime, store, settings):
        self.bedrock, self.runtime, self.store, self.settings = bedrock, runtime, store, settings

    def submit(self, name: str, model_id: str, input_key: str, output_prefix: str, token: str) -> str:
        """Starts a Bedrock batch job. clientRequestToken makes the call idempotent: asking twice returns the same job."""
        resp = self.bedrock.create_model_invocation_job(
            jobName=name, roleArn=self.settings.batch_role_arn, modelId=model_id, clientRequestToken=token,
            inputDataConfig={"s3InputDataConfig": {"s3InputFormat": "JSONL", "s3Uri": self.store.uri(input_key)}},
            outputDataConfig={"s3OutputDataConfig": {"s3Uri": self.store.uri(output_prefix)}},
            timeoutDurationInHours=self.settings.timeout_hours)
        return resp["jobArn"]

    def status(self, arn: str) -> tuple[str, str]:
        if arn.startswith(SYNC):
            return "Completed", ""
        resp = self.bedrock.get_model_invocation_job(jobIdentifier=arn)
        return resp["status"], resp.get("message", "")

    def run_sync(self, model_id: str, records: list[dict], out_key: str) -> int:
        """Direct calls, a few at a time, written out as Bedrock would have written them."""
        def one(rec):
            try:
                resp = self.runtime.invoke_model(modelId=model_id, body=json.dumps(rec["modelInput"]), contentType="application/json", accept="application/json")
                return {"recordId": rec["recordId"], "modelOutput": json.loads(resp["body"].read())}
            except Exception as e:  # noqa: BLE001 - one record failing must not lose the others
                return {"recordId": rec["recordId"], "error": {"errorMessage": str(e)[:500]}}
        with ThreadPoolExecutor(max_workers=self.settings.sync_concurrency) as pool:
            return self.store.put_jsonl(out_key, list(pool.map(one, records)))

    def output_records(self, batch: dict):
        """Every output line of a finished batch (Bedrock writes <output uri>/<job id>/<input file>.jsonl.out)."""
        bucket, _, prefix = batch["output_s3_uri"][5:].partition("/")
        assert bucket == self.store.bucket, "outputs are expected in the pipeline bucket"
        arn = batch["bedrock_job_arn"]
        folder = f"sync-{arn[len(SYNC):]}" if arn.startswith(SYNC) else arn.rsplit("/", 1)[-1]
        keys = [k for k in self.store.list_keys(f"{prefix}{folder}/") if k.endswith(".jsonl.out")]
        if not keys:
            raise FileNotFoundError(f"No output found under s3://{bucket}/{prefix}{folder}/")
        for key in keys:
            yield from self.store.read_jsonl(key)
