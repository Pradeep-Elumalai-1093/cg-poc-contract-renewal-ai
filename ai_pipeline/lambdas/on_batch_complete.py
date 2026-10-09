"""Lambda 2 - runs when a Bedrock batch job finishes (EventBridge), when a direct run hands over, and on a schedule (sweep).

Events:  Bedrock job state change         {"detail": {"batchJobArn": ..., "status": "Completed"}}
         a finished direct run            {"batchJobArn": "sync:<batch id>", "status": "Completed"}
         the safety-net schedule          {"sweep": true}
         a status report (for operators)  {"report": true}
"""
import json
import logging
import time

from pipeline import stages
from pipeline.runtime import get_runtime

logging.getLogger().setLevel(logging.INFO)
SAFETY_SECONDS = 60


def handler(event, context):
    rt = get_runtime()
    event = event or {}
    deadline = time.time() + context.get_remaining_time_in_millis() / 1000 - SAFETY_SECONDS
    if event.get("report"):
        result = stages.report(rt)
    elif event.get("sweep"):
        result = stages.sweep(rt, deadline)
    else:
        detail = event.get("detail") or event               # an EventBridge event nests the fields; a direct hand-over is flat
        arn = detail.get("batchJobArn")
        if not arn:
            raise ValueError(f"Not a batch completion event: {json.dumps(event)[:300]}")
        status = detail.get("status") or detail.get("batchJobStatus") or ""
        result = stages.process_completion(rt, arn, status, detail.get("message", ""), worker=getattr(context, "aws_request_id", "") or "")
    logging.info("result: %s", json.dumps(result, default=str))
    return json.loads(json.dumps(result, default=str))
