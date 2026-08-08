import React, { useState, useMemo, useCallback } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, Label
} from "recharts";
import { Play, X, ChevronRight, AlertTriangle, CheckCircle2, RotateCcw, Clock, Coins, Copy, ClipboardCheck } from "lucide-react";
import { api } from "./api.js";

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

function ExchangeBlock({ label, prompt, raw, latencyMs }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginTop: 8 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 6, border: "none", background: "none",
          cursor: "pointer", padding: 0, fontSize: 12, fontWeight: 600, color: T.info,
        }}
      >
        <ChevronRight size={13} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 0.12s" }} />
        {label} {latencyMs != null && <span style={{ color: T.inkFaint, fontWeight: 400 }}>({latencyMs}ms)</span>}
      </button>
      {open && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 10.5, fontWeight: 700, color: T.inkFaint, textTransform: "uppercase", letterSpacing: 0.3, marginBottom: 3 }}>Prompt sent</div>
          <pre style={{
            fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace", fontSize: 11, whiteSpace: "pre-wrap", wordBreak: "break-word",
            background: T.surfaceSunken, border: `1px solid ${T.border}`, borderRadius: 6, padding: 10, maxHeight: 220, overflowY: "auto", margin: 0,
          }}>{prompt}</pre>
          <div style={{ fontSize: 10.5, fontWeight: 700, color: T.inkFaint, textTransform: "uppercase", letterSpacing: 0.3, margin: "8px 0 3px" }}>Raw model response</div>
          <pre style={{
            fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace", fontSize: 11, whiteSpace: "pre-wrap", wordBreak: "break-word",
            background: "#101418", color: "#D7DDE5", borderRadius: 6, padding: 10, maxHeight: 220, overflowY: "auto", margin: 0,
          }}>{raw}</pre>
        </div>
      )}
    </div>
  );
}

function DraftContent({ content, contentError }) {
  const [copied, setCopied] = useState(false);

  if (contentError) {
    return (
      <>
        <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Draft content</div>
        <Card style={{ padding: 14, marginBottom: 14, background: T.surfaceSunken }}>
          <div style={{ fontSize: 12.5, color: T.inkFaint }}>Draft generation failed: {contentError}</div>
        </Card>
      </>
    );
  }
  if (!content) return null;

  const copyText = `Subject: ${content.email_subject}\n\n${content.email_body}`;
  const copy = () => {
    navigator.clipboard.writeText(copyText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Draft content</div>
      <Card style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ fontSize: 12.5, color: T.inkMuted, marginBottom: 10, lineHeight: 1.5 }}>{content.summary}</div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <Badge text={`Addressed to: ${content.recipient_role}`} color={T.info} bg={T.infoBg} />
          <button
            onClick={copy}
            style={{
              display: "flex", alignItems: "center", gap: 5, border: `1px solid ${T.border}`, background: copied ? T.safeBg : "#fff",
              color: copied ? T.safe : T.inkMuted, borderRadius: 6, padding: "5px 9px", fontSize: 11.5, fontWeight: 600, cursor: "pointer",
            }}
          >
            {copied ? <ClipboardCheck size={13} /> : <Copy size={13} />}
            {copied ? "Copied" : "Copy email"}
          </button>
        </div>
        <div style={{ border: `1px solid ${T.border}`, borderRadius: 7, overflow: "hidden" }}>
          <div style={{ background: T.surfaceSunken, padding: "7px 10px", fontSize: 12, fontWeight: 700, borderBottom: `1px solid ${T.border}` }}>
            {content.email_subject}
          </div>
          <div style={{ padding: 10, fontSize: 12.5, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{content.email_body}</div>
        </div>
      </Card>
    </>
  );
}

function EscalationPanel({ record, onToggleAction }) {
  if (!record.escalated) return null;
  const actionDone = record.actionStatus === "Action done";
  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Escalation - human review needed</div>
      <Card style={{ padding: 14, marginBottom: 14, background: T.riskBg, borderColor: T.risk }}>
        <ul style={{ margin: "0 0 12px", paddingLeft: 18, fontSize: 12.5, lineHeight: 1.6, color: T.ink }}>
          {(record.suggestedActions || []).map((a, i) => <li key={i}>{a}</li>)}
        </ul>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: `1px solid rgba(193,80,46,0.25)`, paddingTop: 10 }}>
          <Badge
            text={record.actionStatus}
            color={actionDone ? T.safe : T.risk}
            bg={actionDone ? T.safeBg : "#fff"}
          />
          <button
            onClick={() => onToggleAction(record.contractId, record.actionStatus)}
            style={{
              border: "none", borderRadius: 6, padding: "6px 12px", fontSize: 11.5, fontWeight: 600, cursor: "pointer",
              background: actionDone ? T.surfaceSunken : T.ink, color: actionDone ? T.inkMuted : "#fff",
            }}
          >
            {actionDone ? "Mark as not done" : "Mark action done"}
          </button>
        </div>
      </Card>
    </>
  );
}


function AgentInspector({ attempts }) {
  if (!attempts || attempts.length === 0) return null;
  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>
        Inspect LLM exchange
      </div>
      <Card style={{ padding: 14, marginBottom: 14 }}>
        {attempts.map((a) => (
          <div key={a.attemptNumber} style={{ paddingBottom: 12, marginBottom: 12, borderBottom: attempts.length > 1 ? `1px solid ${T.border}` : "none" }}>
            {attempts.length > 1 && (
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
                Attempt {a.attemptNumber} {a.passed ? <Badge text="Passed" color={T.safe} bg={T.safeBg} /> : <Badge text="Retried" color={T.amber} bg={T.amberBg} />}
              </div>
            )}
            <ExchangeBlock label="Recommendation agent call" prompt={a.recommendationPrompt} raw={a.recommendationRaw} latencyMs={a.recommendationLatencyMs} />
            <ExchangeBlock label="Evaluation agent call" prompt={a.evaluationPrompt} raw={a.evaluationRaw} latencyMs={a.evaluationLatencyMs} />
          </div>
        ))}
      </Card>
    </>
  );
}

/* ---------------------------------------------------------------
   MAIN APP
   All data manipulation and AI orchestration now happens in FastAPI.
   This component only fetches, displays, and triggers actions.
----------------------------------------------------------------*/
export default function ContractRenewalPOC() {
  const [contracts, setContracts] = useState([]);
  const [trace, setTrace] = useState([]);
  const [metrics, setMetrics] = useState({ firstPassRate: 0, avgRetries: "0.00", escalationRate: 0, avgLatency: 0, totalCost: 0, responseRate: null, totalRuns: 0 });
  const [campaignSummary, setCampaignSummary] = useState({});
  const [tab, setTab] = useState("dashboard");
  const [bucketFilter, setBucketFilter] = useState(null);
  const [selected, setSelected] = useState(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [apiError, setApiError] = useState(null);
  const [loaded, setLoaded] = useState(false);

  const refreshAll = useCallback(async () => {
    try {
      const [c, t, m, cs] = await Promise.all([
        api.getContracts(), api.getTrace(), api.getMetrics(), api.getCampaigns(),
      ]);
      setContracts(c); setTrace(t); setMetrics(m); setCampaignSummary(cs);
    } catch (e) {
      setApiError(String(e.message || e));
    }
  }, []);

  // Initial load — also check whether a batch is already running server-side
  // (e.g. the page was refreshed mid-run) and resume polling if so, instead
  // of losing track of it.
  React.useEffect(() => {
    (async () => {
      await refreshAll();
      try {
        const status = await api.getBatchStatus();
        if (status.running) {
          setProgress({ done: status.done, total: status.total });
          setRunning(true);
        }
      } catch (e) {
        // non-fatal — just means we can't confirm batch status on load
      }
      setLoaded(true);
    })();
  }, [refreshAll]);

  // While a batch is running, poll status and refresh once it completes.
  React.useEffect(() => {
    if (!running) return;
    const interval = setInterval(async () => {
      try {
        const status = await api.getBatchStatus();
        setProgress({ done: status.done, total: status.total });
        if (status.lastError) setApiError(status.lastError);
        if (!status.running) {
          setRunning(false);
          await refreshAll();
        }
      } catch (e) {
        setApiError(String(e.message || e));
        setRunning(false);
      }
    }, 700);
    return () => clearInterval(interval);
  }, [running, refreshAll]);

  const traceByContract = useMemo(() => {
    const m = {};
    trace.forEach((t) => { m[t.contractId] = t; });
    return m;
  }, [trace]);

  const dueContracts = useMemo(
    () => contracts.filter((c) => DUE_BUCKETS.includes(c.bucket) && c.lastMilestoneProcessed !== c.bucket),
    [contracts]
  );

  const runBatch = useCallback(async () => {
    setApiError(null);
    setRunning(true); // disable the button immediately, before the network round-trip completes
    try {
      const res = await api.runBatch();
      setProgress({ done: 0, total: res.due });
    } catch (e) {
      const message = String(e.message || e);
      if (message.toLowerCase().includes("already running")) {
        // Someone else (or a prior click before this one landed) already
        // started a batch — stay in the running state, the poll effect
        // above will pick up real progress and clear it when done.
      } else {
        setApiError(message);
        setRunning(false);
      }
    }
  }, []);

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

  const selectedContract = contracts.find((c) => c.contractId === selected);
  const portfolioContracts = selectedContract
    ? contracts.filter((c) => c.customerId === selectedContract.customerId).sort((a, b) => b.riskScore - a.riskScore)
    : [];
  const selectedTrace = selected ? traceByContract[selected] : null;

  const setOutcome = async (contractId, outcome, note) => {
    // Optimistic local update so the UI feels immediate...
    setTrace((prev) => prev.map((r) => (r.contractId === contractId && r === traceByContract[contractId]
      ? { ...r, outcome, outcomeNote: note ?? r.outcomeNote } : r)));
    // ...then persist to the backend, which is the source of truth.
    try {
      await api.sendFeedback(contractId, outcome, note);
      const [t, m, cs] = await Promise.all([api.getTrace(), api.getMetrics(), api.getCampaigns()]);
      setTrace(t); setMetrics(m); setCampaignSummary(cs);
    } catch (e) {
      setApiError(String(e.message || e));
    }
  };

  const toggleActionStatus = async (contractId, current) => {
    const next = current === "Action done" ? "Action required" : "Action done";
    setTrace((prev) => prev.map((r) => (r.contractId === contractId && r === traceByContract[contractId]
      ? { ...r, actionStatus: next } : r)));
    try {
      await api.setActionStatus(contractId, next);
    } catch (e) {
      setApiError(String(e.message || e));
    }
  };

  if (!loaded) {
    return (
      <div style={{ fontFamily: "-apple-system, sans-serif", background: T.bg, color: T.inkMuted, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>
        Loading contract data from the backend...
      </div>
    );
  }


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
          <div style={{ fontSize: 11.5, color: T.inkFaint, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>Climate Solutions Transportation - Proof of Concept</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: "2px 0 0" }}>Proactive Contract Renewal</h1>
          <div style={{ fontSize: 12.5, color: T.inkMuted, marginTop: 3 }}>Simulated data - in-memory only - NATT · ETT · APAC TT</div>
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
            {running ? `Running ${progress.done}/${progress.total}...` : `Run daily batch (${dueContracts.length} due)`}
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
              <div style={{ fontSize: 12, color: T.inkMuted, marginBottom: 10 }}>Contract value ($) vs. Months on book - colored by risk tier</div>
              <ResponsiveContainer width="100%" height={280}>
                <ScatterChart margin={{ top: 6, right: 12, bottom: 11, left: 0 }}>
                  <CartesianGrid stroke={T.border} strokeDasharray="3 3" />
                  <XAxis type="number" dataKey="x" name="Months on book" stroke={T.inkFaint} tick={{ fontSize: 11 }}>
                    <Label value="Months on book" offset={-10} position="insideBottom" style={{ fontSize: 12, fill: T.inkFaint }} />
                  </XAxis>
                  <YAxis type="number" dataKey="y" name="Contract value ($)" stroke={T.inkFaint} tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Math.round(v / 1000)}k`}>
                    <Label value="Contract value ($)" angle={-90} position="insideLeft" style={{ textAnchor: 'middle', fontSize: 12, fill: T.inkFaint }} />
                  </YAxis>
                  <ZAxis range={[55, 55]} />
                  <Tooltip
                    cursor={{ strokeDasharray: "3 3" }}
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: `1px solid ${T.border}` }}
                    labelFormatter={() => ""}
                    formatter={(value, key, item) => item.dataKey === "y" ? [`$${value.toLocaleString()}`, "Contract value"] : [value, "Months on book"]}
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
                <StatBlock label="Campaign response rate" value={metrics.responseRate !== null ? `${metrics.responseRate}%` : "-"} sub="of logged outcomes" />
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
              <div style={{ fontSize: 13.5, fontWeight: 700 }}>Worklist {bucketFilter ? `- ${BUCKET_LABEL[bucketFilter]}` : ""}</div>
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
                        <td><Badge text={`${c.riskTier} · ${c.riskScore}`} color={RISK_TIER_COLOR[c.riskTier]} bg={c.riskTier === "High" ? T.riskBg : c.riskTier === "Medium" ? T.amberBg : T.safeBg} /></td>
                        <td>${c.contractValue.toLocaleString()}</td>
                        <td>
                          {!t && <span style={{ color: T.inkFaint, fontSize: 12 }}>Not run</span>}
                          {t && t.error && <span title={t.errorMessage}><Badge text="Error" color={T.risk} bg={T.riskBg} /></span>}
                          {t && !t.error && t.pass && !t.escalated && <Badge text="Recommended" color={T.safe} bg={T.safeBg} />}
                          {t && !t.error && t.escalated && (
                            <span style={{ display: "flex", gap: 5, alignItems: "center" }}>
                              <Badge text="Escalated" color={T.risk} bg={T.riskBg} />
                              {t.actionStatus === "Action required"
                                ? <Badge text="Action required" color={T.amber} bg={T.amberBg} />
                                : <Badge text="Done" color={T.safe} bg={T.safeBg} />}
                            </span>
                          )}
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
            <Card><StatBlock label="Avg. retries" value={metrics.avgRetries} sub={`limit ${2}`} /></Card>
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
                      <td style={{ color: T.inkMuted }}>{r.recommendation?.campaign || "-"}</td>
                      <td>{r.retryCount}</td>
                      <td>
                        {r.error
                          ? <span title={r.errorMessage}><Badge text="Error" color={T.risk} bg={T.riskBg} /></span>
                          : r.escalated
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
                    <tr><td colSpan={7} style={{ textAlign: "center", color: T.inkFaint, padding: 24 }}>No runs yet - run the daily batch from the Dashboard tab.</td></tr>
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
                  Response rate {responseRate}% · Engagement rate {engagedRate}%
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Detail drawer */}
      {selectedContract && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(22,27,34,0.35)", display: "flex", justifyContent: "flex-end", zIndex: 50 }} onClick={() => setSelected(null)}>
          <div style={{ width: "50%", minWidth: 460, maxWidth: "94vw", background: T.surface, height: "100%", overflowY: "auto", padding: 22, boxShadow: "-8px 0 24px rgba(0,0,0,0.12)" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 4 }}>
              <div>
                <div style={{ fontSize: 11.5, color: T.inkFaint, fontWeight: 600 }}>{selectedContract.contractId} · {selectedContract.region}</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{selectedContract.customerName}</div>
              </div>
              <button onClick={() => setSelected(null)} style={{ border: "none", background: "none", cursor: "pointer" }}><X size={18} color={T.inkFaint} /></button>
            </div>

            <div style={{ display: "flex", gap: 8, margin: "10px 0 16px", flexWrap: "wrap" }}>
              <Badge text={selectedContract.channel} color={T.info} bg={T.infoBg} />
              <Badge text={BUCKET_LABEL[selectedContract.bucket]} color={T.inkMuted} bg={T.surfaceSunken} />
              <Badge text={`${selectedContract.riskTier} risk · ${selectedContract.riskScore}`} color={RISK_TIER_COLOR[selectedContract.riskTier]} bg={selectedContract.riskTier === "High" ? T.riskBg : selectedContract.riskTier === "Medium" ? T.amberBg : T.safeBg} />
            </div>

            {portfolioContracts.length > 1 && (
              <>
                <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>
                  Customer portfolio - {portfolioContracts.length} contracts
                </div>
                <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
                  <table>
                    <thead><tr><th>Contract</th><th>Bucket</th><th>Value</th><th>Risk</th></tr></thead>
                    <tbody>
                      {portfolioContracts.map((pc) => (
                        <tr
                          key={pc.contractId}
                          className="rowhover"
                          style={{ cursor: "pointer", background: pc.contractId === selectedContract.contractId ? T.surfaceSunken : "transparent" }}
                          onClick={() => setSelected(pc.contractId)}
                        >
                          <td style={{ fontWeight: pc.contractId === selectedContract.contractId ? 700 : 500 }}>{pc.contractId}</td>
                          <td><Badge text={BUCKET_LABEL[pc.bucket]} color={T.inkMuted} bg={T.surfaceSunken} /></td>
                          <td>${pc.contractValue.toLocaleString()}</td>
                          <td><Badge text={pc.riskTier} color={RISK_TIER_COLOR[pc.riskTier]} bg={pc.riskTier === "High" ? T.riskBg : pc.riskTier === "Medium" ? T.amberBg : T.safeBg} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              </>
            )}

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

            {selectedTrace && selectedTrace.error && (
              <Card style={{ padding: 14, marginBottom: 14, background: T.riskBg, borderColor: T.risk }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <AlertTriangle size={15} color={T.risk} />
                  <span style={{ fontSize: 13, fontWeight: 700, color: T.risk }}>Run failed</span>
                </div>
                <div style={{ fontSize: 12.5, color: T.ink }}>{selectedTrace.errorMessage}</div>
                <div style={{ fontSize: 11.5, color: T.inkMuted, marginTop: 8 }}>This contract-milestone was not marked as processed - it will be retried on the next batch run.</div>
              </Card>
            )}

            {selectedTrace && !selectedTrace.error && (
              <>
                <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Recommendation</div>
                <Card style={{ padding: 14, marginBottom: 14, background: T.surfaceSunken }}>
                  <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{selectedTrace.recommendation?.campaign}</div>
                  <div style={{ fontSize: 12.5, color: T.inkMuted, marginBottom: 8 }}>Owner: {selectedTrace.recommendation?.execution_owner}</div>
                  <div style={{ fontSize: 13 }}>{selectedTrace.recommendation?.rationale}</div>
                </Card>

                <DraftContent content={selectedTrace.content} contentError={selectedTrace.contentError} />

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

                <EscalationPanel record={selectedTrace} onToggleAction={toggleActionStatus} />

                <AgentInspector attempts={selectedTrace.attempts} />

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
                    placeholder="Optional rep note..."
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
