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
    // The session ended, or an admin changed this user's access while the page
    // was open: tell the app to re-check who the user is. /api/auth/* is excluded
    // so the initial "am I signed in?" 401 can't trigger itself in a loop.
    const accessChanged = res.status === 401 ||
      (res.status === 403 && (detail === "access_pending" || detail === "account_disabled"));
    if (accessChanged && !path.startsWith("/api/auth/")) {
      window.dispatchEvent(new Event("auth:changed"));
    }
    const err = new Error(typeof detail === "string" && detail ? detail : `Request to ${path} failed (HTTP ${res.status})`);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return res.json();
}

const put = (path, body) => request(path, { method: "PUT", body: JSON.stringify(body) });

export const api = {
  // --- auth ---
  getAuthConfig: () => request("/api/auth/config"),
  getMe: () => request("/api/auth/me"),
  devLogin: (email) => request("/api/auth/dev/login", { method: "POST", body: JSON.stringify({ email }) }),
  logout: () => request("/api/auth/logout", { method: "POST" }),

  // --- admin (Access page) ---
  getAdminUsers: () => request("/api/admin/users"),
  getAdminCtx: () => request("/api/admin/ctx"),
  getAdminAudit: (limit = 20) => request(`/api/admin/audit?limit=${limit}`),
  setUserCtx: (userId, ctxs) => put(`/api/admin/users/${userId}/ctx`, { ctxs }),
  setUserRole: (userId, role) => put(`/api/admin/users/${userId}/role`, { role }),
  setUserDisabled: (userId, disabled) => put(`/api/admin/users/${userId}/status`, { disabled }),
  renameCtx: (code, name) => put(`/api/admin/ctx/${code}`, { name }),

  // --- app data ---
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
  // A customer summary is per (customer, CTX): pass the selected contract's ctx.
  runCustomerSummary: (customerId, ctx) =>
    request("/api/customer-summaries/run", { method: "POST", body: JSON.stringify({ customerId, ctx: ctx || null }) }),
  getOutcomeByRiskBucket: () => request("/api/outcome-by-risk-bucket"),
  getRegionSummary: () => request("/api/region-summary"),
  reset: () => request("/api/reset", { method: "POST" }),
};
