const API_BASE = '/api';

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }).then((r) => r.json()),

  getHealth: () => fetch('/health').then((r) => r.json()),

  // Webhook Playground
  testPlayground: (source, payload) =>
    request('/playground/test', {
      method: 'POST',
      body: JSON.stringify({ source, payload }),
    }),

  // Audit Trail
  getAuditTrail: (incidentId) =>
    request(`/audit-trail${incidentId ? '?incident_id=' + incidentId : ''}`),

  getIncidentReplay: (incidentId) =>
    request(`/audit-trail/${incidentId}/replay`),

  // Send live webhook (goes through API to bypass webhook auth)
  sendWebhook: (source, payload) =>
    request('/playground/send', {
      method: 'POST',
      body: JSON.stringify({ source, payload }),
    }),

  // Semantic Search
  search: (query, scope = 'all') =>
    request('/search', {
      method: 'POST',
      body: JSON.stringify({ query, scope }),
    }),

  // Incident Commander
  commanderQuery: (incidentId, message, conversationId = '') =>
    request(`/incidents/${incidentId}/commander`, {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id: conversationId }),
    }),

  clearCommanderHistory: (incidentId) =>
    request(`/incidents/${incidentId}/commander/history`, { method: 'DELETE' }),
};
