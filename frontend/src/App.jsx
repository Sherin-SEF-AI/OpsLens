import React, { useState, useEffect, useCallback } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  Link,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router-dom';
import {
  AlertTriangle,
  LayoutDashboard,
  BarChart3,
  Bot,
  Plus,
  RefreshCw,
  Settings,
  Zap,
  History,
  Search,
  X,
  Phone,
  Shield,
  Bell,
  BookOpen,
  FileText,
  Users,
  Menu,
  ChevronLeft,
} from 'lucide-react';

import IncidentList from './components/IncidentList';
import IncidentDetail from './components/IncidentDetail';
import MetricsPanel from './components/MetricsPanel';
import AgentActivityFeed from './components/AgentActivityFeed';
import SettingsPage from './components/SettingsPage';
import WebhookPlayground from './components/WebhookPlayground';
import AuditTrail from './components/AuditTrail';
import SearchPanel from './components/SearchPanel';
import LoginPage from './components/LoginPage';
import ProtectedRoute from './components/ProtectedRoute';
import UserMenu from './components/UserMenu';
import UsersManagement from './components/UsersManagement';
import EnterpriseDashboard from './components/EnterpriseDashboard';
import ErrorBoundary from './components/ErrorBoundary';
import AccessDenied from './components/AccessDenied';
import NotFound from './components/NotFound';

import { api } from './api/client';
import { useWebSocket } from './hooks/useWebSocket';
import { useAuthStore } from './stores/authStore';
import { useIncidentStore } from './stores/incidentStore';

// ─── Navigation config ────────────────────────
const NAV_SECTIONS = [
  {
    label: 'Incidents',
    items: [
      { path: '/incidents/active', label: 'Active', icon: AlertTriangle },
      { path: '/incidents/all', label: 'All Incidents', icon: LayoutDashboard },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { path: '/metrics', label: 'Metrics', icon: BarChart3 },
      { path: '/agents', label: 'Agent Feed', icon: Bot },
      { path: '/audit', label: 'Audit Trail', icon: History },
      { path: '/search', label: 'Search', icon: Search, shortcut: true },
    ],
  },
  {
    label: 'Enterprise',
    items: [
      { path: '/enterprise/oncall', label: 'On-Call', icon: Phone, minRole: 'responder' },
      { path: '/enterprise/sla', label: 'SLA', icon: Shield, minRole: 'responder' },
      { path: '/enterprise/rules', label: 'Alert Rules', icon: Bell, minRole: 'responder' },
      { path: '/enterprise/runbooks', label: 'Runbooks', icon: BookOpen, minRole: 'responder' },
      { path: '/enterprise/reports', label: 'Reports', icon: FileText, minRole: 'responder' },
    ],
  },
  {
    label: 'Tools',
    items: [
      { path: '/playground', label: 'Playground', icon: Zap },
      { path: '/settings', label: 'Settings', icon: Settings, minRole: 'admin' },
      { path: '/users', label: 'Users', icon: Users, minRole: 'admin' },
    ],
  },
];

// ─── Toast Container ──────────────────────────
function ToastContainer({ toasts, onDismiss }) {
  if (!toasts.length) return null;
  return (
    <div className="fixed top-4 right-4 z-[60] flex flex-col gap-2 max-w-sm" aria-live="polite">
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
          role="status"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-medium">{t.title}</p>
            {t.subtitle && <p className="text-[10px] opacity-70 mt-0.5 truncate">{t.subtitle}</p>}
          </div>
          <button onClick={() => onDismiss(t.id)} className="p-0.5 hover:opacity-70 shrink-0" aria-label="Dismiss notification">
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

// ─── Incident List Wrapper (for route) ────────
function IncidentListPage({ mode }) {
  const [incidents, setIncidents] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const data = mode === 'active' ? await api.getActiveIncidents() : await api.getIncidents();
      setIncidents(data);
    } catch (e) {
      console.error('Failed to load incidents:', e);
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  if (loading && incidents.length === 0) {
    return (
      <div className="flex items-center justify-center h-64" aria-label="Loading incidents">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    );
  }

  return (
    <IncidentList
      incidents={incidents}
      onSelect={(id) => navigate(`/incidents/${id}`)}
      selectedId={null}
    />
  );
}

// ─── Incident Detail Page (full page) ─────────
function IncidentDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();

  return (
    <div className="h-full">
      <IncidentDetail
        incidentId={id}
        onClose={() => navigate(-1)}
        onUpdate={() => {}}
      />
    </div>
  );
}

// ─── Agent Feed Wrapper ───────────────────────
function AgentFeedPage({ events }) {
  return <AgentActivityFeed events={events} />;
}

// ─── Search Page Wrapper ──────────────────────
function SearchPageWrapper() {
  const navigate = useNavigate();
  return (
    <SearchPanel
      onSelectIncident={(id) => navigate(`/incidents/${id}`)}
    />
  );
}

// ─── Enterprise Route Wrappers ────────────────
function EnterpriseOnCall() { return <EnterpriseDashboard defaultTab="oncall" />; }
function EnterpriseSLA() { return <EnterpriseDashboard defaultTab="sla" />; }
function EnterpriseRules() { return <EnterpriseDashboard defaultTab="rules" />; }
function EnterpriseRunbooks() { return <EnterpriseDashboard defaultTab="runbooks" />; }
function EnterpriseReports() { return <EnterpriseDashboard defaultTab="reports" />; }

// ─── Dashboard Layout ─────────────────────────
function DashboardLayout({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, isAuthenticated } = useAuthStore();
  const hasRole = useAuthStore((s) => s.hasRole);

  const [health, setHealth] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [allAgentEvents, setAllAgentEvents] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [activeCount, setActiveCount] = useState(0);

  function addToast(title, subtitle, type) {
    const id = Date.now();
    setToasts((prev) => [...prev.slice(-4), { id, title, subtitle, type }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 5000);
  }

  function dismissToast(id) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  // WebSocket handler
  const handleWsMessage = useCallback((msg) => {
    if (msg.type === 'incident_created') {
      const d = msg.data;
      addToast('New Incident Created', d?.title || d?.incident_id || '', 'created');
    } else if (msg.type === 'incident_updated') {
      const d = msg.data;
      addToast(`Incident ${d?.status || 'Updated'}`, d?.title || d?.incident_id || '', 'updated');
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
  }, []);

  const { connected } = useWebSocket(isAuthenticated ? handleWsMessage : null);

  // Load active count
  useEffect(() => {
    if (!isAuthenticated) return;
    function loadActive() {
      api.getActiveIncidents().then((data) => setActiveCount(data.length)).catch(() => {});
    }
    loadActive();
    const interval = setInterval(loadActive, 10000);
    return () => clearInterval(interval);
  }, [isAuthenticated]);

  // Health check
  useEffect(() => {
    if (!isAuthenticated) return;
    function checkHealth() {
      api.getHealth().then(setHealth).catch(() => {});
    }
    checkHealth();
    const interval = setInterval(checkHealth, 15000);
    return () => clearInterval(interval);
  }, [isAuthenticated]);

  // Load historical agent events
  useEffect(() => {
    if (!isAuthenticated) return;
    api.getAuditTrail().then((trail) => {
      const events = trail
        .filter((e) =>
          e.event_type?.startsWith('agent_') ||
          e.event_type === 'status_change' ||
          e.event_type === 'created' ||
          e.event_type === 'comment'
        )
        .map((e) => ({
          timestamp: e.timestamp,
          event_type: e.event_type,
          message: e.message,
          actor: e.actor,
          incident_id: e.incident_id,
        }));
      setAllAgentEvents(events);
    }).catch(() => {});
  }, [isAuthenticated]);

  // Global Ctrl+K
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        navigate('/search');
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [navigate]);

  // Close mobile sidebar on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  // Listen for auth:logout events from API client
  useEffect(() => {
    function handleAuthLogout() {
      useAuthStore.getState().logout();
      navigate('/login', { replace: true });
    }
    window.addEventListener('auth:logout', handleAuthLogout);
    return () => window.removeEventListener('auth:logout', handleAuthLogout);
  }, [navigate]);

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
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  }

  // Determine current page label
  const currentLabel = NAV_SECTIONS
    .flatMap((s) => s.items)
    .find((item) => location.pathname.startsWith(item.path))?.label || '';

  // Provide agent events context to child routes
  const childrenWithProps = React.Children.map(children, (child) => {
    if (React.isValidElement(child) && child.type === AgentFeedPage) {
      return React.cloneElement(child, { events: allAgentEvents });
    }
    return child;
  });

  return (
    <div className="flex h-screen bg-dark-bg">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar */}
      <aside
        className={`${
          sidebarOpen ? 'w-56' : 'w-16'
        } bg-dark-card border-r border-dark-border flex flex-col shrink-0 transition-all duration-200 ${
          mobileOpen ? 'fixed inset-y-0 left-0 z-40' : 'hidden lg:flex'
        }`}
        aria-label="Main navigation"
      >
        {/* Logo */}
        <div className="p-4 border-b border-dark-border flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2" aria-label="OpsLens home">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center text-white text-xs font-bold shrink-0">
              O
            </div>
            {sidebarOpen && (
              <div>
                <h1 className="text-base font-bold text-dark-text">OpsLens</h1>
                <p className="text-[10px] text-dark-muted tracking-wider uppercase">Incident Response</p>
              </div>
            )}
          </Link>
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-1 hover:bg-dark-border/50 rounded hidden lg:block"
            aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            <ChevronLeft size={14} className={`text-dark-muted transition-transform ${!sidebarOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-3 overflow-y-auto">
          {NAV_SECTIONS.map((section) => {
            const visibleItems = section.items.filter(
              (item) => !item.minRole || hasRole(item.minRole)
            );
            if (visibleItems.length === 0) return null;
            return (
              <div key={section.label} className="mb-3">
                {sidebarOpen && (
                  <p className="px-4 mb-1.5 text-[10px] font-semibold text-dark-muted/60 uppercase tracking-widest">
                    {section.label}
                  </p>
                )}
                <div className="px-2 space-y-0.5">
                  {visibleItems.map(({ path, label, icon: Icon, shortcut }) => {
                    const isActive = location.pathname === path || location.pathname.startsWith(path + '/');
                    return (
                      <Link
                        key={path}
                        to={path}
                        className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all ${
                          isActive
                            ? 'bg-blue-600/15 text-blue-400 nav-active-glow'
                            : 'text-dark-muted hover:text-dark-text hover:bg-dark-border/30'
                        }`}
                        title={!sidebarOpen ? label : undefined}
                        aria-current={isActive ? 'page' : undefined}
                      >
                        <Icon size={15} strokeWidth={isActive ? 2.5 : 2} />
                        {sidebarOpen && (
                          <>
                            <span className="truncate">{label}</span>
                            {shortcut && (
                              <kbd className="ml-auto text-[9px] font-mono px-1 py-0.5 rounded bg-dark-border/50 text-dark-muted">
                                {navigator.platform?.includes('Mac') ? '\u2318' : 'Ctrl'}K
                              </kbd>
                            )}
                            {path === '/incidents/active' && activeCount > 0 && (
                              <span className="ml-auto flex items-center justify-center min-w-[20px] h-5 bg-red-500/20 text-red-400 text-[10px] font-bold px-1.5 rounded-full">
                                {activeCount}
                              </span>
                            )}
                          </>
                        )}
                      </Link>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>

        {/* Bottom actions */}
        <div className="p-3 border-t border-dark-border space-y-2.5">
          <button
            onClick={() => setShowCreate(true)}
            className={`w-full flex items-center justify-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors shadow-sm shadow-blue-500/20 ${
              !sidebarOpen ? 'px-0' : ''
            }`}
            aria-label="Create new incident"
          >
            <Plus size={14} />
            {sidebarOpen && 'New Incident'}
          </button>
          <div className={`flex items-center ${sidebarOpen ? 'justify-between' : 'justify-center'} text-[11px] text-dark-muted px-1`}>
            <span className="flex items-center gap-1.5">
              {connected ? (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400" style={{ animation: 'pulse-dot 2s ease-in-out infinite' }} />
                  {sidebarOpen && <span className="text-green-400/80">Live</span>}
                </>
              ) : (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
                  {sidebarOpen && <span className="text-red-400/80">Offline</span>}
                </>
              )}
            </span>
            {sidebarOpen && health && (
              <span className={`flex items-center gap-1 ${health.mcp_connected ? 'text-green-400/80' : 'text-yellow-400/80'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${health.mcp_connected ? 'bg-green-400' : 'bg-yellow-400'}`} />
                MCP
              </span>
            )}
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="sticky top-0 z-20 bg-dark-bg/90 backdrop-blur-sm border-b border-dark-border px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileOpen(true)}
              className="p-1.5 hover:bg-dark-border/50 rounded-lg lg:hidden"
              aria-label="Open navigation menu"
            >
              <Menu size={18} className="text-dark-muted" />
            </button>
            <h2 className="text-sm font-semibold text-dark-text">{currentLabel}</h2>
          </div>
          <div className="flex items-center gap-2">
            <UserMenu />
          </div>
        </header>

        {/* Route content */}
        <main className="flex-1 overflow-y-auto p-5">
          <ErrorBoundary>
            <div className="animate-fade-in">
              {/* Pass agent events through context for the feed page */}
              <AgentEventsContext.Provider value={allAgentEvents}>
                {children}
              </AgentEventsContext.Provider>
            </div>
          </ErrorBoundary>
        </main>
      </div>

      {/* Toast notifications */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Create incident modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50" role="dialog" aria-label="Create incident" aria-modal="true">
          <div className="bg-dark-card border border-dark-border rounded-xl w-full max-w-md p-6 shadow-2xl shadow-black/40 animate-fade-in">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-base font-semibold text-dark-text">Create Incident</h2>
              <button onClick={() => setShowCreate(false)} className="p-1 hover:bg-dark-border rounded-lg" aria-label="Close">
                <X size={16} className="text-dark-muted" />
              </button>
            </div>
            <form onSubmit={handleCreate} className="space-y-3">
              <input
                name="title"
                required
                placeholder="Incident title"
                aria-label="Incident title"
                className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
              />
              <textarea
                name="description"
                placeholder="Description"
                aria-label="Incident description"
                rows={3}
                className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
              />
              <div className="grid grid-cols-2 gap-3">
                <select
                  name="severity"
                  defaultValue="P2"
                  aria-label="Severity"
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
                  aria-label="Service name"
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

// ─── Agent events context ─────────────────────
const AgentEventsContext = React.createContext([]);

function AgentFeedRoute() {
  const events = React.useContext(AgentEventsContext);
  return <AgentActivityFeed events={events} />;
}

// ─── App Root ─────────────────────────────────
export default function App() {
  const { isLoading, isAuthenticated, initAuth } = useAuthStore();

  useEffect(() => {
    initAuth();
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-dark-bg" aria-label="Loading application">
        <div className="flex flex-col items-center gap-3">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center text-white text-2xl font-bold animate-pulse">
            O
          </div>
          <p className="text-sm text-dark-muted">Loading OpsLens...</p>
        </div>
      </div>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        {/* Public route */}
        <Route path="/login" element={
          isAuthenticated ? <Navigate to="/" replace /> : <LoginPage />
        } />

        {/* Protected routes */}
        <Route path="/" element={
          <ProtectedRoute>
            <DashboardLayout>
              <Navigate to="/incidents/active" replace />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/incidents/active" element={
          <ProtectedRoute>
            <DashboardLayout>
              <IncidentListPage mode="active" />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/incidents/all" element={
          <ProtectedRoute>
            <DashboardLayout>
              <IncidentListPage mode="all" />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/incidents/:id" element={
          <ProtectedRoute>
            <DashboardLayout>
              <IncidentDetailPage />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/metrics" element={
          <ProtectedRoute>
            <DashboardLayout>
              <MetricsPanel />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/agents" element={
          <ProtectedRoute>
            <DashboardLayout>
              <AgentFeedRoute />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/audit" element={
          <ProtectedRoute>
            <DashboardLayout>
              <AuditTrail />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/search" element={
          <ProtectedRoute>
            <DashboardLayout>
              <SearchPageWrapper />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/playground" element={
          <ProtectedRoute>
            <DashboardLayout>
              <WebhookPlayground />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/settings" element={
          <ProtectedRoute minRole="admin">
            <DashboardLayout>
              <SettingsPage />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/users" element={
          <ProtectedRoute minRole="admin">
            <DashboardLayout>
              <UsersManagement />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        {/* Enterprise routes */}
        <Route path="/enterprise" element={
          <ProtectedRoute>
            <DashboardLayout>
              <EnterpriseDashboard />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/enterprise/oncall" element={
          <ProtectedRoute>
            <DashboardLayout>
              <EnterpriseOnCall />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/enterprise/sla" element={
          <ProtectedRoute>
            <DashboardLayout>
              <EnterpriseSLA />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/enterprise/rules" element={
          <ProtectedRoute>
            <DashboardLayout>
              <EnterpriseRules />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/enterprise/runbooks" element={
          <ProtectedRoute>
            <DashboardLayout>
              <EnterpriseRunbooks />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        <Route path="/enterprise/reports" element={
          <ProtectedRoute>
            <DashboardLayout>
              <EnterpriseReports />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        {/* Utility routes */}
        <Route path="/access-denied" element={
          <ProtectedRoute>
            <DashboardLayout>
              <AccessDenied />
            </DashboardLayout>
          </ProtectedRoute>
        } />

        {/* 404 */}
        <Route path="*" element={
          isAuthenticated ? (
            <DashboardLayout>
              <NotFound />
            </DashboardLayout>
          ) : (
            <Navigate to="/login" replace />
          )
        } />
      </Routes>
    </BrowserRouter>
  );
}
