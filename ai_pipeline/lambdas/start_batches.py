"""Lambda 1 - runs on a daily schedule: decides what needs the model, builds the batch inputs, starts the Bedrock jobs.

Test events (invoke it by hand):  {"dry_run": true}   plan and count, start nothing
                                  {"limit": 120}      cap the records per job
                                  {"jobs": ["contract_summary"]}   only these job types
                                  {"force": true}     regenerate even where the stored input_hash matches
"""
import json
import logging
import time

from pipeline import stages
from pipeline.runtime import get_runtime

logging.getLogger().setLevel(logging.INFO)
SAFETY_SECONDS = 120          # stop planning this long before the Lambda timeout, so what has been planned still gets submitted


def handler(event, context):
    rt = get_runtime()
    deadline = time.time() + context.get_remaining_time_in_millis() / 1000 - SAFETY_SECONDS
    result = stages.start_batches(rt, event or {}, deadline)
    logging.info("started: %s", json.dumps(result, default=str))
    return json.loads(json.dumps(result, default=str))
