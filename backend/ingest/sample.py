"""
Writes a synthetic extract shaped like the Contract data product (all 102 columns) plus a
claims extract, so the whole stack can run without Snowflake - and so ingest speed can be
measured at the real size:

    python -m ingest.sample --rows 5000 --out data
    python -m ingest.sample --rows 300000 --out /tmp/bench

Everything is fake and deliberately obvious (Teststrasse, +00 phone numbers). The risk points
follow the same bands as the Snowflake risk SQL, and RISK_SCORE is their sum, so the data is
internally consistent. About a quarter of contracts are historical (not live / not the latest
version), some carry a CONTRACTID_OLD link, and each account sits in exactly one CTX.
"""
import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .catalog import load_catalog, source_columns

CTX_CODES = np.array(["034", "049", "043", "045", "046"])
CURRENCY = {"034": "EUR", "049": "EUR", "043": "EUR", "045": "DKK", "046": "SEK"}
FAULTS = {701: "Electrical fault", 702: "Control unit", 707: "Compressor", 712: "Refrigerant leak", 714: "Fan motor",
          716: "Other (see comments)", 719: "Service", 720: "Improvement Prg", 721: "Fuel leak", 723: "Battery flat"}


def generate(n: int, seed: int = 7, today: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    today = today or date.today()
    t0 = np.datetime64(today)
    day = lambda offsets: t0 + np.asarray(offsets).astype("timedelta64[D]")  # noqa: E731
    idx = np.arange(n)

    n_acc = max(8, n // 8)
    acc_ctx = rng.choice(CTX_CODES, n_acc, p=[.3, .3, .15, .15, .1])
    acc = rng.integers(0, n_acc, n)            # one CTX per account, as in the real data
    ctx = acc_ctx[acc]
    cust = acc * 3 + rng.integers(0, 3, n)

    live = rng.random(n) < 0.72
    latest = rng.random(n) < 0.93
    end_off = np.where(live, rng.integers(-15, 720, n), rng.integers(-900, -3, n))
    dur = rng.choice([12, 24, 36, 48, 60, 72, 84], n)
    end, start = day(end_off), day(end_off - dur * 30)
    manuf = day(end_off - dur * 30 - rng.integers(0, 365, n))

    # ~12% "problem" contracts (many, recent, repeating claims) so the book has a realistic
    # at-risk tail; the rest are quiet. On the real book roughly 13% scored 50 or more.
    problem = rng.random(n) < 0.12
    total_claims = rng.poisson(np.where(problem, 9.0, 1.8))
    last90 = np.minimum(total_claims, rng.poisson(np.where(problem, 1.8, 0.25)))
    repeat = np.where(total_claims > 0, np.minimum(total_claims, 1 + rng.poisson(np.where(problem, 4.0, 0.6))), 0)
    jobs = total_claims + rng.poisson(1.0, n)
    written = np.minimum(jobs, rng.binomial(1, 0.15, n) * rng.integers(1, 3, n))
    general = rng.random(n) < 0.55
    age = (np.datetime64(today) - manuf).astype(int) / 365
    pct = np.where(rng.random(n) < 0.3, rng.uniform(0.02, 0.15, n), 0.0)

    pts = {
        "REPEAT_ISSUE_POINTS": np.clip((repeat - 1) * 4, 0, 20),
        "CLAIM_FREQUENCY_POINTS": np.select([total_claims / dur >= .6, total_claims / dur >= .3, total_claims / dur >= .1], [15, 9, 4], 0),
        "CLAIM_RECENCY_POINTS": np.select([last90 >= 2, last90 == 1], [16, 8], 0),
        "WRITTEN_OFF_RATIO_POINTS": np.select([written / np.maximum(jobs, 1) > .25, written > 0], [14, 7], 0),
        "COVERAGE_GAP_POINTS": np.where(general, 0, 6),
        "EQUIPMENT_AGE_POINTS": np.select([age >= 8, age >= 4], [13, 7], 0),
        "WARRANTY_STATUS_POINTS": np.where(rng.random(n) < .9, 6, 0),
        "PRICE_INCREASE_POINTS": np.select([pct > .10, pct > .05, pct > 0], [10, 6, 3], 0),
    }
    price = np.round(rng.lognormal(5.0, 0.6, n), 2)
    annual = np.where(rng.random(n) < 0.92, np.round(price * 12), np.nan)

    old = np.where(rng.random(n) < 0.15, 100000 + rng.integers(0, n, n), 0)
    towns = np.array(["Padborg", "Hamburg", "Wien", "Madrid", "Lyon", "Uppsala", "Oslo"])
    cat = rng.choice(["LCVMT", "IND", "POWER GENERATION UNIT", "TRAILER"], n, p=[.45, .25, .2, .1])
    values = {
        "CONTRACTID": 100000 + idx, "CONTRACTID_OLD": old, "JDECONTRACTNO": 2800000 + idx,
        "CONTRACTREFNO": np.char.add("Q-", (20000 + idx).astype(str)), "UNITID": np.char.add("PC", (700000 + idx).astype(str)),
        "CUSTOMERID": cust, "ACCOUNTID": 10000000 + acc, "CTXID": ctx, "CONTRACT_STATUS": np.where(live, "Live", "Inactive"),
        "IS_ACTIVE_CONTRACT_VERSION": latest, "IS_ACTIVE_CONTRACT": live, "ISCURRENT": latest,
        "CONTRACT_START_DATE": start, "STARTDATE": start, "CONTRACT_ORIGINAL_START_DATE": start,
        "CONTRACT_END_DATE": np.where(rng.random(n) < .45, end, np.datetime64("NaT", "D")), "CONTRACT_ORIGINAL_END_DATE": end,
        "CONTRACT_DURATION_MONTHS": dur, "DURATION": dur, "SERVICEGEN": general, "IS_GENERAL_SERVICE_INCLUDE": general,
        "IS_DIRECT_CONTRACT": rng.random(n) < .35, "CONTRACT_INTERVAL_DESC": "Monthly", "CONTRACT_PRICE": price,
        "ANNUAL_CONTRACT_VALUE": annual, "PRICE_INCREASE_PCT": np.round(pct, 3), "CURRENCY": np.array([CURRENCY[c] for c in ctx]),
        "COMPANY": np.char.add(np.char.add("Fleet ", cust.astype(str)), " Transport"), "ACCOUNT": np.char.add("Account ", acc.astype(str)),
        "COUNTRYID": np.char.upper(np.array(["DK", "DE", "AT", "ES", "FR"])[rng.integers(0, 5, n)]),
        "ADDRESS": np.char.add("Teststrasse ", rng.integers(1, 200, n).astype(str)), "TOWN": towns[rng.integers(0, len(towns), n)],
        "COUNTY": ".", "POSTCODE": rng.integers(10000, 99999, n).astype(str), "PHONEOPEN": "+00 000 000000", "PHONECLOSED": "+00 000 000001",
        "MODEL_CATEGORY": cat, "MODEL_TYPE": "TYPE-" + (idx % 4).astype(str), "MODELGROUP": "GROUP-" + (idx % 6).astype(str),
        "MODEL_NAME": "Model " + (idx % 40).astype(str), "MODELID": (idx % 40) + 5000, "MANUFACTUREDATE": manuf,
        "TOTAL_CLAIMS": total_claims, "CLAIMS_LAST_90D": last90, "REPEAT_FAULTS": repeat, "WRITTEN_OFF_JOBS": written,
        "TOTAL_SERVICE_JOBS": jobs, "RISK_SCORE": np.clip(sum(pts.values()), 0, 100), **pts,
    }
    out = {}
    for c in source_columns(load_catalog()):
        key = c.source.split("__")[0]
        if c.source in values:
            out[c.source] = values[c.source]
        elif key in values and "__" not in c.source:
            out[c.source] = values[key]
        elif c.kind == "bool":
            out[c.source] = rng.random(n) < .5
        elif c.kind == "date":
            out[c.source] = day(-rng.integers(0, 3000, n))
        elif c.kind == "int":
            out[c.source] = rng.integers(0, 10, n)
        elif c.kind == "number":
            out[c.source] = np.round(rng.uniform(0, 1000, n), 2)
        else:
            out[c.source] = f"{c.source[:14]}-" + (idx % 7).astype(str)
    contracts = pd.DataFrame(out)
    # Like the current data product, the file carries ISCURRENT twice (a real repeated header).
    contracts.columns = [c.replace("__2", "") for c in contracts.columns]

    m = int(total_claims.sum())
    owner = np.repeat(idx, total_claims)
    codes = rng.choice(list(FAULTS), m)
    when = pd.Timestamp(today) - pd.to_timedelta(rng.integers(0, 1000, m), unit="D") + pd.to_timedelta(rng.integers(0, 86400, m), unit="s")
    claims = pd.DataFrame({
        "CONTRACTID": 100000 + owner, "CLAIMNO": 3000000 + np.arange(m), "CLAIMDATE": when,
        "FAULTID": codes, "FAULT_DESCRIPTION": [f"{c} - {FAULTS[c]}" for c in codes], "CAUSE_DESCRIPTION": np.nan,
        "JOB_TYPE_DESCRIPTION": rng.choice(["Scheduled", "Unscheduled", "Recharge", "Improvement Prg"], m),
        "STAUTS_DESCRIPTION": rng.choice(["History (a)", "Submitted"], m, p=[.95, .05]),  # (sic) - the source spells it STAUTS
        "JOBSTART": when, "JOBEND": when + pd.to_timedelta(rng.integers(1, 48, m), unit="h"),
        "RUNNINGHOURS": np.round(rng.uniform(100, 15000, m), 1),
    })
    return contracts, claims


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m ingest.sample", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rows", type=int, default=5000)
    p.add_argument("--out", default="data")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    contracts, claims = generate(args.rows, args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    contracts.to_csv(out / "contract.csv", index=False, date_format="%Y-%m-%d")
    claims.to_csv(out / "claims.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
    print(f"Wrote {len(contracts):,} contracts ({contracts.shape[1]} columns) and {len(claims):,} claims to {out}/")
    print(f"Load them with:  python -m ingest --source local --contracts {out}/contract.csv --claims {out}/claims.csv")


if __name__ == "__main__":
    main()
