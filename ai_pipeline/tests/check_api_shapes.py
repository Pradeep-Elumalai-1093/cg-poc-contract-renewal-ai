"""
Checks every AWS request the pipeline and the deployment notebook build against AWS's own service definitions (shipped inside
botocore), and that every response field the code reads exists in them. No AWS account, network or credentials needed:

    cd aws && pip install boto3 && python tests/check_api_shapes.py

This catches a wrong parameter name, a missing required field, a value outside the documented range, or a status string that
the service never returns. It cannot prove behaviour (that needs a real account - see the notebook's smoke tests).
"""
import io
import json
import re
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from botocore.validate import ParamValidator  # noqa: E402

from pipeline import bedrock_jobs as bj  # noqa: E402
from pipeline.config import Settings  # noqa: E402
from pipeline.prompts import PromptBook  # noqa: E402
from pipeline.s3io import Store  # noqa: E402

REGION = "eu-west-1"
_clients: dict = {}


def real(service):
    if service not in _clients:
        _clients[service] = boto3.client(service, region_name=REGION, aws_access_key_id="x", aws_secret_access_key="y")
    return _clients[service]


def check_request(service, operation, kwargs):
    shape = real(service).meta.service_model.operation_model(operation).input_shape
    report = ParamValidator().validate(kwargs, shape)
    assert not report.has_errors(), f"{service}.{operation}: {report.generate_report()}"


def member(service, operation, *path):
    """Walks an operation's OUTPUT shape and returns the shape at `path` (list members are entered automatically)."""
    shape = real(service).meta.service_model.operation_model(operation).output_shape
    for step in path:
        while shape.type_name == "list":
            shape = shape.member
        assert shape.type_name == "structure" and step in shape.members, f"{service}.{operation}: no field {'/'.join(path)} (stuck at {step})"
        shape = shape.members[step]
    return shape


class Recorder:
    """Stands in for a boto3 client: validates each call against the real service model, then answers with a canned response."""

    def __init__(self, service, answer):
        self.service, self.answer, self.calls = service, answer, []

    def __getattr__(self, name):
        client = real(self.service)
        if name == "exceptions":
            return client.exceptions
        op = client.meta.method_to_api_mapping.get(name)
        if op is None:
            if name == "upload_file":
                return lambda *a, **k: self.calls.append((name, a))
            if name == "get_waiter":
                return lambda w: types.SimpleNamespace(wait=lambda **k: None)
            return getattr(client, name)

        def call(**kwargs):
            check_request(self.service, op, kwargs)
            self.calls.append((op, kwargs))
            return self.answer(op, kwargs)
        return call


# ============================================================== the pipeline's own calls
settings = Settings(bucket="b", batch_role_arn="arn:aws:iam::123456789012:role/r", model_id="anthropic.claude-3-haiku-20240307-v1:0", db_schema="ai_recommendations",
                    prompts={"contract_summary": {"id": "ABCDEFGHIJ", "version": "1"}}, timeout_hours=24)
batch_calls = Recorder("bedrock", lambda op, kw: {"jobArn": "arn:aws:bedrock:eu-west-1:123456789012:model-invocation-job/abc123def456", "status": "Completed", "message": ""})
runtime_calls = Recorder("bedrock-runtime", lambda op, kw: {"body": io.BytesIO(b'{"content": []}'), "contentType": "application/json"})
jobs = bj.Jobs(batch_calls, runtime_calls, Store(object(), "b"), settings)
arn = jobs.submit(bj.job_name("contract_summary", "01a11ec1-09e8-7927-967b-5500991c4ca2"), settings.model_id, "PCR/AI_Input/contract_summary/r/input.jsonl",
                  "PCR/AI_Output/contract_summary/r/", "01a11ec1-09e8-7927-967b-5500991c4ca2")
assert bj.job_name("web_claims_summary", "01a11ec1-09e8")  # the name pattern is validated by the model on submit
assert jobs.status(arn) == ("Completed", "")
store = Store(types.SimpleNamespace(put_object=lambda **k: None), "b")
bj.Jobs(batch_calls, runtime_calls, store, settings).run_sync(settings.model_id, [{"recordId": "r1", "modelInput": {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 5,
                                                                                                                   "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}}], "k")
assert [c[0] for c in runtime_calls.calls] == ["InvokeModel"]

prompt_answers = lambda op, kw: {"id": "ABCDEFGHIJ", "version": "1", "name": "n", "arn": "a", "defaultVariant": "v", "createdAt": 0, "updatedAt": 0, "variants": [   # noqa: E731
    {"name": "v", "templateType": "TEXT", "templateConfiguration": {"text": {"text": "Hello {{x}}"}}, "modelId": "m", "inferenceConfiguration": {"text": {"maxTokens": 10, "temperature": 0.1}}}]}
agent = Recorder("bedrock-agent", prompt_answers)
book = PromptBook(agent, settings)
book.get("contract_summary")
assert agent.calls[0][1] == {"promptIdentifier": "ABCDEFGHIJ", "promptVersion": "1"}
for service, op, path in [
    ("bedrock", "CreateModelInvocationJob", ("jobArn",)), ("bedrock", "GetModelInvocationJob", ("status",)), ("bedrock", "GetModelInvocationJob", ("message",)),
    ("bedrock-runtime", "InvokeModel", ("body",)),
    ("bedrock-agent", "GetPrompt", ("version",)), ("bedrock-agent", "GetPrompt", ("defaultVariant",)), ("bedrock-agent", "GetPrompt", ("variants", "name")),
    ("bedrock-agent", "GetPrompt", ("variants", "modelId")), ("bedrock-agent", "GetPrompt", ("variants", "templateConfiguration", "text", "text")),
    ("bedrock-agent", "GetPrompt", ("variants", "templateConfiguration", "chat", "system")), ("bedrock-agent", "GetPrompt", ("variants", "templateConfiguration", "chat", "messages", "role")),
    ("bedrock-agent", "GetPrompt", ("variants", "templateConfiguration", "chat", "messages", "content")),
    ("bedrock-agent", "GetPrompt", ("variants", "inferenceConfiguration", "text", "maxTokens")), ("bedrock-agent", "GetPrompt", ("variants", "inferenceConfiguration", "text", "temperature")),
    ("bedrock-agent", "GetPrompt", ("variants", "inferenceConfiguration", "text", "topP")), ("bedrock-agent", "GetPrompt", ("variants", "inferenceConfiguration", "text", "stopSequences")),
    ("secretsmanager", "GetSecretValue", ("SecretString",)),
]:
    member(service, op, *path)
status_enum = set(member("bedrock", "GetModelInvocationJob", "status").enum)
assert (bj.DONE | bj.FAILED) <= status_enum, f"statuses the code reacts to that Bedrock never returns: {(bj.DONE | bj.FAILED) - status_enum}"
assert not (bj.DONE & bj.FAILED)
for op, kw in [("PutObject", dict(Bucket="b", Key="k", Body=b"x", ContentType="application/x-ndjson")), ("GetObject", dict(Bucket="b", Key="k")),
               ("ListObjectsV2", dict(Bucket="b", Prefix="p", ContinuationToken="t"))]:
    check_request("s3", op, kw)
check_request("lambda", "Invoke", dict(FunctionName="complete-fn", InvocationType="Event", Payload=b"{}"))
check_request("secretsmanager", "GetSecretValue", dict(SecretId="arn:aws:secretsmanager:eu-west-1:123456789012:secret:x-AbCdEf"))
print("ok - the pipeline's Bedrock, Prompt Management, S3, Lambda and Secrets calls are valid requests; every response field it reads exists; the job states it reacts to are real")

# ============================================================== the EventBridge rule
tmpl = (HERE / "template.yaml").read_text()
assert re.search(r"source:\s*\[aws\.bedrock\]", tmpl) and "Batch Inference Job State Change" in tmpl
print("ok - the EventBridge rule matches source aws.bedrock / 'Batch Inference Job State Change' (its payload shape is confirmed on the first real batch - see the notebook, step 9)")

# ============================================================== the deployment notebook, run against validating stand-ins
nb = json.loads((HERE / "deploy.ipynb").read_text())
cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
cfn_state = {"created": False}


def answer(service):
    def respond(op, kw):
        if op == "GetCallerIdentity":
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/tester"}
        if op == "ListPrompts":
            return {"promptSummaries": []}
        if op == "CreatePrompt":
            return {"id": "PRMPT" + str(len(kw["name"])).zfill(5), "name": kw["name"]}
        if op == "CreatePromptVersion":
            return {"version": "1"}
        if op == "DescribeStacks":
            if not cfn_state["created"]:
                raise ClientError({"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}}, "DescribeStacks")
            return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in
                                           {"StartFunctionName": "pcr-ai-start-batches", "CompleteFunctionName": "pcr-ai-on-batch-complete", "DailyRuleName": "pcr-ai-daily"}.items()]}]}
        if op == "CreateStack":
            cfn_state["created"] = True
            return {"StackId": "arn:aws:cloudformation:eu-west-1:123456789012:stack/s/1"}
        if op == "HeadBucket":
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket")
        if op == "Invoke":
            if json.loads(kw["Payload"]).get("report"):
                row = {"id": "x", "job_type": "contract_summary", "status": "written", "records_requested": 3, "records_written": 3, "records_skipped": 0, "records_failed": 0, "error": None}
                return {"Payload": io.BytesIO(json.dumps([row]).encode())}
            return {"Payload": io.BytesIO(b'{"run_id": "r", "dry_run": true, "jobs": {"contract_summary": {"records": 3}}}')}
        if op == "FilterLogEvents":
            return {"events": []}
        return {}
    return respond


class Session:
    def __init__(self, **kw):
        self.made = {}

    def client(self, service, **kw):
        return self.made.setdefault(service, Recorder(service, answer(service)))


fake_boto3 = types.SimpleNamespace(Session=Session)
ns = {"__name__": "notebook"}
import os  # noqa: E402
os.chdir(HERE)
sys.modules["boto3_real"] = boto3
sys.modules["boto3"] = fake_boto3                       # the notebook's `import boto3` gets the validating stand-in
import package  # noqa: E402

package.build_code = lambda out: (HERE / "template.yaml", 1)         # packaging is checked by package.py itself; skip the pip download here
package.build_layer = lambda out: (HERE / "template.yaml", 1)
# code cells of the notebook: 0 settings, 1 setup, 2 secrets (interactive: skipped), 3 prompts, 4 package + upload, 5 deploy,
# 6 database role SQL, 7 smoke test A, 8 smoke test B (guarded by its flag), 9 operations helpers, 10 teardown (guarded)
try:
    exec(cells[0], ns)
    ns["CONFIG"].update(artifact_bucket="art-bucket", pipeline_bucket="pipe-bucket", subnet_ids=["subnet-0abc1234", "subnet-0def5678"], security_group_ids=["sg-0abc1234"],
                        snowflake_contract_table="DB.SCHEMA.CONTRACT", snowflake_claims_table="DB.SCHEMA.CLAIMS", model_id="anthropic.claude-3-haiku-20240307-v1:0")
    exec(cells[1], ns)
    ns["STATE"].update(pg_secret_arn="arn:aws:secretsmanager:eu-west-1:123456789012:secret:pg-AbCdEf", snowflake_secret_arn="arn:aws:secretsmanager:eu-west-1:123456789012:secret:sf-AbCdEf")
    for i in (3, 4, 5, 6, 7, 8):
        exec(cells[i], ns)
    exec(cells[9].replace("\nreport(5)", ""), ns)
    ns["report"](1)
finally:
    sys.modules["boto3"] = sys.modules.pop("boto3_real")
    import shutil                                           # the notebook cells write these next to the notebook; leave nothing behind
    (HERE / "deploy_state.json").unlink(missing_ok=True)
    shutil.rmtree(HERE / "build", ignore_errors=True)
agent_calls = [c for c in ns["agent"].calls if c[0] == "CreatePrompt"]
assert len(agent_calls) == 6 and {c[1]["name"] for c in agent_calls} == {json.loads(p.read_text())["name"] for p in (HERE / "prompts").glob("*.json")}
assert ns["STATE"]["prompts"].keys() == {"contract_summary", "web_claims_summary", "account_summary", "recommendation", "evaluation", "draft"}
cfn_create = [c for c in ns["cfn"].calls if c[0] == "CreateStack"][0][1]
declared = set(re.findall(r"^  (\w+):\n    Type: (?:String|Number|List<[\w:]+>)", tmpl, re.M))
given = {p["ParameterKey"] for p in cfn_create["Parameters"]}
assert given <= declared, f"the notebook passes parameters the template does not declare: {given - declared}"
required = set(re.findall(r"^  (\w+):\n    Type: (?:String|Number|List<[\w:]+>)\n(?!    Default)", tmpl, re.M))
assert required <= given, f"the template requires parameters the notebook does not pass: {required - given}"
assert json.loads(dict((p["ParameterKey"], p["ParameterValue"]) for p in cfn_create["Parameters"])["PromptsJson"]).keys() == ns["STATE"]["prompts"].keys()
print("ok - the deployment notebook's prompt, upload, stack and invoke calls are valid requests; it passes exactly the template's parameters")
print("ALL API-SHAPE CHECKS PASSED")
