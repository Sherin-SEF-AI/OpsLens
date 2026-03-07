import React, { useState, useEffect, useCallback } from 'react';
import {
  AlertTriangle,
  LayoutDashboard,
  BarChart3,
  Bot,
  Plus,
  Wifi,
  WifiOff,
  RefreshCw,
  Settings,
  Zap,
  History,
  Search,
  X,
} from 'lucide-react';
import IncidentList from './components/IncidentList';
import IncidentDetail from './components/IncidentDetail';
import MetricsPanel from './components/MetricsPanel';
import AgentActivityFeed from './components/AgentActivityFeed';
import SettingsPage from './components/SettingsPage';
import WebhookPlayground from './components/WebhookPlayground';
import AuditTrail from './components/AuditTrail';
import SearchPanel from './components/SearchPanel';
import { api } from './api/client';
import { useWebSocket } from './hooks/useWebSocket';

const NAV_SECTIONS = [
  {
    label: 'Incidents',
    items: [
      { id: 'active', label: 'Active', icon: AlertTriangle },
      { id: 'all', label: 'All Incidents', icon: LayoutDashboard },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { id: 'metrics', label: 'Metrics', icon: BarChart3 },
      { id: 'agents', label: 'Agent Feed', icon: Bot },
      { id: 'audit', label: 'Audit Trail', icon: History },
      { id: 'search', label: 'Search', icon: Search },
    ],
  },
  {
    label: 'Tools',
    items: [
      { id: 'playground', label: 'Playground', icon: Zap },
      { id: 'settings', label: 'Settings', icon: Settings },
    ],
  },
];

function ToastContainer({ toasts, onDismiss }) {
  if (!toasts.length) return null;
  return (
    <div className="fixed top-4 right-4 z-[60] flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-start gap-2.5 px-3.5 py-2.5 rounded-lg border shadow-lg shadow-black/30 animate-fade-in text-xs ${
            t.type === 'created'
              ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
              : t.type === 'updated'
              ? 'bg-blue-500/10 border-blue-500/20 text-blue-400'
              : 'bg-dark-card border-dark-border text-dark-text'
          }`}
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-medium">{t.title}</p>
            {t.subtitle && <p className="text-[10px] opacity-70 mt-0.5 truncate">{t.subtitle}</p>}
          </div>
          <button onClick={() => onDismiss(t.id)} className="p-0.5 hover:opacity-70 shrink-0">
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState('active');
  const [incidents, setIncidents] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [allAgentEvents, setAllAgentEvents] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [health, setHealth] = useState(null);
  const [toasts, setToasts] = useState([]);

  function addToast(title, subtitle, type) {
    const id = Date.now();
    setToasts((prev) => [...prev.slice(-4), { id, title, subtitle, type }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 5000);
  }

  function dismissToast(id) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  const loadIncidents = useCallback(async () => {
    try {
      const data =
        tab === 'active'
          ? await api.getActiveIncidents()
          : await api.getIncidents();
      setIncidents(data);
    } catch (e) {
      console.error('Failed to load incidents:', e);
    }
  }, [tab]);

  const handleWsMessage = useCallback((msg) => {
    if (msg.type === 'incident_created') {
      loadIncidents();
      const d = msg.data;
      addToast(
        'New Incident Created',
        d?.title || d?.incident_id || '',
        'created'
      );
    } else if (msg.type === 'incident_updated') {
      loadIncidents();
      const d = msg.data;
      addToast(
        `Incident ${d?.status || 'Updated'}`,
        d?.title || d?.incident_id || '',
        'updated'
      );
    }
    if (msg.type === 'timeline_event') {
      const event = msg.data?.event;
      if (event) {
        setAllAgentEvents((prev) => [
          ...prev.slice(-99),
          { ...event, incident_id: msg.data.incident_id },
        ]);
      }
    }
  }, [loadIncidents]);

  const { connected } = useWebSocket(handleWsMessage);

  useEffect(() => {
    loadIncidents();
    const interval = setInterval(loadIncidents, 10000);
    return () => clearInterval(interval);
  }, [loadIncidents]);

  useEffect(() => {
    api.getHealth().then(setHealth).catch(() => {});
    const interval = setInterval(() => {
      api.getHealth().then(setHealth).catch(() => {});
    }, 15000);
    return () => clearInterval(interval);
  }, []);

  // Load historical agent events on mount
  useEffect(() => {
    api.getAuditTrail().then((trail) => {
      const events = trail
        .filter((e) => e.event_type.startsWith('agent_') || e.event_type === 'status_change' || e.event_type === 'created' || e.event_type === 'comment')
        .map((e) => ({
          timestamp: e.timestamp,
          event_type: e.event_type,
          message: e.message,
          actor: e.actor,
          incident_id: e.incident_id,
        }));
      setAllAgentEvents(events);
    }).catch(() => {});
  }, []);

  // Global Ctrl+K shortcut to open search
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setTab('search');
        setSelectedId(null);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  async function handleCreate(e) {
    e.preventDefault();
    const form = new FormData(e.target);
    try {
      await api.createManualIncident({
        title: form.get('title'),
        description: form.get('description'),
        severity: form.get('severity'),
        service: form.get('service'),
      });
      setShowCreate(false);
      loadIncidents();
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  }

  const activeCount = incidents.filter(
    (i) => !['Resolved', 'Postmortem'].includes(i.status)
  ).length;

  // Full-page tabs (no incident detail split)
  const isFullPage = ['metrics', 'agents', 'settings', 'playground', 'audit', 'search'].includes(tab);

  return (
    <div className="flex h-screen bg-dark-bg">
      {/* Sidebar */}
      <div className="w-56 bg-dark-card border-r border-dark-border flex flex-col shrink-0">
        {/* Logo */}
        <div className="p-4 border-b border-dark-border">
          <h1 className="text-base font-bold text-dark-text flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center text-white text-xs font-bold">
              O
            </div>
            OpsLens
          </h1>
          <p className="text-[10px] text-dark-muted mt-1 tracking-wider uppercase">Incident Response</p>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-3 overflow-y-auto">
          {NAV_SECTIONS.map((section) => (
            <div key={section.label} className="mb-3">
              <p className="px-4 mb-1.5 text-[10px] font-semibold text-dark-muted/60 uppercase tracking-widest">
                {section.label}
              </p>
              <div className="px-2 space-y-0.5">
                {section.items.map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    onClick={() => { setTab(id); setSelectedId(null); }}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all ${
                      tab === id
                        ? 'bg-blue-600/15 text-blue-400 nav-active-glow'
                        : 'text-dark-muted hover:text-dark-text hover:bg-dark-border/30'
                    }`}
                  >
                    <Icon size={15} strokeWidth={tab === id ? 2.5 : 2} />
                    <span className="truncate">{label}</span>
                    {id === 'search' && (
                      <kbd className="ml-auto text-[9px] font-mono px-1 py-0.5 rounded bg-dark-border/50 text-dark-muted">
                        {navigator.platform?.includes('Mac') ? '\u2318' : 'Ctrl'}K
                      </kbd>
                    )}
                    {id === 'active' && activeCount > 0 && (
                      <span className="ml-auto flex items-center justify-center min-w-[20px] h-5 bg-red-500/20 text-red-400 text-[10px] font-bold px-1.5 rounded-full">
                        {activeCount}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* Bottom actions */}
        <div className="p-3 border-t border-dark-border space-y-2.5">
          <button
            onClick={() => setShowCreate(true)}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors shadow-sm shadow-blue-500/20"
          >
            <Plus size={14} /> New Incident
          </button>
          <div className="flex items-center justify-between text-[11px] text-dark-muted px-1">
            <span className="flex items-center gap-1.5">
              {connected ? (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400" style={{ animation: 'pulse-dot 2s ease-in-out infinite' }} />
                  <span className="text-green-400/80">Live</span>
                </>
              ) : (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
                  <span className="text-red-400/80">Offline</span>
                </>
              )}
            </span>
            {health && (
              <span className={`flex items-center gap-1 ${health.mcp_connected ? 'text-green-400/80' : 'text-yellow-400/80'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${health.mcp_connected ? 'bg-green-400' : 'bg-yellow-400'}`} />
                MCP
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* List / Panel area */}
        <div className={`${selectedId && !isFullPage ? 'w-1/2' : 'w-full'} border-r border-dark-border overflow-y-auto`}>
          {/* Top bar */}
          <div className="sticky top-0 z-10 bg-dark-bg/90 backdrop-blur-sm border-b border-dark-border px-5 py-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-dark-text">
              {NAV_SECTIONS.flatMap((s) => s.items).find((t) => t.id === tab)?.label || tab}
            </h2>
            {(tab === 'active' || tab === 'all') && (
              <button onClick={loadIncidents} className="p-1.5 hover:bg-dark-border/50 rounded-lg transition-colors">
                <RefreshCw size={14} className="text-dark-muted" />
              </button>
            )}
          </div>

          {/* Content */}
          <div className="p-5 animate-fade-in">
            {tab === 'settings' ? (
              <SettingsPage />
            ) : tab === 'metrics' ? (
              <MetricsPanel />
            ) : tab === 'agents' ? (
              <AgentActivityFeed events={allAgentEvents} />
            ) : tab === 'playground' ? (
              <WebhookPlayground />
            ) : tab === 'audit' ? (
              <AuditTrail />
            ) : tab === 'search' ? (
              <SearchPanel onSelectIncident={(id) => { setSelectedId(id); setTab('active'); }} />
            ) : (
              <IncidentList
                incidents={incidents}
                onSelect={setSelectedId}
                selectedId={selectedId}
              />
            )}
          </div>
        </div>

        {/* Detail panel */}
        {selectedId && !isFullPage && (
          <div className="w-1/2 overflow-hidden animate-fade-in">
            <IncidentDetail
              incidentId={selectedId}
              onClose={() => setSelectedId(null)}
              onUpdate={loadIncidents}
            />
          </div>
        )}
      </div>

      {/* Toast notifications */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Create incident modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-dark-card border border-dark-border rounded-xl w-full max-w-md p-6 shadow-2xl shadow-black/40 animate-fade-in">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-base font-semibold text-dark-text">Create Incident</h2>
              <button onClick={() => setShowCreate(false)} className="p-1 hover:bg-dark-border rounded-lg">
                <X size={16} className="text-dark-muted" />
              </button>
            </div>
            <form onSubmit={handleCreate} className="space-y-3">
              <input
                name="title"
                required
                placeholder="Incident title"
                className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
              />
              <textarea
                name="description"
                placeholder="Description"
                rows={3}
                className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
              />
              <div className="grid grid-cols-2 gap-3">
                <select
                  name="severity"
                  defaultValue="P2"
                  className="bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text"
                >
                  <option value="P0">P0 - Critical</option>
                  <option value="P1">P1 - High</option>
                  <option value="P2">P2 - Medium</option>
                  <option value="P3">P3 - Low</option>
                </select>
                <input
                  name="service"
                  placeholder="Service name"
                  defaultValue="unknown"
                  className="bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="px-4 py-2 text-sm text-dark-muted hover:text-dark-text rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors shadow-sm shadow-blue-500/20"
                >
                  Create
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
