/* =====================================================================
   Contract Risk Score + Raw Claim/Pricing Metrics — Unified (v4, Europe MVP)
   Source: REF_DB.ECARE_CTE_STG
   =====================================================================
   Combines contract_risk_score_v1.sql (v3) and
   contract_claim_pricing_metrics.sql into one query, per request. Each
   underlying CTE (claims, repeat-fault, service jobs, invoice/price
   trend) is now defined exactly ONCE and feeds both the weighted risk
   score and the raw metric columns - the two source files each defined
   these independently, which is why combining them by concatenation
   produced duplicate CTE names (a hard compile error in Snowflake, not
   just untidy) and a stray trailing comma before the final FROM.

   Also fixed: the final SELECT was reading from
   REF_DB.ECARE_CTE_STG.TEMP_CONTRACT - a different, unfiltered table
   than contract_base (built from INT_CONTRACT with ISCURRENT = TRUE and
   DURATION > 0 already applied). That looked like a stray reference left
   over from merging the two files rather than an intentional swap -
   confirm if TEMP_CONTRACT was actually meant on purpose; this version
   selects from contract_base, matching both source files' original intent.

   All the design notes from v3 still apply and are condensed below;
   see contract_risk_score_v1.sql's full history if you need the complete
   reasoning trail (score-clustering diagnosis, the three still-pending
   aggregate confirmations, etc).

   STILL OPEN:
   - Claim-frequency band thresholds (Factor 2) are placeholders - run
     Step 0 below against the real book before trusting them.
   - Written-off ratio direction (Factor 4) and warranty-record-missing
     handling (Factor 7) are flagged assumptions, not confirmed rules.
   - Price-increase direction (Factor 8) is a judgment call, not read
     off any confirmed source.
   - REPEAT_FAULTS is the highest recurrence count of any single fault
     (matches Factor 1's basis) - not "total claims that were a repeat,"
     which is a different number. See the alternate calc commented below
     if you actually meant the latter.
   ===================================================================== */


/* ---------------------------------------------------------------------
   STEP 0 (run first, not part of the score): check the real distribution
   of claims-per-month-of-duration across the European book, to replace
   the placeholder bands in Factor 2 below with actual percentiles.
   --------------------------------------------------------------------- */
-- SELECT
--     APPROX_PERCENTILE(claims_per_month, 0.5)  AS p50,
--     APPROX_PERCENTILE(claims_per_month, 0.75) AS p75,
--     APPROX_PERCENTILE(claims_per_month, 0.90) AS p90
-- FROM ( ... claims_per_contract CTE below, with claims_per_month computed ... );


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

-- Claim-level signals, from INT_WEBCLAIM only. Defined once - feeds both
-- the risk score (Factors 2 and 3) and the raw TOTAL_CLAIMS/
-- CLAIMS_LAST_90D output columns.
claims_per_contract AS (
    SELECT
        wc.CONTRACTID,
        COUNT(*)                                                          AS TOTAL_CLAIMS,
        SUM(CASE WHEN wc.CLAIMDATE >= DATEADD(day, -90, CURRENT_DATE())
                 THEN 1 ELSE 0 END)                                       AS CLAIMS_LAST_90D
    FROM REF_DB.ECARE_CTE_STG.INT_WEBCLAIM wc
    WHERE wc.CONTRACTID IS NOT NULL
    GROUP BY wc.CONTRACTID
),

-- Repeat-fault signal: does the SAME fault recur on this contract, and
-- how many times? Feeds Factor 1 and the raw REPEAT_FAULTS column.
repeat_fault_check AS (
    SELECT
        CONTRACTID,
        MAX(fault_count) AS REPEAT_FAULTS
        -- ALTERNATE interpretation - total claims that were a repeat of an
        -- earlier fault, not the single highest recurrence count:
        -- SUM(fault_count) - COUNT(*) AS REPEAT_FAULTS
    FROM (
        SELECT CONTRACTID, FAULTID, COUNT(*) AS fault_count
        FROM REF_DB.ECARE_CTE_STG.INT_WEBCLAIM
        WHERE CONTRACTID IS NOT NULL AND FAULTID IS NOT NULL
        GROUP BY CONTRACTID, FAULTID
    )
    GROUP BY CONTRACTID
),

-- Service-job signals, from INT_Service only (JOBWRITTENOFF lives here,
-- not on the claim table). Feeds Factor 4 and the raw
-- WRITTEN_OFF_JOBS/TOTAL_SERVICE_JOBS columns.
service_per_contract AS (
    SELECT
        s.CONTRACTID,
        COUNT(*)                                                AS TOTAL_SERVICE_JOBS,
        SUM(CASE WHEN s.JOBWRITTENOFF = TRUE THEN 1 ELSE 0 END) AS WRITTEN_OFF_JOBS
    FROM REF_DB.ECARE_CTE_STG.INT_Service s
    WHERE s.CONTRACTID IS NOT NULL
    GROUP BY s.CONTRACTID
),

-- Equipment age, from INT_UNIT. Feeds Factor 6 only (no raw-metric
-- equivalent was requested).
equipment AS (
    SELECT
        u.UNITID,
        u.MANUFACTUREDATE,
        DATEDIFF(year, u.MANUFACTUREDATE, CURRENT_DATE()) AS equipment_age_years
    FROM REF_DB.ECARE_CTE_STG.INT_UNIT u
),

-- Warranty status as of today, from INT_WARRANTY + INT_WARRANTYADN.
-- Feeds Factor 7 only.
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

-- Price trend from INT_INVOICE: first vs. latest billed installment
-- amount for this contract, by due date. Feeds Factor 8 and the raw
-- PRICE_INCREASE_PCT column.
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
        CASE WHEN f.AMOUNT > 0 THEN (l.AMOUNT - f.AMOUNT) / f.AMOUNT::FLOAT ELSE NULL END AS PRICE_INCREASE_PCT
    FROM invoice_endpoints f
    JOIN invoice_endpoints l ON l.CONTRACTID = f.CONTRACTID AND l.rn_last = 1
    WHERE f.rn_first = 1
),

-- Real billed annual value, replacing the ambiguous point-in-time
-- CONTRACT_PRICE. Informational output, not a risk factor. Assumes
-- monthly installments; adjust the *12 multiplier if a contract's
-- interval isn't monthly (join CONTRACT_INTERVAL_DESC to confirm per-row).
annual_value AS (
    SELECT
        CONTRACTID,
        AVG(AMOUNT) * 12 AS ANNUAL_CONTRACT_VALUE
    FROM REF_DB.ECARE_CTE_STG.INT_INVOICE
    WHERE DATEDUE >= DATEADD(month, -12, CURRENT_DATE())
    GROUP BY CONTRACTID
)

SELECT
    cb.CONTRACTID,
    cb.CUSTOMERID,
    cb.UNITID,

    -- Raw metrics (the 5 requested columns, plus TOTAL_SERVICE_JOBS as
    -- supporting context for the written-off ratio if needed downstream)
    COALESCE(cpc.TOTAL_CLAIMS, 0)     AS TOTAL_CLAIMS,
    COALESCE(cpc.CLAIMS_LAST_90D, 0)  AS CLAIMS_LAST_90D,
    COALESCE(rf.REPEAT_FAULTS, 0)     AS REPEAT_FAULTS,
    COALESCE(spc.WRITTEN_OFF_JOBS, 0) AS WRITTEN_OFF_JOBS,
    spc.TOTAL_SERVICE_JOBS,
    pt.PRICE_INCREASE_PCT,

    av.ANNUAL_CONTRACT_VALUE,

    -- ============================================================
    -- Factor 1: Repeat issue signal (max 20 pts) — magnitude-scaled
    -- ============================================================
    LEAST(20, GREATEST(0, (COALESCE(rf.REPEAT_FAULTS, 1) - 1) * 4)) AS repeat_issue_points,

    -- ============================================================
    -- Factor 2: Claim frequency, normalized by duration (max 15 pts)
    -- PLACEHOLDER BANDS — replace with real percentiles from Step 0.
    -- ============================================================
    CASE
        WHEN (COALESCE(cpc.TOTAL_CLAIMS, 0) / cb.DURATION::FLOAT) >= 0.6 THEN 15
        WHEN (COALESCE(cpc.TOTAL_CLAIMS, 0) / cb.DURATION::FLOAT) >= 0.3 THEN 9
        WHEN (COALESCE(cpc.TOTAL_CLAIMS, 0) / cb.DURATION::FLOAT) >= 0.1 THEN 4
        ELSE 0
    END AS claim_frequency_points,

    -- ============================================================
    -- Factor 3: Claim recency (max 16 pts)
    -- ============================================================
    CASE
        WHEN COALESCE(cpc.CLAIMS_LAST_90D, 0) >= 2 THEN 16
        WHEN COALESCE(cpc.CLAIMS_LAST_90D, 0) = 1  THEN 8
        ELSE 0
    END AS claim_recency_points,

    -- ============================================================
    -- Factor 4: Written-off job ratio (max 14 pts)
    -- ASSUMPTION FLAGGED: treats a written-off job as unresolved, not
    -- neutral — business meaning of JOBWRITTENOFF still unconfirmed.
    -- ============================================================
    CASE
        WHEN COALESCE(spc.TOTAL_SERVICE_JOBS, 0) = 0 THEN 0
        WHEN (spc.WRITTEN_OFF_JOBS / spc.TOTAL_SERVICE_JOBS::FLOAT) > 0.25 THEN 14
        WHEN (spc.WRITTEN_OFF_JOBS / spc.TOTAL_SERVICE_JOBS::FLOAT) > 0    THEN 7
        ELSE 0
    END AS written_off_ratio_points,

    -- ============================================================
    -- Factor 5: Coverage gap (max 6 pts)
    -- ============================================================
    CASE WHEN cb.SERVICEGEN = FALSE THEN 6 ELSE 0 END AS coverage_gap_points,

    -- ============================================================
    -- Factor 6: Equipment age (max 13 pts) — PLACEHOLDER BANDS.
    -- ============================================================
    CASE
        WHEN eq.equipment_age_years >= 8 THEN 13
        WHEN eq.equipment_age_years >= 4 THEN 7
        ELSE 0
    END AS equipment_age_points,

    -- ============================================================
    -- Factor 7: Warranty status (max 6 pts)
    -- No warranty record at all is treated the same as "not under
    -- warranty" (flagged assumption).
    -- ============================================================
    CASE WHEN COALESCE(ws.is_under_warranty, 0) = 0 THEN 6 ELSE 0 END AS warranty_status_points,

    -- ============================================================
    -- Factor 8: Price increase magnitude (max 10 pts)
    -- DIRECTION FLAGGED AS AN ASSUMPTION, not a confirmed business rule.
    -- ============================================================
    CASE
        WHEN pt.PRICE_INCREASE_PCT IS NULL THEN 0
        WHEN pt.PRICE_INCREASE_PCT > 0.10  THEN 10
        WHEN pt.PRICE_INCREASE_PCT > 0.05  THEN 6
        WHEN pt.PRICE_INCREASE_PCT > 0     THEN 3
        ELSE 0
    END AS price_increase_points,

    -- ============================================================
    -- TOTAL: sum of the 8 factors above, weights sum to 100 at max
    -- (20+15+16+14+6+13+6+10 = 100)
    -- ============================================================
    LEAST(100, GREATEST(0,
        LEAST(20, GREATEST(0, (COALESCE(rf.REPEAT_FAULTS, 1) - 1) * 4))
        + (CASE WHEN (COALESCE(cpc.TOTAL_CLAIMS, 0) / cb.DURATION::FLOAT) >= 0.6 THEN 15
                WHEN (COALESCE(cpc.TOTAL_CLAIMS, 0) / cb.DURATION::FLOAT) >= 0.3 THEN 9
                WHEN (COALESCE(cpc.TOTAL_CLAIMS, 0) / cb.DURATION::FLOAT) >= 0.1 THEN 4 ELSE 0 END)
        + (CASE WHEN COALESCE(cpc.CLAIMS_LAST_90D, 0) >= 2 THEN 16
                WHEN COALESCE(cpc.CLAIMS_LAST_90D, 0) = 1  THEN 8 ELSE 0 END)
        + (CASE WHEN COALESCE(spc.TOTAL_SERVICE_JOBS, 0) = 0 THEN 0
                WHEN (spc.WRITTEN_OFF_JOBS / spc.TOTAL_SERVICE_JOBS::FLOAT) > 0.25 THEN 14
                WHEN (spc.WRITTEN_OFF_JOBS / spc.TOTAL_SERVICE_JOBS::FLOAT) > 0    THEN 7 ELSE 0 END)
        + (CASE WHEN cb.SERVICEGEN = FALSE THEN 6 ELSE 0 END)
        + (CASE WHEN eq.equipment_age_years >= 8 THEN 13
                WHEN eq.equipment_age_years >= 4 THEN 7 ELSE 0 END)
        + (CASE WHEN COALESCE(ws.is_under_warranty, 0) = 0 THEN 6 ELSE 0 END)
        + (CASE WHEN pt.PRICE_INCREASE_PCT IS NULL THEN 0
                WHEN pt.PRICE_INCREASE_PCT > 0.10 THEN 10
                WHEN pt.PRICE_INCREASE_PCT > 0.05 THEN 6
                WHEN pt.PRICE_INCREASE_PCT > 0    THEN 3 ELSE 0 END)
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
    