import { create } from 'zustand';
import { api } from '../api/client';

export const useEnterpriseStore = create((set, get) => ({
  // On-Call
  oncallSchedules: [],
  oncallLoading: false,

  // SLA
  slaPolicies: [],
  slaStatus: [],
  slaCompliance: null,
  slaLoading: false,

  // Alert Rules
  alertRules: [],
  alertRulesLoading: false,

  // Runbooks
  runbookExecutions: [],
  runbooksLoading: false,

  // Reports
  reports: [],
  reportsLoading: false,

  error: null,

  // ─── On-Call ────────────────────────────────
  async fetchOncallSchedules() {
    set({ oncallLoading: true });
    try {
      const data = await api.getOncallSchedules();
      set({ oncallSchedules: data, oncallLoading: false, error: null });
    } catch (err) {
      set({ oncallLoading: false, error: err.message });
    }
  },

  async createOncallSchedule(schedule) {
    try {
      const data = await api.createOncallSchedule(schedule);
      set((s) => ({ oncallSchedules: [...s.oncallSchedules, data] }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async updateOncallSchedule(id, schedule) {
    try {
      const data = await api.updateOncallSchedule(id, schedule);
      set((s) => ({
        oncallSchedules: s.oncallSchedules.map((o) =>
          o.id === id ? data : o
        ),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async deleteOncallSchedule(id) {
    try {
      await api.deleteOncallSchedule(id);
      set((s) => ({
        oncallSchedules: s.oncallSchedules.filter((o) => o.id !== id),
      }));
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async rotateOncall(id) {
    try {
      const data = await api.rotateOncall(id);
      set((s) => ({
        oncallSchedules: s.oncallSchedules.map((o) =>
          o.id === id ? data : o
        ),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async escalateOncall(id, reason) {
    try {
      const data = await api.escalateOncall(id, reason);
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  // ─── SLA ────────────────────────────────────
  async fetchSlaPolicies() {
    set({ slaLoading: true });
    try {
      const data = await api.getSlaPolicies();
      set({ slaPolicies: data, slaLoading: false, error: null });
    } catch (err) {
      set({ slaLoading: false, error: err.message });
    }
  },

  async createSlaPolicy(policy) {
    try {
      const data = await api.createSlaPolicy(policy);
      set((s) => ({ slaPolicies: [...s.slaPolicies, data] }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async updateSlaPolicy(id, policy) {
    try {
      const data = await api.updateSlaPolicy(id, policy);
      set((s) => ({
        slaPolicies: s.slaPolicies.map((p) => (p.id === id ? data : p)),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async deleteSlaPolicy(id) {
    try {
      await api.deleteSlaPolicy(id);
      set((s) => ({
        slaPolicies: s.slaPolicies.filter((p) => p.id !== id),
      }));
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async fetchSlaStatus() {
    try {
      const data = await api.getSlaStatus();
      set({ slaStatus: data, error: null });
    } catch (err) {
      set({ error: err.message });
    }
  },

  async fetchSlaCompliance(startDate, endDate) {
    try {
      const data = await api.getSlaCompliance(startDate, endDate);
      set({ slaCompliance: data, error: null });
      return data;
    } catch (err) {
      set({ error: err.message });
    }
  },

  // ─── Alert Rules ────────────────────────────
  async fetchAlertRules() {
    set({ alertRulesLoading: true });
    try {
      const data = await api.getAlertRules();
      set({ alertRules: data, alertRulesLoading: false, error: null });
    } catch (err) {
      set({ alertRulesLoading: false, error: err.message });
    }
  },

  async createAlertRule(rule) {
    try {
      const data = await api.createAlertRule(rule);
      set((s) => ({ alertRules: [...s.alertRules, data] }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async updateAlertRule(id, rule) {
    try {
      const data = await api.updateAlertRule(id, rule);
      set((s) => ({
        alertRules: s.alertRules.map((r) => (r.id === id ? data : r)),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async deleteAlertRule(id) {
    try {
      await api.deleteAlertRule(id);
      set((s) => ({
        alertRules: s.alertRules.filter((r) => r.id !== id),
      }));
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async toggleAlertRule(id) {
    const rule = get().alertRules.find((r) => r.id === id);
    if (!rule) return;
    try {
      const data = await api.toggleAlertRule(id, !rule.enabled);
      set((s) => ({
        alertRules: s.alertRules.map((r) => (r.id === id ? data : r)),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  // ─── Runbooks ───────────────────────────────
  async fetchRunbookExecutions() {
    set({ runbooksLoading: true });
    try {
      const data = await api.getRunbookExecutions();
      set({ runbookExecutions: data, runbooksLoading: false, error: null });
    } catch (err) {
      set({ runbooksLoading: false, error: err.message });
    }
  },

  async executeRunbook(runbookId, params) {
    try {
      const data = await api.executeRunbook(runbookId, params);
      set((s) => ({
        runbookExecutions: [data, ...s.runbookExecutions],
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async getRunbookStatus(executionId) {
    try {
      return await api.getRunbookStatus(executionId);
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async approveRunbookStep(executionId, stepId) {
    try {
      const data = await api.approveRunbookStep(executionId, stepId);
      set((s) => ({
        runbookExecutions: s.runbookExecutions.map((r) =>
          r.id === executionId ? data : r
        ),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async rejectRunbookStep(executionId, stepId, reason) {
    try {
      const data = await api.rejectRunbookStep(executionId, stepId, reason);
      set((s) => ({
        runbookExecutions: s.runbookExecutions.map((r) =>
          r.id === executionId ? data : r
        ),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async cancelRunbook(executionId) {
    try {
      const data = await api.cancelRunbook(executionId);
      set((s) => ({
        runbookExecutions: s.runbookExecutions.map((r) =>
          r.id === executionId ? data : r
        ),
      }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  // ─── Reports ────────────────────────────────
  async fetchReports() {
    set({ reportsLoading: true });
    try {
      const data = await api.getReports();
      set({ reports: data, reportsLoading: false, error: null });
    } catch (err) {
      set({ reportsLoading: false, error: err.message });
    }
  },

  async generateReport(type, startDate, endDate) {
    try {
      const data = await api.generateReport(type, startDate, endDate);
      set((s) => ({ reports: [data, ...s.reports] }));
      return data;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async getReport(id) {
    try {
      return await api.getReport(id);
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async downloadReportCsv(id) {
    try {
      return await api.getReportCsv(id);
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  clearError() {
    set({ error: null });
  },
}));
