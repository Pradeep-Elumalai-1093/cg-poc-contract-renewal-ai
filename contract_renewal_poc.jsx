import React, { useState, useMemo, useCallback } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell
} from "recharts";
import { Play, X, ChevronRight, AlertTriangle, CheckCircle2, RotateCcw, Clock, Coins } from "lucide-react";

/* ---------------------------------------------------------------
   DESIGN TOKENS
   Internal ops tool for sales reps / account managers. Functional,
   data-dense, industrial — not a marketing page. Cool graphite
   surface, ink-navy text, a single warm alert hue reserved for risk,
   a deep teal reserved for healthy/renewed. Everything else stays
   quiet so risk state is the only thing that visually shouts.
----------------------------------------------------------------*/
const T = {
  bg: "#EEF1F4",
  surface: "#FFFFFF",
  surfaceSunken: "#F6F7F9",
  ink: "#161B22",
  inkMuted: "#5B6472",
  inkFaint: "#8A93A3",
  border: "#DFE3E8",
  borderStrong: "#C7CCD4",
  risk: "#C1502E",
  riskBg: "#FBEBE5",
  safe: "#1F7A5C",
  safeBg: "#E7F3EE",
  info: "#2B5B8C",
  infoBg: "#E8EFF6",
  amber: "#B8863B",
  amberBg: "#F7EFE1",
};

const RISK_TIER_COLOR = { Low: T.safe, Medium: T.amber, High: T.risk };
const BUCKETS = [">90", "90", "60", "45", "30", "10", "Lost"];
const BUCKET_LABEL = {
  ">90": "Not yet due", "90": "\u226490 days", "60": "\u226460 days",
  "45": "\u226445 days", "30": "\u226430 days", "10": "\u226410 days", "Lost": "Lost",
};
const DUE_BUCKETS = ["90", "60", "45", "30", "10"];

const CAMPAIGN_TAXONOMY = [
  { id: "outreach_call", name: "Personal outreach call" },
  { id: "loyalty_pricing", name: "Discount / loyalty pricing offer" },
  { id: "service_checkin", name: "Free service check-in" },
  { id: "restructure", name: "Contract restructuring" },
  { id: "escalate_am", name: "Escalation to account manager" },
];

const REGIONS = [
  { id: "NATT", label: "North America Truck & Trailer", channels: ["Dealer"] },
  { id: "ETT", label: "Europe Truck & Trailer", channels: ["Dealer", "Direct"] },
  { id: "APAC_TT", label: "APAC Truck & Trailer", channels: ["Dealer"] },
];

// Sonnet-class blended placeholder rate, for cost estimation display only.
const COST_PER_M_INPUT = 3.0;
const COST_PER_M_OUTPUT = 15.0;

/* ---------------------------------------------------------------
   DATA SIMULATION  (in-memory only — no database, no persistence)
----------------------------------------------------------------*/
function rand(min, max) { return Math.random() * (max - min) + min; }
function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
function weightedBucket() {
  const pool = [">90", ">90", "90", "90", "60", "60", "45", "45", "30", "30", "10", "Lost"];
  return pick(pool);
}

const FLEET_NAMES = [
  "Alder Freight Co", "Boreal Transit", "Cascade Logistics", "Delta Haulage", "Evergreen Fleet Services",
  "Fenwick Cold Chain", "Granite Transport", "Harborline Trucking", "Ironbridge Freight", "Juniper Fleet Corp",
  "Kestrel Logistics", "Lattimer Transport", "Meridian Cold Freight", "Northgate Haulers", "Orchard Fleet Solutions",
  "Palisade Trucking", "Quarrystone Freight", "Ridgeway Logistics", "Sablewood Transport", "Thornfield Fleet",
  "Umberline Haulage", "Vantage Cold Chain", "Westmark Trucking", "Yarrow Freight Services", "Zephyr Logistics",
  "Ashgrove Transit", "Briarcliff Freight", "Cedarline Haulers", "Dunmoor Logistics", "Elmscourt Fleet",
  "Foxglen Transport", "Greywick Freight", "Hollowmere Trucking", "Ivywood Logistics", "Larkspur Fleet Corp",
  "Millbrook Cold Chain", "Nettlewood Haulage",
];

function generateContracts() {
  const contracts = [];
  let seq = 1;
  const regionCounts = { NATT: 14, ETT: 14, APAC_TT: 10 };
  let nameIdx = 0;

  Object.entries(regionCounts).forEach(([regionId, count]) => {
    const regionDef = REGIONS.find((r) => r.id === regionId);
    for (let i = 0; i < count; i++) {
      const channel = pick(regionDef.channels);
      const monthsOnBook = Math.round(rand(3, 72));
      const contractValue = Math.round(rand(8000, 145000) / 500) * 500;
      const costToServeRatio = rand(0.45, 0.85);
      const costToServe = Math.round(contractValue * costToServeRatio);
      const margin = contractValue - costToServe;
      const paymentLagDays = Math.round(rand(0, 55));
      const complaintCount = Math.random() < 0.3 ? Math.round(rand(1, 4)) : 0;
      const serviceTrend = pick(["declining", "stable", "stable", "increasing"]);
      const bucket = weightedBucket();

      let riskScore =
        20 + paymentLagDays * 0.9 + complaintCount * 9 +
        (serviceTrend === "declining" ? 22 : serviceTrend === "increasing" ? -8 : 0) -
        (monthsOnBook > 30 ? 8 : 0);
      riskScore = Math.max(4, Math.min(97, Math.round(riskScore)));
      const riskTier = riskScore >= 66 ? "High" : riskScore >= 38 ? "Medium" : "Low";

      contracts.push({
        contractId: `CT-${String(seq).padStart(4, "0")}`,
        customerId: `CU-${String(seq).padStart(4, "0")}`,
        customerName: FLEET_NAMES[nameIdx % FLEET_NAMES.length],
        region: regionId,
        channel,
        dealerId: channel === "Dealer" ? `DLR-${100 + (seq % 12)}` : null,
        monthsOnBook,
        contractValue,
        costToServe,
        margin,
        paymentLagDays,
        complaintCount,
        serviceTrend,
        bucket,
        lastMilestoneProcessed: null,
        riskScore,
        riskTier,
      });
      seq++; nameIdx++;
    }
  });
  return contracts;
}

/* ---------------------------------------------------------------
   LLM CLIENT — thin wrapper, swappable provider boundary
----------------------------------------------------------------*/
async function callClaude(promptText) {
  const started = performance.now();
  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 1000,
      messages: [{ role: "user", content: promptText }],
    }),
  });
  const data = await response.json();
  const latencyMs = Math.round(performance.now() - started);
  const textBlock = (data.content || []).find((b) => b.type === "text");
  const raw = textBlock ? textBlock.text : "";
  const cleaned = raw.replace(/```json|```/g, "").trim();
  let parsed = null;
  try { parsed = JSON.parse(cleaned); } catch (e) { parsed = null; }
  return {
    parsed,
    raw,
    latencyMs,
    inputTokens: data.usage?.input_tokens ?? 0,
    outputTokens: data.usage?.output_tokens ?? 0,
  };
}

function buildAggregatorContext(contract, priorTrace) {
  return {
    customer_id: contract.customerId,
    customer_name: contract.customerName,
    region: contract.region,
    channel: contract.channel,
    dealer_id: contract.dealerId,
    months_on_book: contract.monthsOnBook,
    contract_value_usd: contract.contractValue,
    margin_usd: contract.margin,
    payment_lag_days: contract.paymentLagDays,
    complaint_count_last_year: contract.complaintCount,
    service_call_trend: contract.serviceTrend,
    milestone: contract.bucket,
    risk_score: contract.riskScore,
    prior_milestone_action: priorTrace ? priorTrace.recommendation?.campaign : null,
    prior_milestone_outcome: priorTrace ? priorTrace.outcome ?? "not yet recorded" : null,
  };
}

function recommendationPrompt(ctx, taxonomy, priorFeedback) {
  return `You are a retention recommendation engine for a B2B truck/trailer HVAC service contract renewal system.
Choose exactly ONE action from this taxonomy: ${taxonomy.map((t) => t.name).join(" | ")}.
If channel is "Dealer", execution_owner must be "Dealer". If channel is "Direct", execution_owner must be "Direct Sales Rep".
Do not repeat an action that was already tried at a prior milestone for this same contract if it did not work.
${priorFeedback ? `Your previous attempt was rejected by QA for this reason, revise accordingly: ${priorFeedback}` : ""}

Customer-contract context (JSON):
${JSON.stringify(ctx, null, 2)}

Respond with ONLY valid JSON, no markdown fences, no preamble:
{"action": "<short action name>", "campaign": "<one taxonomy name exactly>", "execution_owner": "Dealer or Direct Sales Rep", "rationale": "<2-3 sentences, grounded only in the context above>", "confidence": <0 to 1 number>}`;
}

function evaluationPrompt(ctx, recommendation) {
  return `You are a QA evaluator scoring a retention recommendation before it reaches a sales rep.
Score each criterion 0-10 based on the context and recommendation below.
- groundedness: does the rationale only reference facts present in the context, no invented details?
- policy_compliance: is the campaign exactly one of the approved taxonomy names, and is execution_owner correct for the channel?
- actionability: concrete enough for a rep to act on today, not vague?
- non_repetition: does it avoid repeating the prior_milestone_action if that action's outcome was not positive?
- tone: professional, no overpromising?

Context:
${JSON.stringify(ctx, null, 2)}

Recommendation:
${JSON.stringify(recommendation, null, 2)}

Respond with ONLY valid JSON, no markdown fences, no preamble:
{"scores": {"groundedness": <0-10>, "policy_compliance": <0-10>, "actionability": <0-10>, "non_repetition": <0-10>, "tone": <0-10>}, "notes": "<one short sentence>"}`;
}

/* ---------------------------------------------------------------
   AGENT GRAPH ORCHESTRATOR
   aggregator -> recommend -> evaluate -> (retry up to 2 | pass | escalate)
   Policy-compliance failure is a hard fail regardless of composite.
----------------------------------------------------------------*/
const MAX_RETRIES = 2;
const COMPOSITE_PASS = 7;
const POLICY_FLOOR = 6;

async function runAgentGraph(contract, priorTrace) {
  const ctx = buildAggregatorContext(contract, priorTrace);
  let retryCount = 0;
  let priorFeedback = null;
  let totalInputTokens = 0, totalOutputTokens = 0, totalLatency = 0;
  let lastRecommendation = null, lastEvaluation = null, escalated = false, passed = false;

  while (retryCount <= MAX_RETRIES) {
    const recRes = await callClaude(recommendationPrompt(ctx, CAMPAIGN_TAXONOMY, priorFeedback));
    totalInputTokens += recRes.inputTokens; totalOutputTokens += recRes.outputTokens; totalLatency += recRes.latencyMs;
    const recommendation = recRes.parsed || { action: "Unparsed", campaign: "Personal outreach call", execution_owner: "Dealer", rationale: "Model response could not be parsed.", confidence: 0 };
    lastRecommendation = recommendation;

    const evalRes = await callClaude(evaluationPrompt(ctx, recommendation));
    totalInputTokens += evalRes.inputTokens; totalOutputTokens += evalRes.outputTokens; totalLatency += evalRes.latencyMs;
    const evaluation = evalRes.parsed || { scores: { groundedness: 0, policy_compliance: 0, actionability: 0, non_repetition: 0, tone: 0 }, notes: "Model response could not be parsed." };
    lastEvaluation = evaluation;

    const s = evaluation.scores || {};
    const vals = ["groundedness", "policy_compliance", "actionability", "non_repetition", "tone"].map((k) => Number(s[k]) || 0);
    const composite = vals.reduce((a, b) => a + b, 0) / vals.length;
    const policyOk = (Number(s.policy_compliance) || 0) >= POLICY_FLOOR;
    passed = policyOk && composite >= COMPOSITE_PASS;
    lastEvaluation.composite = Math.round(composite * 10) / 10;
    lastEvaluation.pass = passed;

    if (passed) break;
    retryCount++;
    priorFeedback = evaluation.notes || "Composite or policy-compliance score too low.";
    if (retryCount > MAX_RETRIES) { escalated = true; break; }
  }

  return {
    runId: `RUN-${contract.contractId}-${contract.bucket}-${Date.now()}`,
    contractId: contract.contractId,
    customerId: contract.customerId,
    milestone: contract.bucket,
    timestamp: new Date().toISOString(),
    context: ctx,
    recommendation: lastRecommendation,
    evaluation: lastEvaluation,
    retryCount: Math.min(retryCount, MAX_RETRIES),
    escalated,
    pass: passed,
    latencyMs: totalLatency,
    inputTokens: totalInputTokens,
    outputTokens: totalOutputTokens,
    costUsd: (totalInputTokens / 1e6) * COST_PER_M_INPUT + (totalOutputTokens / 1e6) * COST_PER_M_OUTPUT,
    outcome: null,
    outcomeNote: "",
  };
}

/* ---------------------------------------------------------------
   SMALL UI PRIMITIVES
----------------------------------------------------------------*/
function Card({ children, style, onClick, className }) {
  return (
    <div
      onClick={onClick}
      className={className}
      style={{
        background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10,
        padding: 16, cursor: onClick ? "pointer" : "default", ...style,
      }}
    >
      {children}
    </div>
  );
}

function Badge({ text, color, bg }) {
  return (
    <span style={{
      display: "inline-block", fontSize: 11.5, fontWeight: 600, letterSpacing: 0.2,
      color, background: bg, borderRadius: 999, padding: "3px 9px",
    }}>{text}</span>
  );
}

function StatBlock({ label, value, sub, accent }) {
  return (
    <div>
      <div style={{ fontSize: 11.5, color: T.inkFaint, textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: accent || T.ink, fontVariantNumeric: "tabular-nums", marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: T.inkMuted, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------
   MAIN APP
----------------------------------------------------------------*/
export default function ContractRenewalPOC() {
  const [contracts, setContracts] = useState(() => generateContracts());
  const [trace, setTrace] = useState([]); // array of run records, most recent per contract-milestone kept
  const [tab, setTab] = useState("dashboard");
  const [bucketFilter, setBucketFilter] = useState(null);
  const [selected, setSelected] = useState(null); // contractId
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [apiError, setApiError] = useState(null);

  const traceByContract = useMemo(() => {
    const m = {};
    trace.forEach((t) => { m[t.contractId] = t; }); // latest wins since we push in order
    return m;
  }, [trace]);

  const dueContracts = useMemo(
    () => contracts.filter((c) => DUE_BUCKETS.includes(c.bucket) && c.lastMilestoneProcessed !== c.bucket),
    [contracts]
  );

  const runBatch = useCallback(async () => {
    setRunning(true); setApiError(null);
    const queue = [...dueContracts];
    setProgress({ done: 0, total: queue.length });
    const CONCURRENCY = 3;
    let idx = 0, done = 0;

    async function worker() {
      while (idx < queue.length) {
        const myIdx = idx++;
        const contract = queue[myIdx];
        const priorTrace = traceByContract[contract.contractId] || null;
        try {
          const result = await runAgentGraph(contract, priorTrace);
          setTrace((prev) => [...prev, result]);
          setContracts((prev) => prev.map((c) =>
            c.contractId === contract.contractId ? { ...c, lastMilestoneProcessed: contract.bucket } : c
          ));
        } catch (e) {
          setApiError(String(e?.message || e));
        }
        done++;
        setProgress({ done, total: queue.length });
      }
    }
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));
    setRunning(false);
  }, [dueContracts, traceByContract]);

  const bucketCounts = useMemo(() => {
    const c = {};
    BUCKETS.forEach((b) => (c[b] = 0));
    contracts.forEach((ct) => { c[ct.bucket] = (c[ct.bucket] || 0) + 1; });
    return c;
  }, [contracts]);

  const worklist = useMemo(() => {
    let list = contracts;
    if (bucketFilter) list = list.filter((c) => c.bucket === bucketFilter);
    return [...list].sort((a, b) => b.riskScore - a.riskScore);
  }, [contracts, bucketFilter]);

  const scatterData = useMemo(() => contracts.map((c) => ({
    x: c.monthsOnBook, y: c.contractValue, tier: c.riskTier, id: c.contractId, name: c.customerName,
  })), [contracts]);

  const metrics = useMemo(() => {
    const runs = trace;
    const n = runs.length || 1;
    const firstPass = runs.filter((r) => r.retryCount === 0 && r.pass).length;
    const avgRetries = runs.reduce((a, r) => a + r.retryCount, 0) / n;
    const escalations = runs.filter((r) => r.escalated).length;
    const avgLatency = runs.reduce((a, r) => a + r.latencyMs, 0) / n;
    const totalCost = runs.reduce((a, r) => a + r.costUsd, 0);
    const withOutcome = runs.filter((r) => r.outcome);
    const engaged = withOutcome.filter((r) => r.outcome === "Engaged").length;
    return {
      firstPassRate: runs.length ? Math.round((firstPass / runs.length) * 100) : 0,
      avgRetries: runs.length ? avgRetries.toFixed(2) : "0.00",
      escalationRate: runs.length ? Math.round((escalations / runs.length) * 100) : 0,
      avgLatency: runs.length ? Math.round(avgLatency) : 0,
      totalCost: totalCost,
      responseRate: withOutcome.length ? Math.round((engaged / withOutcome.length) * 100) : null,
      totalRuns: runs.length,
    };
  }, [trace]);

  const campaignSummary = useMemo(() => {
    const m = {};
    CAMPAIGN_TAXONOMY.forEach((t) => { m[t.name] = { assigned: 0, engaged: 0, declined: 0, noResponse: 0 }; });
    trace.forEach((r) => {
      const name = r.recommendation?.campaign;
      if (!name || !m[name]) return;
      m[name].assigned++;
      if (r.outcome === "Engaged") m[name].engaged++;
      else if (r.outcome === "Declined") m[name].declined++;
      else if (r.outcome === "No response") m[name].noResponse++;
    });
    return m;
  }, [trace]);

  const selectedContract = contracts.find((c) => c.contractId === selected);
  const selectedTrace = selected ? traceByContract[selected] : null;

  const setOutcome = (contractId, outcome, note) => {
    setTrace((prev) => prev.map((r) => (r.contractId === contractId && r === traceByContract[contractId]
      ? { ...r, outcome, outcomeNote: note ?? r.outcomeNote } : r)));
  };

  return (
    <div style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", background: T.bg, color: T.ink, minHeight: "100vh", padding: "22px 26px" }}>
      <style>{`
        * { box-sizing: border-box; }
        button:focus-visible, div[tabindex]:focus-visible { outline: 2px solid ${T.info}; outline-offset: 2px; }
        .tabbtn { border:none; background:transparent; padding:8px 4px; font-size:13.5px; font-weight:600; color:${T.inkFaint}; cursor:pointer; border-bottom:2px solid transparent; }
        .tabbtn.active { color:${T.ink}; border-bottom-color:${T.ink}; }
        .rowhover:hover { background:${T.surfaceSunken}; }
        table { border-collapse: collapse; width: 100%; }
        th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:0.4px; color:${T.inkFaint}; font-weight:600; padding:8px 10px; border-bottom:1px solid ${T.border}; }
        td { padding:9px 10px; font-size:13px; border-bottom:1px solid ${T.border}; }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 18, flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ fontSize: 11.5, color: T.inkFaint, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>Climate Solutions Transportation \u2014 Proof of Concept</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: "2px 0 0" }}>Proactive Contract Renewal</h1>
          <div style={{ fontSize: 12.5, color: T.inkMuted, marginTop: 3 }}>Simulated data \u2014 in-memory only \u2014 NATT \u00b7 ETT \u00b7 APAC TT</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <button
            onClick={runBatch}
            disabled={running || dueContracts.length === 0}
            style={{
              display: "flex", alignItems: "center", gap: 8, background: running ? T.inkFaint : T.ink, color: "#fff",
              border: "none", borderRadius: 8, padding: "10px 16px", fontSize: 13.5, fontWeight: 600,
              cursor: running || dueContracts.length === 0 ? "default" : "pointer",
            }}
          >
            <Play size={15} fill="#fff" />
            {running ? `Running ${progress.done}/${progress.total}\u2026` : `Run daily batch (${dueContracts.length} due)`}
          </button>
          {apiError && <div style={{ fontSize: 11.5, color: T.risk, marginTop: 6, maxWidth: 260 }}>{apiError}</div>}
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 20, borderBottom: `1px solid ${T.border}`, marginBottom: 18 }}>
        <button className={`tabbtn ${tab === "dashboard" ? "active" : ""}`} onClick={() => setTab("dashboard")}>Dashboard</button>
        <button className={`tabbtn ${tab === "trace" ? "active" : ""}`} onClick={() => setTab("trace")}>Trace &amp; Agent Metrics</button>
        <button className={`tabbtn ${tab === "campaigns" ? "active" : ""}`} onClick={() => setTab("campaigns")}>Campaigns</button>
      </div>

      {tab === "dashboard" && (
        <>
          {/* Bucket cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0,1fr))", gap: 10, marginBottom: 18 }}>
            {BUCKETS.map((b) => (
              <Card
                key={b}
                onClick={() => setBucketFilter(bucketFilter === b ? null : b)}
                style={{
                  padding: "12px 14px",
                  borderColor: bucketFilter === b ? T.ink : T.border,
                  borderWidth: bucketFilter === b ? 1.5 : 1,
                }}
              >
                <div style={{ fontSize: 11, color: T.inkFaint, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.3 }}>{BUCKET_LABEL[b]}</div>
                <div style={{ fontSize: 24, fontWeight: 700, marginTop: 2, color: b === "Lost" ? T.risk : T.ink }}>{bucketCounts[b]}</div>
              </Card>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 16, marginBottom: 18 }}>
            {/* Scatter */}
            <Card style={{ padding: 18 }}>
              <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 2 }}>Value segmentation</div>
              <div style={{ fontSize: 12, color: T.inkMuted, marginBottom: 10 }}>Months on book vs. contract value \u2014 colored by risk tier</div>
              <ResponsiveContainer width="100%" height={280}>
                <ScatterChart margin={{ top: 6, right: 12, bottom: 6, left: 0 }}>
                  <CartesianGrid stroke={T.border} strokeDasharray="3 3" />
                  <XAxis type="number" dataKey="x" name="Months on book" stroke={T.inkFaint} tick={{ fontSize: 11 }} />
                  <YAxis type="number" dataKey="y" name="Contract value" stroke={T.inkFaint} tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Math.round(v / 1000)}k`} />
                  <ZAxis range={[55, 55]} />
                  <Tooltip
                    cursor={{ strokeDasharray: "3 3" }}
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: `1px solid ${T.border}` }}
                    formatter={(value, key) => key === "y" ? [`$${value.toLocaleString()}`, "Contract value"] : [value, "Months on book"]}
                    labelFormatter={() => ""}
                  />
                  {["Low", "Medium", "High"].map((tier) => (
                    <Scatter
                      key={tier}
                      name={tier}
                      data={scatterData.filter((d) => d.tier === tier)}
                      fill={RISK_TIER_COLOR[tier]}
                      onClick={(d) => setSelected(d.id)}
                      cursor="pointer"
                    />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
              <div style={{ display: "flex", gap: 14, marginTop: 4 }}>
                {["Low", "Medium", "High"].map((tier) => (
                  <div key={tier} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: T.inkMuted }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: RISK_TIER_COLOR[tier], display: "inline-block" }} />
                    {tier} risk
                  </div>
                ))}
              </div>
            </Card>

            {/* KPI summary */}
            <Card style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
              <div style={{ fontSize: 13.5, fontWeight: 700 }}>Portfolio KPIs</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <StatBlock label="At-risk contracts" value={contracts.filter((c) => c.riskTier === "High").length} sub="risk score \u2265 66" accent={T.risk} />
                <StatBlock label="Lost" value={bucketCounts["Lost"]} sub="past expiry" accent={T.risk} />
                <StatBlock label="Campaign response rate" value={metrics.responseRate !== null ? `${metrics.responseRate}%` : "\u2014"} sub="of logged outcomes" />
                <StatBlock label="Recommendations run" value={metrics.totalRuns} sub={`of ${contracts.length} contracts`} />
              </div>
              <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 12, fontSize: 12, color: T.inkMuted }}>
                {dueContracts.length > 0
                  ? `${dueContracts.length} contract-milestones are due for a fresh recommendation.`
                  : "All eligible contracts are up to date for their current milestone."}
              </div>
            </Card>
          </div>

          {/* Worklist */}
          <Card style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ padding: "14px 16px", borderBottom: `1px solid ${T.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 13.5, fontWeight: 700 }}>Worklist {bucketFilter ? `\u2014 ${BUCKET_LABEL[bucketFilter]}` : ""}</div>
              {bucketFilter && <button onClick={() => setBucketFilter(null)} style={{ border: "none", background: "none", fontSize: 12, color: T.info, cursor: "pointer" }}>Clear filter</button>}
            </div>
            <div style={{ maxHeight: 360, overflowY: "auto" }}>
              <table>
                <thead><tr>
                  <th>Customer</th><th>Region</th><th>Channel</th><th>Bucket</th><th>Risk</th><th>Value</th><th>Status</th><th></th>
                </tr></thead>
                <tbody>
                  {worklist.map((c) => {
                    const t = traceByContract[c.contractId];
                    return (
                      <tr key={c.contractId} className="rowhover" style={{ cursor: "pointer" }} onClick={() => setSelected(c.contractId)}>
                        <td style={{ fontWeight: 600 }}>{c.customerName}</td>
                        <td>{c.region}</td>
                        <td>{c.channel}</td>
                        <td><Badge text={BUCKET_LABEL[c.bucket]} color={T.inkMuted} bg={T.surfaceSunken} /></td>
                        <td><Badge text={`${c.riskTier} \u00b7 ${c.riskScore}`} color={RISK_TIER_COLOR[c.riskTier]} bg={c.riskTier === "High" ? T.riskBg : c.riskTier === "Medium" ? T.amberBg : T.safeBg} /></td>
                        <td>${c.contractValue.toLocaleString()}</td>
                        <td>
                          {!t && <span style={{ color: T.inkFaint, fontSize: 12 }}>Not run</span>}
                          {t && t.pass && !t.escalated && <Badge text="Recommended" color={T.safe} bg={T.safeBg} />}
                          {t && t.escalated && <Badge text="Escalated" color={T.risk} bg={T.riskBg} />}
                        </td>
                        <td><ChevronRight size={15} color={T.inkFaint} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {tab === "trace" && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12, marginBottom: 18 }}>
            <Card><StatBlock label="First-pass rate" value={`${metrics.firstPassRate}%`} sub="passed with 0 retries" /></Card>
            <Card><StatBlock label="Avg. retries" value={metrics.avgRetries} sub={`limit ${MAX_RETRIES}`} /></Card>
            <Card><StatBlock label="Escalation rate" value={`${metrics.escalationRate}%`} sub="sent to human review" accent={metrics.escalationRate > 0 ? T.risk : T.ink} /></Card>
            <Card><StatBlock label="Avg. latency" value={`${metrics.avgLatency}ms`} sub="per contract-milestone" /></Card>
            <Card><StatBlock label="Est. token cost" value={`$${metrics.totalCost.toFixed(3)}`} sub="cumulative, this session" /></Card>
          </div>

          <Card style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ padding: "14px 16px", borderBottom: `1px solid ${T.border}`, fontSize: 13.5, fontWeight: 700 }}>
              Recommendation trace log ({trace.length} runs)
            </div>
            <div style={{ maxHeight: 420, overflowY: "auto" }}>
              <table>
                <thead><tr>
                  <th>Customer</th><th>Milestone</th><th>Campaign</th><th>Retries</th><th>Result</th><th>Latency</th><th>Cost</th>
                </tr></thead>
                <tbody>
                  {[...trace].reverse().map((r, i) => (
                    <tr key={r.runId} className="rowhover" style={{ cursor: "pointer" }} onClick={() => setSelected(r.contractId)}>
                      <td style={{ fontWeight: 600 }}>{contracts.find((c) => c.contractId === r.contractId)?.customerName}</td>
                      <td>{BUCKET_LABEL[r.milestone]}</td>
                      <td style={{ color: T.inkMuted }}>{r.recommendation?.campaign || "\u2014"}</td>
                      <td>{r.retryCount}</td>
                      <td>
                        {r.escalated
                          ? <Badge text="Escalated" color={T.risk} bg={T.riskBg} />
                          : r.pass
                            ? <Badge text="Passed" color={T.safe} bg={T.safeBg} />
                            : <Badge text="Failed" color={T.risk} bg={T.riskBg} />}
                      </td>
                      <td style={{ color: T.inkMuted }}>{r.latencyMs}ms</td>
                      <td style={{ color: T.inkMuted }}>${r.costUsd.toFixed(4)}</td>
                    </tr>
                  ))}
                  {trace.length === 0 && (
                    <tr><td colSpan={7} style={{ textAlign: "center", color: T.inkFaint, padding: 24 }}>No runs yet \u2014 run the daily batch from the Dashboard tab.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {tab === "campaigns" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 14 }}>
          {CAMPAIGN_TAXONOMY.map((t) => {
            const s = campaignSummary[t.name];
            const total = s.assigned;
            const responseRate = total ? Math.round(((s.engaged + s.declined) / total) * 100) : 0;
            const engagedRate = total ? Math.round((s.engaged / total) * 100) : 0;
            return (
              <Card key={t.id} style={{ padding: 16 }}>
                <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>{t.name}</div>
                <div style={{ display: "flex", gap: 18, marginBottom: 10 }}>
                  <StatBlock label="Assigned" value={total} />
                  <StatBlock label="Engaged" value={s.engaged} accent={T.safe} />
                </div>
                <div style={{ fontSize: 11.5, color: T.inkMuted }}>
                  Response rate {responseRate}% \u00b7 Engagement rate {engagedRate}%
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Detail drawer */}
      {selectedContract && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(22,27,34,0.35)", display: "flex", justifyContent: "flex-end", zIndex: 50 }} onClick={() => setSelected(null)}>
          <div style={{ width: 460, maxWidth: "94vw", background: T.surface, height: "100%", overflowY: "auto", padding: 22, boxShadow: "-8px 0 24px rgba(0,0,0,0.12)" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 4 }}>
              <div>
                <div style={{ fontSize: 11.5, color: T.inkFaint, fontWeight: 600 }}>{selectedContract.contractId} \u00b7 {selectedContract.region}</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{selectedContract.customerName}</div>
              </div>
              <button onClick={() => setSelected(null)} style={{ border: "none", background: "none", cursor: "pointer" }}><X size={18} color={T.inkFaint} /></button>
            </div>

            <div style={{ display: "flex", gap: 8, margin: "10px 0 16px", flexWrap: "wrap" }}>
              <Badge text={selectedContract.channel} color={T.info} bg={T.infoBg} />
              <Badge text={BUCKET_LABEL[selectedContract.bucket]} color={T.inkMuted} bg={T.surfaceSunken} />
              <Badge text={`${selectedContract.riskTier} risk \u00b7 ${selectedContract.riskScore}`} color={RISK_TIER_COLOR[selectedContract.riskTier]} bg={selectedContract.riskTier === "High" ? T.riskBg : selectedContract.riskTier === "Medium" ? T.amberBg : T.safeBg} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 18 }}>
              <StatBlock label="Contract value" value={`$${selectedContract.contractValue.toLocaleString()}`} />
              <StatBlock label="Margin" value={`$${selectedContract.margin.toLocaleString()}`} />
              <StatBlock label="Months on book" value={selectedContract.monthsOnBook} />
              <StatBlock label="Payment lag" value={`${selectedContract.paymentLagDays}d`} />
            </div>

            {!selectedTrace && (
              <div style={{ fontSize: 13, color: T.inkFaint, padding: 14, background: T.surfaceSunken, borderRadius: 8 }}>
                No recommendation run yet for this contract's current milestone.
              </div>
            )}

            {selectedTrace && (
              <>
                <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Recommendation</div>
                <Card style={{ padding: 14, marginBottom: 14, background: T.surfaceSunken }}>
                  <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{selectedTrace.recommendation?.campaign}</div>
                  <div style={{ fontSize: 12.5, color: T.inkMuted, marginBottom: 8 }}>Owner: {selectedTrace.recommendation?.execution_owner}</div>
                  <div style={{ fontSize: 13 }}>{selectedTrace.recommendation?.rationale}</div>
                </Card>

                <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Evaluation</div>
                <Card style={{ padding: 14, marginBottom: 14 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                    {selectedTrace.escalated
                      ? <><AlertTriangle size={15} color={T.risk} /><span style={{ fontSize: 13, fontWeight: 600, color: T.risk }}>Escalated to human review</span></>
                      : selectedTrace.pass
                        ? <><CheckCircle2 size={15} color={T.safe} /><span style={{ fontSize: 13, fontWeight: 600, color: T.safe }}>Passed evaluation</span></>
                        : <><AlertTriangle size={15} color={T.risk} /><span style={{ fontSize: 13, fontWeight: 600, color: T.risk }}>Failed evaluation</span></>}
                  </div>
                  {Object.entries(selectedTrace.evaluation?.scores || {}).map(([k, v]) => (
                    <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, padding: "3px 0" }}>
                      <span style={{ color: T.inkMuted, textTransform: "capitalize" }}>{k.replace(/_/g, " ")}</span>
                      <span style={{ fontWeight: 600 }}>{v}/10</span>
                    </div>
                  ))}
                  <div style={{ display: "flex", gap: 14, marginTop: 10, fontSize: 11.5, color: T.inkFaint }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}><RotateCcw size={12} /> {selectedTrace.retryCount} retries</span>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}><Clock size={12} /> {selectedTrace.latencyMs}ms</span>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}><Coins size={12} /> ${selectedTrace.costUsd.toFixed(4)}</span>
                  </div>
                </Card>

                <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Log outcome</div>
                <Card style={{ padding: 14 }}>
                  <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                    {["No response", "Engaged", "Declined"].map((o) => (
                      <button
                        key={o}
                        onClick={() => setOutcome(selectedContract.contractId, o)}
                        style={{
                          flex: 1, padding: "8px 6px", borderRadius: 7, fontSize: 12, fontWeight: 600, cursor: "pointer",
                          border: `1px solid ${selectedTrace.outcome === o ? T.ink : T.border}`,
                          background: selectedTrace.outcome === o ? T.ink : "#fff",
                          color: selectedTrace.outcome === o ? "#fff" : T.inkMuted,
                        }}
                      >{o}</button>
                    ))}
                  </div>
                  <textarea
                    placeholder="Optional rep note\u2026"
                    value={selectedTrace.outcomeNote}
                    onChange={(e) => setOutcome(selectedContract.contractId, selectedTrace.outcome, e.target.value)}
                    style={{ width: "100%", minHeight: 60, border: `1px solid ${T.border}`, borderRadius: 7, padding: 8, fontSize: 12.5, fontFamily: "inherit", resize: "vertical" }}
                  />
                </Card>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
