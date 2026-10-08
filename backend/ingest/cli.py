"""
python -m ingest --source local --contracts data/contract.csv --claims data/claims.csv
python -m ingest --source snowflake
python -m ingest --source local --contracts data/contract.csv --skip-claims --dry-run

Reads the contract data product (and claims), applies the load rules, and replaces the live
data in PostgreSQL atomically. Exit code: 0 ok, 1 failed, 2 refused (extract looks wrong), 3 loaded but rule matches not recomputed.
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timezone


def _print_report(rep: dict, claims_rep: dict | None) -> None:
    n = lambda x: f"{x:,}"  # noqa: E731
    print(f"\nContracts read: {n(rep['rows_read'])}   kept: {n(rep['contracts'])}   live (in scope): {n(rep['in_scope'])}   not live: {n(rep['out_of_scope'])}")
    if rep.get("duplicate_contractid_dropped") or rep.get("dropped_no_contractid"):
        print(f"  dropped: {rep['duplicate_contractid_dropped']} duplicate id, {rep['dropped_no_contractid']} without an id")
    if rep["segments"]:
        print("Segments (live):  " + ", ".join(f"{k} {n(v)}" for k, v in rep["segments"].items()))
    if rep["buckets"]:
        print("Expiry (live):    " + ", ".join(f"{k} {n(v)}" for k, v in rep["buckets"].items()))
    print("Per CTX:")
    for s in rep["ctx"]:
        med = "n/a" if s["median_value"] is None else f"{s['median_value']:,.2f}"
        how = "all-CTX median (too few valued contracts)" if s["used_global_median"] else "own median"
        print(f"  {s['ctx']}: {n(s['contracts'])} contracts, {n(s['in_scope'])} live, median {med} from {n(s['valued_contracts'])} valued - {how}")
    if rep["unmapped_equipment_categories"]:
        print("Equipment categories with no crosswalk entry (loaded as 'Unclassified'): " +
              ", ".join(f"{k} ({n(v)})" for k, v in rep["unmapped_equipment_categories"].items()))
    if rep["ignored_columns"]:
        print(f"Columns in the extract that the catalog doesn't know (ignored): {', '.join(rep['ignored_columns'])}")
    if rep["missing_optional_columns"]:
        print(f"Catalog columns not in the extract (loaded empty): {', '.join(rep['missing_optional_columns'])}")
    if claims_rep:
        print(f"Claims: {n(claims_rep['claims'])} kept of {n(claims_rep['rows_read'])} read")
    for w in rep["warnings"] + (claims_rep or {}).get("warnings", []):
        print(f"  ! {w}")


def main(argv=None) -> int:
    from dotenv import load_dotenv
    load_dotenv()  # before the rules modules read their settings

    p = argparse.ArgumentParser(prog="python -m ingest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=["local", "snowflake"], required=True)
    p.add_argument("--contracts", help="local: the contract extract (.csv/.xlsx); default INGEST_CONTRACTS_FILE")
    p.add_argument("--claims", help="local: the claims extract (.csv/.xlsx); default INGEST_CLAIMS_FILE")
    p.add_argument("--skip-claims", action="store_true", help="leave the live claims untouched")
    p.add_argument("--dry-run", action="store_true", help="read, validate and report; change nothing")
    p.add_argument("--force", action="store_true", help="load even if live contracts dropped by more than INGEST_MAX_DROP_PERCENT")
    p.add_argument("--rebuild-schema", action="store_true", help="drop and recreate the contract/claim tables (needed if a column's type changed)")
    p.add_argument("--as-of", help="YYYY-MM-DD: compute expiry buckets as of this date (default: today, UTC)")
    args = p.parse_args(argv)

    from . import IngestError, sources, transform
    from .load import IngestAborted

    try:
        today = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
        t0 = time.perf_counter()
        if args.source == "local":
            cpath = args.contracts or os.environ.get("INGEST_CONTRACTS_FILE")
            kpath = args.claims or os.environ.get("INGEST_CLAIMS_FILE")
            if not cpath:
                raise IngestError("Give the contract extract with --contracts (or set INGEST_CONTRACTS_FILE).")
            if not kpath and not args.skip_claims:
                raise IngestError("Give the claims extract with --claims, or pass --skip-claims.")
            raw_contracts = sources.read_file(cpath)
            raw_claims = None if args.skip_claims else sources.read_file(kpath)
        else:
            ctable, ktable = os.environ.get("SNOWFLAKE_CONTRACT_TABLE"), os.environ.get("SNOWFLAKE_CLAIMS_TABLE")
            if not ctable:
                raise IngestError("Set SNOWFLAKE_CONTRACT_TABLE (e.g. DB.SCHEMA.CONTRACT).")
            if not ktable and not args.skip_claims:
                raise IngestError("Set SNOWFLAKE_CLAIMS_TABLE, or pass --skip-claims.")
            raw_contracts = sources.read_snowflake(ctable)
            raw_claims = None if args.skip_claims else sources.read_snowflake(ktable)
        read_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        contracts, rep = transform.prepare_contracts(raw_contracts, today)
        del raw_contracts
        claims, claims_rep = (None, None)
        if raw_claims is not None:
            claims, claims_rep = transform.prepare_claims(raw_claims, set(contracts["contractid"]))
            del raw_claims
        _print_report(rep, claims_rep)
        print(f"\nRead {read_s:.1f}s, prepared {time.perf_counter() - t0:.1f}s")
        if args.dry_run:
            print("Dry run: nothing was written.")
            return 0

        from . import load
        result = load.run(contracts, rep, claims, claims_rep, source=args.source, rebuild=args.rebuild_schema, force=args.force)
        for note in result["schema_notes"]:
            print(f"  schema: {note}")
        print(f"\nDone. Data version {result['data_version']} is live: {result['contracts']:,} contracts "
              f"({result['in_scope']:,} live)" + (f", {result['claims']:,} claims" if result["claims"] is not None else ", claims unchanged") +
              f". Timings: {result['timings']}")
        # The contracts were just replaced, so what each exclusion set and retention action matches is stale.
        # The data is already live; if this step fails the pipeline must not trust the old matches, so say so loudly.
        try:
            import db
            import matching
            with db.SessionLocal() as session:
                done = matching.recompute_all(session)
                session.commit()
            print(f"Recomputed the matches of {done['exclusion_sets']} exclusion set(s) and {done['actions']} retention action(s).")
        except Exception as e:  # noqa: BLE001
            print(f"\nWARNING: the data is live, but exclusion and retention-action matches could NOT be recomputed: {e}\n"
                  "Re-run the load, or fix the rule that fails, before the AI pipeline runs.", file=sys.stderr)
            return 3
        return 0
    except IngestAborted as e:
        print(f"\nREFUSED: {e}", file=sys.stderr)
        return 2
    except IngestError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1
