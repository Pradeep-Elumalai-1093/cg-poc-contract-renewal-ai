// All data manipulation and AI orchestration now lives in the FastAPI
// backend. This module is the only place the React app talks to it.
const BASE = ""; // same-origin in prod; Vite's dev proxy handles /api in dev

async function request(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch (e) { detail = res.statusText; }
    throw new Error(detail || `Request to ${path} failed (HTTP ${res.status})`);
  }
  return res.json();
}

export const api = {
  getContracts: () => request("/api/contracts"),
  getTrace: () => request("/api/trace"),
  getMetrics: () => request("/api/metrics"),
  getCampaigns: () => request("/api/campaigns"),
  getBatchStatus: () => request("/api/batch/status"),
  runBatch: () => request("/api/batch/run", { method: "POST" }),
  sendFeedback: (contractId, outcome, note) =>
    request("/api/feedback", { method: "POST", body: JSON.stringify({ contractId, outcome, note }) }),
  setActionStatus: (contractId, actionStatus) =>
    request("/api/action-status", { method: "POST", body: JSON.stringify({ contractId, actionStatus }) }),
  getModelInfo: () => request("/api/model-info"),
  getTicketSummaries: () => request("/api/ticket-summaries"),
  runTicketSummary: (contractId) =>
    request("/api/ticket-summaries/run", { method: "POST", body: JSON.stringify({ contractId }) }),
  getCustomerSummaries: () => request("/api/customer-summaries"),
  runCustomerSummary: (customerId) =>
    request("/api/customer-summaries/run", { method: "POST", body: JSON.stringify({ customerId }) }),
  getOutcomeByRiskBucket: () => request("/api/outcome-by-risk-bucket"),
  getRegionSummary: () => request("/api/region-summary"),
  reset: () => request("/api/reset", { method: "POST" }),
};
