import React, { useState, useMemo, useCallback, useRef, useEffect } from "react";
import {
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, Legend, LabelList
} from "recharts";
import { X, ChevronRight, ChevronDown, AlertTriangle, CheckCircle2, RotateCcw, Clock, Coins, Send, Maximize2, Minimize2, Search } from "lucide-react";
import { api } from "./api.js";

/* ---------------------------------------------------------------
   DESIGN TOKENS
   Internal ops tool for sales reps / account managers. Functional,
   data-dense, industrial - not a marketing page. Cool graphite
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
  // (PMS 072C / #142C73, per Carrier's 2013 Brand Identity Guidelines) -
  // this naturally carries through to every channel badge, link, and
  // active-state surface that already used this token.
  info: "#142C73",
  infoBg: "#E6E9F2",
  amber: "#B8863B",
  amberBg: "#F7EFE1",
  purple: "#5A4FB0",
  purpleBg: "#EEECFA",
  // Explicit brand tokens for primary chrome (buttons, active tab, header
  // accent) - same Carrier Blue, kept separate from "info" for clarity
  // about which usages are brand-driven vs. semantic.
  brand: "#142C73",
  brandBg: "#E6E9F2",
  brandLight: "#4A63A8",
};

// Red / Yellow / Green / Blue per the risk x value quadrant scheme. Reuses
// existing theme tokens (T.info/T.infoBg - already the app's blue, used for
// "Medium" priority elsewhere) rather than introducing new colors. Changing
// this one map recolors every segment badge in the app.
const SEGMENT_COLOR = { "High Risk": T.risk, "At Risk": T.amber, "Healthy": T.safe, "Standard": T.info };
const SEGMENT_BG = { "High Risk": T.riskBg, "At Risk": T.amberBg, "Healthy": T.safeBg, "Standard": T.infoBg };
const BUCKETS = [">90", "90", "60", "45", "30", "10", "Lost"];
const BUCKET_LABEL = {
  ">90": "Not yet due", "90": "Expiring in \u226490 days", "60": "Expiring in \u226460 days",
  "45": "Expiring in \u226445 days", "30": "Expiring in \u226430 days", "10": "Expiring in \u226410 days", "Lost": "Lost / expired",
};
// Fuller sentence for a hover tooltip on the bucket cards specifically -
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

// Table sorting - one hook + one comparator + one header cell, reused by
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
  // customers and dealers have no email field) - a slugified customer name at
  // a placeholder domain stands in for it, per the agreed placeholder convention.
  // This opens whatever the browser/OS has set as the default mail handler
  // (Outlook, if that's the user's default) with the fields below prefilled.
  const recipientEmail = `${(customerName || "customer").toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.+|\.+$/g, "")}@client.com`;
  // Outlook Web's own compose URL, not mailto: - mailto: always goes through
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


function ServiceTicketHistory({ equipment, claims }) {
  return (
    <>
      <div style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6 }}>
        Historical service claims
      </div>
      <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
        <div style={{ padding: "9px 14px", borderBottom: `1px solid ${T.border}`, fontSize: 11.5, color: T.inkMuted, background: T.surfaceSunken }}>
          {equipment.type} · {equipment.count} unit{equipment.count === 1 ? "" : "s"} · avg {equipment.avgAgeYears}y old
        </div>
        {(!claims || claims.length === 0) ? (
          <div style={{ padding: 16, fontSize: 12, color: T.inkFaint }}>No service claims on record.</div>
        ) : (
          <div style={{ maxHeight: 240, overflowY: "auto" }}>
            {claims.map((c, i) => (
              <div
                key={i}
                style={{
                  display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
                  borderBottom: i < claims.length - 1 ? `1px solid ${T.border}` : "none", fontSize: 12.5,
                }}
              >
                <div style={{ width: 78, flexShrink: 0, fontFamily: "ui-monospace, monospace", fontSize: 11, color: T.inkFaint }}>{c.date}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>{c.issue}</span>
                  <span style={{ color: T.inkMuted }}> · {c.faultId}</span>
                </div>
                {c.jobWrittenOff && <Badge text="Written off" color={T.risk} bg={T.riskBg} />}
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
  const FACTOR_MAX = { "Repeat issue": 20, "Claim frequency": 15, "Claim recency": 16, "Written-off ratio": 14, "Coverage gap": 6, "Equipment age": 13, "Warranty status": 6, "Price increase": 10 };
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
            {onGenerate && <button onClick={onGenerate} style={{ marginTop: 10, border: "none", background: "none", color: T.info, fontSize: 11.5, fontWeight: 600, cursor: "pointer", padding: 0 }}>Regenerate</button>}
          </>
        )}
        {status === "loading" && <div style={{ fontSize: 12.5, color: T.inkFaint }}>{loadingLabel}…</div>}
        {status === "error" && (
          <>
            <div style={{ fontSize: 12.5, color: T.risk, marginBottom: 8 }}>{record.error}</div>
            {onGenerate && <button onClick={onGenerate} style={{ border: `1px solid ${T.border}`, background: "#fff", borderRadius: 6, padding: "5px 10px", fontSize: 11.5, fontWeight: 600, cursor: "pointer" }}>Retry</button>}
          </>
        )}
        {(!status) && (
          <>
            <div style={{ fontSize: 12.5, color: T.inkFaint, marginBottom: 10 }}>{placeholderLabel}</div>
            {onGenerate && <button onClick={onGenerate} style={{ border: "none", background: T.ink, color: "#fff", borderRadius: 6, padding: "6px 12px", fontSize: 11.5, fontWeight: 600, cursor: "pointer" }}>Generate</button>}
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

// Ported from architecture_diagrams.html's <section id="flow">, including its
// drag-to-rearrange behavior (originally a page-global <script> operating on
// document.querySelectorAll) - scoped to this component's own ref here so it
// can't interfere with (or be interfered with by) anything else on the page,
// and cleaned up on unmount instead of being a bare global side effect.
function ApplicationFlowDiagram() {
  const containerRef = useRef(null);

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;

    const getOffset = (el) => {
      const parts = (el.getAttribute("data-tx") || "0,0").split(",").map(Number);
      return { x: parts[0] || 0, y: parts[1] || 0 };
    };
    const updateConnectors = (nodeId) => {
      root.querySelectorAll(`line[data-from="${nodeId}"], line[data-to="${nodeId}"]`).forEach((line) => {
        const fromNode = root.querySelector(`#${line.getAttribute("data-from")}`);
        const toNode = root.querySelector(`#${line.getAttribute("data-to")}`);
        if (!fromNode || !toNode) return;
        const fo = getOffset(fromNode), to = getOffset(toNode);
        const fa = line.getAttribute("data-from-anchor").split(",").map(Number);
        const ta = line.getAttribute("data-to-anchor").split(",").map(Number);
        line.setAttribute("x1", fa[0] + fo.x);
        line.setAttribute("y1", fa[1] + fo.y);
        line.setAttribute("x2", ta[0] + to.x);
        line.setAttribute("y2", ta[1] + to.y);
      });
    };

    const teardowns = [];
    root.querySelectorAll(".draggable-node").forEach((node) => {
      let dragging = false, startX = 0, startY = 0, origX = 0, origY = 0;
      const onPointerDown = (e) => {
        dragging = true;
        node.setPointerCapture(e.pointerId);
        startX = e.clientX; startY = e.clientY;
        const o = getOffset(node);
        origX = o.x; origY = o.y;
        node.parentNode.appendChild(node); // bring to front while dragging
        e.preventDefault();
      };
      const onPointerMove = (e) => {
        if (!dragging) return;
        const svg = node.closest("svg");
        const rect = svg.getBoundingClientRect();
        const vb = svg.viewBox.baseVal;
        const scaleX = vb.width / rect.width;
        const scaleY = vb.height / rect.height;
        const nx = origX + (e.clientX - startX) * scaleX;
        const ny = origY + (e.clientY - startY) * scaleY;
        node.setAttribute("transform", `translate(${nx},${ny})`);
        node.setAttribute("data-tx", `${nx},${ny}`);
        updateConnectors(node.id);
      };
      const stop = () => { dragging = false; };
      node.addEventListener("pointerdown", onPointerDown);
      node.addEventListener("pointermove", onPointerMove);
      node.addEventListener("pointerup", stop);
      node.addEventListener("pointercancel", stop);
      teardowns.push(() => {
        node.removeEventListener("pointerdown", onPointerDown);
        node.removeEventListener("pointermove", onPointerMove);
        node.removeEventListener("pointerup", stop);
        node.removeEventListener("pointercancel", stop);
      });
    });

    return () => teardowns.forEach((fn) => fn());
  }, []);

  return (
    <Card style={{ padding: "22px 26px", marginBottom: 18, borderTop: `3px solid ${T.brand}` }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, color: T.brand, textTransform: "uppercase", marginBottom: 6 }}>How it runs</div>
      <h3 style={{ fontSize: 15, fontWeight: 700, margin: "0 0 4px" }}>Application flow - daily batch run</h3>
      <p style={{ fontSize: 12.5, color: T.inkMuted, margin: "0 0 10px", maxWidth: 700 }}>
        What happens end to end when the daily batch is triggered.
      </p>
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 600, color: T.brand,
        background: T.brandBg || T.infoBg, borderRadius: 20, padding: "4px 10px", marginBottom: 14,
      }}>
        ↕ Drag any box to rearrange
      </div>
      <div ref={containerRef} style={{ border: `1px solid ${T.border}`, borderRadius: 10, padding: 20 }}>
        <svg viewBox="0 0 680 220" role="img" style={{ display: "block", width: "100%", height: "auto" }}>
          <title>Application flow for the daily batch run</title>
          <desc>Daily batch trigger runs the agent graph for each due contract-milestone, which logs a trace and draft content, a rep reviews and takes action, then logs the outcome, which feeds the next milestone.</desc>
          <defs>
            <marker id="afArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#5B6472" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></marker>
            <marker id="afArrowRed" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#C1502E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></marker>
          </defs>

          <line id="af-l1" data-from="af-trigger" data-to="af-graph" data-from-anchor="140,58" data-to-anchor="168,58" x1="140" y1="58" x2="168" y2="58" stroke="#5B6472" strokeWidth="1.5" markerEnd="url(#afArrow)" />
          <line id="af-l2" data-from="af-graph" data-to="af-trace" data-from-anchor="320,58" data-to-anchor="348,58" x1="320" y1="58" x2="348" y2="58" stroke="#5B6472" strokeWidth="1.5" markerEnd="url(#afArrow)" />
          <line id="af-l3" data-from="af-trace" data-to="af-rep" data-from-anchor="490,58" data-to-anchor="518,58" x1="490" y1="58" x2="518" y2="58" stroke="#5B6472" strokeWidth="1.5" markerEnd="url(#afArrow)" />
          <line id="af-l4" data-from="af-rep" data-to="af-outcome" data-from-anchor="590,86" data-to-anchor="590,124" x1="590" y1="86" x2="590" y2="124" stroke="#5B6472" strokeWidth="1.5" markerEnd="url(#afArrow)" />
          <path id="af-l5" d="M500 154 L245 154 L245 88" fill="none" stroke="#D85A30" strokeWidth="1.5" markerEnd="url(#afArrowRed)" />

          <g className="draggable-node" id="af-trigger" data-tx="0,0" transform="translate(0,0)" style={{ cursor: "grab" }}>
            <rect x="20" y="30" width="120" height="56" rx="8" fill="#F1EFE8" stroke="#B4B2A9" />
            <text x="80" y="50" textAnchor="middle" dominantBaseline="central" fontSize="12" fontWeight="600" fill="#2C2C2A">Daily batch</text>
            <text x="80" y="68" textAnchor="middle" dominantBaseline="central" fontSize="10" fill="#5F5E5A">Due contracts only</text>
          </g>
          <g className="draggable-node" id="af-graph" data-tx="0,0" transform="translate(0,0)" style={{ cursor: "grab" }}>
            <rect x="170" y="30" width="150" height="56" rx="8" fill="#EEEDFE" stroke="#7F77DD" />
            <text x="245" y="50" textAnchor="middle" dominantBaseline="central" fontSize="12" fontWeight="600" fill="#26215C">Agent graph</text>
            <text x="245" y="68" textAnchor="middle" dominantBaseline="central" fontSize="10" fill="#3C3489">Recommend → evaluate → draft</text>
          </g>
          <g className="draggable-node" id="af-trace" data-tx="0,0" transform="translate(0,0)" style={{ cursor: "grab" }}>
            <rect x="350" y="30" width="140" height="56" rx="8" fill="#E1F5EE" stroke="#5DCAA5" />
            <text x="420" y="50" textAnchor="middle" dominantBaseline="central" fontSize="12" fontWeight="600" fill="#04342C">Trace + draft</text>
            <text x="420" y="68" textAnchor="middle" dominantBaseline="central" fontSize="10" fill="#085041">Logged for review</text>
          </g>
          <g className="draggable-node" id="af-rep" data-tx="0,0" transform="translate(0,0)" style={{ cursor: "grab" }}>
            <rect x="520" y="30" width="140" height="56" rx="8" fill="#E1F5EE" stroke="#5DCAA5" />
            <text x="590" y="50" textAnchor="middle" dominantBaseline="central" fontSize="12" fontWeight="600" fill="#04342C">Rep reviews</text>
            <text x="590" y="68" textAnchor="middle" dominantBaseline="central" fontSize="10" fill="#085041">Sends draft or escalates</text>
          </g>
          <g className="draggable-node" id="af-outcome" data-tx="0,0" transform="translate(0,0)" style={{ cursor: "grab" }}>
            <rect x="500" y="126" width="160" height="56" rx="8" fill="#FAECE7" stroke="#F0997B" />
            <text x="580" y="146" textAnchor="middle" dominantBaseline="central" fontSize="12" fontWeight="600" fill="#4A1B0C">Outcome logged</text>
            <text x="580" y="164" textAnchor="middle" dominantBaseline="central" fontSize="10" fill="#712B13">Feeds next milestone</text>
          </g>
        </svg>
      </div>
      <div style={{ fontSize: 11, color: T.inkFaint, marginTop: 8 }}>Drag any box above to rearrange for a presentation.</div>
    </Card>
  );
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

      {/* $ lost (Declined only) and $ converted (Engaged) per risk bucket -
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
   PAGED DATA AND THE WORKLIST
   The browser never receives "all contracts". The worklist arrives one page at a time
   (the server keeps the position in an opaque cursor) and is drawn with a window of
   ~30 rows however far you scroll; every KPI, count and chart comes pre-aggregated
   from /api/summary. Filters are one object shared by both tabs.
----------------------------------------------------------------*/
const SEGMENTS = ["High Risk", "At Risk", "Healthy", "Standard"];
const PAGE_SIZE = 50;
const ROW_H = 42;        // fixed row height is what lets the list render only the rows in view
const LIST_H = 440;
const OVERSCAN = 8;
const PREFETCH = 20;     // ask for the next page when this close to the end of what's loaded
const EMPTY_FILTERS = { area: [], channel: [], segment: [], bucket: null, rb: null, vb: null };
const ZERO_KPIS = {
  contracts: 0, customers: 0, value: 0, segments: { "High Risk": 0, "At Risk": 0, Healthy: 0, Standard: 0 },
  lostCount: 0, lostValue: 0, atRiskValue: 0, convertedValue: 0, actionsNeeded: 0, responseRate: null,
};
// Heatmap value rows, top to bottom (the server's band numbers): >= 2x the area's median ... no value.
const VALUE_ROWS = [3, 2, 1, 0, 4];
const VALUE_LABEL = { 3: "≥ 2× median", 2: "1–2× median", 1: "0.5–1× median", 0: "< 0.5× median", 4: "No value" };

function useDebounced(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

// Pages through /api/worklist. Any change to `params` (or `extraKey`) starts over from the top and
// cancels whatever was in flight, so a slow answer to an old filter can never overwrite a newer one.
// Loaded rows stay in memory as the user scrolls: ~1 KB each, so even 20,000 rows is only ~20 MB.
// (ponytail: no eviction of far-away pages; add a page cap if anyone really scrolls past ~20k rows)
function useInfiniteRows(params, enabled, extraKey) {
  const key = JSON.stringify([params, extraKey]);
  const [s, setS] = useState({ rows: [], cursor: null, done: false, loading: false, error: null, stale: false });
  const ctrl = useRef(null);
  const latest = useRef(s);
  latest.current = s;

  const fetchPage = useCallback((cursor, reset) => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setS((p) => ({ ...(reset ? { rows: [], cursor: null, done: false } : p), loading: true, error: null, stale: false }));
    api.getWorklist({ ...JSON.parse(key)[0], limit: PAGE_SIZE, cursor }, c.signal).then((res) => {
      if (c.signal.aborted) return;
      setS((p) => ({ ...p, rows: reset ? res.rows : [...p.rows, ...res.rows], cursor: res.nextCursor, done: !res.nextCursor, loading: false }));
    }).catch((e) => {
      if (e.name === "AbortError") return;
      // 409: a new data load landed mid-scroll - offer a refresh rather than mixing two versions.
      setS((p) => ({ ...p, loading: false, stale: e.status === 409, error: e.status === 409 ? null : String(e.message || e) }));
    });
  }, [key]);

  useEffect(() => {
    if (!enabled) return undefined;
    fetchPage(null, true);
    return () => ctrl.current?.abort();
  }, [fetchPage, enabled]);

  const loadMore = useCallback(() => {
    const p = latest.current;
    if (!p.loading && !p.done && !p.stale && !p.error) fetchPage(p.cursor, false);
  }, [fetchPage]);
  const reload = useCallback(() => fetchPage(null, true), [fetchPage]);
  const retry = useCallback(() => fetchPage(latest.current.cursor, latest.current.rows.length === 0), [fetchPage]);
  return { ...s, loadMore, reload, retry };
}

function fmtCell(key, kind, v) {
  if (v === null || v === undefined || v === "") return "—";
  if (kind === "bool") return v ? "Yes" : "No";
  if (kind === "date") return new Date(`${v}T00:00:00`).toLocaleDateString();
  if (key === "annual_contract_value") return `$${Math.round(v).toLocaleString()}`;
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}

function fmtDetail(v) {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}

function StatusCell({ rec }) {
  if (!rec) return <span style={{ color: T.inkFaint, fontSize: 12 }}>Not run</span>;
  if (rec.escalated) {
    return (
      <span style={{ display: "flex", gap: 5, alignItems: "center" }}>
        <Badge text="Escalated" color={T.risk} bg={T.riskBg} />
        {rec.status === "Action required"
          ? <Badge text="Action required" color={T.amber} bg={T.amberBg} />
          : <Badge text="Done" color={T.safe} bg={T.safeBg} />}
      </span>
    );
  }
  return <Badge text="Recommended" color={T.safe} bg={T.safeBg} />;
}

// The seven expiry milestones; clicking one filters the worklist and every figure below it.
function BucketCards({ counts, value, onPick }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0,1fr))", gap: 10, marginBottom: 18 }}>
      {BUCKETS.map((b) => (
        <Card
          key={b}
          onClick={() => onPick(b)}
          title={BUCKET_TOOLTIP[b]}
          style={{ padding: "12px 14px", borderColor: value === b ? T.ink : T.border, borderWidth: value === b ? 1.5 : 1 }}
        >
          <div style={{ fontSize: 11, color: T.inkFaint, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.3 }}>{BUCKET_LABEL[b]}</div>
          <div style={{ fontSize: 24, fontWeight: 700, marginTop: 2, color: b === "Lost" ? T.risk : T.ink }}>{(counts?.[b] ?? 0).toLocaleString()}</div>
        </Card>
      ))}
    </div>
  );
}

// One filter state for the whole page: it narrows the worklist, the KPIs, the charts and the
// bucket cards on both tabs at once.
function FilterBar({ filters, onChange, areaOptions, extra }) {
  const set = (k) => (v) => onChange((f) => ({ ...f, [k]: v }));
  const cell = filters.rb !== null && filters.vb !== null;
  const active = filters.area.length || filters.channel.length || filters.segment.length || filters.bucket || filters.rb !== null || filters.vb !== null;
  const chip = { display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, background: T.brandBg, color: T.brand, borderRadius: 99, padding: "4px 6px 4px 10px" };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
      <span style={{ fontSize: 11.5, fontWeight: 700, color: T.inkFaint, textTransform: "uppercase", letterSpacing: 0.4 }}>Filters</span>
      {areaOptions.length > 1 && <MultiSelect label="areas" options={areaOptions} selected={filters.area} onChange={set("area")} />}
      <MultiSelect label="channels" options={[{ value: "Dealer", label: "Dealer" }, { value: "Direct", label: "Direct" }]} selected={filters.channel} onChange={set("channel")} />
      <MultiSelect label="segments" options={SEGMENTS.map((s) => ({ value: s, label: s }))} selected={filters.segment} onChange={set("segment")} />
      {extra}
      {(cell || filters.rb !== null) && (
        <span style={chip}>
          Risk {filters.rb * 10}–{filters.rb * 10 + 9}{filters.vb !== null && ` · ${VALUE_LABEL[filters.vb]}`}
          <button onClick={() => onChange((f) => ({ ...f, rb: null, vb: null }))} style={{ border: "none", background: "none", cursor: "pointer", display: "flex", padding: 0 }}><X size={13} color={T.brand} /></button>
        </span>
      )}
      {active && (
        <button onClick={() => onChange(EMPTY_FILTERS)} style={{ border: "none", background: "none", fontSize: 12, color: T.info, cursor: "pointer", whiteSpace: "nowrap" }}>Clear all filters</button>
      )}
    </div>
  );
}

// Replaces the old scatter plot (one SVG point per contract - unusable at 300K): the same two
// questions - how risky, how valuable - answered as counts per cell. Columns are risk bands
// (the heavy lines are the 30 / 50 / 70 segment cut-offs); rows are contract value against the
// median of the contract's own area (the heavy line is the median).
function RiskValueHeatmap({ cells, rb, vb, onPick, mode }) {
  const at = {};
  (cells || []).forEach((c) => { at[`${c.rb}:${c.vb}`] = c; });
  const amount = (c) => (mode === "value" ? c.value : c.n);
  const max = Math.max(1, ...(cells || []).map(amount));
  const fmt = (n) => (mode === "value" ? (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(Math.round(n))) : n.toLocaleString());
  const cutoff = (band) => (band === 3 || band === 5 || band === 7 ? `2px solid ${T.borderStrong}` : `1px solid ${T.border}`);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "92px repeat(10, minmax(0, 1fr))", gap: 0, fontSize: 10.5 }}>
      {VALUE_ROWS.map((v) => (
        <React.Fragment key={v}>
          <div style={{ color: T.inkFaint, display: "flex", alignItems: "center", justifyContent: "flex-end", paddingRight: 8, height: 34 }}>{VALUE_LABEL[v]}</div>
          {Array.from({ length: 10 }, (_, band) => {
            const c = at[`${band}:${v}`];
            const share = c ? amount(c) / max : 0;
            const picked = rb === band && vb === v;
            return (
              <div
                key={band}
                onClick={() => c && onPick(band, v)}
                title={c ? `${c.n.toLocaleString()} contracts · $${Math.round(c.value).toLocaleString()}` : "none"}
                style={{
                  position: "relative", height: 34, cursor: c ? "pointer" : "default", borderLeft: cutoff(band), borderBottom: v === 2 ? `2px solid ${T.borderStrong}` : `1px solid ${T.border}`,
                  outline: picked ? `2px solid ${T.ink}` : "none", outlineOffset: -2, display: "flex", alignItems: "center", justifyContent: "center",
                }}
              >
                {c && <div style={{ position: "absolute", inset: 0, background: T.brand, opacity: 0.07 + 0.83 * share }} />}
                {c && <span style={{ position: "relative", fontWeight: 600, color: share > 0.45 ? "#fff" : T.ink }}>{fmt(amount(c))}</span>}
              </div>
            );
          })}
        </React.Fragment>
      ))}
      <div />
      {Array.from({ length: 10 }, (_, band) => (
        <div key={band} style={{ textAlign: "center", color: T.inkFaint, paddingTop: 4 }}>{band * 10}</div>
      ))}
    </div>
  );
}

function Worklist({ params, view, extraKey, total, search, onSearch, onSort, onColumns, onOpen, overrides, areaLabel }) {
  const list = useInfiniteRows(params, !!view, extraKey);
  const { rows, done, loading, error, stale, loadMore } = list;
  const box = useRef(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [note, setNote] = useState(null);
  const key = JSON.stringify([params, extraKey]);

  useEffect(() => { // a new query starts at the top
    if (box.current) box.current.scrollTop = 0;
    setScrollTop(0);
  }, [key]);

  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const end = Math.min(rows.length, Math.ceil((scrollTop + LIST_H) / ROW_H) + OVERSCAN);
  useEffect(() => { // near the end of what's loaded: fetch the next page
    if (rows.length && end >= rows.length - PREFETCH) loadMore();
  }, [end, rows.length, done, loading, loadMore]);

  const info = Object.fromEntries(view.available.map((a) => [a.key, a]));
  const label = { contractid: "Contract", risk_score: "Risk", ...Object.fromEntries(view.available.map((a) => [a.key, a.label])) };
  const sortable = new Set([...view.available.filter((a) => a.sortable).map((a) => a.key), "risk_score"]);
  const virtual = ["recommended_action", "action_status"];
  const columns = [
    ...view.columns.filter((c) => c === "company"), "contractid",
    ...view.columns.filter((c) => c !== "company" && !virtual.includes(c)), "risk_score",
    ...view.columns.filter((c) => virtual.includes(c)),
  ];
  const sort = [view.sort.key, view.sort.dir, onSort];

  const cell = (r, c) => {
    const rec = overrides[r.id] ? { ...(r.rec || {}), ...overrides[r.id] } : r.rec;
    if (c === "company") return <span style={{ fontWeight: 600 }}>{r.company ?? "—"}</span>;
    if (c === "contractid") return <span style={{ color: T.inkMuted, fontFamily: "ui-monospace, monospace", fontSize: 12 }}>{r.contractid}</span>;
    if (c === "risk_score") return r.segment ? <Badge text={`${r.segment} · ${r.risk_score ?? "—"}`} color={SEGMENT_COLOR[r.segment]} bg={SEGMENT_BG[r.segment]} /> : "—";
    if (c === "ctxid") return <span title={areaLabel(r.ctxid)}>{r.ctxid ?? "—"}</span>;
    if (c === "recommended_action") {
      return (
        <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
          {r.excl?.length > 0 && <span title={`Excluded by: ${r.excl.join(", ")}`}><Badge text="Excluded" color={T.risk} bg={T.riskBg} /></span>}
          {(rec?.name || !r.excl?.length) && <Badge text={rec?.name || "-"} bg={T.purpleBg} />}
        </span>
      );
    }
    if (c === "action_status") return <StatusCell rec={rec} />;
    return fmtCell(c, info[c]?.kind, r[c]);
  };

  const pad = (h) => <tr style={{ height: h }}><td colSpan={columns.length + 1} style={{ padding: 0, border: "none" }} /></tr>;
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "14px 16px", borderBottom: `1px solid ${T.border}`, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ fontSize: 13.5, fontWeight: 700 }}>
          Worklist
          <span style={{ fontWeight: 500, color: T.inkFaint, fontSize: 12, marginLeft: 8 }}>
            {params.q ? `${rows.length.toLocaleString()}${done ? "" : "+"} matching` : total != null ? `${total.toLocaleString()} contracts` : ""}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {note && <span style={{ fontSize: 11.5, color: T.amber }}>{note}</span>}
          <MultiSelect
            label="columns"
            options={view.available.map((a) => ({ value: a.key, label: a.label }))}
            selected={view.columns}
            onChange={(next) => {
              if (next.length > view.maxColumns) { setNote(`At most ${view.maxColumns} columns`); return; }
              setNote(null);
              onColumns(next);
            }}
          />
          <div style={{ position: "relative" }}>
            <Search size={13} style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", color: T.inkFaint }} />
            <input
              type="text"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              placeholder="Search customer, contract, unit…"
              style={{ border: `1px solid ${T.border}`, borderRadius: 7, padding: "6px 10px 6px 28px", fontSize: 12.5, fontFamily: "inherit", width: 230, color: T.ink }}
            />
            {search && (
              <button onClick={() => onSearch("")} style={{ position: "absolute", right: 7, top: "50%", transform: "translateY(-50%)", border: "none", background: "none", cursor: "pointer", padding: 2, color: T.inkFaint, display: "flex" }}>
                <X size={13} />
              </button>
            )}
          </div>
        </div>
      </div>
      {stale && (
        <div style={{ padding: "8px 16px", background: T.amberBg, fontSize: 12.5, display: "flex", gap: 10, alignItems: "center" }}>
          New data has been loaded since this list was opened.
          <button onClick={list.reload} style={{ ...smallBtn }}>Refresh</button>
        </div>
      )}
      <div ref={box} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)} style={{ height: LIST_H, overflowY: "auto" }}>
        <table className="stickyhead">
          <thead><tr>
            {columns.map((c) => (sortable.has(c)
              ? <SortTh key={c} label={label[c] ?? c} sortKey={c} sort={sort} />
              : <th key={c} style={{ whiteSpace: "nowrap" }}>{label[c] ?? c}</th>))}
            <th></th>
          </tr></thead>
          <tbody>
            {start > 0 && pad(start * ROW_H)}
            {rows.slice(start, end).map((r) => (
              <tr key={r.id} className="rowhover" style={{ cursor: "pointer", height: ROW_H }} onClick={() => onOpen(r.id)}>
                {columns.map((c) => (
                  <td key={c} style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 220 }}>{cell(r, c)}</td>
                ))}
                <td><ChevronRight size={15} color={T.inkFaint} /></td>
              </tr>
            ))}
            {end < rows.length && pad((rows.length - end) * ROW_H)}
          </tbody>
        </table>
        <div style={{ textAlign: "center", padding: 14, color: T.inkFaint, fontSize: 12.5 }}>
          {loading && (rows.length ? "Loading more…" : "Loading…")}
          {error && <>{error} <button onClick={list.retry} style={smallBtn}>Retry</button></>}
          {!loading && !error && rows.length === 0 && (params.q ? `No contracts match "${params.q}".` : "No contracts match the current filters.")}
          {!loading && !error && done && rows.length > 0 && `End of list · ${rows.length.toLocaleString()} contracts`}
        </div>
      </div>
    </Card>
  );
}

/* ---------------------------------------------------------------
   RULES: EXCLUSION SETS AND RETENTION ACTIONS
   Both are defined by the same kind of criteria - conditions on any contract column, stacked like
   Excel filters (different columns are AND-ed, several values of one column are OR-ed) - so they
   share one builder. The server validates everything; what is offered here (columns, operators,
   value lists) comes from the server too.
----------------------------------------------------------------*/
const OP_ORDER = ["in", "eq", "ne", "gt", "gte", "lt", "lte", "between", "contains", "starts_with", "is_null"];
const OP_LABEL = { in: "is one of", eq: "equals", ne: "does not equal", gt: ">", gte: "≥", lt: "<", lte: "≤", between: "between", contains: "contains", starts_with: "starts with", is_null: "is empty / not empty" };
const field = { border: `1px solid ${T.border}`, borderRadius: 7, padding: "6px 8px", fontSize: 12.5, fontFamily: "inherit", color: T.ink, background: "#fff" };
const defaultOp = (kind) => (kind === "bool" ? "eq" : kind === "text" || kind === "id" ? "in" : "gte");
const inputType = (kind) => (kind === "date" ? "date" : kind === "number" || kind === "int" ? "number" : "text");
const toRows = (criteria) => Object.entries(criteria || {}).map(([col, cond]) => (Array.isArray(cond)
  ? { col, op: "in", v: cond.map(String) } : { col, op: Object.keys(cond)[0], v: Object.values(cond)[0] }));
const fromRows = (rows) => Object.fromEntries(rows.filter((r) => r.col).map((r) => [r.col,
  r.op === "in" ? (Array.isArray(r.v) ? r.v : String(r.v ?? "").split(/[,\n]/).map((x) => x.trim()).filter(Boolean)) : { [r.op]: r.v ?? "" }]));

function describeCriteria(criteria, columns) {
  const label = Object.fromEntries(columns.map((c) => [c.key, c.label]));
  const parts = Object.entries(criteria || {}).map(([k, c]) => {
    if (Array.isArray(c)) return `${label[k] || k} is ${c.slice(0, 3).join(" / ")}${c.length > 3 ? ` (+${c.length - 3} more)` : ""}`;
    const [op, v] = Object.entries(c)[0];
    return `${label[k] || k} ${op === "is_null" ? (v ? "is empty" : "is not empty") : `${OP_LABEL[op]} ${[].concat(v).join(" and ")}`}`;
  });
  return parts.length ? parts.join(" · ") : "All contracts";
}

function ValueInput({ col, op, v, options, onChange }) {
  const t = inputType(col.kind);
  if (op === "is_null") return <select value={String(v)} onChange={(e) => onChange(e.target.value === "true")} style={field}><option value="true">is empty</option><option value="false">is not empty</option></select>;
  if (op === "between") {
    const [a, b] = Array.isArray(v) ? v : ["", ""];
    return <><input type={t} value={a} onChange={(e) => onChange([e.target.value, b])} style={field} /> and <input type={t} value={b} onChange={(e) => onChange([a, e.target.value])} style={field} /></>;
  }
  if (col.kind === "bool") return <select value={String(v)} onChange={(e) => onChange(e.target.value)} style={field}><option value="">choose…</option><option value="true">Yes</option><option value="false">No</option></select>;
  if (op === "in" && col.picker) {
    return <MultiSelect label="values" options={(options || []).map((o) => ({ value: o.value, label: `${o.value} (${o.count.toLocaleString()})` }))} selected={Array.isArray(v) ? v : []} onChange={onChange} />;
  }
  if (op === "in") return <input type="text" placeholder="comma-separated" value={Array.isArray(v) ? v.join(", ") : v ?? ""} onChange={(e) => onChange(e.target.value)} style={{ ...field, minWidth: 240 }} />;
  return <input type={t} value={v ?? ""} onChange={(e) => onChange(e.target.value)} style={{ ...field, minWidth: 160 }} />;
}

function CriteriaEditor({ columns, criteria, onChange }) {
  const [rows, setRows] = useState(() => toRows(criteria));
  const [values, setValues] = useState({});
  const byKey = Object.fromEntries(columns.map((c) => [c.key, c]));
  const loadValues = (key) => {
    if (byKey[key]?.picker && !values[key]) api.getColumnValues(key).then((list) => setValues((s) => ({ ...s, [key]: list }))).catch(() => {});
  };
  useEffect(() => { rows.forEach((r) => loadValues(r.col)); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const update = (next) => { setRows(next); onChange(fromRows(next)); };
  const set = (i, patch) => update(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const used = new Set(rows.map((r) => r.col));
  return (
    <div>
      {rows.map((r, i) => {
        const c = byKey[r.col];
        return (
          <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
            <select value={r.col} style={field} onChange={(e) => { const k = e.target.value; loadValues(k); set(i, { col: k, op: defaultOp(byKey[k].kind), v: "" }); }}>
              {!r.col && <option value="">Choose a column…</option>}
              {columns.filter((x) => x.key === r.col || !used.has(x.key)).map((x) => <option key={x.key} value={x.key}>{x.label}{x.personal ? " (personal data)" : ""}</option>)}
            </select>
            {c && (
              <select value={r.op} style={field} onChange={(e) => set(i, { op: e.target.value, v: e.target.value === "is_null" ? true : e.target.value === "between" ? ["", ""] : "" })}>
                {OP_ORDER.filter((o) => c.ops.includes(o)).map((o) => <option key={o} value={o}>{OP_LABEL[o]}</option>)}
              </select>
            )}
            {c && <ValueInput col={c} op={r.op} v={r.v} options={values[r.col]} onChange={(v) => set(i, { v })} />}
            <button title="Remove" onClick={() => update(rows.filter((_, j) => j !== i))} style={{ border: "none", background: "none", cursor: "pointer", display: "flex" }}><X size={14} color={T.inkFaint} /></button>
          </div>
        );
      })}
      <button onClick={() => update([...rows, { col: "", op: "in", v: "" }])} style={smallBtn}>+ Add a condition</button>
    </div>
  );
}

function Modal({ title, onClose, children }) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(22,27,34,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60, padding: 24 }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: T.surface, borderRadius: 12, padding: 22, width: "min(820px, 100%)", maxHeight: "90vh", overflow: "auto", boxShadow: "0 20px 60px rgba(0,0,0,0.25)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
          <button onClick={onClose} style={{ border: "none", background: "none", cursor: "pointer" }}><X size={18} color={T.inkFaint} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

const Labelled = ({ label, children, hint }) => (
  <div style={{ marginBottom: 14 }}>
    <div style={{ ...sectionLabel, marginBottom: 4 }}>{label}</div>
    {children}
    {hint && <div style={{ fontSize: 11.5, color: T.inkFaint, marginTop: 4 }}>{hint}</div>}
  </div>
);

// Turns the server's refusals into something a person can act on.
function explain(e, setError, setConfirm) {
  const d = e.detail;
  if (d && d.code === "confirm_required") { setConfirm(d); setError(null); }
  else if (d === "version_conflict") setError("Someone else changed this while you were editing. Close this window and open it again to see their version.");
  else if (d === "name_taken") setError("A set with this name already exists in this area.");
  else if (d === "already_exists") setError("An action with this category, sub-category and name already exists here.");
  else setError(String(e.message || e));
}

// How many contracts the conditions match right now - shown while you edit, before anything is saved.
function usePreview(fetcher, criteria, allowEmpty) {
  const [p, setP] = useState(null);
  const [err, setErr] = useState(null);
  const key = JSON.stringify(criteria);
  useEffect(() => {
    if (!allowEmpty && Object.keys(criteria).length === 0) { setP(null); setErr(null); return undefined; }
    const t = setTimeout(() => fetcher(criteria).then((r) => { setP(r); setErr(null); }).catch((e) => { setP(null); setErr(String(e.message || e)); }), 400);
    return () => clearTimeout(t);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return [p, err];
}

function PreviewLine({ p, err, empty }) {
  if (err) return <div style={{ fontSize: 12.5, color: T.risk }}>{err}</div>;
  if (!p) return <div style={{ fontSize: 12.5, color: T.inkFaint }}>{empty}</div>;
  return (
    <div style={{ fontSize: 12.5, color: T.ink }}>
      Matches <b>{p.matches.toLocaleString()}</b> of {p.live.toLocaleString()} live contracts ({Math.round(p.share * 100)}%)
      {p.newlyExcluded !== undefined && <> · <b>{p.newlyExcluded.toLocaleString()}</b> not already excluded by another set</>}
    </div>
  );
}

function ExclusionEditor({ existing, columns, areaOptions, onClose, onSaved }) {
  const [name, setName] = useState(existing?.name ?? "");
  const [ctx, setCtx] = useState(existing?.ctx ?? areaOptions[0]?.value ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [criteria, setCriteria] = useState(existing?.criteria ?? {});
  const [error, setError] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [p, perr] = usePreview((c) => api.previewExclusion({ ctx, criteria: c, setId: existing?.id }), criteria, false);

  const save = async (activate) => {
    setBusy(true); setError(null);
    try {
      if (existing) {
        let row = await api.updateExclusion(existing.id, { version: existing.version, name, description, criteria, confirm: confirmed });
        if (activate && row.status !== "active") row = await api.exclusionAction(existing.id, "activate", { version: row.version, confirm: confirmed });
      } else {
        await api.createExclusion({ ctx, name, description, criteria, activate, confirm: confirmed });
      }
      onSaved();
    } catch (e) { explain(e, setError, setConfirm); }
    setBusy(false);
  };
  const ready = name.trim() && ctx && Object.keys(criteria).length > 0;
  return (
    <Modal title={existing ? `Edit exclusion set · ${existing.name}` : "New exclusion set"} onClose={onClose}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 220px", gap: 14 }}>
        <Labelled label="Name"><input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} style={{ ...field, width: "100%" }} /></Labelled>
        <Labelled label="Area"><select value={ctx} disabled={!!existing} onChange={(e) => setCtx(e.target.value)} style={{ ...field, width: "100%" }}>{areaOptions.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}</select></Labelled>
      </div>
      <Labelled label="Description (optional)"><input value={description} onChange={(e) => setDescription(e.target.value)} style={{ ...field, width: "100%" }} /></Labelled>
      <Labelled label="Exclude contracts where…" hint="Contracts matching ALL of these conditions are excluded. These contracts still get their summaries, but no recommended action.">
        <CriteriaEditor columns={columns} criteria={criteria} onChange={setCriteria} />
      </Labelled>
      <Card style={{ padding: 12, marginBottom: 14, background: T.surfaceSunken }}><PreviewLine p={p} err={perr} empty="Add a condition to see how many contracts it would exclude." /></Card>
      {confirm && (
        <Card style={{ padding: 12, marginBottom: 14, background: T.amberBg, borderColor: T.amber }}>
          <label style={{ fontSize: 12.5, display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            This excludes {confirm.matches.toLocaleString()} of {confirm.live.toLocaleString()} contracts ({Math.round(confirm.share * 100)}% of the area). I want to do this.
          </label>
        </Card>
      )}
      {error && <div style={{ fontSize: 12.5, color: T.risk, marginBottom: 10 }}>{error}</div>}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button onClick={onClose} style={smallBtn}>Cancel</button>
        <button disabled={!ready || busy} onClick={() => save(false)} style={{ ...smallBtn, opacity: ready && !busy ? 1 : 0.5 }}>{existing ? "Save changes" : "Save as draft"}</button>
        {(!existing || existing.status !== "active") && <button disabled={!ready || busy} onClick={() => save(true)} style={{ ...smallBtn, background: T.ink, color: "#fff", opacity: ready && !busy ? 1 : 0.5 }}>Save and activate</button>}
      </div>
    </Modal>
  );
}

const th = { textAlign: "left" };
function ExclusionsPage({ areaOptions, areaLabel, onChanged }) {
  const [data, setData] = useState({ sets: [], combined: {} });
  const [columns, setColumns] = useState([]);
  const [showDeleted, setShowDeleted] = useState(false);
  const [editing, setEditing] = useState(undefined);   // undefined: closed, null: new, object: editing that set
  const [error, setError] = useState(null);
  const load = useCallback(async () => {
    try {
      const [d, c] = await Promise.all([api.getExclusions(showDeleted), api.getRuleColumns()]);
      setData(d); setColumns(c);
    } catch (e) { setError(String(e.message || e)); }
  }, [showDeleted]);
  useEffect(() => { load(); }, [load]);
  const changed = async () => { setEditing(undefined); await load(); onChanged(); };
  const run = async (fn, ask) => {
    if (ask && !window.confirm(ask)) return;
    setError(null);
    try { await fn(); await changed(); } catch (e) { explain(e, setError, () => {}); }
  };
  const activate = (s) => run(async () => {
    try { await api.exclusionAction(s.id, "activate", { version: s.version }); } catch (e) {
      const d = e.detail;
      if (d && d.code === "confirm_required" && window.confirm(`This will exclude ${d.matches.toLocaleString()} of ${d.live.toLocaleString()} contracts (${Math.round(d.share * 100)}% of the area). Continue?`)) {
        await api.exclusionAction(s.id, "activate", { version: s.version, confirm: true });
      } else if (!(d && d.code === "confirm_required")) throw e;
    }
  });
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <div style={{ maxWidth: 760 }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: "0 0 4px" }}>Exclusion sets</h2>
          <div style={{ fontSize: 12.5, color: T.inkMuted, lineHeight: 1.5 }}>
            Contracts matching an <b>active</b> set get their summaries but no recommended action. Everyone with the area sees its sets.
            A contract matching several sets is counted once. What <i>you</i> hide from your own screens is chosen separately, in the filter bar.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ fontSize: 12, color: T.inkMuted, display: "flex", gap: 5, alignItems: "center" }}><input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />Show deleted</label>
          <button onClick={() => setEditing(null)} style={{ ...smallBtn, background: T.ink, color: "#fff" }}>New exclusion set</button>
        </div>
      </div>
      {error && <div style={{ fontSize: 12.5, color: T.risk, marginBottom: 10 }}>{error}</div>}
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <table>
          <thead><tr><th style={th}>Name</th><th style={th}>Area</th><th style={th}>Status</th><th style={th}>Conditions</th><th style={th}>Excludes</th><th style={th}>Only this set</th><th style={th}>Last change</th><th></th></tr></thead>
          <tbody>
            {data.sets.length === 0 && <tr><td colSpan={8} style={{ textAlign: "center", padding: 24, color: T.inkFaint }}>{showDeleted ? "No deleted sets." : "No exclusion sets yet."}</td></tr>}
            {data.sets.map((s) => (
              <tr key={s.id}>
                <td style={{ fontWeight: 600 }}>{s.name}{s.description && <div style={{ fontWeight: 400, fontSize: 11.5, color: T.inkFaint }}>{s.description}</div>}</td>
                <td>{areaLabel(s.ctx)}</td>
                <td>{s.deleted ? <Badge text="Deleted" color={T.inkFaint} bg={T.surfaceSunken} /> : s.status === "active" ? <Badge text="Active" color={T.safe} bg={T.safeBg} /> : <Badge text="Draft" color={T.amber} bg={T.amberBg} />}</td>
                <td style={{ maxWidth: 320, fontSize: 12, color: T.inkMuted }}>{describeCriteria(s.criteria, columns)}</td>
                <td>{s.status === "active" ? (s.matchCount ?? 0).toLocaleString() : "—"}</td>
                <td>{s.status === "active" ? (s.onlyThisSet ?? 0).toLocaleString() : "—"}</td>
                <td style={{ fontSize: 11.5, color: T.inkFaint }}>{s.updatedBy || "—"}<br />{s.updatedAt ? new Date(s.updatedAt).toLocaleDateString() : ""}</td>
                <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                  {s.deleted ? <button style={smallBtn} onClick={() => run(() => api.exclusionAction(s.id, "restore"))}>Restore</button> : (
                    <>
                      <button style={smallBtn} onClick={() => setEditing(s)}>Edit</button>{" "}
                      {s.status === "active"
                        ? <button style={smallBtn} onClick={() => run(() => api.exclusionAction(s.id, "deactivate", { version: s.version }))}>Deactivate</button>
                        : <button style={smallBtn} onClick={() => activate(s)}>Activate</button>}{" "}
                      <button style={smallBtn} onClick={() => run(() => api.deleteExclusion(s.id, s.version), `Delete "${s.name}"? It can be restored later.`)}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {Object.keys(data.combined).length > 0 && (
        <div style={{ fontSize: 12.5, color: T.inkMuted, marginTop: 10 }}>
          Together, the active sets exclude: {Object.entries(data.combined).map(([c, n]) => `${areaLabel(c)} - ${n.toLocaleString()} contracts`).join(" · ")}
        </div>
      )}
      {editing !== undefined && <ExclusionEditor existing={editing} columns={columns} areaOptions={areaOptions} onClose={() => setEditing(undefined)} onSaved={changed} />}
    </div>
  );
}

function ActionEditor({ existing, columns, areaOptions, isAdmin, onClose, onSaved }) {
  const [scope, setScope] = useState(existing?.scope ?? (isAdmin && areaOptions.length === 0 ? "global" : "local"));
  const [ctx, setCtx] = useState(existing?.ctx ?? areaOptions[0]?.value ?? "");
  const [category, setCategory] = useState(existing?.category ?? "");
  const [subCategory, setSub] = useState(existing?.subCategory ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [criteria, setCriteria] = useState(existing?.criteria ?? {});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [p, perr] = usePreview((c) => api.previewAction({ ctx: scope === "global" ? null : ctx, criteria: c }), criteria, true);
  const save = async () => {
    setBusy(true); setError(null);
    try {
      if (existing) await api.updateAction(existing.id, { version: existing.version, category, subCategory, name, description, criteria });
      else await api.createAction({ scope, ctx: scope === "global" ? null : ctx, category, subCategory, name, description, criteria });
      onSaved();
    } catch (e) { explain(e, setError, () => {}); }
    setBusy(false);
  };
  const ready = category.trim() && name.trim() && (scope === "global" || ctx);
  return (
    <Modal title={existing ? `Edit retention action · ${existing.name}` : "New retention action"} onClose={onClose}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 220px", gap: 14 }}>
        <Labelled label="Category"><input value={category} onChange={(e) => setCategory(e.target.value)} maxLength={100} style={{ ...field, width: "100%" }} /></Labelled>
        <Labelled label="Sub-category (optional)"><input value={subCategory} onChange={(e) => setSub(e.target.value)} maxLength={100} style={{ ...field, width: "100%" }} /></Labelled>
        <Labelled label="Applies to">
          <select value={scope === "global" ? "global" : ctx} disabled={!!existing} onChange={(e) => { if (e.target.value === "global") setScope("global"); else { setScope("local"); setCtx(e.target.value); } }} style={{ ...field, width: "100%" }}>
            {isAdmin && <option value="global">Every area (global)</option>}
            {areaOptions.map((a) => <option key={a.value} value={a.value}>{a.label} only</option>)}
          </select>
        </Labelled>
      </div>
      <Labelled label="Name" hint="A local action replaces a global one only when category, sub-category and name are all the same.">
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} style={{ ...field, width: "100%" }} />
      </Labelled>
      <Labelled label="Description" hint="Shown to the model when it chooses, so say what the action is and when it fits.">
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} style={{ ...field, width: "100%", minHeight: 70, resize: "vertical" }} />
      </Labelled>
      <Labelled label="Applies to contracts where… (optional)" hint="Leave empty and the action is available for every contract.">
        <CriteriaEditor columns={columns} criteria={criteria} onChange={setCriteria} />
      </Labelled>
      <Card style={{ padding: 12, marginBottom: 14, background: T.surfaceSunken }}><PreviewLine p={p} err={perr} empty="Applies to every contract." /></Card>
      {error && <div style={{ fontSize: 12.5, color: T.risk, marginBottom: 10 }}>{error}</div>}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button onClick={onClose} style={smallBtn}>Cancel</button>
        <button disabled={!ready || busy} onClick={save} style={{ ...smallBtn, background: T.ink, color: "#fff", opacity: ready && !busy ? 1 : 0.5 }}>{existing ? "Save changes" : "Create action"}</button>
      </div>
    </Modal>
  );
}

function ActionsPage({ user, areaOptions, areaLabel, onChanged }) {
  const [list, setList] = useState([]);
  const [columns, setColumns] = useState([]);
  const [showDeleted, setShowDeleted] = useState(false);
  const [editing, setEditing] = useState(undefined);
  const [cloning, setCloning] = useState(null);
  const [error, setError] = useState(null);
  const isAdmin = user.role === "admin";
  const load = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([api.getActions(showDeleted), api.getRuleColumns()]);
      setList(a); setColumns(c);
    } catch (e) { setError(String(e.message || e)); }
  }, [showDeleted]);
  useEffect(() => { load(); }, [load]);
  const changed = async () => { setEditing(undefined); setCloning(null); await load(); onChanged(); };
  const run = async (fn, ask) => {
    if (ask && !window.confirm(ask)) return;
    setError(null);
    try { await fn(); await changed(); } catch (e) { explain(e, setError, () => {}); }
  };
  const canManage = (a) => (a.scope === "global" ? isAdmin : isAdmin || areaOptions.some((o) => o.value === a.ctx));
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <div style={{ maxWidth: 760 }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: "0 0 4px" }}>Retention actions</h2>
          <div style={{ fontSize: 12.5, color: T.inkMuted, lineHeight: 1.5 }}>
            The menu the model recommends from. <b>Global</b> actions apply everywhere and are managed by admins; <b>area</b> actions belong to one area.
            To customise a global action for your area, clone it and keep the name - your version then replaces it there.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ fontSize: 12, color: T.inkMuted, display: "flex", gap: 5, alignItems: "center" }}><input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />Show deleted</label>
          <button onClick={() => setEditing(null)} style={{ ...smallBtn, background: T.ink, color: "#fff" }}>New retention action</button>
        </div>
      </div>
      {error && <div style={{ fontSize: 12.5, color: T.risk, marginBottom: 10 }}>{error}</div>}
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <table>
          <thead><tr><th style={th}>Category</th><th style={th}>Action</th><th style={th}>Applies to</th><th style={th}>Conditions</th><th style={th}>Matches</th><th></th></tr></thead>
          <tbody>
            {list.length === 0 && <tr><td colSpan={6} style={{ textAlign: "center", padding: 24, color: T.inkFaint }}>{showDeleted ? "No deleted actions." : "No retention actions yet."}</td></tr>}
            {list.map((a) => (
              <tr key={a.id}>
                <td>{a.category}{a.subCategory && <span style={{ color: T.inkFaint }}> › {a.subCategory}</span>}</td>
                <td style={{ fontWeight: 600 }}>{a.name}{a.description && <div style={{ fontWeight: 400, fontSize: 11.5, color: T.inkFaint, maxWidth: 360 }}>{a.description}</div>}</td>
                <td>
                  {a.scope === "global" ? <Badge text="Every area" color={T.info} bg={T.infoBg} /> : <Badge text={areaLabel(a.ctx)} color={T.purple} bg={T.purpleBg} />}
                  {a.overridesGlobal && <div style={{ fontSize: 11, color: T.amber }}>replaces the global action here</div>}
                  {a.overriddenIn.length > 0 && <div style={{ fontSize: 11, color: T.amber }}>replaced in {a.overriddenIn.join(", ")}</div>}
                </td>
                <td style={{ maxWidth: 300, fontSize: 12, color: T.inkMuted }}>{describeCriteria(a.criteria, columns)}</td>
                <td>{a.matchCount == null ? "all" : a.matchCount.toLocaleString()}</td>
                <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                  {a.deleted ? (canManage(a) && <button style={smallBtn} onClick={() => run(() => api.restoreAction(a.id))}>Restore</button>) : (
                    <>
                      {canManage(a) && <><button style={smallBtn} onClick={() => setEditing(a)}>Edit</button>{" "}</>}
                      {areaOptions.length > 0 && <><button style={smallBtn} onClick={() => setCloning(a)}>Clone</button>{" "}</>}
                      {canManage(a) && <button style={smallBtn} onClick={() => run(() => api.deleteAction(a.id, a.version), `Delete "${a.name}"? It can be restored later.`)}>Delete</button>}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {editing !== undefined && <ActionEditor existing={editing} columns={columns} areaOptions={areaOptions} isAdmin={isAdmin} onClose={() => setEditing(undefined)} onSaved={changed} />}
      {cloning && (
        <Modal title={`Clone · ${cloning.name}`} onClose={() => setCloning(null)}>
          <CloneForm action={cloning} areaOptions={areaOptions} onClone={(body) => run(() => api.cloneAction(cloning.id, body))} />
        </Modal>
      )}
    </div>
  );
}

function CloneForm({ action, areaOptions, onClone }) {
  const [ctx, setCtx] = useState(areaOptions[0]?.value ?? "");
  const [name, setName] = useState(action.name);
  return (
    <>
      <Labelled label="Copy into area"><select value={ctx} onChange={(e) => setCtx(e.target.value)} style={field}>{areaOptions.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}</select></Labelled>
      <Labelled label="Name" hint={name.trim().toLowerCase() === action.name.trim().toLowerCase() && action.scope === "global" ? "Same name: your copy will replace the global action in this area." : "A different name makes a separate action; the global one stays."}>
        <input value={name} onChange={(e) => setName(e.target.value)} style={{ ...field, width: "100%" }} />
      </Labelled>
      <div style={{ textAlign: "right" }}><button style={{ ...smallBtn, background: T.ink, color: "#fff" }} disabled={!ctx || !name.trim()} onClick={() => onClone({ ctx, name })}>Clone</button></div>
    </>
  );
}

// Which exclusion sets hide contracts from MY screens: all active sets (default), only the ones I pick, or none.
function ExclusionControl({ sets, pref, onChange, hidden, areaLabel }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const out = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", out);
    return () => document.removeEventListener("mousedown", out);
  }, []);
  if (!sets.length) return null;
  const label = pref.mode === "none" ? "Exclusions: show everything" : pref.mode === "custom" ? `Exclusions: ${pref.setIds.length} of ${sets.length} sets` : `Exclusions: all ${sets.length} active sets`;
  const radio = (mode, text) => (
    <label style={{ display: "flex", gap: 8, padding: "6px 8px", fontSize: 12.5, cursor: "pointer" }}>
      <input type="radio" checked={pref.mode === mode} onChange={() => onChange({ mode, setIds: mode === "custom" ? pref.setIds : [] })} />{text}
    </label>
  );
  return (
    <div ref={ref} style={{ position: "relative", display: "flex", alignItems: "center", gap: 8 }}>
      <button onClick={() => setOpen((v) => !v)} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 600, border: `1px solid ${pref.mode === "all" ? T.border : T.brand}`, background: pref.mode === "all" ? T.surface : T.brandBg, color: pref.mode === "all" ? T.inkMuted : T.brand, borderRadius: 7, padding: "7px 10px", cursor: "pointer" }}>
        {label}<ChevronDown size={13} />
      </button>
      {hidden > 0 && <span style={{ fontSize: 11.5, color: T.inkFaint }}>{hidden.toLocaleString()} contracts hidden</span>}
      {open && (
        <div style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, boxShadow: "0 6px 18px rgba(22,27,34,0.12)", padding: 6, zIndex: 30, minWidth: 300 }}>
          {radio("all", "Hide contracts matched by any active set")}
          {radio("custom", "Hide only the sets I pick")}
          {radio("none", "Show everything")}
          {pref.mode === "custom" && sets.map((s) => (
            <label key={s.id} style={{ display: "flex", gap: 8, padding: "5px 8px 5px 28px", fontSize: 12.5, cursor: "pointer" }}>
              <input type="checkbox" checked={pref.setIds.includes(s.id)} onChange={() => onChange({ mode: "custom", setIds: pref.setIds.includes(s.id) ? pref.setIds.filter((i) => i !== s.id) : [...pref.setIds, s.id] })} />
              {s.name} <span style={{ color: T.inkFaint }}>· {areaLabel(s.ctx)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------
   MAIN APP
   All data manipulation and AI orchestration now happens in FastAPI.
   This component only fetches, displays, and triggers actions.
----------------------------------------------------------------*/
function ContractRenewalPOC({ user, onLogout }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);   // one filter state, shared by both tabs
  const [summary, setSummary] = useState(null);            // aggregates for the current filters
  const [view, setView] = useState(null);                  // this user's one saved worklist view
  const [areaNames, setAreaNames] = useState({});
  const [modelInfo, setModelInfo] = useState(null);
  const [tab, setTab] = useState("renewal-prioritization");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim().length >= 2 ? search.trim() : "", 300);   // the server searches; under 2 characters it doesn't
  const [selected, setSelected] = useState(null);          // id of the open contract
  const [detail, setDetail] = useState(null);              // everything the drawer shows - fetched when a contract is opened
  const [detailError, setDetailError] = useState(null);
  const [contact, setContact] = useState(null);            // contact data, only after an explicit (audited) request
  const [overrides, setOverrides] = useState({});          // the rep's own edits, shown in the list immediately
  const [refresh, setRefresh] = useState(0);               // bump to recompute the summary after an edit
  const [heatMode, setHeatMode] = useState("contracts");
  const [maximizedChart, setMaximizedChart] = useState(null); // "campaign-bar" | null
  // Which tab is active inside the contract detail drawer - reset to the
  // default whenever a different contract is opened, so switching contracts
  // doesn't leave you stranded on a tab that made sense for the last one.
  const [drawerTab, setDrawerTab] = useState("action");
  const [apiError, setApiError] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const fail = (e) => { if (e?.name !== "AbortError") setApiError(String(e?.message || e)); };
  // Exclusion sets: the active ones, and which of them this user has chosen to hide from their own screens.
  const [exclSets, setExclSets] = useState([]);
  const [pref, setPref] = useState({ mode: "all", setIds: [] });
  const [prefTick, setPrefTick] = useState(0);              // bump when the sets or the choice change: list and figures re-query
  const loadExclusions = useCallback(() => {
    api.getExclusions().then((r) => setExclSets(r.sets.filter((x) => x.status === "active"))).catch(fail);
    api.getExclusionPref().then(setPref).catch(fail);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadExclusions(); }, [loadExclusions]);
  const savePref = async (next) => {
    try { setPref(await api.saveExclusionPref(next)); setPrefTick((t) => t + 1); } catch (e) { fail(e); }
  };
  const onRulesChanged = () => { loadExclusions(); setPrefTick((t) => t + 1); };

  // Initial load: the saved view, the model description and the area names - small, and nothing about contracts.
  useEffect(() => {
    (async () => {
      try {
        const [v, mi] = await Promise.all([api.getWorklistView(), api.getModelInfo()]);
        setView(v); setModelInfo(mi);
        const names = {};
        if (user.allCtx) (await api.getAdminCtx()).ctxs.forEach((c) => { names[c.code] = c.name; });
        else user.ctxs.forEach((c) => { names[c.code] = c.name; });
        setAreaNames(names);
      } catch (e) { fail(e); }
      setLoaded(true);
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // KPIs, bucket counts, heatmap, per-area and campaign figures: one aggregate query per change of filter.
  useEffect(() => {
    const c = new AbortController();
    api.getSummary(filters, c.signal).then(setSummary).catch(fail);
    return () => c.abort();
  }, [JSON.stringify(filters), refresh, prefTick]); // eslint-disable-line react-hooks/exhaustive-deps

  // Opening a contract fetches everything about it; nothing but the list row is held beforehand.
  useEffect(() => {
    setDrawerTab("action"); setContact(null); setDetail(null); setDetailError(null);
    if (!selected) return undefined;
    const c = new AbortController();
    api.getContract(selected, c.signal).then(setDetail).catch((e) => { if (e.name !== "AbortError") setDetailError(String(e.message || e)); });
    return () => c.abort();
  }, [selected]);

  const areaLabel = (code) => (code == null ? "No area" : areaNames[code] && areaNames[code] !== code ? `${code} · ${areaNames[code]}` : code);
  const areaOptions = Object.keys(areaNames).sort().map((c) => ({ value: c, label: areaLabel(c) }));

  // Sort changes apply at once and are saved in the background. A column change is saved FIRST
  // (the server reads the saved view to decide which columns to send), then the list reloads.
  const onSort = (key) => {
    const textual = view.available.find((a) => a.key === key)?.kind === "text";   // names read A-Z first; numbers and dates biggest / latest first
    const sort = { key, dir: key === view.sort.key ? (view.sort.dir === "asc" ? "desc" : "asc") : (textual ? "asc" : "desc") };
    setView((v) => ({ ...v, sort }));
    api.saveWorklistView({ columns: view.columns, sort }).catch(fail);
  };
  const onColumns = async (columns) => {
    try {
      const saved = await api.saveWorklistView({ columns, sort: view.sort });
      setView((v) => ({ ...v, columns: saved.columns }));
    } catch (e) { fail(e); }
  };
  const onPickCell = (rb, vb) => setFilters((f) => (f.rb === rb && f.vb === vb ? { ...f, rb: null, vb: null } : { ...f, rb, vb }));

  // What a rep records. The drawer and the list update at once; the summary is recomputed from the
  // database a moment later. If the save fails, the contract is re-read so the screen shows the truth.
  const patchTrace = (contractId, patch) => {
    setDetail((d) => (d && d.contract.contractId === contractId && d.trace ? { ...d, trace: { ...d.trace, ...patch } } : d));
    setOverrides((o) => ({
      ...o,
      [contractId]: { ...o[contractId], ...(patch.outcome !== undefined && { outcome: patch.outcome }), ...(patch.actionStatus !== undefined && { status: patch.actionStatus }) },
    }));
  };
  const revert = (contractId, e) => {
    fail(e);
    setOverrides((o) => { const n = { ...o }; delete n[contractId]; return n; });
    api.getContract(contractId).then(setDetail).catch(() => {});
  };
  const setOutcome = async (contractId, outcome) => {
    patchTrace(contractId, { outcome });
    try { await api.sendFeedback(contractId, outcome); setRefresh((n) => n + 1); } catch (e) { revert(contractId, e); }
  };
  const noteTimer = useRef(null);
  const setNote = (contractId, note) => {   // typed text is saved once typing pauses, not on every keystroke
    patchTrace(contractId, { outcomeNote: note });
    clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => api.sendFeedback(contractId, undefined, note).catch((e) => revert(contractId, e)), 600);
  };
  const toggleActionStatus = async (contractId, current) => {
    const next = current === "Action done" ? "Action required" : "Action done";
    patchTrace(contractId, { actionStatus: next });
    try { await api.setActionStatus(contractId, next); setRefresh((n) => n + 1); } catch (e) { revert(contractId, e); }
  };
  const revealContact = async (contractId) => {
    setContact("loading");
    try { setContact((await api.revealContact(contractId)).contact); } catch (e) { setContact(null); fail(e); }
  };

  const k = summary?.kpis ?? ZERO_KPIS;
  const exclusionUi = <ExclusionControl sets={exclSets} pref={pref} onChange={savePref} hidden={summary?.excluded ?? 0} areaLabel={areaLabel} />;
  // Same shape the Dashboard markup below has always read.
  const dashGlobalMetrics = {
    customerCount: k.customers, contractCount: k.contracts, segmentCounts: k.segments, totalValue: k.value, lostCount: k.lostCount,
    lostRevenueValue: k.lostValue, potentialRevenueValue: k.convertedValue, potentialAtRiskValue: k.atRiskValue,
  };
  const dashCampaignData = useMemo(() => {
    const pct = (n, of) => (of ? Math.round((n / of) * 100) : 0);
    return (summary?.campaigns ?? []).map((s) => {
      const responseCount = s.engaged + s.declined; // customer responded, either way
      return {
        name: s.name, Assigned: s.assigned, Engaged: s.engaged, Declined: s.declined, "No response": s.noResponse,
        declined: s.declined, noResponse: s.noResponse, responseCount,
        // Each bar's own rate is that bar's count over Assigned.
        engagedRate: pct(s.engaged, s.assigned), declinedRate: pct(s.declined, s.assigned),
        noResponseRate: pct(s.noResponse, s.assigned), responseRate: pct(responseCount, s.assigned),
        assignedValue: s.assignedValue, potentialAtRiskValue: s.atRiskValue, lostRevenueValue: s.lostValue, potentialRevenueValue: s.convertedValue,
      };
    });
  }, [summary]);

  // Shared by the inline card and the maximize modal - same chart, just a
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

  // Totals across every campaign, for the summary stat row above the chart -
  // sums dashCampaignData rather than re-deriving it from the summary a second time.
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

  const selectedContract = detail?.contract;
  const portfolioContracts = detail?.portfolio ?? [];
  const portfolioSort = useSort(null, "asc");
  const [portfolioSortKey, portfolioSortDir] = portfolioSort;
  const sortedPortfolioContracts = useMemo(() => sortRows(portfolioContracts, portfolioSortKey, portfolioSortDir, {
    contractId: (c) => c.contractId,
    region: (c) => c.region,
    bucket: (c) => BUCKETS.indexOf(c.bucket),
    contractValue: (c) => c.contractValue,
    riskScore: (c) => c.riskScore,
  }), [portfolioContracts, portfolioSortKey, portfolioSortDir]);
  const selectedTrace = detail?.trace ?? null;

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
          <div style={{ fontSize: 11.5, color: T.inkMuted, marginBottom: 8, display: "flex", gap: 8, justifyContent: "flex-end", alignItems: "center", flexWrap: "wrap" }}>
            <span title={user.email} style={{ fontWeight: 600, color: T.ink }}>{user.name || user.email}</span>
            <span style={{ color: T.inkFaint }}>·</span>
            <span data-testid="user-areas">
              {user.allCtx ? "All areas" : user.ctxs.map((c) => (c.name === c.code ? c.code : `${c.code} ${c.name}`)).join(", ")}
            </span>
            <button
              onClick={onLogout}
              style={{ border: "none", background: "none", color: T.info, fontSize: 11.5, fontWeight: 600, cursor: "pointer", padding: 0 }}
            >
              Sign out
            </button>
          </div>
          {apiError && <div style={{ fontSize: 11.5, color: T.risk, marginTop: 6, maxWidth: 260 }}>{apiError}</div>}
        </div>
          
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 20, borderBottom: `1px solid ${T.border}`, marginBottom: 18 }}>
        <button className={`tabbtn ${tab === "overview" ? "active" : ""}`} onClick={() => setTab("overview")}>Overview</button>
        <button className={`tabbtn ${tab === "renewal-prioritization" ? "active" : ""}`} onClick={() => setTab("renewal-prioritization")}>Renewal Prioritization</button>
        <button className={`tabbtn ${tab === "dashboard" ? "active" : ""}`} onClick={() => setTab("dashboard")}>Dashboard</button>
        <button className={`tabbtn ${tab === "exclusions" ? "active" : ""}`} onClick={() => setTab("exclusions")}>Exclusions</button>
        <button className={`tabbtn ${tab === "actions" ? "active" : ""}`} onClick={() => setTab("actions")}>Retention Actions</button>
        <button className={`tabbtn ${tab === "technical-details" ? "active" : ""}`} onClick={() => setTab("technical-details")}>Technical Details</button>
        {user.role === "admin" && (
          <button className={`tabbtn ${tab === "access" ? "active" : ""}`} onClick={() => setTab("access")}>Access</button>
        )}
      </div>

      {tab === "access" && user.role === "admin" && <AccessPage currentUserId={user.id} />}

      {tab === "overview" && (
        <div style={{ maxWidth: "90%"}}>
          <Card style={{ padding: "26px 30px", marginBottom: 18, borderTop: `3px solid ${T.brand}` }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, color: T.brand, textTransform: "uppercase", marginBottom: 6 }}>What we're building</div>
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 4px" }}>Proactive Contract Renewal</h2>
            <p style={{ fontSize: 13.5, color: T.inkMuted, margin: "0 0 20px", fontStyle: "italic" }}>
              Not a contract renewal prediction tool - a proactive renewal system.
            </p>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>The problem</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, margin: "0 0 20px" }}>
              Climate Solutions Transportation (CST) renews thousands of service contracts every year.
              Today, that process is reactive: an at-risk account is usually noticed only after the contract has already lapsed or not noticed at all.
              The signals that would have predicted the loss (a recurring equipment fault, a claim just weeks ago, a contract quietly out of warranty) exist somewhere in the business, but nothing pulls
              them together in time for a rep to act. And when a rep does spot a risk, there's no consistent playbook
              for what to do next, so the response depends entirely on that one person's judgment and available time.
            </p>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>Why we need this solution</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, margin: "0 0 20px" }}>
              Manually reviewing thousands of contracts for renewal risk doesn't scale, and it doesn't happen
              consistently - different reps and different regions develop different habits. Contracts renew on
              autopilot until the moment they don't, and by the time a churn shows up in the numbers, the account is
              already gone. Without a system that checks every contract on a fixed clock, scores risk the same way
              everywhere, and hands a rep a specific next step, retention is left to chance rather than to a process.
            </p>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>How the business benefits</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, margin: "0 0 10px" }}>
              This isn't a contract renewal <i>prediction</i> tool - the difference is what happens after the
              number. It's a proactive renewal system that:
            </p>
            <ol style={{ fontSize: 13.5, lineHeight: 1.75, color: T.ink, margin: "0 0 20px", paddingLeft: 20 }}>
              <li><b>Identifies upcoming renewal risk</b> - every contract is checked automatically against its renewal milestones, so risk surfaces weeks before a contract could lapse, not after.</li>
              <li><b>Prioritizes accounts by business value</b> - risk alone isn't the whole story; a high-risk, high-value account is a very different priority than a high-risk, low-value one, so accounts are ranked by risk crossed with value, not risk in isolation.</li>
              <li><b>Recommends an action to retain the customer</b> - each flagged account gets one concrete, grounded retention action, not just a "high risk" label.</li>
              <li><b>Generates the outreach content</b> - the actual email a rep can send is drafted for them, referencing this specific customer's real history, so there's no blank page between "risk found" and "customer contacted."</li>
              <li><b>Tracks the outcome</b> - every recommendation and its real-world result (engaged, declined, no response) is logged, so the business can see which actions actually retain customers, not just how many were sent.</li>
            </ol>

            <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 12, fontSize: 12, color: T.inkMuted, fontStyle: "italic" }}>
              For how this is actually built - the agent pipeline, the scoring model, current state, and the
              assumptions behind it - see the Technical Details tab.
            </div>
            
          </Card>

          <ApplicationFlowDiagram />

        </div>
      )}

      {tab === "technical-details" && (
        <div style={{ maxWidth: "90%"}}>
          <Card style={{ padding: "26px 30px", borderTop: `3px solid ${T.brand}` }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, color: T.brand, textTransform: "uppercase", marginBottom: 6 }}>How it's built</div>
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 14px" }}>Technical details</h2>

            <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, borderBottom: `1px solid ${T.border}`, paddingBottom: 6, marginBottom: 8 }}>How we're achieving it</div>
            <ul style={{ fontSize: 13.5, lineHeight: 1.7, color: T.ink, margin: "0 0 20px", paddingLeft: 20 }}>
              <li><b>Rule-based risk scorecard</b> - 8 weighted factors grounded in real service claim and contract history (repeat faults, claim frequency and recency, written-off jobs, coverage gaps, equipment age, warranty status, price increases) produce a 0&ndash;100 score with a ranked driver-feature breakdown. This is a deterministic scorecard, not a trained ML model, and it's labeled that way honestly in the product. A handful of factors considered early on (payment behavior, NPS, competitor activity) were checked against real data and dropped rather than approximated, since no real source for them has been confirmed yet.</li>
              <li><b>Risk &times; value Segmentation</b> - crosses the risk score against contract value so a high-risk, high-value account is treated as a different priority than a high-risk, low-value one.</li>
              <li><b>Six-agent pipeline</b> - Service Ticket Summary, Customer Summary & Customer Feedback Summary agents run ahead of time and are cached; a Recommendation Agent proposes a retention action and any relevant upsell; an Evaluation Agent scores it against a rubric and triggers a retry or escalation; a Content Agent drafts the outreach email.</li>
              <li><b>Full traceability</b> - every recommendation logs its prompts, scores, retries, latency, and token cost, inspectable end to end.</li>
              <li><b>Swappable LLM provider</b> - Claude API or a locally-hosted vLLM model, switched with one configuration change.</li>
              <li><b>One engine, three regions</b> - NATT, ETT, and APAC TT run on the same pipeline; region and channel are configuration, not forked code.</li>
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
              <li>Risk x Value Segmentation thresholds (score ≥50/30, contract value vs. book median) are illustrative starting points, not calibrated.</li>
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
              Status: proof of concept - synthetic data, rule-based scoring labeled honestly as such, real agentic pipeline.
            </div>
          </Card>
        </div>
      )}

      {tab === "renewal-prioritization" && (
        <>
          <FilterBar filters={filters} onChange={setFilters} areaOptions={areaOptions} extra={exclusionUi} />

          <div style={{ fontSize: 13.5, fontWeight: 700 }}>Contract expiring in: </div>
          <div style={{ fontSize: 12, color: T.inkMuted, marginBottom: 10 }}>Click a milestone to filter the worklist, charts, and KPIs below by how soon each contract is due for renewal.</div>
          <BucketCards counts={summary?.buckets} value={filters.bucket} onPick={(b) => setFilters((f) => ({ ...f, bucket: f.bucket === b ? null : b }))} />

          <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr 1fr", gap: 16, marginBottom: 18 }}>
            {/* Risk x value heatmap */}
            <Card style={{ padding: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                <div>
                  <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 2 }}>Risk × Value</div>
                  <div style={{ fontSize: 12, color: T.inkMuted, marginBottom: 10 }}>Contracts by risk score and by value against their own area's median. Click a cell to filter.</div>
                </div>
                <div style={{ display: "flex", flexShrink: 0 }}>
                  {[["contracts", "Contracts"], ["value", "$"]].map(([m, lab]) => (
                    <button key={m} onClick={() => setHeatMode(m)} style={{
                      border: `1px solid ${T.border}`, padding: "4px 9px", fontSize: 11.5, fontWeight: 600, cursor: "pointer",
                      background: heatMode === m ? T.ink : T.surface, color: heatMode === m ? "#fff" : T.inkMuted,
                      borderRadius: m === "contracts" ? "6px 0 0 6px" : "0 6px 6px 0",
                    }}>{lab}</button>
                  ))}
                </div>
              </div>
              <RiskValueHeatmap cells={summary?.heat} rb={filters.rb} vb={filters.vb} onPick={onPickCell} mode={heatMode} />
              <div style={{ fontSize: 11, color: T.inkFaint, marginTop: 6 }}>Risk score →  (heavy lines: segment cut-offs at 30, 50, 70)</div>
            </Card>

            {/* KPI summary */}
            <Card style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
              <div style={{ fontSize: 13.5, fontWeight: 700 }}>Portfolio KPIs</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <StatBlock label="Customers | Contracts" value={`${k.customers.toLocaleString()} | ${k.contracts.toLocaleString()}`} />
                <StatBlock label="Actions needed" value={k.actionsNeeded.toLocaleString()} sub={`of ${k.contracts.toLocaleString()} contracts`} accent={k.actionsNeeded > 0 ? T.brand : undefined} />
                <StatBlock label="High risk contracts" value={k.segments["High Risk"].toLocaleString()} sub="High Risk segment" accent={T.risk} />
                <StatBlock label="At-risk contracts" value={k.segments["At Risk"].toLocaleString()} sub="At Risk segment" accent={T.amber} />
                <StatBlock label="Lost" value={k.lostCount.toLocaleString()} sub="declined our outreach" accent={T.risk} />
                <StatBlock label="Campaign response rate" value={k.responseRate !== null ? `${k.responseRate}%` : "-"} sub="of logged outcomes" />
                <StatBlock label="Total contract value" value={`$${(k.value / 1000000).toFixed(2)}M`} sub="Active contracts" />
                <StatBlock label="Converted $" value={`$${(k.convertedValue / 1000000).toFixed(2)}M`} sub="engaged" accent={T.safe} />
                <StatBlock label="Potential $ at risk" value={`$${(k.atRiskValue / 1000000).toFixed(2)}M`} sub="no response received" accent={T.amber} />
                <StatBlock label="Lost revenue $" value={`$${(k.lostValue / 1000000).toFixed(2)}M`} sub="declined" accent={T.risk} />
              </div>
            </Card>

            <Card style={{ padding: 16 }}>
              <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 2 }}>Campaign Response by Risk Bucket</div>
              <div style={{ fontSize: 11, color: T.inkFaint, marginBottom: 10, lineHeight: 1.4 }}>
                Live signal from logged outcomes, reflecting the filters above.
              </div>
              {summary?.outcomeByRisk?.totalWithOutcome > 0 ? (
                <OutcomeBucketChart data={summary.outcomeByRisk} />
              ) : (
                <div style={{ fontSize: 12, color: T.inkFaint, padding: "18px 0", textAlign: "center" }}>
                  Not enough logged outcomes yet for this filter - log a few from a contract's drawer, or clear the filters.
                </div>
              )}
            </Card>
          </div>

          <Worklist
            params={{ ...filters, q, sort: view.sort.key, dir: view.sort.dir }}
            view={view}
            extraKey={`${view.columns.join(",")}|${prefTick}`}
            total={q ? null : k.contracts}
            search={search}
            onSearch={setSearch}
            onSort={onSort}
            onColumns={onColumns}
            onOpen={setSelected}
            overrides={overrides}
            areaLabel={areaLabel}
          />

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


      {tab === "dashboard" && (
        <>
          <FilterBar filters={filters} onChange={setFilters} areaOptions={areaOptions} extra={exclusionUi} />

          {/* Milestone drill-down - the same cards and the same filter as Renewal Prioritization */}
          <BucketCards counts={summary?.buckets} value={filters.bucket} onPick={(b) => setFilters((f) => ({ ...f, bucket: f.bucket === b ? null : b }))} />

          {/* Book overview: Global, then per area */}
          <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 8 }}>Book overview</div>
          <Card style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, color: T.inkFaint, marginBottom: 12 }}>
              Global{filters.bucket ? ` - ${BUCKET_LABEL[filters.bucket]}` : ""}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16, marginBottom: 16 }}>
              <StatBlock label="Customers" value={dashGlobalMetrics.customerCount} />
              <StatBlock label="Contracts" value={dashGlobalMetrics.contractCount} />
              <StatBlock label="Campaign response rate" value={k.responseRate !== null ? `${k.responseRate}%` : "-"} sub="of logged outcomes" />
              <StatBlock label="" value="" />
              <StatBlock label="" value="" />
              <StatBlock label="Healthy contracts" value={dashGlobalMetrics.segmentCounts["Healthy"]} sub="High Risk segment" accent={T.safe} />
              <StatBlock label="High risk contracts" value={dashGlobalMetrics.segmentCounts["High Risk"]} sub="High Risk segment" accent={T.risk} />
              <StatBlock label="At-risk contracts" value={dashGlobalMetrics.segmentCounts["At Risk"]} sub="At Risk segment" accent={T.amber} />
              <StatBlock label="Lost" value={dashGlobalMetrics.lostCount} sub="declined our outreach" accent={T.risk} />
              <StatBlock label="" value="" />
              <StatBlock label="Total contract value" value={`$${(dashGlobalMetrics.totalValue / 1000000).toFixed(2)}M`} sub="Active contracts" />
              <StatBlock label="" value="" />
              <StatBlock label="Lost revenue $" value={`$${(dashGlobalMetrics.lostRevenueValue / 1000000).toFixed(2)}M`} sub="declined" accent={T.risk} />
              <StatBlock label="Converted $" value={`$${(dashGlobalMetrics.potentialRevenueValue / 1000000).toFixed(2)}M`} sub="engaged" accent={T.safe} />
              <StatBlock label="Potential $ at risk" value={`$${(dashGlobalMetrics.potentialAtRiskValue / 1000000).toFixed(2)}M`} sub="no response received" accent={T.amber} />
            </div>
            <SegmentBar counts={dashGlobalMetrics.segmentCounts} />
          </Card>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14, marginBottom: 24 }}>
            {(summary?.byCtx ?? []).map((c) => {
              const logged = c.engaged + c.declined + c.noResponse;
              const m = {
                customerCount: c.customers, contractCount: c.contracts, segmentCounts: c.segments, totalValue: c.value,
                potentialRevenueValue: c.convertedValue, potentialAtRiskValue: c.atRiskValue, lostRevenueValue: c.lostValue,
                responseRate: logged ? Math.round((c.engaged / logged) * 100) : null,
              };
              return (
                <Card key={c.ctx ?? "none"} style={{ padding: 16 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                    <div>
                      <div style={{ fontSize: 13.5, fontWeight: 700 }}>{areaLabel(c.ctx)}</div>
                      <div style={{ fontSize: 11, color: T.inkFaint, marginTop: 2 }}>{c.ctx ? `Area ${c.ctx}` : "Contracts without an area · admins only"}</div>
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
                    <StatBlock label="Customers" value={m.customerCount} />
                    <StatBlock label="Contracts" value={m.contractCount} />
                    <StatBlock label="Campaign response rate" value={m.responseRate !== null ? `${m.responseRate}%` : "-"} sub="of logged outcomes" />
                    <StatBlock label="" value="" />

                    <StatBlock label="Healthy contracts" value={m.segmentCounts["Healthy"]} sub="High Risk segment" accent={T.safe} />
                    <StatBlock label="High risk contracts" value={m.segmentCounts["High Risk"]} sub="High Risk segment" accent={T.risk} />
                    <StatBlock label="At-risk contracts" value={m.segmentCounts["At Risk"]} sub="At Risk segment" accent={T.amber} />
                    <StatBlock label="" value="" />
                    <StatBlock label="Total contract value" value={`$${(m.totalValue / 1000000).toFixed(2)}M`} sub="Active contracts" />
                    <StatBlock label="Converted $" value={`$${(m.potentialRevenueValue / 1000000).toFixed(2)}M`} sub="engaged" accent={T.safe} />
                    <StatBlock label="Potential $ at risk" value={`$${(m.potentialAtRiskValue / 1000000).toFixed(2)}M`} sub="no response received" accent={T.amber} />
                    <StatBlock label="Lost revenue $" value={`$${(m.lostRevenueValue / 1000000).toFixed(2)}M`} sub="declined" accent={T.risk} />
                  </div>
                  <SegmentBar counts={m.segmentCounts} />
                </Card>
              );
            })}
          </div>

          {/* Campaign performance - the same filters as above, applied to the recommendations. */}
          <div style={{ fontSize: 12, fontWeight: 700, color: T.brand, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 8 }}>Campaign performance</div>
          <Card style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16, marginBottom: 16 }}>
              <StatBlock label="Contracts in filter" value={k.contracts.toLocaleString()} />
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


      {tab === "exclusions" && <ExclusionsPage areaOptions={areaOptions} areaLabel={areaLabel} onChanged={onRulesChanged} />}
      {tab === "actions" && <ActionsPage user={user} areaOptions={areaOptions} areaLabel={areaLabel} onChanged={onRulesChanged} />}

      {/* Chart maximize modal - the campaign bar chart.
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
                Campaign performance
              </div>
              <button
                onClick={() => setMaximizedChart(null)}
                title="Minimize"
                style={{ border: "none", background: "none", cursor: "pointer", padding: 4, color: T.inkFaint }}
              >
                <Minimize2 size={18} />
              </button>
            </div>
            {maximizedChart === "campaign-bar" && renderCampaignBarChart(520)}
          </div>
        </div>
      )}

      {selected && !selectedContract && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(22,27,34,0.35)", display: "flex", justifyContent: "flex-end", zIndex: 50 }} onClick={() => setSelected(null)}>
          <div style={{ width: "56%", minWidth: 460, maxWidth: "94vw", background: T.surface, height: "100%", padding: 22, boxShadow: "-8px 0 24px rgba(0,0,0,0.12)" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div style={{ fontSize: 13, color: detailError ? T.risk : T.inkFaint }}>{detailError || `Loading contract ${selected}…`}</div>
              <button onClick={() => setSelected(null)} style={{ border: "none", background: "none", cursor: "pointer" }}><X size={18} color={T.inkFaint} /></button>
            </div>
          </div>
        </div>
      )}

      {/* Detail drawer */}
      {selectedContract && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(22,27,34,0.35)", display: "flex", justifyContent: "flex-end", zIndex: 50 }} onClick={() => setSelected(null)}>
          <div style={{ width: "56%", minWidth: 460, maxWidth: "94vw", background: T.surface, height: "100%", overflowY: "auto", padding: 22, boxShadow: "-8px 0 24px rgba(0,0,0,0.12)" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 4 }}>
              <div>
                <div style={{ fontSize: 11.5, color: T.inkFaint, fontWeight: 600 }}>{selectedContract.contractId} · {areaLabel(selectedContract.ctx)}</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{selectedContract.customerName}</div>
              </div>
              <button onClick={() => setSelected(null)} style={{ border: "none", background: "none", cursor: "pointer" }}><X size={18} color={T.inkFaint} /></button>
            </div>

            <div style={{ display: "flex", gap: 8, margin: "10px 0 16px", flexWrap: "wrap" }}>
              {[...new Set(portfolioContracts.map((pc) => pc.region))].map((r) => (
                <Badge key={r} text={areaLabel(r === "—" ? null : r)} color={T.purple} bg={T.purpleBg} />
              ))}
              <Badge text={selectedContract.channel} color={T.info} bg={T.infoBg} />
              <Badge text={BUCKET_LABEL[selectedContract.bucket]} color={T.inkMuted} bg={T.surfaceSunken} />
              <Badge text={`${selectedContract.segment} · ${selectedContract.riskScore}`} color={SEGMENT_COLOR[selectedContract.segment]} bg={SEGMENT_BG[selectedContract.segment]} />
            </div>

            {selectedContract.bucket === "Lost" && selectedContract.lostReasons && selectedContract.lostReasons.length > 0 && (
              <Card style={{ padding: 14, marginBottom: 16, background: T.riskBg, borderColor: T.risk }}>
                <div style={{ fontSize: 11.5, fontWeight: 700, color: T.risk, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 6 }}>
                  Reason for loss - top service issues
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
                  Customer portfolio - {portfolioContracts.length} contracts
                </div>
                <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
                  <table>
                    <thead><tr>
                      <SortTh label="Contract" sortKey="contractId" sort={portfolioSort} />
                      <SortTh label="Area" sortKey="region" sort={portfolioSort} />
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
                          <td><Badge text={areaLabel(pc.region === "—" ? null : pc.region)} color={T.purple} bg={T.purpleBg} /></td>
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
              <StatBlock label="Contract value" value={`$${selectedContract.contractValue.toLocaleString()}`} sub="annual, from billing history" />
              <StatBlock label="Monthly amount" value={`$${selectedContract.monthlyAmount.toLocaleString()}`} />
              <StatBlock label="Months on book" value={selectedContract.monthsOnBook} />
              <StatBlock label="Price increase" value={selectedContract.priceIncreasePct != null ? `${(selectedContract.priceIncreasePct * 100).toFixed(1)}%` : "—"} sub={selectedContract.priceIncreasePct == null ? "no billing history on record" : undefined} />
            </div>

            {/* The three summaries come from the daily batch; the drawer only shows them. */}
            <CachedAgentCard
              title="Contract Summary (AI Generated)"
              record={detail.summaries.contract}
              placeholderLabel="No summary has been generated for this contract yet - they are produced by the daily batch."
            />
            <CachedAgentCard
              title="Customer Summary (AI Generated)"
              record={detail.summaries.account}
              placeholderLabel="No customer summary has been generated yet - they are produced by the daily batch."
            />

            <div style={{ display: "flex", gap: 18, borderBottom: `1px solid ${T.border}`, marginBottom: 16 }}>
              <button className={`tabbtn ${drawerTab === "action" ? "active" : ""}`} onClick={() => setDrawerTab("action")}>Recommended Action</button>
              <button className={`tabbtn ${drawerTab === "portfolio" ? "active" : ""}`} onClick={() => setDrawerTab("portfolio")}>Customer Portfolio</button>
              <button className={`tabbtn ${drawerTab === "service" ? "active" : ""}`} onClick={() => setDrawerTab("service")}>Service History</button>
              <button className={`tabbtn ${drawerTab === "details" ? "active" : ""}`} onClick={() => setDrawerTab("details")}>Details</button>
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
                    <div style={{ fontSize: 11.5, color: T.inkMuted, marginTop: 8 }}>This contract-milestone was not marked as processed - it will be retried on the next batch run.</div>
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
                        onChange={(e) => setNote(selectedContract.contractId, e.target.value)}
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
                  record={detail.summaries.webClaims}
                  placeholderLabel="No service summary has been generated for this contract yet - they are produced by the daily batch."
                />
                <ServiceTicketHistory equipment={selectedContract.equipment} claims={selectedContract.claims} />
              </>
            )}

            {drawerTab === "details" && (
              <>
                <div style={sectionLabel}>Contact</div>
                <Card style={{ padding: 14, marginBottom: 14 }}>
                  {contact === null && (
                    <>
                      <div style={{ fontSize: 12.5, color: T.inkFaint, marginBottom: 10 }}>Contact details are only shown on request, and every request is recorded in the audit trail.</div>
                      <button onClick={() => revealContact(selectedContract.contractId)} style={{ border: "none", background: T.ink, color: "#fff", borderRadius: 6, padding: "6px 12px", fontSize: 11.5, fontWeight: 600, cursor: "pointer" }}>Show contact details</button>
                    </>
                  )}
                  {contact === "loading" && <div style={{ fontSize: 12.5, color: T.inkFaint }}>Loading…</div>}
                  {Array.isArray(contact) && (contact.length === 0
                    ? <div style={{ fontSize: 12.5, color: T.inkFaint }}>No contact details on record.</div>
                    : <table><tbody>{contact.map((i) => <tr key={i.key}><td style={{ color: T.inkMuted, width: 170 }}>{i.label}</td><td>{fmtDetail(i.value)}</td></tr>)}</tbody></table>)}
                </Card>
                {detail.details.map((g) => (
                  <React.Fragment key={g.group}>
                    <div style={sectionLabel}>{g.group}</div>
                    <Card style={{ padding: 0, overflow: "hidden", marginBottom: 14 }}>
                      <table><tbody>
                        {g.items.map((i) => <tr key={i.key}><td style={{ color: T.inkMuted, width: 220 }}>{i.label}</td><td>{fmtDetail(i.value)}</td></tr>)}
                      </tbody></table>
                    </Card>
                  </React.Fragment>
                ))}
              </>
            )}

            {drawerTab === "evaluation" && (
              <>
                {!selectedTrace && (
                  <div style={{ fontSize: 13, color: T.inkFaint, padding: 14, background: T.surfaceSunken, borderRadius: 8 }}>
                    No evaluation available yet - the daily batch has not produced a recommendation for this contract.
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


/* ---------------------------------------------------------------
   AUTH GATE + ACCESS ADMIN
   The gate picks a screen from /api/auth/me alone. The server is the
   authority on who someone is and what they may see, so nothing here
   stores or trusts a role on the client - it only decides what to draw.
----------------------------------------------------------------*/
const STATUS_STYLE = {
  pending: { color: T.amber, bg: T.amberBg },
  active: { color: T.safe, bg: T.safeBg },
  disabled: { color: T.inkFaint, bg: T.surfaceSunken },
};

const smallBtn = {
  border: `1px solid ${T.border}`, background: T.surface, color: T.ink, borderRadius: 6,
  padding: "5px 9px", fontSize: 12, fontWeight: 600, cursor: "pointer",
};
const primaryBtn = {
  width: "100%", background: T.brand, color: "#fff", border: "none", borderRadius: 8,
  padding: "10px 16px", fontSize: 13.5, fontWeight: 600, cursor: "pointer",
};
const sectionLabel = {
  fontSize: 12.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.3, color: T.inkFaint, marginBottom: 6,
};

function CenteredScreen({ children }) {
  return (
    <div style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", background: T.bg, color: T.ink, minHeight: "100vh" }}>
      <div style={{ height: 4, background: T.brand }} />
      <div style={{ display: "flex", justifyContent: "center", padding: "12vh 24px 24px" }}>
        <Card style={{ maxWidth: 420, width: "100%", padding: 28 }}>
          <div style={{ fontSize: 11.5, color: T.brand, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>Carrier Global</div>
          <h1 style={{ fontSize: 20, fontWeight: 700, margin: "2px 0 18px" }}>Proactive Contract Renewal</h1>
          {children}
        </Card>
      </div>
    </div>
  );
}

function LoginScreen({ config, onDevLogin, error }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!email.trim() || busy) return;
    setBusy(true);
    try { await onDevLogin(email.trim()); } finally { setBusy(false); }
  };
  return (
    <CenteredScreen>
      {config.mode === "sso" ? (
        <button onClick={() => { window.location.href = config.loginUrl; }} style={primaryBtn}>
          Sign in with Microsoft
        </button>
      ) : (
        <>
          <div style={{ fontSize: 12, color: T.amber, background: T.amberBg, borderRadius: 6, padding: "7px 10px", marginBottom: 12 }}>
            Development sign-in: any email is accepted. This is switched off in production.
          </div>
          <input
            type="email" value={email} placeholder="you@company.com" autoFocus
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            style={{ width: "100%", padding: "9px 11px", fontSize: 13.5, border: `1px solid ${T.borderStrong}`, borderRadius: 7, marginBottom: 10 }}
          />
          <button onClick={submit} disabled={busy || !email.trim()} style={{ ...primaryBtn, opacity: busy || !email.trim() ? 0.6 : 1 }}>
            Continue
          </button>
        </>
      )}
      {error && <div style={{ color: T.risk, fontSize: 12, marginTop: 10 }}>{error}</div>}
    </CenteredScreen>
  );
}

function WaitingScreen({ user, status, onRecheck, onLogout }) {
  return (
    <CenteredScreen>
      <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>
        {status === "disabled" ? "Account disabled" : "Waiting for access"}
      </div>
      <div style={{ fontSize: 13, color: T.inkMuted, lineHeight: 1.5, marginBottom: 16 }}>
        {status === "disabled"
          ? <>Access for <b>{user.email}</b> has been turned off. Contact an administrator if you think this is a mistake.</>
          : <>You're signed in as <b>{user.email}</b>, but no area (CTX) has been assigned to you yet. An administrator needs to grant access before you can see any contracts.</>}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        {status !== "disabled" && <button onClick={onRecheck} style={{ ...smallBtn, padding: "8px 14px" }}>Check again</button>}
        <button onClick={onLogout} style={{ ...smallBtn, padding: "8px 14px" }}>Sign out</button>
      </div>
    </CenteredScreen>
  );
}

const ctxLabel = (c) => (c.name === c.code ? c.code : `${c.code} · ${c.name}`);

// Not MultiSelect: its empty state reads "All <label>", which for access
// assignment would be actively misleading (no areas = NO access, not all).
function CtxPicker({ ctxs, value, onApply, disabled }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const ref = useRef(null);
  const valueKey = [...value].sort().join(",");

  useEffect(() => { setDraft(value); }, [valueKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const changed = [...draft].sort().join(",") !== valueKey;
  const toggle = (code) => setDraft((d) => (d.includes(code) ? d.filter((x) => x !== code) : [...d, code]));

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        style={{ ...smallBtn, display: "flex", alignItems: "center", gap: 5, color: value.length ? T.ink : T.risk }}
      >
        {value.length ? value.join(", ") : "No areas"}
        <ChevronDown size={12} />
      </button>
      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, background: T.surface, border: `1px solid ${T.border}`,
          borderRadius: 8, boxShadow: "0 6px 18px rgba(22,27,34,0.12)", padding: 6, zIndex: 30, minWidth: 230,
        }}>
          {ctxs.length === 0 && <div style={{ padding: 8, fontSize: 12, color: T.inkFaint }}>No areas registered yet.</div>}
          {ctxs.map((c) => (
            <label key={c.code} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", fontSize: 12.5, cursor: "pointer" }}>
              <input type="checkbox" checked={draft.includes(c.code)} onChange={() => toggle(c.code)} />
              {ctxLabel(c)}
            </label>
          ))}
          <div style={{ display: "flex", gap: 6, marginTop: 6, padding: "0 2px" }}>
            <button
              disabled={!changed}
              onClick={() => { onApply(draft); setOpen(false); }}
              style={{ ...smallBtn, background: changed ? T.brand : T.surfaceSunken, color: changed ? "#fff" : T.inkFaint, borderColor: changed ? T.brand : T.border }}
            >
              Apply
            </button>
            <button onClick={() => { setDraft(value); setOpen(false); }} style={smallBtn}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function CtxNameRow({ ctx, onRename }) {
  const [name, setName] = useState(ctx.name);
  useEffect(() => { setName(ctx.name); }, [ctx.name]);
  const commit = () => { if (name.trim() && name.trim() !== ctx.name) onRename(ctx.code, name.trim()); else setName(ctx.name); };
  return (
    <tr>
      <td style={{ fontFamily: "ui-monospace, monospace", fontWeight: 600 }}>{ctx.code}</td>
      <td>
        <input
          value={name} onChange={(e) => setName(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
          style={{ padding: "5px 8px", fontSize: 13, border: `1px solid ${T.border}`, borderRadius: 6, width: 220 }}
        />
      </td>
      <td style={{ color: T.inkMuted }}>{ctx.contractCount.toLocaleString()}</td>
    </tr>
  );
}

function describeAudit(a) {
  const d = a.detail || {};
  const list = (v) => (v && v.length ? v.join(", ") : "none");
  switch (a.action) {
    case "user.ctx_assigned": return `${d.email}: ${list(d.before)} → ${list(d.after)}`;
    case "user.role_changed": return `${d.email}: ${d.role?.[0]} → ${d.role?.[1]}`;
    case "ctx.renamed": return `${a.ctxCode}: ${d.name?.[0]} → ${d.name?.[1]}`;
    default: return d.email || "";
  }
}

function AccessPage({ currentUserId }) {
  const [users, setUsers] = useState(null);
  const [ctxInfo, setCtxInfo] = useState({ ctxs: [], contractsWithoutCtx: 0 });
  const [audit, setAudit] = useState([]);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      const [u, c, a] = await Promise.all([api.getAdminUsers(), api.getAdminCtx(), api.getAdminAudit(20)]);
      setUsers(u); setCtxInfo(c); setAudit(a); setError(null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (userId, fn) => {
    setBusyId(userId);
    try { await fn(); await load(); } catch (e) { setError(String(e.message || e)); } finally { setBusyId(null); }
  };
  const renameCtx = async (code, name) => {
    try { await api.renameCtx(code, name); await load(); } catch (e) { setError(String(e.message || e)); }
  };

  const pending = (users || []).filter((u) => u.status === "pending").length;

  return (
    <div>
      {error && (
        <div style={{ fontSize: 12.5, color: T.risk, background: T.riskBg, borderRadius: 8, padding: "9px 12px", marginBottom: 14 }}>{error}</div>
      )}
      {ctxInfo.contractsWithoutCtx > 0 && (
        <div style={{ fontSize: 12.5, color: T.amber, background: T.amberBg, borderRadius: 8, padding: "9px 12px", marginBottom: 14 }}>
          {ctxInfo.contractsWithoutCtx.toLocaleString()} contract{ctxInfo.contractsWithoutCtx === 1 ? "" : "s"} have no CTX value, so only admins can see them.
          Check that the contract data includes a CTX column.
        </div>
      )}

      <div style={sectionLabel}>
        Users {pending > 0 && <span style={{ color: T.amber }}>· {pending} waiting for access</span>}
      </div>
      <Card style={{ padding: 0, marginBottom: 22 }}>
        {users === null ? (
          <div style={{ padding: 16, fontSize: 12.5, color: T.inkFaint }}>Loading…</div>
        ) : (
          <table>
            <thead>
              <tr><th>User</th><th>Role</th><th>Status</th><th>Areas (CTX)</th><th>Last sign-in</th><th /></tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{u.name || u.email}{u.id === currentUserId && <span style={{ color: T.inkFaint, fontWeight: 400 }}> (you)</span>}</div>
                    <div style={{ fontSize: 11.5, color: T.inkMuted }}>{u.email}</div>
                  </td>
                  <td>{u.role === "admin" ? <Badge text="Admin" color={T.brand} bg={T.brandBg} /> : "User"}</td>
                  <td><Badge text={u.status} color={STATUS_STYLE[u.status].color} bg={STATUS_STYLE[u.status].bg} /></td>
                  <td>
                    {u.role === "admin"
                      ? <span style={{ color: T.inkMuted, fontSize: 12.5 }}>All areas</span>
                      : <CtxPicker
                          ctxs={ctxInfo.ctxs} value={u.ctxs} disabled={busyId === u.id || u.status === "disabled"}
                          onApply={(codes) => act(u.id, () => api.setUserCtx(u.id, codes))}
                        />}
                  </td>
                  <td style={{ color: T.inkMuted, fontSize: 12 }}>{u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString() : "—"}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button
                      style={smallBtn} disabled={busyId === u.id}
                      onClick={() => act(u.id, () => api.setUserRole(u.id, u.role === "admin" ? "user" : "admin"))}
                    >
                      {u.role === "admin" ? "Remove admin" : "Make admin"}
                    </button>{" "}
                    <button
                      style={smallBtn} disabled={busyId === u.id}
                      onClick={() => act(u.id, () => api.setUserDisabled(u.id, u.status !== "disabled"))}
                    >
                      {u.status === "disabled" ? "Enable" : "Disable"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div style={sectionLabel}>Areas (CTX)</div>
      <Card style={{ padding: 0, marginBottom: 22 }}>
        <table>
          <thead><tr><th>Code</th><th>Name</th><th>Contracts</th></tr></thead>
          <tbody>
            {ctxInfo.ctxs.map((c) => <CtxNameRow key={c.code} ctx={c} onRename={renameCtx} />)}
            {ctxInfo.ctxs.length === 0 && <tr><td colSpan={3} style={{ color: T.inkFaint }}>No CTX codes found in the contract data yet.</td></tr>}
          </tbody>
        </table>
      </Card>

      <div style={sectionLabel}>Recent activity</div>
      <Card style={{ padding: 0 }}>
        <table>
          <thead><tr><th>When</th><th>By</th><th>Action</th><th>Detail</th></tr></thead>
          <tbody>
            {audit.map((a) => (
              <tr key={a.id}>
                <td style={{ color: T.inkMuted, fontSize: 12, whiteSpace: "nowrap" }}>{new Date(a.at).toLocaleString()}</td>
                <td style={{ fontSize: 12.5 }}>{a.actorEmail || "—"}</td>
                <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 11.5 }}>{a.action}</td>
                <td style={{ fontSize: 12.5, color: T.inkMuted }}>{describeAudit(a)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

export default function App() {
  const [phase, setPhase] = useState("loading"); // loading | login | pending | disabled | ready | error
  const [config, setConfig] = useState(null);
  const [user, setUser] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setConfig(await api.getAuthConfig());
      try {
        const me = await api.getMe();
        setUser(me);
        setPhase(me.status === "active" ? "ready" : me.status);
      } catch (e) {
        if (e.status !== 401) throw e;
        setUser(null);
        setPhase("login");
      }
    } catch (e) {
      setError(String(e.message || e));
      setPhase("error");
    }
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("auth:changed", load); // fired by api.js on a 401 / access change
    return () => window.removeEventListener("auth:changed", load);
  }, [load]);

  const logout = async () => {
    try { await api.logout(); } catch (e) { /* the session is gone either way */ }
    setError(null);
    await load();
  };
  const devLogin = async (email) => {
    try { await api.devLogin(email); setError(null); await load(); } catch (e) { setError(String(e.message || e)); }
  };

  if (phase === "loading") return <CenteredScreen><div style={{ color: T.inkMuted, fontSize: 13 }}>Loading…</div></CenteredScreen>;
  if (phase === "error") {
    return (
      <CenteredScreen>
        <div style={{ color: T.risk, fontSize: 13, marginBottom: 12 }}>{error}</div>
        <button onClick={() => { setPhase("loading"); load(); }} style={{ ...smallBtn, padding: "8px 14px" }}>Retry</button>
      </CenteredScreen>
    );
  }
  if (phase === "login") return <LoginScreen config={config} onDevLogin={devLogin} error={error} />;
  if (phase === "pending" || phase === "disabled") {
    return <WaitingScreen user={user} status={phase} onRecheck={load} onLogout={logout} />;
  }
  // key={user.id}: signing in as someone else remounts the app, so the previous
  // user's loaded contracts and summaries can't linger in component state.
  return <ContractRenewalPOC key={user.id} user={user} onLogout={logout} />;
}
