import { create } from 'zustand';
import { api } from '../api/client';

export const useIncidentStore = create((set, get) => ({
  incidents: [],
  activeIncidents: [],
  selectedIncident: null,
  stats: null,
  isLoading: false,
  error: null,
  filters: {
    status: '',
    severity: '',
    service: '',
  },

  async fetchIncidents() {
    set({ isLoading: true });
    try {
      const { filters } = get();
      const params = {};
      if (filters.status) params.status = filters.status;
      if (filters.severity) params.severity = filters.severity;
      if (filters.service) params.service = filters.service;
      const data = await api.getIncidents(params);
      set({ incidents: data, isLoading: false, error: null });
    } catch (err) {
      set({ isLoading: false, error: err.message });
    }
  },

  async fetchActiveIncidents() {
    try {
      const data = await api.getActiveIncidents();
      set({ activeIncidents: data, error: null });
    } catch (err) {
      set({ error: err.message });
    }
  },

  async fetchIncident(id) {
    try {
      const data = await api.getIncident(id);
      set({ selectedIncident: data, error: null });
      return data;
    } catch (err) {
      set({ error: err.message });
      return null;
    }
  },

  async fetchStats() {
    try {
      const data = await api.getStats();
      set({ stats: data });
    } catch (err) {
      set({ error: err.message });
    }
  },

  setFilter(key, value) {
    set((state) => ({
      filters: { ...state.filters, [key]: value },
    }));
  },

  clearFilters() {
    set({
      filters: { status: '', severity: '', service: '' },
    });
  },

  addOrUpdateIncident(incident) {
    set((state) => {
      const idx = state.incidents.findIndex(
        (i) => i.incident_id === incident.incident_id
      );
      const incidents =
        idx >= 0
          ? state.incidents.map((i, j) => (j === idx ? { ...i, ...incident } : i))
          : [incident, ...state.incidents];

      const activeStatuses = ['Triggered', 'Triaged', 'Investigating', 'Mitigated'];
      const isActive = activeStatuses.includes(incident.status);
      const activeIdx = state.activeIncidents.findIndex(
        (i) => i.incident_id === incident.incident_id
      );

      let activeIncidents;
      if (isActive) {
        activeIncidents =
          activeIdx >= 0
            ? state.activeIncidents.map((i, j) =>
                j === activeIdx ? { ...i, ...incident } : i
              )
            : [incident, ...state.activeIncidents];
      } else {
        activeIncidents =
          activeIdx >= 0
            ? state.activeIncidents.filter((_, j) => j !== activeIdx)
            : state.activeIncidents;
      }

      // Update selectedIncident if it matches
      const selectedIncident =
        state.selectedIncident?.incident_id === incident.incident_id
          ? { ...state.selectedIncident, ...incident }
          : state.selectedIncident;

      return { incidents, activeIncidents, selectedIncident };
    });
  },

  removeIncident(incidentId) {
    set((state) => ({
      incidents: state.incidents.filter((i) => i.incident_id !== incidentId),
      activeIncidents: state.activeIncidents.filter(
        (i) => i.incident_id !== incidentId
      ),
      selectedIncident:
        state.selectedIncident?.incident_id === incidentId
          ? null
          : state.selectedIncident,
    }));
  },

  setSelectedIncident(incident) {
    set({ selectedIncident: incident });
  },

  clearSelectedIncident() {
    set({ selectedIncident: null });
  },
}));
