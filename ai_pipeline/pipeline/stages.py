"""
The pipeline's logic, free of any AWS handler plumbing so it can be tested end to end with fakes.

  start_batches   (Lambda 1)  decide what needs the model today, build the batch inputs, start the jobs
  process_completion (Lambda 2)  a job finished: write summaries, or chain the next pass of a recommendation
  sweep           (Lambda 2)  safety net for a missed completion event, and for batches stuck half way

A recommendation takes three passes - recommend, evaluate, draft - each its own batch reading the last one's output.
The result row is inserted ONCE, after the last pass, because the job's database role cannot update a row.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import bedrock_jobs as bj
from .columns import ACCOUNT_COLUMNS, assert_no_personal_data, contract_context
from .config import EVAL_CRITERIA, FIRST_STAGE, NEXT_PASS
from .prompts import output_json, output_text
from .util import chunks, input_hash, jsonable, pretty

log = logging.getLogger(__name__)
CHUNK = 1000
MAX_OPTIONS = 25
UPGRADE_PATHS = {k: v for k, v in json.loads((Path(__file__).parent / "upgrade_paths.json").read_text()).items() if not k.startswith("_")}


@dataclass
class Rec:
    variables: dict                     # what fills the prompt
    meta: dict                          # what must survive until the result is written (kept in S3 next to the input)
    rid: str = ""


def run_id_for(event: dict) -> str:
    """One run per scheduled delivery: EventBridge gives a delivery an id, and a re-delivery of the same event carries
    the same id - so a retried Lambda lands on the same run and the batch log refuses to start the jobs twice."""
    ident = event.get("run_id") or event.get("id")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pcr-run:{ident}")) if ident else str(uuid.uuid4())


# ================================================================ planning: what goes to the model today ====================
def _contract_summary(rt, force, limit, deadline):
    recs = []
    for chunk in chunks(rt.db.candidates("contract_summary"), CHUNK):
        if rt.clock() > deadline:
            log.warning("time budget reached while planning contract_summary; the rest waits for the next run")
            break
        rows = rt.snowflake.contracts([c["contractid"] for c in chunk])
        for c in chunk:
            row = rows.get(c["contractid"])
            if row is None:
                log.warning("contract %s is not in Snowflake; skipped", c["contractid"])
                continue
            ctx = contract_context(row, c["expiry_bucket"])
            data = {"contract": ctx}
            h = input_hash(data)
            if not force and h == c["contract_summary_hash"]:
                continue                                             # the same inputs as the stored summary
            recs.append(Rec({"contract_json": pretty(ctx)}, {"key": c["contractid"], "accountid": c["accountid"], "ctx": c["ctx"], "input_data": data, "input_hash": h}))
            if len(recs) >= limit:
                return recs
    return recs


def _web_claims_summary(rt, force, limit, deadline):
    recs = []
    for chunk in chunks(rt.db.candidates("web_claims_summary"), CHUNK):
        if rt.clock() > deadline:
            log.warning("time budget reached while planning web_claims_summary")
            break
        ids = [c["contractid"] for c in chunk]
        rows, claims = rt.snowflake.contracts(ids), rt.snowflake.claims(ids, rt.settings.max_claims)
        for c in chunk:
            row, cl = rows.get(c["contractid"]), claims.get(c["contractid"])
            if row is None or not cl:
                continue                                             # nothing to summarise
            equipment = {k: jsonable(row.get(k)) for k in ("model_name", "model_category", "model_type", "vehiclebrand", "manufacturedate", "runninghours") if row.get(k) is not None}
            totals = {k: jsonable(row.get(k)) for k in ("total_claims", "claims_last_90d", "repeat_faults", "written_off_jobs", "total_service_jobs") if row.get(k) is not None}
            data = {"equipment": equipment, "totals": totals, "claims": cl}
            h = input_hash(data)
            if not force and h == c["web_claims_summary_hash"]:
                continue
            recs.append(Rec({"equipment_json": pretty(equipment), "totals_json": pretty(totals), "claims_json": pretty(cl)},
                            {"key": c["contractid"], "accountid": c["accountid"], "ctx": c["ctx"], "input_data": data, "input_hash": h}))
            if len(recs) >= limit:
                return recs
    return recs


def _account_summary(rt, force, limit, deadline):
    recs, today = [], date.fromtimestamp(rt.clock())
    for chunk in chunks(rt.db.account_candidates(), 200):
        if rt.clock() > deadline:
            log.warning("time budget reached while planning account_summary")
            break
        members = rt.db.account_contracts([a["accountid"] for a in chunk])
        rows = rt.snowflake.contracts([cid for ids in members.values() for cid in ids], columns=ACCOUNT_COLUMNS)
        for a in chunk:
            accs = [rows[c] for c in members.get(a["accountid"], []) if c in rows]
            if not accs:
                continue
            value: dict = {}
            for r in accs:
                if r.get("annual_contract_value") is not None:
                    value[r.get("currency") or "?"] = value.get(r.get("currency") or "?", 0) + float(r["annual_contract_value"])
            risks = [int(r["risk_score"]) for r in accs if r.get("risk_score") is not None]

            def ends(r):   # Snowflake returns a date for a DATE column but a datetime for a TIMESTAMP one
                d = r.get("contract_end_date") or r.get("contract_original_end_date")
                if isinstance(d, datetime):
                    return d.date()
                return d if isinstance(d, date) else (date.fromisoformat(str(d)[:10]) if d else None)
            soon = sum(1 for r in accs if ends(r) and 0 <= (ends(r) - today).days <= 90)
            top = sorted(accs, key=lambda r: -(r.get("risk_score") or 0))[:15]
            data = {"account": {
                "customer_name": next((r["company"] for r in accs if r.get("company")), "the customer"), "live_contracts": len(accs),
                "annual_value_by_currency": {k: round(v) for k, v in sorted(value.items())},
                "average_risk_score": round(sum(risks) / len(risks), 1) if risks else None,
                "contracts_at_risk_50_plus": sum(1 for x in risks if x >= 50), "contracts_high_risk_70_plus": sum(1 for x in risks if x >= 70),
                "total_claims": sum(int(r.get("total_claims") or 0) for r in accs), "claims_last_90_days": sum(int(r.get("claims_last_90d") or 0) for r in accs),
                "contracts_ending_within_90_days": soon,
                "highest_risk_contracts": [{"contract": r["contractid"], "risk_score": r.get("risk_score"), "annual_value": r.get("annual_contract_value"),
                                            "currency": r.get("currency"), "equipment": r.get("model_category"), "ends": ends(r)} for r in top]}}
            h = input_hash(data)
            if not force and h == a["account_summary_hash"]:
                continue
            recs.append(Rec({"account_json": pretty(data["account"])}, {"key": a["accountid"], "accountid": a["accountid"], "ctx": a["ctx"], "input_data": data, "input_hash": h}))
            if len(recs) >= limit:
                return recs
    return recs


def _recommendation(rt, force, limit, deadline):
    cands = rt.db.candidates("recommendation")[:limit]
    recs = []
    for chunk in chunks(cands, CHUNK):
        if rt.clock() > deadline:
            log.warning("time budget reached while planning recommendation")
            break
        ids, accts = [c["contractid"] for c in chunk], list({c["accountid"] for c in chunk if c["accountid"]})
        rows, options = rt.snowflake.contracts(ids), rt.db.options(ids)
        texts, prior = rt.db.latest_texts(ids, accts), rt.db.prior_recommendations(ids)
        for c in chunk:
            row, opts = rows.get(c["contractid"]), options.get(c["contractid"])
            if row is None or not opts:
                continue
            ctx = contract_context(row, c["expiry_bucket"])
            labelled = [{"label": f"opt_{i}", "category": o["category"], "sub_category": o["sub_category"], "name": o["name"], "description": o["description"]}
                        for i, o in enumerate(opts[:MAX_OPTIONS], 1)]
            option_map = {f"opt_{i}": {"action_id": o["action_id"], "name": o["name"], "category": o["category"]} for i, o in enumerate(opts[:MAX_OPTIONS], 1)}
            summaries = {"contract": texts["contract"].get(c["contractid"]) or "none yet", "claims": texts["claims"].get(c["contractid"]) or "none yet",
                         "account": texts["account"].get(c["accountid"]) or "none yet"}
            prev = prior.get(c["contractid"]) or {}
            data = {"context": ctx, "options": labelled, "summaries": summaries, "prior": prev}
            upgrade = UPGRADE_PATHS.get(str(row.get("model_category") or "").upper(), "none available")
            recs.append(Rec(
                {"context_json": pretty(ctx), "options_json": pretty(labelled), "contract_summary": summaries["contract"], "claims_summary": summaries["claims"],
                 "account_summary": summaries["account"], "prior_json": pretty(prev), "upgrade_path": upgrade},
                {"key": c["contractid"], "accountid": c["accountid"], "ctx": c["ctx"], "milestone": c["expiry_bucket"], "input_data": data,
                 "input_hash": input_hash(data), "context": ctx, "options": labelled, "option_map": option_map, "channel": ctx["channel"],
                 "prior": prev, "config": {}, "state": {}}))
    return recs


BUILDERS = {"contract_summary": _contract_summary, "web_claims_summary": _web_claims_summary, "account_summary": _account_summary, "recommendation": _recommendation}


# ================================================================ launching a batch ===========================================
def launch(rt, run_id: str, job_type: str, parent_id, recs: list[Rec], dry: bool = False) -> dict:
    s = rt.settings
    if not recs:
        return {"records": 0}
    if dry:
        return {"records": len(recs), "dry_run": True}
    prompt = rt.prompts.get(job_type)
    model_id = s.model_for(job_type, prompt.model_id)
    out_prefix = s.output_prefix_for(job_type, run_id)
    in_key, meta_key = s.input_key(job_type, run_id, "input.jsonl"), s.input_key(job_type, run_id, "meta.jsonl")
    batch_id = rt.db.batch_create(run_id, job_type, parent_id, model_id, {"prompt": prompt.ref}, rt.store.uri(in_key), rt.store.uri(out_prefix), len(recs))
    status = rt.db.batch(batch_id)["status"]
    if status != "created":                                      # a retry of a step that already got through: leave it alone
        return {"batch": str(batch_id), "already": status}
    records, metas = [], []
    for i, r in enumerate(recs, 1):
        r.rid = f"r{i:06d}"
        records.append({"recordId": r.rid, "modelInput": prompt.model_input(r.variables)})
        metas.append({**r.meta, "rid": r.rid})                   # this pass's id must win over the one carried from the previous pass
    assert_no_personal_data(metas, "with an AI result")
    rt.store.put_jsonl(in_key, records)
    rt.store.put_jsonl(meta_key, metas)
    direct = len(recs) < s.min_batch_records
    if direct:
        arn = f"{bj.SYNC}{batch_id}"
        rt.jobs.run_sync(model_id, records, f"{out_prefix}sync-{batch_id}/input.jsonl.out")
    else:
        arn = rt.jobs.submit(bj.job_name(job_type, run_id), model_id, in_key, out_prefix, str(batch_id))
    rt.db.batch_submitted(batch_id, arn)
    if direct:                                                   # no Bedrock event will come: hand over to the completion Lambda ourselves
        rt.invoke_completion({"batchJobArn": arn, "status": "Completed", "source": "sync"})
    return {"batch": str(batch_id), "records": len(recs), "mode": "direct" if direct else "batch", "arn": arn}


def start_batches(rt, event: dict, deadline: float) -> dict:
    jobs = [j for j in FIRST_STAGE if j in (event.get("jobs") or FIRST_STAGE)]
    force, dry = bool(event.get("force")), bool(event.get("dry_run"))
    limit = min(int(event.get("limit") or rt.settings.max_records_per_job), rt.settings.max_records_per_job)
    run_id = run_id_for(event)
    result: dict = {"run_id": run_id, "dry_run": dry, "jobs": {}}
    for job in jobs:                                             # summaries first; recommendations use the summaries already stored
        try:
            result["jobs"][job] = launch(rt, run_id, job, None, BUILDERS[job](rt, force, limit, deadline), dry)
        except Exception as e:  # noqa: BLE001 - one job type failing must not stop the others
            log.exception("job %s failed to start", job)
            result["jobs"][job] = {"error": f"{type(e).__name__}: {e}"}
    if any("error" in r for r in result["jobs"].values()):
        raise RuntimeError(json.dumps(result, default=str))     # fail the invocation (alarms, retries) - the batch log makes a retry safe
    return result


# ================================================================ a batch finished ============================================
def _stage_config(batch: dict) -> dict:
    return {"model": batch["model_id"], "prompt": (batch.get("prompt_versions") or {}).get("prompt"), "batch_id": str(batch["id"]), "bedrock_job": batch["bedrock_job_arn"]}


def _text_of(outputs: dict, rid: str) -> str:
    out = outputs.get(rid)
    return output_text(out["modelOutput"]) if out and "modelOutput" in out else ""


def _json_of(outputs: dict, rid: str) -> dict | None:
    out = outputs.get(rid)
    return output_json(out["modelOutput"]) if out and "modelOutput" in out else None


def _public(rec: dict) -> dict:
    return {k: rec[k] for k in ("campaign", "category", "execution_owner", "rationale", "upsell", "confidence")}


def _score(parsed: dict | None, st) -> dict:
    scores = (parsed or {}).get("scores")
    if not isinstance(scores, dict):
        vals, notes = {k: 0.0 for k in EVAL_CRITERIA}, "Model response could not be parsed."
    else:
        def num(x):
            try:
                return min(10.0, max(0.0, float(x)))
            except (TypeError, ValueError):
                return 0.0
        vals, notes = {k: num(scores.get(k, 0)) for k in EVAL_CRITERIA}, str((parsed or {}).get("notes", ""))[:500]
    composite = sum(vals.values()) / len(vals)
    return {"scores": vals, "notes": notes, "composite": round(composite, 1), "retries": 0,
            "pass": vals["policy_compliance"] >= st.eval_policy_floor and composite >= st.eval_composite_pass}


def _handle_summary(rt, batch, metas, outputs):
    written = skipped = failed = 0
    cfg = _stage_config(batch)
    for rid, meta in metas.items():
        text = _text_of(outputs, rid)
        if not text:
            failed += 1
        elif rt.db.write_summary(batch["job_type"], meta, text, cfg):
            written += 1
        else:
            skipped += 1                                         # an earlier delivery already stored this exact result
    return {"written": written, "skipped": skipped, "failed": failed}


def _handle_recommendation(rt, batch, metas, outputs):
    nxt, failed = [], 0
    for rid, meta in metas.items():
        parsed = _json_of(outputs, rid) or {}
        label = parsed.get("option")
        opt = meta["option_map"].get(label) if isinstance(label, str) else None
        rationale = parsed.get("rationale")
        if not opt or not isinstance(rationale, str) or not rationale.strip():
            failed += 1                                          # unparsable, or it chose something that isn't on the menu: try again next run
            continue
        try:
            conf = min(1.0, max(0.0, float(parsed.get("confidence"))))
        except (TypeError, ValueError):
            conf = None
        rec = {"option": label, "action_id": opt["action_id"], "campaign": opt["name"], "category": opt["category"], "rationale": rationale.strip(),
               "execution_owner": "Direct Sales Rep" if meta["channel"] == "Direct" else "Dealer", "confidence": conf,
               "upsell": str(parsed.get("upsell") or "Not recommended for this account right now")[:500]}
        nxt.append(Rec({"context_json": pretty(meta["context"]), "options_json": pretty(meta["options"]), "recommendation_json": pretty(_public(rec)),
                        "prior_json": pretty(meta["prior"])},
                       {**meta, "state": {"recommendation": rec}, "config": {**meta["config"], "recommendation": _stage_config(batch)}}))
    chained = launch(rt, str(batch["run_id"]), NEXT_PASS["recommendation"], batch["id"], nxt)
    return {"written": 0, "skipped": 0, "failed": failed, "chained": chained}


def _draft_variables(rt, meta, rec):
    direct = meta["channel"] == "Direct"
    guidance = ('This is a "Direct" channel customer - the email is addressed directly to the fleet operator/customer contact. recipient_role should be "Customer".' if direct else
                'This is a "Dealer" channel customer - the sales rep does not have a direct relationship with the end customer. The email is addressed to the dealer '
                'contact, asking them to reach out to their end customer with this recommendation. recipient_role should be "Dealer".')
    return {"context_json": pretty(meta["context"]), "recommendation_json": pretty(_public(rec)), "recipient_guidance": guidance,
            "contact_email": rt.settings.contact_email_direct if direct else rt.settings.contact_email_dealer, "customer_name": meta["context"]["customer_name"],
            "feedback_guidance": "No recent customer feedback is on record for this contract - don't reference feedback that doesn't exist."}


def _handle_evaluation(rt, batch, metas, outputs):
    nxt, unparsed = [], 0
    for rid, meta in metas.items():
        parsed = _json_of(outputs, rid)
        unparsed += parsed is None
        ev = _score(parsed, rt.settings)                         # an unparsable evaluation scores 0 and so escalates to a human
        rec = meta["state"]["recommendation"]
        nxt.append(Rec(_draft_variables(rt, meta, rec),
                       {**meta, "state": {**meta["state"], "evaluation": ev}, "config": {**meta["config"], "evaluation": _stage_config(batch)}}))
    chained = launch(rt, str(batch["run_id"]), NEXT_PASS["evaluation"], batch["id"], nxt)
    return {"written": 0, "skipped": 0, "failed": unparsed, "chained": chained}


def _handle_draft(rt, batch, metas, outputs):
    written = skipped = failed = 0
    for rid, meta in metas.items():
        parsed = _json_of(outputs, rid) or {}
        draft = None
        if isinstance(parsed.get("email_subject"), str) and isinstance(parsed.get("email_body"), str):
            draft = {k: parsed.get(k) for k in ("summary", "recipient_role", "email_subject", "email_body")}
        else:
            failed += 1                                          # the recommendation is still worth storing without its draft
        rec, ev = meta["state"]["recommendation"], meta["state"]["evaluation"]
        row = {"config": {**meta["config"], "draft": _stage_config(batch)}, "rationale": rec["rationale"], "action_id": rec["action_id"], "action_name": rec["campaign"],
               "execution_owner": rec["execution_owner"], "upsell": rec["upsell"], "confidence": rec["confidence"], "evaluation": ev, "draft": draft}
        if rt.db.write_recommendation(meta, row):
            written += 1
        else:
            skipped += 1
    return {"written": written, "skipped": skipped, "failed": failed}


HANDLERS = {"contract_summary": _handle_summary, "web_claims_summary": _handle_summary, "account_summary": _handle_summary,
            "recommendation": _handle_recommendation, "evaluation": _handle_evaluation, "draft": _handle_draft}


def process_completion(rt, arn: str, status: str, message: str = "", worker: str = "") -> dict:
    """A Bedrock job (or a direct run) finished. Safe to call any number of times for the same job."""
    if status in bj.FAILED:
        return {"arn": arn, "marked_failed": bool(rt.db.batch_fail_arn(arn, f"{status}: {message}"))}
    if status not in bj.DONE:
        return {"arn": arn, "ignored": status}
    batch_id = rt.db.batch_claim(arn, worker or "completion-lambda")
    if batch_id is None:                                         # a duplicate event, or already written: exactly one invocation gets past this line
        return {"arn": arn, "duplicate": True}
    batch = rt.db.batch(batch_id)
    meta_key = batch["input_s3_uri"].split("/", 3)[3].rsplit("/", 1)[0] + "/meta.jsonl"
    try:
        metas = {m["rid"]: m for m in rt.store.read_jsonl(meta_key)}
        outputs = {o["recordId"]: o for o in rt.jobs.output_records(batch)}
    except FileNotFoundError as e:                               # deterministic: there is nothing to write
        rt.db.batch_fail_id(batch_id, str(e))
        return {"arn": arn, "batch": str(batch_id), "marked_failed": True, "error": str(e)}
    # Any other exception propagates and leaves the batch 'writing': the claim lease expires and the sweeper retries it,
    # which is safe because every write skips a result whose input_hash is already stored.
    counts = HANDLERS[batch["job_type"]](rt, batch, metas, outputs)
    rt.db.batch_finish(batch_id, len(outputs), counts["written"], counts["skipped"], counts["failed"])
    return {"arn": arn, "batch": str(batch_id), "job_type": batch["job_type"], **counts}


def sweep(rt, deadline: float) -> dict:
    """Run on a schedule. Finds batches whose completion event never arrived (or whose handler crashed) by asking Bedrock directly."""
    out: dict = {"processed": [], "marked_failed": [], "waiting": 0}
    for b in rt.db.open_batches():
        if rt.clock() > deadline:
            break
        if b["status"] == "created":
            if b["age_s"] >= rt.settings.stale_created_hours * 3600:
                rt.db.batch_fail_id(b["id"], "never submitted")
                out["marked_failed"].append(str(b["id"]))
            continue
        if b["status"] == "submitted" and b["age_s"] < 300:      # give the normal event a few minutes first
            out["waiting"] += 1
            continue
        status, message = rt.jobs.status(b["bedrock_job_arn"])
        if status in bj.DONE or status in bj.FAILED:
            out["processed"].append(process_completion(rt, b["bedrock_job_arn"], status, message, "sweeper"))
        else:
            out["waiting"] += 1
    return out


def report(rt, n: int = 20) -> list[dict]:
    return [{k: (str(v) if k in ("id", "run_id") else v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in rt.db.recent_batches(n)]
