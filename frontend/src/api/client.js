const API_BASE = '/api';

function getToken() {
  return localStorage.getItem('opslens_token');
}

function getRefreshToken() {
  return localStorage.getItem('opslens_refresh_token');
}

function setToken(token) {
  if (token) localStorage.setItem('opslens_token', token);
  else localStorage.removeItem('opslens_token');
}

let isRefreshing = false;
let refreshQueue = [];

function processQueue(error, token) {
  refreshQueue.forEach((p) => {
    if (error) p.reject(error);
    else p.resolve(token);
  });
  refreshQueue = [];
}

async function refreshAccessToken() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) throw new Error('No refresh token');
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) throw new Error('Token refresh failed');
  const data = await res.json();
  setToken(data.access_token);
  if (data.refresh_token) {
    localStorage.setItem('opslens_refresh_token', data.refresh_token);
  }
  return data.access_token;
}

async function request(path, options = {}) {
  const token = getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  let res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  // Handle 401 with token refresh
  if (res.status === 401 && token) {
    if (!isRefreshing) {
      isRefreshing = true;
      try {
        const newToken = await refreshAccessToken();
        isRefreshing = false;
        processQueue(null, newToken);
        // Retry original request
        headers['Authorization'] = `Bearer ${newToken}`;
        res = await fetch(`${API_BASE}${path}`, { ...options, headers });
      } catch (err) {
        isRefreshing = false;
        processQueue(err, null);
        // Clear auth state on refresh failure
        localStorage.removeItem('opslens_token');
        localStorage.removeItem('opslens_refresh_token');
        localStorage.removeItem('opslens_user');
        window.dispatchEvent(new CustomEvent('auth:logout'));
        throw new Error('Session expired. Please log in again.');
      }
    } else {
      // Wait for the ongoing refresh
      try {
        const newToken = await new Promise((resolve, reject) => {
          refreshQueue.push({ resolve, reject });
        });
        headers['Authorization'] = `Bearer ${newToken}`;
        res = await fetch(`${API_BASE}${path}`, { ...options, headers });
      } catch {
        throw new Error('Session expired. Please log in again.');
      }
    }
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function requestRaw(path, options = {}) {
  const token = getToken();
  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res;
}

export const api = {
  // ─── Incidents ───────────────────────────────
  getIncidents: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v)
    ).toString();
    return request(`/incidents${qs ? '?' + qs : ''}`);
  },

  getActiveIncidents: () => request('/incidents/active'),

  getIncident: (id) => request(`/incidents/${id}`),

  getTimeline: (id) => request(`/incidents/${id}/timeline`),

  getStats: () => request('/incidents/stats'),

  transitionIncident: (id, newStatus, reason = '', actor = 'dashboard') =>
    request(`/incidents/${id}/transition`, {
      method: 'POST',
      body: JSON.stringify({ new_status: newStatus, reason, actor }),
    }),

  addComment: (id, comment, actor = 'dashboard') =>
    request(`/incidents/${id}/comment`, {
      method: 'POST',
      body: JSON.stringify({ comment, actor }),
    }),

  createManualIncident: (data) =>
    fetch('/webhooks/manual', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      },
      body: JSON.stringify(data),
    }).then((r) => r.json()),

  getHealth: () => fetch('/health').then((r) => r.json()),

  // ─── Webhook Playground ─────────────────────
  testPlayground: (source, payload) =>
    request('/playground/test', {
      method: 'POST',
      body: JSON.stringify({ source, payload }),
    }),

  sendWebhook: (source, payload) =>
    request('/playground/send', {
      method: 'POST',
      body: JSON.stringify({ source, payload }),
    }),

  // ─── Audit Trail ────────────────────────────
  getAuditTrail: (incidentId) =>
    request(`/audit-trail${incidentId ? '?incident_id=' + incidentId : ''}`),

  getIncidentReplay: (incidentId) =>
    request(`/audit-trail/${incidentId}/replay`),

  // ─── Search ─────────────────────────────────
  search: (query, scope = 'all') =>
    request('/search', {
      method: 'POST',
      body: JSON.stringify({ query, scope }),
    }),

  // ─── Incident Commander ─────────────────────
  commanderQuery: (incidentId, message, conversationId = '') =>
    request(`/incidents/${incidentId}/commander`, {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id: conversationId }),
    }),

  clearCommanderHistory: (incidentId) =>
    request(`/incidents/${incidentId}/commander/history`, { method: 'DELETE' }),

  // ─── Auth ───────────────────────────────────
  authRegister: (email, password, name) =>
    request('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, name }),
    }),

  authLogin: (email, password) =>
    request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  authRefresh: (refreshToken) =>
    request('/auth/refresh', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),

  getMe: () => request('/auth/me'),

  updateMe: (data) =>
    request('/auth/me', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  changePassword: (currentPassword, newPassword) =>
    request('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  oauthAuthorize: (provider) =>
    request(`/auth/oauth/${provider}/authorize`),

  getUsers: () => request('/auth/users'),

  updateUserRole: (userId, role) =>
    request(`/auth/users/${userId}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role }),
    }),

  updateUserStatus: (userId, active) =>
    request(`/auth/users/${userId}/status`, {
      method: 'PUT',
      body: JSON.stringify({ active }),
    }),

  inviteUser: (email, name, role) =>
    request('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, name, role, password: crypto.randomUUID().slice(0, 16) }),
    }),

  // ─── On-Call ────────────────────────────────
  getOncallSchedules: () => request('/enterprise/oncall'),

  createOncallSchedule: (schedule) =>
    request('/enterprise/oncall', {
      method: 'POST',
      body: JSON.stringify(schedule),
    }),

  updateOncallSchedule: (id, schedule) =>
    request(`/enterprise/oncall/${id}`, {
      method: 'PUT',
      body: JSON.stringify(schedule),
    }),

  deleteOncallSchedule: (id) =>
    request(`/enterprise/oncall/${id}`, { method: 'DELETE' }),

  rotateOncall: (id) =>
    request(`/enterprise/oncall/${id}/rotate`, { method: 'POST' }),

  escalateOncall: (id, reason) =>
    request(`/enterprise/oncall/${id}/escalate`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  // ─── SLA ────────────────────────────────────
  getSlaPolicies: () => request('/enterprise/sla'),

  createSlaPolicy: (policy) =>
    request('/enterprise/sla', {
      method: 'POST',
      body: JSON.stringify(policy),
    }),

  updateSlaPolicy: (id, policy) =>
    request(`/enterprise/sla/${id}`, {
      method: 'PUT',
      body: JSON.stringify(policy),
    }),

  deleteSlaPolicy: (id) =>
    request(`/enterprise/sla/${id}`, { method: 'DELETE' }),

  getSlaStatus: () => request('/enterprise/sla/status'),

  getSlaCompliance: (startDate, endDate) => {
    const params = new URLSearchParams();
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    const qs = params.toString();
    return request(`/enterprise/sla/compliance${qs ? '?' + qs : ''}`);
  },

  // ─── Alert Rules ────────────────────────────
  getAlertRules: () => request('/enterprise/alert-rules'),

  createAlertRule: (rule) =>
    request('/enterprise/alert-rules', {
      method: 'POST',
      body: JSON.stringify(rule),
    }),

  updateAlertRule: (id, rule) =>
    request(`/enterprise/alert-rules/${id}`, {
      method: 'PUT',
      body: JSON.stringify(rule),
    }),

  deleteAlertRule: (id) =>
    request(`/enterprise/alert-rules/${id}`, { method: 'DELETE' }),

  toggleAlertRule: (id, enabled) =>
    request(`/enterprise/alert-rules/${id}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),

  // ─── Runbooks ───────────────────────────────
  getRunbookExecutions: () => request('/enterprise/runbooks/executions'),

  executeRunbook: (runbookId, params) =>
    request('/enterprise/runbooks/execute', {
      method: 'POST',
      body: JSON.stringify({ runbook_id: runbookId, params }),
    }),

  getRunbookStatus: (executionId) =>
    request(`/enterprise/runbooks/executions/${executionId}`),

  approveRunbookStep: (executionId, stepId) =>
    request(`/enterprise/runbooks/executions/${executionId}/steps/${stepId}/approve`, {
      method: 'POST',
    }),

  rejectRunbookStep: (executionId, stepId, reason) =>
    request(
      `/enterprise/runbooks/executions/${executionId}/steps/${stepId}/reject`,
      {
        method: 'POST',
        body: JSON.stringify({ reason }),
      }
    ),

  cancelRunbook: (executionId) =>
    request(`/enterprise/runbooks/executions/${executionId}/cancel`, {
      method: 'POST',
    }),

  // ─── Reports ────────────────────────────────
  getReports: () => request('/enterprise/reports'),

  generateReport: (type, startDate, endDate) =>
    request('/enterprise/reports/generate', {
      method: 'POST',
      body: JSON.stringify({
        type,
        start_date: startDate,
        end_date: endDate,
      }),
    }),

  getReport: (id) => request(`/enterprise/reports/${id}`),

  getReportCsv: (id) =>
    requestRaw(`/enterprise/reports/${id}/csv`).then(async (res) => {
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `report-${id}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }),
};
