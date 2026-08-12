import React, { useState, useMemo, useCallback, useRef, useEffect } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine, ReferenceArea,
  BarChart, Bar, Legend, LabelList
} from "recharts";
import { Play, X, ChevronRight, ChevronDown, AlertTriangle, CheckCircle2, RotateCcw, Clock, Coins, Send, Maximize2, Minimize2 } from "lucide-react";
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
  // "info" is rebranded to Carrier's officially documented brand blue
  // (PMS 072C / #142C73, per Carrier's 2013 Brand Identity Guidelines) —
  // this naturally carries through to every channel badge, link, and
  // active-state surface that already used this token.
  info: "#142C73",
  infoBg: "#E6E9F2",
  amber: "#B8863B",
  amberBg: "#F7EFE1",
  purple: "#5A4FB0",
  purpleBg: "#EEECFA",
  // Explicit brand tokens for primary chrome (buttons, active tab, header
  // accent) — same Carrier Blue, kept separate from "info" for clarity
  // about which usages are brand-driven vs. semantic.
  brand: "#142C73",
  brandBg: "#E6E9F2",
  brandLight: "#4A63A8",
};

// Mirrors the "score >= 50" split named in the risk x value segmentation
// (illustrative, per project assumptions — not read from rules.py, which
// isn't exposed over the API). If that threshold changes server-side, this
// line and the backend's segment cutoff will drift apart.
const RISK_QUADRANT_THRESHOLD = 50;
// Red / Yellow / Green / Blue per the risk x value quadrant scheme. Reuses
// existing theme tokens (T.info/T.infoBg — already the app's blue, used for
// "Medium" priority elsewhere) rather than introducing new colors. Changing
// this one map recolors every segment badge in the app, not just the scatter.
const SEGMENT_COLOR = { "High Risk": T.risk, "At Risk": T.amber, "Healthy": T.safe, "Standard": T.info };
const SEGMENT_BG = { "High Risk": T.riskBg, "At Risk": T.amberBg, "Healthy": T.safeBg, "Standard": T.infoBg };
const BUCKETS = [">90", "90", "60", "45", "30", "10", "Lost"];
const BUCKET_LABEL = {
  ">90": "Not yet due", "90": "Expiring in \u226490 days", "60": "Expiring in \u226460 days",
  "45": "Expiring in \u226445 days", "30": "Expiring in \u226430 days", "10": "Expiring in \u226410 days", "Lost": "Lost / expired",
};
// Fuller sentence for a hover tooltip on the bucket cards specifically —
// BUCKET_LABEL above stays short since it's also reused in tight spaces
// (table cells, pill badges) where a full sentence wouldn't fit.
const BUCKET_TOOLTIP = {
  ">90": "More than 90 days remain before this contract enters its renewal window.",
  "90": "Contract is about to expire in \u226490 days.",
  "60": "Contract is about to expire in \u226460 days.",
  "45": "Contract is about to expire in \u226445 days.",
  "30": "Contract is about to expire in \u226430 days.",
  "10": "Contract is about to expire in \u226410 days.",
  "Lost": "Contract has already expired or been lost.",
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

// Display-only — must stay in sync with MAX_RETRIES in backend/agents.py,
// which is what actually enforces the retry limit. This constant only
// labels the "limit N" sub-text on the Trace tab's Avg. retries stat.
const MAX_RETRIES = 2;
/* ---------------------------------------------------------------
   SMALL UI PRIMITIVES
----------------------------------------------------------------*/
function Card({ children, style, onClick, className, title }) {
  return (
    <div
      onClick={onClick}
      className={className}
      title={title}
      style={{
        background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10,
        padding: 16, cursor: onClick ? "pointer" : "default", ...style,
      }}
    >
      {children}
    </div>
  );
}

function MultiSelect({ label, options, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const toggle = (val) => {
    onChange(selected.includes(val) ? selected.filter((v) => v !== val) : [...selected, val]);
  };

  const buttonLabel = selected.length === 0 ? `All ${label}` : `${selected.length} of ${options.length} ${label}`;
  const isFiltered = selected.length > 0;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 600,
          border: `1px solid ${isFiltered ? T.brand : T.border}`, background: isFiltered ? T.brandBg : T.surface,
          color: isFiltered ? T.brand : T.inkMuted, borderRadius: 7, padding: "7px 10px", cursor: "pointer",
        }}
      >
        {buttonLabel}
        <ChevronDown size={13} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.12s" }} />
      </button>
      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, background: T.surface, border: `1px solid ${T.border}`,
          borderRadius: 8, boxShadow: "0 6px 18px rgba(22,27,34,0.12)", padding: 6, zIndex: 30, minWidth: 170,
        }}>
          {options.map((opt) => (
            <label key={opt.value} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", fontSize: 12.5, cursor: "pointer", borderRadius: 5 }}>
              <input type="checkbox" checked={selected.includes(opt.value)} onChange={() => toggle(opt.value)} />
              {opt.label}
            </label>
          ))}
          {isFiltered && (
            <button
              onClick={() => onChange([])}
              style={{ width: "100%", marginTop: 4, border: "none", background: "none", color: T.info, fontSize: 11.5, fontWeight: 600, cursor: "pointer", padding: "5px 8px", textAlign: "left" }}
            >
              Clear
            </button>
          )}
        </div>
      )}
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

// Table sorting — one hook + one comparator + one header cell, reused by
// every sortable table instead of bespoke sort state/logic per table.
function useSort(defaultKey = null, defaultDir = "asc") {
  const [sortKey, setSortKey] = useState(defaultKey);
  const [sortDir, setSortDir] = useState(defaultDir);
  const toggleSort = (key) => {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };
  return [sortKey, sortDir, toggleSort];
}

// accessors: { [sortKey]: (row) => comparable value }. Returns rows
// unchanged if sortKey is null/unrecognized, so a table's existing default
// order (whatever was passed in) is the fallback until a header is clicked.
function sortRows(rows, sortKey, sortDir, accessors) {
  const getValue = sortKey && accessors[sortKey];
  if (!getValue) return rows;
  const sorted = [...rows].sort((a, b) => {
    const va = getValue(a), vb = getValue(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "string") return va.localeCompare(vb);
    return va - vb;
  });
  return sortDir === "desc" ? sorted.reverse() : sorted;
}

// sort: the [sortKey, sortDir, toggleSort] tuple from useSort(). sortKey
// prop here is which column this header controls, not the current sort state.
function SortTh({ label, sortKey, sort, style }) {
  const [activeKey, dir, toggleSort] = sort;
  const active = activeKey === sortKey;
  return (
    <th onClick={() => toggleSort(sortKey)} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap", ...style }}>
      {label}
      <ChevronDown
        size={11}
        style={{
          marginLeft: 3, verticalAlign: "middle", opacity: active ? 0.9 : 0.25,
          transform: active && dir === "asc" ? "rotate(180deg)" : "none",
        }}
      />
    </th>
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

function DraftContent({ content, contentError, customerName }) {
  if (contentError) {
    return (
      <>
        <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Draft content (AI Generated)</div>
        <Card style={{ padding: 14, marginBottom: 14, background: T.surfaceSunken }}>
          <div style={{ fontSize: 12.5, color: T.inkFaint }}>Draft generation failed: {contentError}</div>
        </Card>
      </>
    );
  }
  if (!content) return null;

  // No real recipient email exists anywhere in this POC's data model (synthetic
  // customers and dealers have no email field) — a slugified customer name at
  // a placeholder domain stands in for it, per the agreed placeholder convention.
  // This opens whatever the browser/OS has set as the default mail handler
  // (Outlook, if that's the user's default) with the fields below prefilled.
  const recipientEmail = `${(customerName || "customer").toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.+|\.+$/g, "")}@client.com`;
  // Outlook Web's own compose URL, not mailto: — mailto: always goes through
  // whatever the OS has registered as the default mail handler (the desktop
  // app, if one's installed), and there's no browser-level way to redirect
  // that to a specific webmail provider instead. This is Outlook-specific;
  // it does nothing useful if the rep doesn't use Outlook/Microsoft 365.
  const sendEmail = () => {
    const to = encodeURIComponent(recipientEmail);
    const subject = encodeURIComponent(content.email_subject);
    const body = encodeURIComponent(content.email_body);
    window.open(`https://outlook.cloud.microsoft/mail/deeplink/compose?to=${to}&subject=${subject}&body=${body}`, "_blank");
  };

  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Draft content (AI Generated)</div>
      <Card style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ fontSize: 12.5, color: T.inkMuted, marginBottom: 10, lineHeight: 1.5 }}>{content.summary}</div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <Badge text={`Addressed to: ${content.recipient_role}`} color={T.info} bg={T.infoBg} />
          <button
            onClick={sendEmail}
            style={{
              display: "flex", alignItems: "center", gap: 5, border: `1px solid ${T.border}`, background: "#fff",
              color: T.inkMuted, borderRadius: 6, padding: "5px 9px", fontSize: 11.5, fontWeight: 600, cursor: "pointer",
            }}
          >
            <Send size={13} />
            Send email
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
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Escalation — human review needed</div>
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


const PRIORITY_COLOR = { Critical: T.risk, High: T.amber, Medium: T.info, Low: T.inkFaint };
const PRIORITY_BG = { Critical: T.riskBg, High: T.amberBg, Medium: T.infoBg, Low: T.surfaceSunken };

function ServiceTicketHistory({ equipment, tickets }) {
  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>
        Historical service tickets
      </div>
      <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
        <div style={{ padding: "9px 14px", borderBottom: `1px solid ${T.border}`, fontSize: 11.5, color: T.inkMuted, background: T.surfaceSunken }}>
          {equipment.type} · {equipment.count} unit{equipment.count === 1 ? "" : "s"} · avg {equipment.avgAgeYears}y old
          {equipment.critical && <span style={{ color: T.risk, fontWeight: 600 }}> · Critical equipment</span>}
        </div>
        {(!tickets || tickets.length === 0) ? (
          <div style={{ padding: 16, fontSize: 12, color: T.inkFaint }}>No service tickets on record.</div>
        ) : (
          <div style={{ maxHeight: 240, overflowY: "auto" }}>
            {tickets.map((t, i) => (
              <div
                key={i}
                style={{
                  display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
                  borderBottom: i < tickets.length - 1 ? `1px solid ${T.border}` : "none", fontSize: 12.5,
                }}
              >
                <div style={{ width: 78, flexShrink: 0, fontFamily: "ui-monospace, monospace", fontSize: 11, color: T.inkFaint }}>{t.date}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>{t.issue}</span>
                  <span style={{ color: T.inkMuted }}>
                    {" "}· {t.type}{t.slaMet === false ? " · SLA missed" : ""} · {t.resolutionHours}h resolution
                  </span>
                </div>
                <Badge text={t.priority} color={PRIORITY_COLOR[t.priority] || T.inkMuted} bg={PRIORITY_BG[t.priority] || T.surfaceSunken} />
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

const SENTIMENT_COLOR = { Positive: T.safe, Neutral: T.inkFaint, Negative: T.risk };
const SENTIMENT_BG = { Positive: T.safeBg, Neutral: T.surfaceSunken, Negative: T.riskBg };
const TREND_COLOR = { Improving: T.safe, Stable: T.inkFaint, Declining: T.risk };
const TREND_BG = { Improving: T.safeBg, Stable: T.surfaceSunken, Declining: T.riskBg };

function CustomerFeedbackPanel({ feedback, trend }) {
  const [view, setView] = useState("recent");
  const recent = feedback?.recent12Months || [];
  const historical = feedback?.historical || [];
  const entries = view === "recent" ? recent : historical;

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint }}>
          Customer feedback
        </div>
        <Badge text={trend} color={TREND_COLOR[trend] || T.inkFaint} bg={TREND_BG[trend] || T.surfaceSunken} />
      </div>
      <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
        <div style={{ display: "flex", borderBottom: `1px solid ${T.border}` }}>
          <button
            onClick={() => setView("recent")}
            style={{
              flex: 1, padding: "8px 10px", border: "none", cursor: "pointer", fontSize: 11.5, fontWeight: 600,
              background: view === "recent" ? T.surfaceSunken : T.surface, color: view === "recent" ? T.ink : T.inkMuted,
            }}
          >
            Recent 12 months ({recent.length})
          </button>
          <button
            onClick={() => setView("historical")}
            style={{
              flex: 1, padding: "8px 10px", border: "none", cursor: "pointer", fontSize: 11.5, fontWeight: 600,
              background: view === "historical" ? T.surfaceSunken : T.surface, color: view === "historical" ? T.ink : T.inkMuted,
              borderLeft: `1px solid ${T.border}`,
            }}
          >
            Historical ({historical.length})
          </button>
        </div>
        {entries.length === 0 ? (
          <div style={{ padding: 16, fontSize: 12, color: T.inkFaint }}>
            No {view === "recent" ? "recent" : "historical"} feedback on record.
          </div>
        ) : (
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            {entries.map((e, i) => (
              <div key={i} style={{ padding: "9px 14px", borderBottom: i < entries.length - 1 ? `1px solid ${T.border}` : "none" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                  <Badge text={e.sentiment} color={SENTIMENT_COLOR[e.sentiment] || T.inkFaint} bg={SENTIMENT_BG[e.sentiment] || T.surfaceSunken} />
                  <span style={{ fontSize: 11, color: T.inkFaint }}>{e.category} · {e.source}</span>
                  <span style={{ fontSize: 10.5, color: T.inkFaint, marginLeft: "auto", fontFamily: "ui-monospace, monospace" }}>{e.date}</span>
                </div>
                <div style={{ fontSize: 12.5, color: T.ink, fontStyle: "italic" }}>&ldquo;{e.comment}&rdquo;</div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

function RiskFactorBreakdown({ factors }) {
  if (!factors) return null;
  const entries = Object.entries(factors).sort((a, b) => b[1] - a[1]);
  const FACTOR_MAX = { "SLA breaches": 20, "Emergency ticket ratio": 12, "Repeat issues": 10, "PM completion rate": 15, "Late payments": 15, "Outstanding balance": 6, "Competitor bid": 15, "NPS score": 15, "Portal engagement": 8, "Last price increase": 6, "Exec touchpoint gap": 8 };
  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>
        Risk driver features
      </div>
      <Card style={{ padding: 14, marginBottom: 18 }}>
        {entries.map(([label, value]) => {
          const max = FACTOR_MAX[label] || 20;
          const pct = Math.min(100, Math.round((value / max) * 100));
          const color = pct >= 60 ? T.risk : pct >= 30 ? T.amber : T.safe;
          return (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 10, padding: "4px 0" }}>
              <div style={{ width: 140, fontSize: 11.5, color: T.inkMuted, flexShrink: 0 }}>{label}</div>
              <div style={{ flex: 1, height: 6, background: T.surfaceSunken, borderRadius: 3, overflow: "hidden" }}>
                <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
              </div>
              <div style={{ width: 44, textAlign: "right", fontSize: 11, fontFamily: "ui-monospace, monospace", color: T.inkMuted, flexShrink: 0 }}>{value}/{max}</div>
            </div>
          );
        })}
      </Card>
    </>
  );
}

function CachedAgentCard({ title, record, onGenerate, loadingLabel, placeholderLabel }) {
  const status = record?.status;
  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>{title}</div>
      <Card style={{ padding: 14, marginBottom: 18, background: status === "done" ? T.surfaceSunken : T.surface }}>
        {status === "done" && (
          <>
            <div style={{ fontSize: 13, lineHeight: 1.5 }}>{record.data}</div>
            <button onClick={onGenerate} style={{ marginTop: 10, border: "none", background: "none", color: T.info, fontSize: 11.5, fontWeight: 600, cursor: "pointer", padding: 0 }}>Regenerate</button>
          </>
        )}
        {status === "loading" && <div style={{ fontSize: 12.5, color: T.inkFaint }}>{loadingLabel}…</div>}
        {status === "error" && (
          <>
            <div style={{ fontSize: 12.5, color: T.risk, marginBottom: 8 }}>{record.error}</div>
            <button onClick={onGenerate} style={{ border: `1px solid ${T.border}`, background: "#fff", borderRadius: 6, padding: "5px 10px", fontSize: 11.5, fontWeight: 600, cursor: "pointer" }}>Retry</button>
          </>
        )}
        {(!status) && (
          <>
            <div style={{ fontSize: 12.5, color: T.inkFaint, marginBottom: 10 }}>{placeholderLabel}</div>
            <button onClick={onGenerate} style={{ border: "none", background: T.ink, color: "#fff", borderRadius: 6, padding: "6px 12px", fontSize: 11.5, fontWeight: 600, cursor: "pointer" }}>Generate</button>
          </>
        )}
      </Card>
    </>
  );
}

function SegmentBar({ counts }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  const order = ["High Risk", "At Risk", "Healthy", "Standard"];
  return (
    <div>
      <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden" }}>
        {order.map((seg) => {
          const pct = (counts[seg] / total) * 100;
          if (pct === 0) return null;
          return <div key={seg} title={`${seg}: ${counts[seg]}`} style={{ width: `${pct}%`, background: SEGMENT_COLOR[seg] }} />;
        })}
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 6, flexWrap: "wrap" }}>
        {order.map((seg) => (
          <span key={seg} style={{ fontSize: 10.5, color: T.inkMuted, display: "flex", alignItems: "center", gap: 3 }}>
            <span style={{ width: 6, height: 6, borderRadius: 2, background: SEGMENT_COLOR[seg], display: "inline-block" }} />
            {seg} {counts[seg]}
          </span>
        ))}
      </div>
    </div>
  );
}

// Rolls a list of contracts up into the KPIs the Dashboard/Renewal Prioritization
// tabs show — one definition of these $ metrics, not one per call site.
// - "Potential $ at risk" = still-open contract, outreach sent, No response
//   logged — uncertain, not yet a confirmed loss.
// - "Lost revenue $" = either the contract already churned (bucket ===
//   "Lost") or the customer explicitly Declined — both are a realized loss,
//   not a maybe. Bucket-Lost contracts never get a trace outcome at all
//   (due_contracts() excludes the "Lost" bucket from the batch pipeline
//   entirely), so this can't be computed from outcome alone or every Lost
//   contract would show $0 here.
// - "Converted $" (field: potentialRevenueValue) = contacted and Engaged.
function aggregateBookMetrics(list, traceByContract) {
  const customerIds = new Set();
  const segmentCounts = { "High Risk": 0, "At Risk": 0, "Healthy": 0, "Standard": 0 };
  let totalValue = 0, totalMargin = 0, potentialAtRiskValue = 0, lostRevenueValue = 0, lostCount = 0, potentialRevenueValue = 0;
  list.forEach((c) => {
    customerIds.add(c.customerId);
    totalValue += c.contractValue;
    totalMargin += c.margin;
    segmentCounts[c.segment] = (segmentCounts[c.segment] || 0) + 1;
    const outcome = traceByContract[c.contractId]?.outcome;
    if (c.bucket === "Lost" || outcome === "Declined") {
      lostRevenueValue += c.contractValue;
      lostCount += 1;
    } else if (outcome === "No response") {
      potentialAtRiskValue += c.contractValue;
    } else if (outcome === "Engaged") {
      potentialRevenueValue += c.contractValue;
    }
  });
  return { customerCount: customerIds.size, contractCount: list.length, totalValue, totalMargin, potentialAtRiskValue, lostRevenueValue, lostCount, potentialRevenueValue, segmentCounts };
}

// Same shape/logic as the backend's /api/campaigns (assigned/engaged/
// declined/noResponse per taxonomy name), plus the $ metrics, computed
// over whatever trace subset the Dashboard filters produce. No bucket-Lost
// case here — a trace record's milestone can never be "Lost" (same reason
// as above), so Declined is the only source of lost revenue at this level.
function aggregateCampaigns(traceList) {
  const result = {};
  CAMPAIGN_TAXONOMY.forEach((t) => { result[t.name] = { assigned: 0, assignedValue: 0, engaged: 0, declined: 0, noResponse: 0, potentialAtRiskValue: 0, lostRevenueValue: 0, potentialRevenueValue: 0 }; });
  traceList.forEach((r) => {
    const name = (r.recommendation || {}).campaign;
    const entry = result[name];
    if (!entry) return;
    entry.assigned += 1;
    const value = (r.context || {}).contract_value_usd || 0;
    entry.assignedValue += value;
    if (r.outcome === "Engaged") { entry.engaged += 1; entry.potentialRevenueValue += value; }
    else if (r.outcome === "Declined") { entry.declined += 1; entry.lostRevenueValue += value; }
    else if (r.outcome === "No response") { entry.noResponse += 1; entry.potentialAtRiskValue += value; }
  });
  return result;
}


function OutcomeBucketChart({ data }) {
  const max = Math.max(...data.engaged, ...data.notEngaged, 1);
  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 10, height: 100 }}>
        {data.buckets.map((label, i) => {
          const engagedH = Math.round((data.engaged[i] / max) * 76);
          const notEngagedH = Math.round((data.notEngaged[i] / max) * 76);
          return (
            <div key={label} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 76 }}>
                <div title={`${data.engaged[i]} engaged`} style={{ width: 9, height: Math.max(2, engagedH), background: T.safe, borderRadius: "2px 2px 0 0" }} />
                <div title={`${data.notEngaged[i]} declined/no response`} style={{ width: 9, height: Math.max(2, notEngagedH), background: T.risk, borderRadius: "2px 2px 0 0" }} />
              </div>
              <div style={{ fontSize: 10, color: T.inkFaint, fontFamily: "ui-monospace, monospace" }}>{label}</div>
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 12, marginTop: 10, fontSize: 11 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 4, color: T.inkMuted }}><span style={{ width: 7, height: 7, borderRadius: 2, background: T.safe, display: "inline-block" }} />Engaged</span>
        <span style={{ display: "flex", alignItems: "center", gap: 4, color: T.inkMuted }}><span style={{ width: 7, height: 7, borderRadius: 2, background: T.risk, display: "inline-block" }} />Declined / no response</span>
        <span style={{ color: T.inkFaint, marginLeft: "auto" }}>Responses={data.totalWithOutcome}</span>
      </div>

      {/* $ lost (Declined only) and $ converted (Engaged) per risk bucket —
          same figures as the campaign table's Lost revenue $ / Potential
          revenue $, just broken down by risk score instead of by campaign. */}
      <div style={{ borderTop: `1px solid ${T.border}`, marginTop: 10, paddingTop: 8 }}>
        <table>
          <thead>
            <tr>
              <th style={{ fontSize: 10 }}>Risk score</th>
              <th style={{ fontSize: 10, color: T.risk }}>$ Lost</th>
              <th style={{ fontSize: 10, color: T.safe }}>$ Converted</th>
            </tr>
          </thead>
          <tbody>
            {data.buckets.map((label, i) => (
              <tr key={label}>
                <td style={{ fontSize: 11, fontFamily: "ui-monospace, monospace" }}>{label}</td>
                <td style={{ fontSize: 11, color: data.revenueLost[i] > 0 ? T.risk : T.inkMuted }}>${data.revenueLost[i].toLocaleString()}</td>
                <td style={{ fontSize: 11, color: data.revenueConverted[i] > 0 ? T.safe : T.inkMuted }}>${data.revenueConverted[i].toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
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
  const [modelInfo, setModelInfo] = useState(null);
  const [ticketSummaries, setTicketSummaries] = useState({});
  const [customerSummaries, setCustomerSummaries] = useState({});
  const [tab, setTab] = useState("renewal-prioritization");
  const [bucketFilter, setBucketFilter] = useState(null);
  const [regionFilter, setRegionFilter] = useState([]); // empty = all regions
  const [channelFilter, setChannelFilter] = useState([]); // empty = all channels
  // Dashboard tab's own Global -> Regional -> Segment -> Milestone drill-down.
  // Kept separate from the Renewal Prioritization filters above — the two tabs
  // answer different questions, so filtering one shouldn't silently filter
  // the other when you switch tabs.
  const [dashRegionFilter, setDashRegionFilter] = useState([]);
  const [dashChannelFilter, setDashChannelFilter] = useState([]);
  const [dashSegmentFilter, setDashSegmentFilter] = useState([]);
  const [dashBucketFilter, setDashBucketFilter] = useState(null);
  const [selected, setSelected] = useState(null);
  // Which chart, if any, is currently blown up into the full-screen modal.
  const [maximizedChart, setMaximizedChart] = useState(null); // "scatter" | "campaign-bar" | null
  // Which tab is active inside the contract detail drawer — reset to the
  // default whenever a different contract is opened, so switching contracts
  // doesn't leave you stranded on a tab that made sense for the last one.
  const [drawerTab, setDrawerTab] = useState("action");
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [apiError, setApiError] = useState(null);
  const [loaded, setLoaded] = useState(false);

  const refreshAll = useCallback(async () => {
    try {
      const [c, t, m, mi, ts, cust] = await Promise.all([
        api.getContracts(), api.getTrace(), api.getMetrics(),
        api.getModelInfo(), api.getTicketSummaries(), api.getCustomerSummaries(),
      ]);
      setContracts(c); setTrace(t); setMetrics(m);
      setModelInfo(mi); setTicketSummaries(ts); setCustomerSummaries(cust);
    } catch (e) {
      setApiError(String(e.message || e));
    }
  }, []);

  const runTicketSummary = async (contractId) => {
    setTicketSummaries((prev) => ({ ...prev, [contractId]: { status: "loading" } }));
    try {
      const record = await api.runTicketSummary(contractId);
      setTicketSummaries((prev) => ({ ...prev, [contractId]: record }));
    } catch (e) {
      setTicketSummaries((prev) => ({ ...prev, [contractId]: { status: "error", error: String(e.message || e) } }));
    }
  };

  const runCustomerSummary = async (customerId) => {
    setCustomerSummaries((prev) => ({ ...prev, [customerId]: { status: "loading" } }));
    try {
      const record = await api.runCustomerSummary(customerId);
      setCustomerSummaries((prev) => ({ ...prev, [customerId]: record }));
    } catch (e) {
      setCustomerSummaries((prev) => ({ ...prev, [customerId]: { status: "error", error: String(e.message || e) } }));
    }
  };

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

  React.useEffect(() => {
    setDrawerTab("action");
  }, [selected]);

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

  // Region/channel only — bucket cards need this unfiltered by bucketFilter
  // itself, otherwise selecting one bucket would zero out every other card.
  const regionChannelContracts = useMemo(() => {
    return contracts.filter((c) => {
      const regionOk = regionFilter.length === 0 || regionFilter.includes(c.region);
      const channelOk = channelFilter.length === 0 || channelFilter.includes(c.channel);
      return regionOk && channelOk;
    });
  }, [contracts, regionFilter, channelFilter]);

  // The actual page-wide filter: region + channel + bucket. Everything that
  // should respect the bucket-card click reads from this, not from
  // regionChannelContracts, so the filter only has to be defined once.
  const filteredContracts = useMemo(() => {
    if (!bucketFilter) return regionChannelContracts;
    return regionChannelContracts.filter((c) => c.bucket === bucketFilter);
  }, [regionChannelContracts, bucketFilter]);

  // Computed client-side (not fetched from /api/outcome-by-risk-bucket) so it
  // stays in sync with the region/channel/bucket filters instantly. Each trace
  // record's own context.region/context.channel/context.milestone is a
  // snapshot taken at the time that recommendation ran, which is what
  // filtering should key off — not the contract's current fields (though
  // those are the same in practice, since none of them change after generation).
  const filteredOutcomeByRiskBucket = useMemo(() => {
    const bucketEdges = [[0, 19], [20, 39], [40, 59], [60, 79], [80, 100]];
    const labels = ["0-19", "20-39", "40-59", "60-79", "80-100"];
    const engaged = [0, 0, 0, 0, 0];
    const notEngaged = [0, 0, 0, 0, 0];
    const revenueLost = [0, 0, 0, 0, 0];       // Declined only — realized loss, not "no response" (uncertain)
    const revenueConverted = [0, 0, 0, 0, 0];  // Engaged
    let totalWithOutcome = 0;

    trace.forEach((r) => {
      if (!r.outcome) return;
      const ctx = r.context || {};
      const regionOk = regionFilter.length === 0 || regionFilter.includes(ctx.region);
      const channelOk = channelFilter.length === 0 || channelFilter.includes(ctx.channel);
      const bucketOk = !bucketFilter || ctx.milestone === bucketFilter;
      if (!regionOk || !channelOk || !bucketOk) return;
      const score = ctx.risk_score;
      if (score == null) return;
      totalWithOutcome++;
      const value = ctx.contract_value_usd || 0;
      for (let i = 0; i < bucketEdges.length; i++) {
        const [lo, hi] = bucketEdges[i];
        if (score >= lo && score <= hi) {
          if (r.outcome === "Engaged") { engaged[i]++; revenueConverted[i] += value; }
          else { notEngaged[i]++; if (r.outcome === "Declined") revenueLost[i] += value; }
          break;
        }
      }
    });

    return { buckets: labels, engaged, notEngaged, revenueLost, revenueConverted, totalWithOutcome };
  }, [trace, regionFilter, channelFilter, bucketFilter]);

  const bucketCounts = useMemo(() => {
    const c = {};
    BUCKETS.forEach((b) => (c[b] = 0));
    regionChannelContracts.forEach((ct) => { c[ct.bucket] = (c[ct.bucket] || 0) + 1; });
    return c;
  }, [regionChannelContracts]);

  // Dashboard tab: region/channel/segment only — bucket cards here need this
  // unfiltered by dashBucketFilter itself, same reasoning as regionChannelContracts above.
  const dashRegionChannelSegmentContracts = useMemo(() => {
    return contracts.filter((c) => {
      const regionOk = dashRegionFilter.length === 0 || dashRegionFilter.includes(c.region);
      const channelOk = dashChannelFilter.length === 0 || dashChannelFilter.includes(c.channel);
      const segmentOk = dashSegmentFilter.length === 0 || dashSegmentFilter.includes(c.segment);
      return regionOk && channelOk && segmentOk;
    });
  }, [contracts, dashRegionFilter, dashChannelFilter, dashSegmentFilter]);

  const dashFilteredContracts = useMemo(() => {
    if (!dashBucketFilter) return dashRegionChannelSegmentContracts;
    return dashRegionChannelSegmentContracts.filter((c) => c.bucket === dashBucketFilter);
  }, [dashRegionChannelSegmentContracts, dashBucketFilter]);

  const dashBucketCounts = useMemo(() => {
    const c = {};
    BUCKETS.forEach((b) => (c[b] = 0));
    dashRegionChannelSegmentContracts.forEach((ct) => { c[ct.bucket] = (c[ct.bucket] || 0) + 1; });
    return c;
  }, [dashRegionChannelSegmentContracts]);

  // Trace-side counterpart for the campaign section — filters on each trace
  // record's own snapshotted context (region/channel/segment/milestone), same
  // design principle already used by filteredOutcomeByRiskBucket above: the
  // snapshot at recommendation time, not the contract's current live fields.
  const dashFilteredTrace = useMemo(() => {
    return trace.filter((r) => {
      const ctx = r.context || {};
      const regionOk = dashRegionFilter.length === 0 || dashRegionFilter.includes(ctx.region);
      const channelOk = dashChannelFilter.length === 0 || dashChannelFilter.includes(ctx.channel);
      const segmentOk = dashSegmentFilter.length === 0 || dashSegmentFilter.includes(ctx.segment);
      const bucketOk = !dashBucketFilter || ctx.milestone === dashBucketFilter;
      return regionOk && channelOk && segmentOk && bucketOk;
    });
  }, [trace, dashRegionFilter, dashChannelFilter, dashSegmentFilter, dashBucketFilter]);

  const dashGlobalMetrics = useMemo(
    () => aggregateBookMetrics(dashFilteredContracts, traceByContract),
    [dashFilteredContracts, traceByContract]
  );

  const dashRegionsToShow = useMemo(
    () => (dashRegionFilter.length === 0 ? REGIONS : REGIONS.filter((r) => dashRegionFilter.includes(r.id))),
    [dashRegionFilter]
  );

  const dashCampaignData = useMemo(() => {
    const agg = aggregateCampaigns(dashFilteredTrace);
    return CAMPAIGN_TAXONOMY.map((t) => {
      const s = agg[t.name];
      const responseCount = s.engaged + s.declined; // customer responded, either way — same definition the old Campaigns tab used
      return {
        name: t.name, Assigned: s.assigned, Engaged: s.engaged, Declined: s.declined, "No response": s.noResponse,
        declined: s.declined, noResponse: s.noResponse, responseCount,
        // Each bar's own rate is that bar's count over Assigned — previously
        // every bar showed the same combined (engaged+declined)/assigned
        // figure, which was only ever correct for one of the three bars.
        engagedRate: s.assigned ? Math.round((s.engaged / s.assigned) * 100) : 0,
        declinedRate: s.assigned ? Math.round((s.declined / s.assigned) * 100) : 0,
        noResponseRate: s.assigned ? Math.round((s.noResponse / s.assigned) * 100) : 0,
        responseRate: s.assigned ? Math.round((responseCount / s.assigned) * 100) : 0,
        assignedValue: s.assignedValue, potentialAtRiskValue: s.potentialAtRiskValue, lostRevenueValue: s.lostRevenueValue, potentialRevenueValue: s.potentialRevenueValue,
      };
    });
  }, [dashFilteredTrace]);

  // Shared by the inline card and the maximize modal — same chart, just a
  // different height, so the two views can't drift apart.
  const renderCampaignBarChart = (height) => (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={dashCampaignData} margin={{ top: 24, right: 12, bottom: 40, left: 0 }}>
        <CartesianGrid stroke={T.border} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="name" stroke={T.inkFaint} tick={{ fontSize: 10.5 }} interval={0} angle={-20} textAnchor="end" height={60} />
        <YAxis stroke={T.inkFaint} tick={{ fontSize: 11 }} allowDecimals={false} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `1px solid ${T.border}` }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="Assigned" fill={T.borderStrong} radius={[4, 4, 0, 0]}>
          <LabelList
            dataKey="Assigned"
            content={({ x, y, width, index }) => {
              const row = dashCampaignData[index];
              if (!row) return null;
              return (
                <text x={x + width / 2} y={y - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill={T.ink}>
                  {row.Assigned}
                </text>
              );
            }}
          />
          </Bar>
        <Bar dataKey="Engaged" fill={T.safe} radius={[4, 4, 0, 0]}>
          <LabelList
            dataKey="Engaged"
            content={({ x, y, width, index }) => {
              const row = dashCampaignData[index];
              if (!row || !row.Engaged) return null;
              return (
                <text x={x + width / 2} y={y - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill={T.ink}>
                  {row.Engaged} ({row.engagedRate}%)
                </text>
              );
            }}
          />
          </Bar>
        <Bar dataKey="Declined" fill={T.risk} radius={[4, 4, 0, 0]}>
          <LabelList
            dataKey="Declined"
            content={({ x, y, width, index }) => {
              const row = dashCampaignData[index];
              if (!row || !row.Declined) return null;
              return (
                <text x={x + width / 2} y={y - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill={T.ink}>
                  {row.Declined} ({row.declinedRate}%)
                </text>
              );
            }}
          />
          </Bar>
        <Bar dataKey="No response" fill={T.amber} radius={[4, 4, 0, 0]}>
          <LabelList
            dataKey="No response"
            content={({ x, y, width, index }) => {
              const row = dashCampaignData[index];
              if (!row || !row.noResponse) return null;
              return (
                <text x={x + width / 2} y={y - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill={T.ink}>
                  {row.noResponse} ({row.noResponseRate}%)
                </text>
              );
            }}
          />
          </Bar>
      </BarChart>
    </ResponsiveContainer>
  );

  // Totals across every campaign, for the summary stat row above the chart —
  // sums dashCampaignData rather than re-deriving from dashFilteredTrace, so
  // there's one pass over the trace data, not two.
  const dashCampaignTotals = useMemo(() => {
    return dashCampaignData.reduce((acc, row) => {
      acc.assigned += row.Assigned;
      acc.engaged += row.Engaged;
      acc.declined += row.declined;
      acc.noResponse += row.noResponse;
      return acc;
    }, { assigned: 0, engaged: 0, declined: 0, noResponse: 0 });
  }, [dashCampaignData]);

  const campaignSort = useSort(null, "asc");
  const [campaignSortKey, campaignSortDir] = campaignSort;
  const sortedCampaignData = useMemo(() => sortRows(dashCampaignData, campaignSortKey, campaignSortDir, {
    name: (r) => r.name,
    Assigned: (r) => r.Assigned,
    Engaged: (r) => r.Engaged,
    declined: (r) => r.declined,
    noResponse: (r) => r.noResponse,
    assignedValue: (r) => r.assignedValue,
    potentialAtRiskValue: (r) => r.potentialAtRiskValue,
    lostRevenueValue: (r) => r.lostRevenueValue,
    potentialRevenueValue: (r) => r.potentialRevenueValue,
  }), [dashCampaignData, campaignSortKey, campaignSortDir]);

  const worklist = useMemo(() => {
    return [...filteredContracts].sort((a, b) => b.riskScore - a.riskScore);
  }, [filteredContracts]);

  const worklistSort = useSort("riskScore", "desc");
  const [worklistSortKey, worklistSortDir] = worklistSort;
  const sortedWorklist = useMemo(() => sortRows(worklist, worklistSortKey, worklistSortDir, {
    customerName: (c) => c.customerName,
    region: (c) => c.region,
    channel: (c) => c.channel,
    bucket: (c) => BUCKETS.indexOf(c.bucket),
    riskScore: (c) => c.riskScore,
    contractValue: (c) => c.contractValue,
    status: (c) => {
      const t = traceByContract[c.contractId];
      if (!t) return 0;
      if (t.error) return 1;
      if (t.escalated) return t.actionStatus === "Action required" ? 2 : 3;
      return t.pass ? 4 : 1;
    },
  }), [worklist, worklistSortKey, worklistSortDir, traceByContract]);

  const traceSort = useSort(null, "asc");
  const [traceSortKey, traceSortDir] = traceSort;
  const sortedTraceRows = useMemo(() => sortRows([...trace].reverse(), traceSortKey, traceSortDir, {
    customer: (r) => contracts.find((c) => c.contractId === r.contractId)?.customerName || "",
    milestone: (r) => BUCKETS.indexOf(r.milestone),
    campaign: (r) => r.recommendation?.campaign || "",
    retryCount: (r) => r.retryCount,
    result: (r) => (r.error ? 0 : r.escalated ? 1 : r.pass ? 3 : 2),
    latencyMs: (r) => r.latencyMs,
    costUsd: (r) => r.costUsd,
  }), [trace, traceSortKey, traceSortDir, contracts]);

  const scatterData = useMemo(() => filteredContracts.map((c) => ({
    x: c.riskScore, y: c.contractValue, tier: c.segment, id: c.contractId, name: c.customerName,
  })), [filteredContracts]);

  // Horizontal quadrant split — median of whatever's currently filtered,
  // so it tracks the visible book rather than a fixed dollar figure.
  const medianContractValue = useMemo(() => {
    const vals = filteredContracts.map((c) => c.contractValue).sort((a, b) => a - b);
    if (!vals.length) return 0;
    const mid = Math.floor(vals.length / 2);
    return vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
  }, [filteredContracts]);

  // Explicit y-axis ceiling (rounded up, +10% headroom) so the quadrant tint
  // rectangles and the axis domain agree exactly — recharts' auto domain
  // wouldn't necessarily line up with a hand-picked ReferenceArea bound.
  const scatterYMax = useMemo(() => {
    const maxVal = Math.max(1, ...scatterData.map((d) => d.y));
    return Math.ceil((maxVal * 1.1) / 5000) * 5000;
  }, [scatterData]);

  // Shared by the inline card and the maximize modal — one definition of the
  // chart, just rendered at a different height, so the two views can't drift.
  const renderScatterChart = (height) => (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 6, right: 12, bottom: 6, left: 0 }}>
        <CartesianGrid stroke={T.border} strokeDasharray="3 3" />
        {/* Quadrant tints — reuse each segment's existing badge
            background color, so this stays in sync with SEGMENT_BG
            instead of being a second, hand-maintained color list. */}
        <ReferenceArea x1={0} x2={RISK_QUADRANT_THRESHOLD} y1={medianContractValue} y2={scatterYMax} fill={SEGMENT_BG["Healthy"]} fillOpacity={1} stroke="none" label={{ value: "Q4: Low risk, High value", position: "insideTopLeft", fill: T.inkMuted, fontSize: 12, fontWeight: 700 }} />
        <ReferenceArea x1={RISK_QUADRANT_THRESHOLD} x2={100} y1={medianContractValue} y2={scatterYMax} fill={SEGMENT_BG["High Risk"]} fillOpacity={1} stroke="none" label={{ value: "Q1: High risk, High value", position: "insideTopRight", fill: T.inkMuted, fontSize: 12, fontWeight: 700 }} />
        <ReferenceArea x1={RISK_QUADRANT_THRESHOLD} x2={100} y1={0} y2={medianContractValue} fill={SEGMENT_BG["At Risk"]} fillOpacity={1} stroke="none" label={{ value: "Q2: High risk, Low value", position: "insideBottomRight", fill: T.inkMuted, fontSize: 12, fontWeight: 700 }} />
        <ReferenceArea x1={0} x2={RISK_QUADRANT_THRESHOLD} y1={0} y2={medianContractValue} fill={SEGMENT_BG["Standard"]} fillOpacity={1} stroke="none" label={{ value: "Q3: Low risk, Low value", position: "insideBottomLeft", fill: T.inkMuted, fontSize: 12, fontWeight: 700 }} />
        <XAxis type="number" dataKey="x" name="Risk score" domain={[0, 100]} stroke={T.inkFaint} tick={{ fontSize: 11 }} />
        <YAxis type="number" dataKey="y" name="Contract value ($)" domain={[0, scatterYMax]} stroke={T.inkFaint} tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Math.round(v / 1000)}k`} />
        <ZAxis range={[55, 55]} />
        <ReferenceLine x={RISK_QUADRANT_THRESHOLD} stroke={T.borderStrong} strokeDasharray="4 4" label={{ value: "Risk " + RISK_QUADRANT_THRESHOLD, position: "insideTopLeft", fill: T.inkFaint, fontSize: 10 }} />
        <ReferenceLine y={medianContractValue} stroke={T.borderStrong} strokeDasharray="4 4" label={{ value: "Median value ($)", position: "insideBottomRight", fill: T.inkFaint, fontSize: 10 }} />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload; // the original scatterData point: { x, y, tier, id, name }
            return (
              <div style={{ fontSize: 12, borderRadius: 8, border: `1px solid ${T.border}`, background: T.surface, padding: "8px 10px" }}>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>{d.name}</div>
                <div style={{ color: T.inkMuted }}>{d.id}</div>
                <div style={{ marginTop: 4 }}>Risk score: {d.x}</div>
                <div style={{ marginTop: 4 }}>Tier: {d.tier}</div>
                <div>Contract value ($): ${d.y.toLocaleString()}</div>
              </div>
            );
          }}
        />
        {["High Risk", "At Risk", "Healthy", "Standard"].map((seg) => (
          <Scatter
            key={seg}
            name={seg}
            data={scatterData.filter((d) => d.tier === seg)}
            fill={SEGMENT_COLOR[seg]}
            onClick={(d) => { setSelected(d.id); setMaximizedChart(null); }}
            cursor="pointer"
          />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  );

  // Same helper the Dashboard tab uses for Total value / At risk $ / Converted
  // $ — one definition of these metrics, not a second copy for this tab.
  const plannerBookMetrics = useMemo(
    () => aggregateBookMetrics(filteredContracts, traceByContract),
    [filteredContracts, traceByContract]
  );

  // "Action required" is the default actionStatus every trace record gets
  // and flips to "Action done" once a rep marks it complete — scoped to
  // filteredContracts so this stays in sync with the region/channel/bucket
  // filters instead of reading the backend's unfiltered global total.
  const actionsNeeded = useMemo(
    () => filteredContracts.filter((c) => traceByContract[c.contractId]?.actionStatus === "Action required").length,
    [filteredContracts, traceByContract]
  );

  const selectedContract = contracts.find((c) => c.contractId === selected);
  const portfolioContracts = selectedContract
    ? contracts.filter((c) => c.customerId === selectedContract.customerId).sort((a, b) => b.riskScore - a.riskScore)
    : [];
  const portfolioSort = useSort(null, "asc");
  const [portfolioSortKey, portfolioSortDir] = portfolioSort;
  const sortedPortfolioContracts = useMemo(() => sortRows(portfolioContracts, portfolioSortKey, portfolioSortDir, {
    contractId: (c) => c.contractId,
    region: (c) => c.region,
    bucket: (c) => BUCKETS.indexOf(c.bucket),
    contractValue: (c) => c.contractValue,
    riskScore: (c) => c.riskScore,
  }), [portfolioContracts, portfolioSortKey, portfolioSortDir]);
  const selectedTrace = selected ? traceByContract[selected] : null;

  const setOutcome = async (contractId, outcome, note) => {
    // Optimistic local update so the UI feels immediate...
    setTrace((prev) => prev.map((r) => (r.contractId === contractId && r === traceByContract[contractId]
      ? { ...r, outcome, outcomeNote: note ?? r.outcomeNote } : r)));
    // ...then persist to the backend, which is the source of truth.
    try {
      await api.sendFeedback(contractId, outcome, note);
      const [t, m] = await Promise.all([api.getTrace(), api.getMetrics()]);
      setTrace(t); setMetrics(m);
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
        Loading contract data from the backend…
      </div>
    );
  }


  return (
    <div style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", background: T.bg, color: T.ink, minHeight: "100vh" }}>
      <div style={{ height: 4, background: T.brand }} />
      <div style={{ padding: "22px 26px" }}>
      <style>{`
        * { box-sizing: border-box; }
        button:focus-visible, div[tabindex]:focus-visible { outline: 2px solid ${T.brand}; outline-offset: 2px; }
        .tabbtn { border:none; background:transparent; padding:8px 4px; font-size:13.5px; font-weight:600; color:${T.inkFaint}; cursor:pointer; border-bottom:2px solid transparent; }
        .tabbtn.active { color:${T.brand}; border-bottom-color:${T.brand}; }
        .rowhover:hover { background:${T.surfaceSunken}; }
        table { border-collapse: collapse; width: 100%; }
        th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:0.4px; color:${T.inkFaint}; font-weight:600; padding:8px 10px; border-bottom:1px solid ${T.border}; }
        td { padding:9px 10px; font-size:13px; border-bottom:1px solid ${T.border}; }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 18, flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <img
            src="/carrier-logo.svg"
            alt="Carrier Global logo"
            width="120"
            height="50"
            style={{ flexShrink: 0, marginTop: 2, objectFit: "contain" }}
            onError={(e) => { e.currentTarget.style.display = "none"; }}
          />
          <div>
            <div style={{ fontSize: 11.5, color: T.brand, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>Carrier Global</div>
            <h1 style={{ fontSize: 22, fontWeight: 700, margin: "2px 0 0" }}>Proactive Contract Renewal</h1>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <button
            onClick={runBatch}
            disabled={running || dueContracts.length === 0}
            style={{
              display: "flex", alignItems: "center", gap: 8, background: running ? T.inkFaint : T.brand, color: "#fff",
              border: "none", borderRadius: 8, padding: "10px 16px", fontSize: 13.5, fontWeight: 600,
              cursor: running || dueContracts.length === 0 ? "default" : "pointer",
            }}
          >
            <Play size={15} fill="#fff" />
            {running ? `Running ${progress.done}/${progress.total}…` : `Run daily batch (${dueContracts.length} due)`}
          </button>
          {apiError && <div style={{ fontSize: 11.5, color: T.risk, marginTop: 6, maxWidth: 260 }}>{apiError}</div>}
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 20, borderBottom: `1px solid ${T.border}`, marginBottom: 18 }}>
        <button className={`tabbtn ${tab === "overview" ? "active" : ""}`} onClick={() => setTab("overview")}>Overview</button>
        <button className={`tabbtn ${tab === "technical-details" ? "active" : ""}`} onClick={() => setTab("technical-details")}>Technical Details</button>
        <button className={`tabbtn ${tab === "renewal-prioritization" ? "active" : ""}`} onClick={() => setTab("renewal-prioritization")}>Renewal Prioritization</button>
        <button className={`tabbtn ${tab === "dashboard" ? "active" : ""}`} onClick={() => setTab("dashboard")}>Dashboard</button>
        <button className={`tabbtn ${tab === "trace" ? "active" : ""}`} onClick={() => setTab("trace")}>Trace &amp; Agent Metrics</button>
      </div>

      {tab === "overview" && (
        <div style={{ maxWidth: "90%"}}>
          <Card style={{ padding: "26px 30px", marginBottom: 18, borderTop: `3px solid ${T.brand}` }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, color: T.brand, textTransform: "uppercase", marginBottom: 6 }}>What we're building</div>
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 4px" }}>Proactive Contract Renewal</h2>
            <p style={{ fontSize: 13.5, color: T.inkMuted, margin: "0 0 20px", fontStyle: "italic" }}>
              Not a contract renewal prediction tool — a proactive renewal system.
            </p>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>The problem</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, margin: "0 0 20px" }}>
              CST renews thousands of HVAC service contracts every year across NATT, ETT, and APAC TT. Today, that
              process is reactive: an at-risk account is usually noticed only after the contract has already lapsed
              &mdash; or not noticed at all. The signals that would have predicted the loss (a slipping SLA, a late
              payment, a cooling NPS score, a competitor circling) exist somewhere in the business, but nothing pulls
              them together in time for a rep to act. And when a rep does spot a risk, there's no consistent playbook
              for what to do next, so the response depends entirely on that one person's judgment and available time.
            </p>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>Why we need this solution</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, margin: "0 0 20px" }}>
              Manually reviewing thousands of contracts for renewal risk doesn't scale, and it doesn't happen
              consistently &mdash; different reps and different regions develop different habits. Contracts renew on
              autopilot until the moment they don't, and by the time a churn shows up in the numbers, the account is
              already gone. Without a system that checks every contract on a fixed clock, scores risk the same way
              everywhere, and hands a rep a specific next step, retention is left to chance rather than to a process.
            </p>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>How the business benefits</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, margin: "0 0 10px" }}>
              This isn't a contract renewal <i>prediction</i> tool &mdash; the difference is what happens after the
              number. It's a proactive renewal system that:
            </p>
            <ol style={{ fontSize: 13.5, lineHeight: 1.75, color: T.ink, margin: "0 0 20px", paddingLeft: 20 }}>
              <li><b>Identifies upcoming renewal risk</b> &mdash; every contract is checked automatically against its renewal milestones, so risk surfaces weeks before a contract could lapse, not after.</li>
              <li><b>Prioritizes accounts by business value</b> &mdash; risk alone isn't the whole story; a high-risk, high-margin account is a very different priority than a high-risk, low-margin one, so accounts are ranked by risk crossed with value, not risk in isolation.</li>
              <li><b>Recommends an action to retain the customer</b> &mdash; each flagged account gets one concrete, grounded retention action, not just a "high risk" label.</li>
              <li><b>Generates the outreach content</b> &mdash; the actual email a rep can send is drafted for them, referencing this specific customer's real history, so there's no blank page between "risk found" and "customer contacted."</li>
              <li><b>Tracks the outcome</b> &mdash; every recommendation and its real-world result (engaged, declined, no response) is logged, so the business can see which actions actually retain customers, not just how many were sent.</li>
            </ol>

            <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 12, fontSize: 12, color: T.inkMuted, fontStyle: "italic" }}>
              For how this is actually built &mdash; the agent pipeline, the scoring model, current state, and the
              assumptions behind it &mdash; see the Technical Details tab.
            </div>
          </Card>

        </div>
      )}

      {tab === "technical-details" && (
        <div style={{ maxWidth: "90%"}}>
          <Card style={{ padding: "26px 30px", borderTop: `3px solid ${T.brand}` }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, color: T.brand, textTransform: "uppercase", marginBottom: 6 }}>How it's built</div>
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 14px" }}>Technical details</h2>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>How we're achieving it</div>
            <ul style={{ fontSize: 13.5, lineHeight: 1.7, color: T.ink, margin: "0 0 20px", paddingLeft: 20 }}>
              <li><b>Rule-based risk scorecard</b> &mdash; 11 weighted factors (SLA breaches, payment behavior, NPS, competitor activity, and more) produce a 0&ndash;100 score with a ranked driver-feature breakdown. This is a deterministic scorecard, not a trained ML model, and it's labeled that way honestly in the product.</li>
              <li><b>Risk &times; value Segmentation</b> &mdash; crosses the risk score against contract margin so a high-risk, high-margin account is treated as a different priority than a high-risk, low-margin one.</li>
              <li><b>Six-agent pipeline</b> &mdash; Service Ticket Summary, Customer Summary & Customer Feedback Summary agents run ahead of time and are cached; a Recommendation Agent proposes a retention action and any relevant upsell; an Evaluation Agent scores it against a rubric and triggers a retry or escalation; a Content Agent drafts the outreach email.</li>
              <li><b>Full traceability</b> &mdash; every recommendation logs its prompts, scores, retries, latency, and token cost, inspectable end to end.</li>
              <li><b>Swappable LLM provider</b> &mdash; Claude API or a locally-hosted vLLM model, switched with one configuration change.</li>
              <li><b>One engine, three regions</b> &mdash; NATT, ETT, and APAC TT run on the same pipeline; region and channel are configuration, not forked code.</li>
            </ul>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>Current state</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, margin: "0 0 20px" }}>
              A working proof of concept: FastAPI backend and React frontend, in-memory synthetic data, real
              agentic LLM calls. Includes a renewal dashboard, customer/contract detail with full agent
              traceability, campaign tracking, and a global/region summary view.
            </p>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>Assumptions taken</div>
            <ul style={{ fontSize: 12.5, lineHeight: 1.65, color: T.ink, margin: "0 0 18px", paddingLeft: 20 }}>
              <li>Unit of analysis is customer-contract, not customer alone - a customer with 3 contracts is 3 independent renewal journeys.</li>
              <li>Renewal milestones are 90/60/45/30/10 days to expiry - not yet validated against CST's actual renewal cadence.</li>
              <li>The 5-item retention action taxonomy is our proposal, not CST's existing playbook (none was provided).</li>
              <li>The risk model is deliberately rule-based, not trained ML - labeled honestly as a scorecard standing in for where a real model would go.</li>
              <li>Risk x Value Segmentation thresholds (score ≥50/30, margin vs. book median) are illustrative starting points, not calibrated.</li>
              <li>All data is synthetic - customers, contracts, service tickets, financials, and engagement signals are generated, not sourced from CST systems.</li>
              <li>The product catalog (5 equipment types) is representative, not CST's actual catalog.</li>
              <li>The "Campaign Response by Risk Bucket" chart is a live proxy from logged outcomes, not a validated historical renewal backtest - no ground truth exists to validate against.</li>
              <li>Suggested renewal terms (price move %, term length) are rule-based suggestions feeding the draft email, not negotiated or approved figures.</li>
              <li>Dealer-channel visibility is assumed limited - the system may only see what the dealer relationship exposes, not necessarily the true end customer.</li>
              <li>No database - all state is in-memory and resets on backend restart; a POC simplification, not a production data-architecture recommendation.</li>
              <li>Displayed cost/latency figures use a placeholder blended LLM rate, not live provider pricing.</li>
              <li>Retry limit (2) and the policy-compliance hard-fail rule are configurable defaults, not validated thresholds.</li>
              <li>Ticket/Customer Summary agents are cached and only regenerate on demand or when missing during a batch run, not on every milestone.</li>
              <li>Outcome tracking uses a simplified 3-value enum (No response / Engaged / Declined) - real engagement signals (opens, clicks, replies) aren't modeled.</li>
              <li>UI theming uses Carrier's publicly documented 2013 brand blue (#142C73); their internal system may have evolved since the 2025 identity refresh, for which exact specifications weren't available to us.</li>
              <li>Single-process deployment (FastAPI serving the built frontend) is a demo convenience, not a production deployment recommendation.</li>
              <li>APAC TT's dealer-only channel pattern is assumed analogous to NATT's - not confirmed by the client.</li>
            </ul>

            <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 12, fontSize: 12, color: T.inkMuted, fontStyle: "italic" }}>
              Status: proof of concept &mdash; synthetic data, rule-based scoring labeled honestly as such, real agentic pipeline.
            </div>
          </Card>
        </div>
      )}

      {tab === "renewal-prioritization" && (
        <>
          {/* Global filters */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
            <span style={{ fontSize: 11.5, fontWeight: 700, color: T.inkFaint, textTransform: "uppercase", letterSpacing: 0.4 }}>Filters</span>
            <MultiSelect
              label="regions"
              options={[{ value: "NATT", label: "NATT" }, { value: "ETT", label: "ETT" }, { value: "APAC_TT", label: "APAC-TT" }]}
              selected={regionFilter}
              onChange={setRegionFilter}
            />
            <MultiSelect
              label="channels"
              options={[{ value: "Dealer", label: "Dealer" }, { value: "Direct", label: "Direct" }]}
              selected={channelFilter}
              onChange={setChannelFilter}
            />
            {(regionFilter.length > 0 || channelFilter.length > 0) && (
              <span style={{ fontSize: 11.5, color: T.inkFaint }}>{filteredContracts.length} of {contracts.length} contracts shown</span>
            )}
          </div>
    
          <div style={{ fontSize: 13.5, fontWeight: 700 }}>Contract expiring in: </div>
          <div style={{ fontSize: 12, color: T.inkMuted, marginBottom: 10 }}>Click a milestone to filter the worklist, chart, and KPIs below by how soon each contract is due for renewal.</div>

          {/* Bucket cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0,1fr))", gap: 10, marginBottom: 18 }}>
            {BUCKETS.map((b) => (
              <Card
                key={b}
                onClick={() => setBucketFilter(bucketFilter === b ? null : b)}
                title={BUCKET_TOOLTIP[b]}
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

          <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr 1fr", gap: 16, marginBottom: 18 }}>
            {/* Scatter */}
            <Card style={{ padding: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 2 }}>Value Segmentation</div>
                  <div style={{ fontSize: 12, color: T.inkMuted, marginBottom: 10 }}>Risk Score vs. Contract Value ($) — quadrants split at risk {RISK_QUADRANT_THRESHOLD} and median contract value ($)</div>
                </div>
                <button
                  onClick={() => setMaximizedChart("scatter")}
                  title="Maximize"
                  style={{ border: "none", background: "none", cursor: "pointer", padding: 4, color: T.inkFaint, flexShrink: 0 }}
                >
                  <Maximize2 size={15} />
                </button>
              </div>
              {renderScatterChart(280)}
              <div style={{ display: "flex", gap: 14, marginTop: 4, flexWrap: "wrap" }}>
                {["High Risk", "At Risk", "Healthy", "Standard"].map((seg) => (
                  <div key={seg} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: T.inkMuted }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: SEGMENT_COLOR[seg], display: "inline-block" }} />
                    {seg}
                  </div>
                ))}
              </div>
            </Card>

            {/* KPI summary */}
            <Card style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
              <div style={{ fontSize: 13.5, fontWeight: 700 }}>Portfolio KPIs</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <StatBlock label="Customers | Contracts" value={plannerBookMetrics.customerCount + " | " + plannerBookMetrics.contractCount} />
                <StatBlock label="Actions needed" value={actionsNeeded} sub={`of ${plannerBookMetrics.contractCount} contracts`} accent={actionsNeeded > 0 ? T.brand : undefined} />
                <StatBlock label="High risk contracts" value={filteredContracts.filter((c) => c.segment === "High Risk").length} sub="High Risk segment" accent={T.risk} />
                <StatBlock label="At-risk contracts" value={filteredContracts.filter((c) => c.segment === "At Risk").length} sub="At Risk segment" accent={T.amber} />
                <StatBlock label="Lost" value={plannerBookMetrics.lostCount} sub="declined our outreach" accent={T.risk} />
                <StatBlock label="Campaign response rate" value={metrics.responseRate !== null ? `${metrics.responseRate}%` : "—"} sub="of logged outcomes" />
                <StatBlock label="Total contract value" value={`$${(plannerBookMetrics.totalValue / 1000).toFixed(0)}k`} sub="Active contracts" />
                <StatBlock label="Converted $" value={`$${(plannerBookMetrics.potentialRevenueValue / 1000).toFixed(0)}k`} sub="engaged" accent={T.safe} />
                <StatBlock label="Potential $ at risk" value={`$${(plannerBookMetrics.potentialAtRiskValue / 1000).toFixed(0)}k`} sub="no response received" accent={T.amber} />
                <StatBlock label="Lost revenue $" value={`$${(plannerBookMetrics.lostRevenueValue / 1000).toFixed(0)}k`} sub="declined" accent={T.risk} />
              </div>
            </Card>

            <Card style={{ padding: 16 }}>
              <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 2 }}>Campaign Response by Risk Bucket</div>
              <div style={{ fontSize: 11, color: T.inkFaint, marginBottom: 10, lineHeight: 1.4 }}>
                Live signal from logged outcomes, reflecting the region/channel filters above.
              </div>
              {filteredOutcomeByRiskBucket.totalWithOutcome > 0 ? (
                <OutcomeBucketChart data={filteredOutcomeByRiskBucket} />
              ) : (
                <div style={{ fontSize: 12, color: T.inkFaint, padding: "18px 0", textAlign: "center" }}>
                  Not enough logged outcomes yet for this filter — log a few via Feedback Logging, or clear the filters.
                </div>
              )}
            </Card>
          </div>

          {/* Worklist */}
          <Card style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ padding: "14px 16px", borderBottom: `1px solid ${T.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 13.5, fontWeight: 700 }}>Worklist {bucketFilter ? `— ${BUCKET_LABEL[bucketFilter]}` : ""}</div>
              {bucketFilter && <button onClick={() => setBucketFilter(null)} style={{ border: "none", background: "none", fontSize: 12, color: T.info, cursor: "pointer" }}>Clear filter</button>}
            </div>
            <div style={{ maxHeight: 360, overflowY: "auto" }}>
              <table>
                <thead><tr>
                  <SortTh label="Customer" sortKey="customerName" sort={worklistSort} />
                  <SortTh label="Region" sortKey="region" sort={worklistSort} />
                  <SortTh label="Channel" sortKey="channel" sort={worklistSort} />
                  <SortTh label="Bucket" sortKey="bucket" sort={worklistSort} />
                  <SortTh label="Risk" sortKey="riskScore" sort={worklistSort} />
                  <SortTh label="Value" sortKey="contractValue" sort={worklistSort} />
                  <SortTh label="Status" sortKey="status" sort={worklistSort} />
                  <th></th>
                </tr></thead>
                <tbody>
                  {sortedWorklist.map((c) => {
                    const t = traceByContract[c.contractId];
                    return (
                      <tr key={c.contractId} className="rowhover" style={{ cursor: "pointer" }} onClick={() => setSelected(c.contractId)}>
                        <td style={{ fontWeight: 600 }}>{c.customerName}</td>
                        <td>{c.region}</td>
                        <td>{c.channel}</td>
                        <td><Badge text={BUCKET_LABEL[c.bucket]} color={T.inkMuted} bg={T.surfaceSunken} /></td>
                        <td><Badge text={`${c.segment} · ${c.riskScore}`} color={SEGMENT_COLOR[c.segment]} bg={SEGMENT_BG[c.segment]} /></td>
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

          {/* Model info */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
            {modelInfo && (
              <>
                <Card style={{ padding: 16 }}>
                  <Badge text={modelInfo.riskModel.type} color={T.info} bg={T.infoBg} />
                  <div style={{ fontSize: 13.5, fontWeight: 700, margin: "8px 0 6px" }}>{modelInfo.riskModel.name}</div>
                  <div style={{ fontSize: 12, color: T.inkMuted, lineHeight: 1.5 }}>{modelInfo.riskModel.description}</div>
                </Card>
                <Card style={{ padding: 16 }}>
                  <Badge text={modelInfo.valueModel.type} color={T.info} bg={T.infoBg} />
                  <div style={{ fontSize: 13.5, fontWeight: 700, margin: "8px 0 6px" }}>{modelInfo.valueModel.name}</div>
                  <div style={{ fontSize: 12, color: T.inkMuted, lineHeight: 1.5 }}>{modelInfo.valueModel.description}</div>
                </Card>
              </>
            )}
          </div>
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
                  <SortTh label="Customer" sortKey="customer" sort={traceSort} />
                  <SortTh label="Milestone" sortKey="milestone" sort={traceSort} />
                  <SortTh label="Campaign" sortKey="campaign" sort={traceSort} />
                  <SortTh label="Retries" sortKey="retryCount" sort={traceSort} />
                  <SortTh label="Result" sortKey="result" sort={traceSort} />
                  <SortTh label="Latency" sortKey="latencyMs" sort={traceSort} />
                  <SortTh label="Cost" sortKey="costUsd" sort={traceSort} />
                </tr></thead>
                <tbody>
                  {sortedTraceRows.map((r, i) => (
                    <tr key={r.runId} className="rowhover" style={{ cursor: "pointer" }} onClick={() => setSelected(r.contractId)}>
                      <td style={{ fontWeight: 600 }}>{contracts.find((c) => c.contractId === r.contractId)?.customerName}</td>
                      <td>{BUCKET_LABEL[r.milestone]}</td>
                      <td style={{ color: T.inkMuted }}>{r.recommendation?.campaign || "—"}</td>
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
                    <tr><td colSpan={7} style={{ textAlign: "center", color: T.inkFaint, padding: 24 }}>No runs yet — run the daily batch from the `Renewal Prioritization` tab.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {tab === "dashboard" && (
        <>
          {/* Drill-down filters — same chip pattern as Renewal Prioritization, plus
              a Segment filter. No filter selected = Global; adding region/
              channel/segment/bucket chips narrows down to Regional / Segment /
              Milestone level, all using the same underlying data. */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11.5, fontWeight: 700, color: T.inkFaint, textTransform: "uppercase", letterSpacing: 0.4 }}>Filters</span>
            <MultiSelect
              label="regions"
              options={REGIONS.map((r) => ({ value: r.id, label: r.id === "APAC_TT" ? "APAC-TT" : r.id }))}
              selected={dashRegionFilter}
              onChange={setDashRegionFilter}
            />
            <MultiSelect
              label="channels"
              options={[{ value: "Dealer", label: "Dealer" }, { value: "Direct", label: "Direct" }]}
              selected={dashChannelFilter}
              onChange={setDashChannelFilter}
            />
            <MultiSelect
              label="segments"
              options={[{ value: "High Risk", label: "High Risk" }, { value: "At Risk", label: "At Risk" }, { value: "Healthy", label: "Healthy" }, { value: "Standard", label: "Standard" }]}
              selected={dashSegmentFilter}
              onChange={setDashSegmentFilter}
            />
            {(dashRegionFilter.length > 0 || dashChannelFilter.length > 0 || dashSegmentFilter.length > 0 || dashBucketFilter) && (
              <span style={{ fontSize: 11.5, color: T.inkFaint }}>{dashFilteredContracts.length} of {contracts.length} contracts shown</span>
            )}
          </div>

          {/* Milestone drill-down — same cards as Renewal Prioritization, own state */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0,1fr))", gap: 10, marginBottom: 20 }}>
            {BUCKETS.map((b) => (
              <Card
                key={b}
                onClick={() => setDashBucketFilter(dashBucketFilter === b ? null : b)}
                title={BUCKET_TOOLTIP[b]}
                style={{
                  padding: "12px 14px",
                  borderColor: dashBucketFilter === b ? T.ink : T.border,
                  borderWidth: dashBucketFilter === b ? 1.5 : 1,
                }}
              >
                <div style={{ fontSize: 11, color: T.inkFaint, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.3 }}>{BUCKET_LABEL[b]}</div>
                <div style={{ fontSize: 24, fontWeight: 700, marginTop: 2, color: b === "Lost" ? T.risk : T.ink }}>{dashBucketCounts[b]}</div>
              </Card>
            ))}
          </div>

          {/* Book overview: Global, then per-region */}
          <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 8 }}>Book overview</div>
          <Card style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, color: T.inkFaint, marginBottom: 12 }}>
              Global{dashBucketFilter ? ` — ${BUCKET_LABEL[dashBucketFilter]}` : ""}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16, marginBottom: 16 }}>
              <StatBlock label="Customers" value={dashGlobalMetrics.customerCount} />
              <StatBlock label="Contracts" value={dashGlobalMetrics.contractCount} />
              <StatBlock label="Campaign response rate" value={metrics.responseRate !== null ? `${metrics.responseRate}%` : "—"} sub="of logged outcomes" />
              <StatBlock label="" value="" />
              <StatBlock label="" value="" />
              <StatBlock label="Healthy contracts" value={dashGlobalMetrics.segmentCounts["Healthy"]} sub="High Risk segment" accent={T.safe} />
              <StatBlock label="High risk contracts" value={dashGlobalMetrics.segmentCounts["High Risk"]} sub="High Risk segment" accent={T.risk} />
              <StatBlock label="At-risk contracts" value={dashGlobalMetrics.segmentCounts["At Risk"]} sub="At Risk segment" accent={T.amber} />
              <StatBlock label="Lost" value={dashGlobalMetrics.lostCount} sub="declined our outreach" accent={T.risk} />
              <StatBlock label="" value="" />
              <StatBlock label="Total contract value" value={`$${(dashGlobalMetrics.totalValue / 1000).toFixed(0)}k`} sub="Active contracts" />
              <StatBlock label="Margin" value={`$${(dashGlobalMetrics.totalMargin / 1000).toFixed(0)}k`} />
              <StatBlock label="Lost revenue $" value={`$${(dashGlobalMetrics.lostRevenueValue / 1000).toFixed(0)}k`} sub="declined" accent={T.risk} />
              <StatBlock label="Converted $" value={`$${(dashGlobalMetrics.potentialRevenueValue / 1000).toFixed(0)}k`} sub="engaged" accent={T.safe} />
              <StatBlock label="Potential $ at risk" value={`$${(dashGlobalMetrics.potentialAtRiskValue / 1000).toFixed(0)}k`} sub="no response received" accent={T.amber} />
            </div>
            <SegmentBar counts={dashGlobalMetrics.segmentCounts} />
          </Card>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14, marginBottom: 24 }}>
            {dashRegionsToShow.map((region) => {
              const m = aggregateBookMetrics(dashFilteredContracts.filter((c) => c.region === region.id), traceByContract);
              return (
                <Card key={region.id} style={{ padding: 16 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                    <div>
                      <div style={{ fontSize: 13.5, fontWeight: 700 }}>{region.label}</div>
                      <div style={{ fontSize: 11, color: T.inkFaint, marginTop: 2 }}>{region.id} · {region.channels.join(" + ")} channel{region.channels.length > 1 ? "s" : ""}</div>
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
                    {console.log(m)}
                    <StatBlock label="Customers" value={m.customerCount} />
                    <StatBlock label="Contracts" value={m.contractCount} />
                    <StatBlock label="Campaign response rate" value={metrics.responseRate !== null ? `${metrics.responseRate}%` : "—"} sub="of logged outcomes" />
                    <StatBlock label="" value="" />

                    <StatBlock label="Healthy contracts" value={m.segmentCounts["Healthy"]} sub="High Risk segment" accent={T.safe} />
                    <StatBlock label="High risk contracts" value={m.segmentCounts["High Risk"]} sub="High Risk segment" accent={T.risk} />
                    <StatBlock label="At-risk contracts" value={m.segmentCounts["At Risk"]} sub="At Risk segment" accent={T.amber} />
                    <StatBlock label="" value="" />
                    <StatBlock label="Total contract value" value={`$${(m.totalValue / 1000).toFixed(0)}k`} sub="Active contracts" />
                    <StatBlock label="Margin" value={`$${(m.totalMargin / 1000).toFixed(0)}k`} />
                    <StatBlock label="Converted $" value={`$${(m.potentialRevenueValue / 1000).toFixed(0)}k`} sub="engaged" accent={T.safe} />
                    <StatBlock label="Potential $ at risk" value={`$${(m.potentialAtRiskValue / 1000).toFixed(0)}k`} sub="no response received" accent={T.amber} />
                    <StatBlock label="Lost revenue $" value={`$${(m.lostRevenueValue / 1000).toFixed(0)}k`} sub="declined" accent={T.risk} />
                  </div>
                  <SegmentBar counts={m.segmentCounts} />
                </Card>
              );
            })}
          </div>

          {/* Campaign performance — same filters as above, applied to trace
              records via their own snapshotted context. */}
          <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 8 }}>Campaign performance</div>
          <Card style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16, marginBottom: 16 }}>
              <StatBlock label="Contracts in filter" value={dashFilteredContracts.length} />
              <StatBlock label="Assigned" value={dashCampaignTotals.assigned} sub="recommended action" />
              <StatBlock label="Engaged" value={dashCampaignTotals.engaged} sub="made response" accent={T.safe} />
              <StatBlock label="Declined" value={dashCampaignTotals.declined} accent={T.risk} />
              <StatBlock label="No response" value={dashCampaignTotals.noResponse} accent={T.amber} />
            </div>
          </Card>
          <Card style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: -8 }}>
              <button
                onClick={() => setMaximizedChart("campaign-bar")}
                title="Maximize"
                style={{ border: "none", background: "none", cursor: "pointer", padding: 4, color: T.inkFaint }}
              >
                <Maximize2 size={15} />
              </button>
            </div>
            {renderCampaignBarChart(300)}
          </Card>

          <Card style={{ padding: 0, overflow: "hidden" }}>
            <table>
              <thead>
                <tr>
                  <SortTh label="Campaign" sortKey="name" sort={campaignSort} />
                  <SortTh label="Assigned" sortKey="Assigned" sort={campaignSort} />
                  <SortTh label="Engaged" sortKey="Engaged" sort={campaignSort} />
                  <SortTh label="Declined" sortKey="declined" sort={campaignSort} />
                  <SortTh label="No response" sortKey="noResponse" sort={campaignSort} />
                  <SortTh label="Total Contract Value $" sortKey="assignedValue" sort={campaignSort} />
                  <SortTh label="Potential $ at risk" sortKey="potentialAtRiskValue" sort={campaignSort} style={{ color: T.amber }} />
                  <SortTh label="Lost revenue $" sortKey="lostRevenueValue" sort={campaignSort} style={{ color: T.risk }} />
                  <SortTh label="Converted $" sortKey="potentialRevenueValue" sort={campaignSort} style={{ color: T.safe }} />
                </tr>
              </thead>
              <tbody>
                {sortedCampaignData.map((row) => (
                  <tr key={row.name} className="rowhover">
                    <td>{row.name}</td>
                    <td>{row.Assigned}</td>
                    <td>{row.Engaged}</td>
                    <td>{row.declined}</td>
                    <td>{row.noResponse}</td>
                    <td>${row.assignedValue.toLocaleString()}</td>
                    <td style={{ color: row.potentialAtRiskValue > 0 ? T.amber : T.inkMuted }}>${row.potentialAtRiskValue.toLocaleString()}</td>
                    <td style={{ color: row.lostRevenueValue > 0 ? T.risk : T.inkMuted }}>${row.lostRevenueValue.toLocaleString()}</td>
                    <td style={{ color: row.potentialRevenueValue > 0 ? T.safe : T.inkMuted }}>${row.potentialRevenueValue.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {/* Chart maximize modal — shared by the scatter and campaign bar charts.
          Click the backdrop or the minimize button to close; z-index sits
          below the detail drawer so selecting a contract from the maximized
          scatter chart surfaces the drawer on top instead of behind it. */}
      {maximizedChart && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(22,27,34,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 45, padding: 24 }}
          onClick={() => setMaximizedChart(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: T.surface, borderRadius: 12, padding: 22, width: "min(1100px, 100%)", maxHeight: "90vh", overflow: "auto", boxShadow: "0 20px 60px rgba(0,0,0,0.25)" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div style={{ fontSize: 15, fontWeight: 700 }}>
                {maximizedChart === "scatter" ? "Value Segmentation" : "Campaign performance"}
              </div>
              <button
                onClick={() => setMaximizedChart(null)}
                title="Minimize"
                style={{ border: "none", background: "none", cursor: "pointer", padding: 4, color: T.inkFaint }}
              >
                <Minimize2 size={18} />
              </button>
            </div>
            {maximizedChart === "scatter" && (
              <>
                {renderScatterChart(560)}
                <div style={{ display: "flex", gap: 14, marginTop: 8, flexWrap: "wrap" }}>
                  {["High Risk", "At Risk", "Healthy", "Standard"].map((seg) => (
                    <div key={seg} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: T.inkMuted }}>
                      <span style={{ width: 8, height: 8, borderRadius: 99, background: SEGMENT_COLOR[seg], display: "inline-block" }} />
                      {seg}
                    </div>
                  ))}
                </div>
              </>
            )}
            {maximizedChart === "campaign-bar" && renderCampaignBarChart(520)}
          </div>
        </div>
      )}

      {/* Detail drawer */}
      {selectedContract && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(22,27,34,0.35)", display: "flex", justifyContent: "flex-end", zIndex: 50 }} onClick={() => setSelected(null)}>
          <div style={{ width: "56%", minWidth: 460, maxWidth: "94vw", background: T.surface, height: "100%", overflowY: "auto", padding: 22, boxShadow: "-8px 0 24px rgba(0,0,0,0.12)" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 4 }}>
              <div>
                <div style={{ fontSize: 11.5, color: T.inkFaint, fontWeight: 600 }}>{selectedContract.contractId} · {selectedContract.region}</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{selectedContract.customerName}</div>
              </div>
              <button onClick={() => setSelected(null)} style={{ border: "none", background: "none", cursor: "pointer" }}><X size={18} color={T.inkFaint} /></button>
            </div>

            <div style={{ display: "flex", gap: 8, margin: "10px 0 16px", flexWrap: "wrap" }}>
              {[...new Set(portfolioContracts.map((pc) => pc.region))].map((r) => (
                <Badge key={r} text={r === "APAC_TT" ? "APAC-TT" : r} color={T.purple} bg={T.purpleBg} />
              ))}
              <Badge text={selectedContract.channel} color={T.info} bg={T.infoBg} />
              <Badge text={BUCKET_LABEL[selectedContract.bucket]} color={T.inkMuted} bg={T.surfaceSunken} />
              <Badge text={`${selectedContract.segment} · ${selectedContract.riskScore}`} color={SEGMENT_COLOR[selectedContract.segment]} bg={SEGMENT_BG[selectedContract.segment]} />
            </div>

            {selectedContract.bucket === "Lost" && selectedContract.lostReasons && selectedContract.lostReasons.length > 0 && (
              <Card style={{ padding: 14, marginBottom: 16, background: T.riskBg, borderColor: T.risk }}>
                <div style={{ fontSize: 11.5, fontWeight: 700, color: T.risk, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 6 }}>
                  Reason for loss — top service issues
                </div>
                <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: T.ink, lineHeight: 1.6 }}>
                  {selectedContract.lostReasons.map((reason, i) => <li key={i}>{reason}</li>)}
                </ol>
                <div style={{ fontSize: 10.5, color: T.inkMuted, marginTop: 8, fontStyle: "italic" }}>
                  Rule-based, ranked by issue severity and SLA performance (not AI generated).
                </div>
              </Card>
            )}

            {portfolioContracts.length > 1 && (
              <>
                <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>
                  Customer portfolio — {portfolioContracts.length} contracts
                </div>
                <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
                  <table>
                    <thead><tr>
                      <SortTh label="Contract" sortKey="contractId" sort={portfolioSort} />
                      <SortTh label="Region" sortKey="region" sort={portfolioSort} />
                      <SortTh label="Bucket" sortKey="bucket" sort={portfolioSort} />
                      <SortTh label="Value" sortKey="contractValue" sort={portfolioSort} />
                      <SortTh label="Risk" sortKey="riskScore" sort={portfolioSort} />
                    </tr></thead>
                    <tbody>
                      {sortedPortfolioContracts.map((pc) => (
                        <tr
                          key={pc.contractId}
                          className="rowhover"
                          style={{ cursor: "pointer", background: pc.contractId === selectedContract.contractId ? T.surfaceSunken : "transparent" }}
                          onClick={() => setSelected(pc.contractId)}
                        >
                          <td style={{ fontWeight: pc.contractId === selectedContract.contractId ? 700 : 500 }}>{pc.contractId}</td>
                          <td><Badge text={pc.region === "APAC_TT" ? "APAC-TT" : pc.region} color={T.purple} bg={T.purpleBg} /></td>
                          <td><Badge text={BUCKET_LABEL[pc.bucket]} color={T.inkMuted} bg={T.surfaceSunken} /></td>
                          <td>${pc.contractValue.toLocaleString()}</td>
                          <td><Badge text={pc.segment} color={SEGMENT_COLOR[pc.segment]} bg={SEGMENT_BG[pc.segment]} /></td>
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

            {/* Globally available regardless of which drawer tab is active */}
            <CachedAgentCard
              title="Customer Summary (AI Generated)"
              record={customerSummaries[selectedContract.customerId]}
              onGenerate={() => runCustomerSummary(selectedContract.customerId)}
              loadingLabel="Reading full customer relationship"
              placeholderLabel="Synthesizes this customer's entire portfolio (all contracts) into one relationship summary. Generated automatically during the next batch run if you skip this."
            />

            <div style={{ display: "flex", gap: 18, borderBottom: `1px solid ${T.border}`, marginBottom: 16 }}>
              <button className={`tabbtn ${drawerTab === "action" ? "active" : ""}`} onClick={() => setDrawerTab("action")}>Recommended Action</button>
              <button className={`tabbtn ${drawerTab === "portfolio" ? "active" : ""}`} onClick={() => setDrawerTab("portfolio")}>Customer Portfolio</button>
              <button className={`tabbtn ${drawerTab === "service" ? "active" : ""}`} onClick={() => setDrawerTab("service")}>Service History</button>
              <button className={`tabbtn ${drawerTab === "evaluation" ? "active" : ""}`} onClick={() => setDrawerTab("evaluation")}>AI Evaluation</button>
            </div>

            {drawerTab === "action" && (
              <>
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
                    <div style={{ fontSize: 11.5, color: T.inkMuted, marginTop: 8 }}>This contract-milestone was not marked as processed — it will be retried on the next batch run.</div>
                  </Card>
                )}

                {selectedTrace && !selectedTrace.error && (
                  <>
                    <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Recommendation (AI Generated)</div>
                    <Card style={{ padding: 14, marginBottom: 14, background: T.surfaceSunken }}>
                      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{selectedTrace.recommendation?.campaign}</div>
                      <div style={{ fontSize: 12.5, color: T.inkMuted, marginBottom: 8 }}>Owner: {selectedTrace.recommendation?.execution_owner}</div>
                      <div style={{ fontSize: 13 }}>{selectedTrace.recommendation?.rationale}</div>
                      {selectedTrace.recommendation?.upsell && selectedTrace.recommendation.upsell !== "Not recommended for this account right now" && (
                        <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${T.border}`, fontSize: 12.5 }}>
                          <b>Upsell:</b> {selectedTrace.recommendation.upsell}
                        </div>
                      )}
                    </Card>

                    <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>Log outcome</div>
                    <Card style={{ padding: 14, marginBottom: 14 }}>
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
                        placeholder="Optional rep note…"
                        value={selectedTrace.outcomeNote}
                        onChange={(e) => setOutcome(selectedContract.contractId, selectedTrace.outcome, e.target.value)}
                        style={{ width: "100%", minHeight: 60, border: `1px solid ${T.border}`, borderRadius: 7, padding: 8, fontSize: 12.5, fontFamily: "inherit", resize: "vertical" }}
                      />
                    </Card>
                    <EscalationPanel record={selectedTrace} onToggleAction={toggleActionStatus} />

                    <DraftContent content={selectedTrace.content} contentError={selectedTrace.contentError} customerName={selectedContract.customerName} />
                  </>
                )}
              </>
            )}

            {drawerTab === "portfolio" && (
              <>
                <RiskFactorBreakdown factors={selectedContract.riskFactors} />
                <CustomerFeedbackPanel feedback={selectedContract.customerFeedback} trend={selectedContract.feedbackTrend} />
              </>
            )}

            {drawerTab === "service" && (
              <>
                <CachedAgentCard
                  title="Service Ticket Summary (AI Generated)"
                  record={ticketSummaries[selectedContract.contractId]}
                  onGenerate={() => runTicketSummary(selectedContract.contractId)}
                  loadingLabel="Reading service ticket history"
                  placeholderLabel="Synthesizes all service tickets for this contract into one summary, fed into the recommendation agent. Generated automatically during the next batch run if you skip this."
                />
                <ServiceTicketHistory equipment={selectedContract.equipment} tickets={selectedContract.serviceTickets} />
              </>
            )}

            {drawerTab === "evaluation" && (
              <>
                {!selectedTrace && (
                  <div style={{ fontSize: 13, color: T.inkFaint, padding: 14, background: T.surfaceSunken, borderRadius: 8 }}>
                    No evaluation available yet — run a recommendation first.
                  </div>
                )}

                {selectedTrace && !selectedTrace.error && (
                  <>
                    <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>AI Result Evaluation</div>
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

                  </>
                )}

                <AgentInspector attempts={selectedTrace?.attempts} />
              </>
            )}
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
