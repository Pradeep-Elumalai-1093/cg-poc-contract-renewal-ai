/* =====================================================================
   Contract Risk Score — Deterministic Model (v3, Europe MVP)
   Source: REF_DB.ECARE_CTE_STG
   =====================================================================
   Design: same mechanics as before — a sum of independent, capped point
   factors, weights sum to exactly 100 at max. Every factor is tied to a
   confirmed real field, verified against actual sample data (not just
   metadata). Placeholder band thresholds are marked explicitly.

   CHANGES FROM v2 (real full-book score distribution, 106,106 contracts):
   The top 8 distinct score values covered 52% of the entire book — not a
   smooth distribution, a model collapsing into a handful of buckets.
   Traced back through the factor weights: every dominant spike (20, 33,
   12, 27) is built from warranty_status_points (12) + coverage_gap_points
   (8) alone, or those plus an equipment-age band. That means:
     - is_under_warranty is likely near-constant across a mature contract
       book (most contracts run 5-10 years, warranty periods are short) —
       it was consuming 12% of the model's weight to say the same thing
       about nearly everyone, not discriminating anything.
     - The claims-based factors (repeat/frequency/recency/written-off —
       57 of the 100 points) sit at 0 for a large share of the book,
       meaning many contracts show zero recorded claims in the join.
   FIX: shifted weight away from the two static/near-constant factors
   (warranty 12->6, coverage gap 8->6) into the two factors that reflect
   current behavior rather than a fixed attribute (recency 12->16,
   written-off ratio 10->14). New weights: 20+15+16+14+6+13+6+10=100.
   NOT YET CONFIRMED — pending three aggregate-only numbers: the real
   is_under_warranty split, the real SERVICEGEN split, and the % of
   contracts with zero total claims (the last one matters most: if that
   rate is surprisingly high, it may be a CONTRACTID join gap rather than
   a genuine clean-record rate).

   CHANGES FROM v1:
   - Factor 1 (repeat issue) is now magnitude-scaled, not a flat 3-tier
     band. Real sample data showed the same fault recurring up to 10
     times on one contract — a flat "3+" bucket would have scored a
     3-repeat and a 10-repeat identically, which real data shows is wrong.
   - Factor 8 (price increase) is NEW — INT_INVOICE confirms a real,
     computable price-change-over-time signal, resolving a gap this
     model previously treated as permanently absent.
   - Late payment / outstanding balance factors remain DROPPED. Checked
     INT_INVOICE directly: it has DATEDUE/AMOUNT (what was billed) but
     no paid/settled/balance field. Actual payment status likely lives
     in JDE, not eCare — this needs a separate access confirmation, not
     more eCare sampling.
   - Contact email fields removed from data model scope entirely (client
     confirmed outreach delivery is handled downstream, not by this app).

   STILL OPEN: claim-frequency band thresholds (Factor 2) are placeholders
   — run Step 0 below against the real book before trusting them.

   STILL OPEN: contract renewal creates a new CONTRACTID (confirmed via
   real sample: same CUSTOMERID moved from CONTRACTID 234276 to 362872).
   Claims/service jobs scoped by CONTRACTID means a renewed contract
   starts with a clean risk history — confirmed as intended behavior,
   not a bug, per client sign-off.
   ===================================================================== */


/* ---------------------------------------------------------------------
   STEP 0 (run first, not part of the score): check the real distribution
   of claims-per-month-of-duration across the European book, to replace
   the placeholder bands in Factor 2 below with actual percentiles rather
   than guessed cutoffs.
   --------------------------------------------------------------------- */
-- SELECT
--     APPROX_PERCENTILE(claims_per_month, 0.5)  AS p50,
--     APPROX_PERCENTILE(claims_per_month, 0.75) AS p75,
--     APPROX_PERCENTILE(claims_per_month, 0.90) AS p90
-- FROM ( ... claims_per_contract CTE below, with claims_per_month computed ... );


/* ---------------------------------------------------------------------
   MAIN QUERY: one row per active contract, with a points breakdown per
   factor (mirrors the app's existing risk-driver breakdown pattern), a
   total RISK_SCORE, and ANNUAL_CONTRACT_VALUE as informational context
   (not part of the score itself — this is the real-value fix from
   INT_INVOICE, used elsewhere for margin/segmentation, not risk).
   --------------------------------------------------------------------- */

WITH contract_base AS (
    SELECT
        c.CONTRACTID,
        c.CUSTOMERID,
        c.UNITID,
        c.STARTDATE,
        c.DURATION,                      -- months
        c.SERVICEGEN,                    -- general-service coverage flag
        c.ISCURRENT
    FROM REF_DB.ECARE_CTE_STG.INT_CONTRACT c
    WHERE c.ISCURRENT = TRUE
      AND c.DURATION > 0                 -- avoid divide-by-zero in Factor 2
),

-- Claim-level signals, from INT_WEBCLAIM only.
claims_per_contract AS (
    SELECT
        wc.CONTRACTID,
        COUNT(*)                                                          AS total_claims,
        MAX(wc.CLAIMDATE)                                                 AS most_recent_claim_date,
        SUM(CASE WHEN wc.CLAIMDATE >= DATEADD(day, -90, CURRENT_DATE())
                 THEN 1 ELSE 0 END)                                       AS claims_last_90d
    FROM REF_DB.ECARE_CTE_STG.INT_WEBCLAIM wc
    WHERE wc.CONTRACTID IS NOT NULL
    GROUP BY wc.CONTRACTID
),

-- Repeat-fault signal: does the SAME fault recur on this contract, and
-- how many times? (magnitude, not just a yes/no flag)
repeat_fault_check AS (
    SELECT
        CONTRACTID,
        MAX(fault_count) AS max_repeat_count
    FROM (
        SELECT CONTRACTID, FAULTID, COUNT(*) AS fault_count
        FROM REF_DB.ECARE_CTE_STG.INT_WEBCLAIM
        WHERE CONTRACTID IS NOT NULL AND FAULTID IS NOT NULL
        GROUP BY CONTRACTID, FAULTID
    )
    GROUP BY CONTRACTID
),

-- Service-job signals, from INT_Service only (JOBWRITTENOFF lives here,
-- not on the claim table).
service_per_contract AS (
    SELECT
        s.CONTRACTID,
        COUNT(*)                                                          AS total_service_jobs,
        SUM(CASE WHEN s.JOBWRITTENOFF = TRUE THEN 1 ELSE 0 END)           AS written_off_jobs
    FROM REF_DB.ECARE_CTE_STG.INT_Service s
    WHERE s.CONTRACTID IS NOT NULL
    GROUP BY s.CONTRACTID
),

-- Equipment age, from INT_UNIT.
equipment AS (
    SELECT
        u.UNITID,
        u.MANUFACTUREDATE,
        DATEDIFF(year, u.MANUFACTUREDATE, CURRENT_DATE()) AS equipment_age_years
    FROM REF_DB.ECARE_CTE_STG.INT_UNIT u
),

-- Warranty status as of today, from INT_WARRANTY + INT_WARRANTYADN.
warranty_status AS (
    SELECT
        w.UNITID,
        MAX(CASE WHEN CURRENT_DATE() BETWEEN wa.STARTDATE AND wa.ENDDATE
                 THEN 1 ELSE 0 END) AS is_under_warranty
    FROM REF_DB.ECARE_CTE_STG.INT_WARRANTY w
    LEFT JOIN REF_DB.ECARE_CTE_STG.INT_WARRANTYADN wa
           ON wa.WARRANTYID = w.WARRANTYID
    GROUP BY w.UNITID
),

-- NEW: price trend from INT_INVOICE. First vs. latest billed installment
-- amount for this contract, by due date. Also gives us the real annual
-- contract value (informational output, not a risk factor).
invoice_endpoints AS (
    SELECT
        CONTRACTID,
        AMOUNT,
        DATEDUE,
        ROW_NUMBER() OVER (PARTITION BY CONTRACTID ORDER BY DATEDUE ASC)  AS rn_first,
        ROW_NUMBER() OVER (PARTITION BY CONTRACTID ORDER BY DATEDUE DESC) AS rn_last
    FROM REF_DB.ECARE_CTE_STG.INT_INVOICE
),
price_trend AS (
    SELECT
        f.CONTRACTID,
        f.AMOUNT AS first_amount,
        l.AMOUNT AS latest_amount,
        CASE WHEN f.AMOUNT > 0 THEN (l.AMOUNT - f.AMOUNT) / f.AMOUNT::FLOAT ELSE NULL END AS price_increase_pct
    FROM invoice_endpoints f
    JOIN invoice_endpoints l ON l.CONTRACTID = f.CONTRACTID AND l.rn_last = 1
    WHERE f.rn_first = 1
),
annual_value AS (
    -- Real billed value, replacing the ambiguous point-in-time CONTRACT_PRICE.
    -- Assumes monthly installments; adjust the *12 multiplier if a contract's
    -- interval isn't monthly (join CONTRACT_INTERVAL_DESC to confirm per-row).
    SELECT
        CONTRACTID,
        AVG(AMOUNT) * 12 AS annual_contract_value
    FROM REF_DB.ECARE_CTE_STG.INT_INVOICE
    WHERE DATEDUE >= DATEADD(month, -12, CURRENT_DATE())
    GROUP BY CONTRACTID
)

SELECT
    cb.CONTRACTID,
    cb.CUSTOMERID,
    cb.UNITID,
    av.annual_contract_value AS ANNUAL_CONTRACT_VALUE,  -- informational, not part of the score

    -- ============================================================
    -- Factor 1: Repeat issue signal (max 20 pts) — MAGNITUDE-SCALED
    -- Real sample data showed the same fault recurring up to 10 times
    -- on one contract; a flat "3+" bucket can't distinguish that from
    -- a single extra repeat. Scales linearly, capped at 20.
    -- ============================================================
    LEAST(20, GREATEST(0, (COALESCE(rf.max_repeat_count, 1) - 1) * 4)) AS repeat_issue_points,

    -- ============================================================
    -- Factor 2: Claim frequency, normalized by contract duration (max 15 pts)
    -- PLACEHOLDER BANDS — replace with real percentiles from Step 0.
    -- ============================================================
    CASE
        WHEN (COALESCE(cpc.total_claims, 0) / cb.DURATION::FLOAT) >= 0.6 THEN 15
        WHEN (COALESCE(cpc.total_claims, 0) / cb.DURATION::FLOAT) >= 0.3 THEN 9
        WHEN (COALESCE(cpc.total_claims, 0) / cb.DURATION::FLOAT) >= 0.1 THEN 4
        ELSE 0
    END AS claim_frequency_points,

    -- ============================================================
    -- Factor 3: Claim recency (max 16 pts) — weight increased from 12,
    -- see header note: shifted from the near-constant warranty factor.
    -- ============================================================
    CASE
        WHEN COALESCE(cpc.claims_last_90d, 0) >= 2 THEN 16
        WHEN COALESCE(cpc.claims_last_90d, 0) = 1  THEN 8
        ELSE 0
    END AS claim_recency_points,

    -- ============================================================
    -- Factor 4: Written-off job ratio (max 14 pts) — weight increased
    -- from 10, see header note: shifted from the near-constant coverage
    -- gap factor. ASSUMPTION STILL FLAGGED: treats a written-off job as
    -- unresolved, not neutral — business meaning of JOBWRITTENOFF is
    -- still unconfirmed.
    -- ============================================================
    CASE
        WHEN COALESCE(spc.total_service_jobs, 0) = 0 THEN 0
        WHEN (spc.written_off_jobs / spc.total_service_jobs::FLOAT) > 0.25 THEN 14
        WHEN (spc.written_off_jobs / spc.total_service_jobs::FLOAT) > 0    THEN 7
        ELSE 0
    END AS written_off_ratio_points,

    -- ============================================================
    -- Factor 5: Coverage gap (max 6 pts) — weight REDUCED from 8. This
    -- was one of the two factors identified as likely near-constant
    -- across the book (see header note) — pending confirmation via the
    -- real SERVICEGEN split.
    -- ============================================================
    CASE WHEN cb.SERVICEGEN = FALSE THEN 6 ELSE 0 END AS coverage_gap_points,

    -- ============================================================
    -- Factor 6: Equipment age (max 13 pts)
    -- PLACEHOLDER BANDS.
    -- ============================================================
    CASE
        WHEN eq.equipment_age_years >= 8 THEN 13
        WHEN eq.equipment_age_years >= 4 THEN 7
        ELSE 0
    END AS equipment_age_points,

    -- ============================================================
    -- Factor 7: Warranty status (max 6 pts) — weight REDUCED from 12.
    -- This was the single biggest driver of the score-clustering problem
    -- (see header note): being out of warranty is likely near-universal
    -- across a mature contract book, so this factor was consuming 12% of
    -- the model's weight to say the same thing about nearly everyone.
    -- No warranty record at all is still treated the same as "not under
    -- warranty" (a separate flagged assumption, collapses a real
    -- distinction between "expired" and "never had one on record").
    -- ============================================================
    CASE WHEN COALESCE(ws.is_under_warranty, 0) = 0 THEN 6 ELSE 0 END AS warranty_status_points,

    -- ============================================================
    -- Factor 8: Price increase magnitude (max 10 pts) — NEW
    -- DIRECTION FLAGGED AS AN ASSUMPTION, NOT A CONFIRMED BUSINESS RULE:
    -- scores a LARGER recent price increase as MORE risk (sticker-shock
    -- churn risk), the more standard retention-risk interpretation. This
    -- is a genuine judgment call, not read off any confirmed source —
    -- confirm the direction with the business before trusting it.
    -- ============================================================
    CASE
        WHEN pt.price_increase_pct IS NULL THEN 0
        WHEN pt.price_increase_pct > 0.10  THEN 10
        WHEN pt.price_increase_pct > 0.05  THEN 6
        WHEN pt.price_increase_pct > 0     THEN 3
        ELSE 0
    END AS price_increase_points,

    -- ============================================================
    -- TOTAL: sum of the 8 factors above, weights sum to 100 at max
    -- (20+15+16+14+6+13+6+10 = 100) — rebalanced from v2's
    -- 20+15+12+10+8+13+12+10 to shift weight off the two near-constant
    -- factors (warranty, coverage gap) onto the two that reflect current
    -- behavior (recency, written-off ratio). See header note.
    -- ============================================================
    LEAST(100, GREATEST(0,
        LEAST(20, GREATEST(0, (COALESCE(rf.max_repeat_count, 1) - 1) * 4))
        + (CASE WHEN (COALESCE(cpc.total_claims, 0) / cb.DURATION::FLOAT) >= 0.6 THEN 15
                WHEN (COALESCE(cpc.total_claims, 0) / cb.DURATION::FLOAT) >= 0.3 THEN 9
                WHEN (COALESCE(cpc.total_claims, 0) / cb.DURATION::FLOAT) >= 0.1 THEN 4 ELSE 0 END)
        + (CASE WHEN COALESCE(cpc.claims_last_90d, 0) >= 2 THEN 16
                WHEN COALESCE(cpc.claims_last_90d, 0) = 1  THEN 8 ELSE 0 END)
        + (CASE WHEN COALESCE(spc.total_service_jobs, 0) = 0 THEN 0
                WHEN (spc.written_off_jobs / spc.total_service_jobs::FLOAT) > 0.25 THEN 14
                WHEN (spc.written_off_jobs / spc.total_service_jobs::FLOAT) > 0    THEN 7 ELSE 0 END)
        + (CASE WHEN cb.SERVICEGEN = FALSE THEN 6 ELSE 0 END)
        + (CASE WHEN eq.equipment_age_years >= 8 THEN 13
                WHEN eq.equipment_age_years >= 4 THEN 7 ELSE 0 END)
        + (CASE WHEN COALESCE(ws.is_under_warranty, 0) = 0 THEN 6 ELSE 0 END)
        + (CASE WHEN pt.price_increase_pct IS NULL THEN 0
                WHEN pt.price_increase_pct > 0.10 THEN 10
                WHEN pt.price_increase_pct > 0.05 THEN 6
                WHEN pt.price_increase_pct > 0    THEN 3 ELSE 0 END)
    )) AS RISK_SCORE

FROM contract_base cb
LEFT JOIN claims_per_contract  cpc ON cpc.CONTRACTID = cb.CONTRACTID
LEFT JOIN repeat_fault_check   rf  ON rf.CONTRACTID  = cb.CONTRACTID
LEFT JOIN service_per_contract spc ON spc.CONTRACTID = cb.CONTRACTID
LEFT JOIN equipment             eq ON eq.UNITID      = cb.UNITID
LEFT JOIN warranty_status       ws ON ws.UNITID      = cb.UNITID
LEFT JOIN price_trend           pt ON pt.CONTRACTID  = cb.CONTRACTID
LEFT JOIN annual_value          av ON av.CONTRACTID  = cb.CONTRACTID
ORDER BY RISK_SCORE DESC;