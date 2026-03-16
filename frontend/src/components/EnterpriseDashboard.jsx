import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  Phone, Shield, Bell, BookOpen, FileText, Loader2, Plus, X, AlertCircle,
  RotateCcw, ArrowUpCircle, CheckCircle2, XCircle, Clock, Download,
  ToggleLeft, ToggleRight, ChevronRight, Play, Pause, AlertTriangle,
  Calendar, BarChart3, Filter,
} from 'lucide-react';
import { useEnterpriseStore } from '../stores/enterpriseStore';

// ─── Shared components ────────────────────────
function TabButton({ active, onClick, icon: Icon, label, count }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg transition-all ${
        active
          ? 'bg-blue-600/15 text-blue-400'
          : 'text-dark-muted hover:text-dark-text hover:bg-dark-border/30'
      }`}
      aria-selected={active}
      role="tab"
    >
      <Icon size={15} />
      <span>{label}</span>
      {count !== undefined && count > 0 && (
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-dark-border/50 text-dark-muted font-medium">
          {count}
        </span>
      )}
    </button>
  );
}

function EmptyState({ icon: Icon, title, description, color = 'blue' }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-dark-muted">
      <div className={`w-14 h-14 rounded-2xl bg-${color}-500/5 border border-${color}-500/10 flex items-center justify-center mb-3`}>
        <Icon size={24} className={`text-${color}-400/40`} />
      </div>
      <p className="text-sm font-medium text-dark-text/60">{title}</p>
      <p className="text-xs mt-1">{description}</p>
    </div>
  );
}

function ErrorBanner({ message, onDismiss }) {
  if (!message) return null;
  return (
    <div className="flex items-start gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs" role="alert">
      <AlertCircle size={14} className="mt-0.5 shrink-0" />
      <span className="flex-1">{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} aria-label="Dismiss error"><X size={12} /></button>
      )}
    </div>
  );
}

// ─── On-Call Tab ──────────────────────────────
function OnCallTab() {
  const {
    oncallSchedules, oncallLoading, fetchOncallSchedules,
    rotateOncall, escalateOncall, error, clearError,
  } = useEnterpriseStore();
  const [actionLoading, setActionLoading] = useState(null);

  useEffect(() => { fetchOncallSchedules(); }, []);

  async function handleRotate(id) {
    setActionLoading(id + '_rotate');
    try { await rotateOncall(id); } catch { /* error set in store */ }
    setActionLoading(null);
  }

  async function handleEscalate(id) {
    const reason = prompt('Escalation reason:');
    if (!reason) return;
    setActionLoading(id + '_escalate');
    try { await escalateOncall(id, reason); } catch { /* error set in store */ }
    setActionLoading(null);
  }

  if (oncallLoading) {
    return <div className="flex justify-center py-12"><Loader2 size={24} className="animate-spin text-blue-400" /></div>;
  }

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} onDismiss={clearError} />

      {oncallSchedules.length === 0 ? (
        <EmptyState icon={Phone} title="No on-call schedules" description="Configure on-call rotations for your teams" />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {oncallSchedules.map((schedule) => (
            <div key={schedule.id} className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-dark-text">{schedule.team || schedule.name}</h3>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 text-green-400 border border-green-500/20">
                  Active
                </span>
              </div>

              {/* Current on-call */}
              <div className="bg-dark-bg rounded-lg p-3">
                <p className="text-[10px] text-dark-muted uppercase tracking-wider mb-1">Currently On-Call</p>
                <div className="flex items-center gap-2">
                  <div className="w-6 h-6 rounded-full bg-blue-500/20 flex items-center justify-center">
                    <Phone size={10} className="text-blue-400" />
                  </div>
                  <span className="text-sm text-dark-text font-medium">
                    {schedule.current_oncall || schedule.members?.[0] || 'Unassigned'}
                  </span>
                </div>
                {schedule.rotation_end && (
                  <p className="text-[10px] text-dark-muted mt-1 flex items-center gap-1">
                    <Clock size={9} />
                    Until {new Date(schedule.rotation_end).toLocaleString()}
                  </p>
                )}
              </div>

              {/* Rotation schedule */}
              {schedule.members && schedule.members.length > 0 && (
                <div>
                  <p className="text-[10px] text-dark-muted uppercase tracking-wider mb-1.5">Rotation Order</p>
                  <div className="flex flex-wrap gap-1.5">
                    {schedule.members.map((member, i) => (
                      <span
                        key={i}
                        className={`text-[11px] px-2 py-0.5 rounded-full border ${
                          member === (schedule.current_oncall || schedule.members[0])
                            ? 'bg-blue-500/10 text-blue-400 border-blue-500/20'
                            : 'bg-dark-bg text-dark-muted border-dark-border'
                        }`}
                      >
                        {member}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => handleRotate(schedule.id)}
                  disabled={actionLoading === schedule.id + '_rotate'}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-dark-border hover:bg-dark-border/50 transition-colors disabled:opacity-50"
                >
                  {actionLoading === schedule.id + '_rotate' ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <RotateCcw size={12} />
                  )}
                  Rotate
                </button>
                <button
                  onClick={() => handleEscalate(schedule.id)}
                  disabled={actionLoading === schedule.id + '_escalate'}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-red-500/20 text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50"
                >
                  {actionLoading === schedule.id + '_escalate' ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <ArrowUpCircle size={12} />
                  )}
                  Escalate
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── SLA Tab ──────────────────────────────────
function SlaTab() {
  const {
    slaPolicies, slaStatus, slaCompliance, slaLoading,
    fetchSlaPolicies, fetchSlaStatus, fetchSlaCompliance,
    error, clearError,
  } = useEnterpriseStore();

  useEffect(() => {
    fetchSlaPolicies();
    fetchSlaStatus();
    fetchSlaCompliance();
  }, []);

  if (slaLoading) {
    return <div className="flex justify-center py-12"><Loader2 size={24} className="animate-spin text-blue-400" /></div>;
  }

  function slaColor(status) {
    if (status === 'breached' || status === 'violated') return 'text-red-400 bg-red-500/10 border-red-500/20';
    if (status === 'warning' || status === 'at_risk') return 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20';
    return 'text-green-400 bg-green-500/10 border-green-500/20';
  }

  return (
    <div className="space-y-5">
      <ErrorBanner message={error} onDismiss={clearError} />

      {/* SLA Status for active incidents */}
      {Array.isArray(slaStatus) && slaStatus.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-dark-muted mb-3 flex items-center gap-2">
            <AlertTriangle size={12} />
            Active Incident SLA Status
          </h3>
          <div className="space-y-2">
            {slaStatus.map((item, i) => {
              const colorClass = slaColor(item.status);
              return (
                <div key={i} className={`flex items-center justify-between p-3 rounded-lg border ${colorClass}`}>
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-xs font-mono truncate">{item.incident_id}</span>
                    <span className="text-xs truncate">{item.title || item.policy_name}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {item.time_remaining && (
                      <span className="text-xs flex items-center gap-1">
                        <Clock size={10} />
                        {item.time_remaining}
                      </span>
                    )}
                    <span className="text-[10px] px-2 py-0.5 rounded-full border font-medium uppercase">
                      {item.status}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Compliance chart placeholder */}
      {slaCompliance && (
        <div className="bg-dark-card border border-dark-border rounded-xl p-4">
          <h3 className="text-sm font-medium text-dark-muted mb-3 flex items-center gap-2">
            <BarChart3 size={14} />
            SLA Compliance
          </h3>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <p className="text-2xl font-bold text-green-400">{slaCompliance.met || 0}</p>
              <p className="text-xs text-dark-muted mt-1">Met</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-yellow-400">{slaCompliance.at_risk || 0}</p>
              <p className="text-xs text-dark-muted mt-1">At Risk</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-red-400">{slaCompliance.breached || 0}</p>
              <p className="text-xs text-dark-muted mt-1">Breached</p>
            </div>
          </div>
          {slaCompliance.compliance_rate !== undefined && (
            <div className="mt-4">
              <div className="flex items-center justify-between text-xs text-dark-muted mb-1">
                <span>Compliance Rate</span>
                <span className="font-medium text-dark-text">{Math.round(slaCompliance.compliance_rate * 100)}%</span>
              </div>
              <div className="w-full h-2 bg-dark-border rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full transition-all"
                  style={{ width: `${Math.round(slaCompliance.compliance_rate * 100)}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Policies */}
      <div>
        <h3 className="text-xs font-medium text-dark-muted mb-3">SLA Policies ({slaPolicies.length})</h3>
        {slaPolicies.length === 0 ? (
          <EmptyState icon={Shield} title="No SLA policies" description="Create policies to track response and resolution times" />
        ) : (
          <div className="space-y-2">
            {slaPolicies.map((policy) => (
              <div key={policy.id} className="bg-dark-card border border-dark-border rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-medium text-dark-text">{policy.name}</h4>
                  <span className="text-[10px] px-2 py-0.5 rounded-full border border-dark-border text-dark-muted">
                    {policy.severity || 'All'}
                  </span>
                </div>
                <div className="flex gap-4 mt-2 text-xs text-dark-muted">
                  {policy.response_time && (
                    <span>Response: {policy.response_time}m</span>
                  )}
                  {policy.resolution_time && (
                    <span>Resolution: {policy.resolution_time}m</span>
                  )}
                  {policy.escalation_time && (
                    <span>Escalation: {policy.escalation_time}m</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Alert Rules Tab ──────────────────────────
function AlertRulesTab() {
  const {
    alertRules, alertRulesLoading, fetchAlertRules,
    toggleAlertRule, createAlertRule, deleteAlertRule,
    error, clearError,
  } = useEnterpriseStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newRule, setNewRule] = useState({ name: '', condition_type: 'threshold', condition_value: '', severity: 'P2', service: '', enabled: true });
  const [actionLoading, setActionLoading] = useState(null);

  useEffect(() => { fetchAlertRules(); }, []);

  async function handleToggle(id) {
    setActionLoading(id);
    try { await toggleAlertRule(id); } catch { /* store has error */ }
    setActionLoading(null);
  }

  async function handleCreate(e) {
    e.preventDefault();
    try {
      await createAlertRule(newRule);
      setShowCreate(false);
      setNewRule({ name: '', condition_type: 'threshold', condition_value: '', severity: 'P2', service: '', enabled: true });
    } catch { /* store has error */ }
  }

  async function handleDelete(id) {
    if (!confirm('Delete this rule?')) return;
    try { await deleteAlertRule(id); } catch { /* store has error */ }
  }

  if (alertRulesLoading) {
    return <div className="flex justify-center py-12"><Loader2 size={24} className="animate-spin text-blue-400" /></div>;
  }

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} onDismiss={clearError} />

      <div className="flex justify-end">
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
        >
          <Plus size={14} />
          New Rule
        </button>
      </div>

      {alertRules.length === 0 ? (
        <EmptyState icon={Bell} title="No alert rules" description="Create rules to automatically manage incoming alerts" />
      ) : (
        <div className="space-y-2">
          {alertRules.map((rule) => (
            <div key={rule.id} className="bg-dark-card border border-dark-border rounded-lg p-3 flex items-center gap-3">
              <button
                onClick={() => handleToggle(rule.id)}
                disabled={actionLoading === rule.id}
                className="shrink-0"
                aria-label={rule.enabled ? 'Disable rule' : 'Enable rule'}
              >
                {actionLoading === rule.id ? (
                  <Loader2 size={20} className="animate-spin text-dark-muted" />
                ) : rule.enabled ? (
                  <ToggleRight size={20} className="text-green-400" />
                ) : (
                  <ToggleLeft size={20} className="text-dark-muted" />
                )}
              </button>
              <div className="flex-1 min-w-0">
                <h4 className={`text-sm font-medium ${rule.enabled ? 'text-dark-text' : 'text-dark-muted'}`}>
                  {rule.name}
                </h4>
                <div className="flex items-center gap-2 mt-1 text-xs text-dark-muted">
                  <span className="capitalize">{rule.condition_type}</span>
                  {rule.condition_value && <span>: {rule.condition_value}</span>}
                  {rule.service && <span>| Service: {rule.service}</span>}
                  {rule.severity && (
                    <span className="px-1.5 py-0.5 rounded bg-dark-border/50 text-[10px]">{rule.severity}</span>
                  )}
                </div>
              </div>
              <button
                onClick={() => handleDelete(rule.id)}
                className="p-1.5 text-dark-muted hover:text-red-400 transition-colors shrink-0"
                aria-label={`Delete rule ${rule.name}`}
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-dark-card border border-dark-border rounded-xl w-full max-w-md p-6 shadow-2xl shadow-black/40 animate-fade-in" role="dialog" aria-label="Create alert rule" aria-modal="true">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-base font-semibold text-dark-text">New Alert Rule</h2>
              <button onClick={() => setShowCreate(false)} className="p-1 hover:bg-dark-border rounded-lg" aria-label="Close"><X size={16} className="text-dark-muted" /></button>
            </div>
            <form onSubmit={handleCreate} className="space-y-3">
              <div>
                <label htmlFor="rule-name" className="block text-xs font-medium text-dark-muted mb-1">Rule Name</label>
                <input id="rule-name" type="text" required value={newRule.name} onChange={(e) => setNewRule((r) => ({ ...r, name: e.target.value }))} placeholder="e.g., High CPU Alert" className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="rule-type" className="block text-xs font-medium text-dark-muted mb-1">Condition Type</label>
                  <select id="rule-type" value={newRule.condition_type} onChange={(e) => setNewRule((r) => ({ ...r, condition_type: e.target.value }))} className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text">
                    <option value="threshold">Threshold</option>
                    <option value="pattern">Pattern Match</option>
                    <option value="frequency">Frequency</option>
                    <option value="absence">Absence</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="rule-value" className="block text-xs font-medium text-dark-muted mb-1">Value</label>
                  <input id="rule-value" type="text" value={newRule.condition_value} onChange={(e) => setNewRule((r) => ({ ...r, condition_value: e.target.value }))} placeholder="e.g., > 90%" className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="rule-severity" className="block text-xs font-medium text-dark-muted mb-1">Severity</label>
                  <select id="rule-severity" value={newRule.severity} onChange={(e) => setNewRule((r) => ({ ...r, severity: e.target.value }))} className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text">
                    <option value="P0">P0 - Critical</option>
                    <option value="P1">P1 - High</option>
                    <option value="P2">P2 - Medium</option>
                    <option value="P3">P3 - Low</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="rule-service" className="block text-xs font-medium text-dark-muted mb-1">Service</label>
                  <input id="rule-service" type="text" value={newRule.service} onChange={(e) => setNewRule((r) => ({ ...r, service: e.target.value }))} placeholder="Any" className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50" />
                </div>
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-dark-muted hover:text-dark-text rounded-lg">Cancel</button>
                <button type="submit" className="px-5 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors">Create Rule</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Runbooks Tab ─────────────────────────────
function RunbooksTab() {
  const {
    runbookExecutions, runbooksLoading, fetchRunbookExecutions,
    approveRunbookStep, rejectRunbookStep, cancelRunbook,
    error, clearError,
  } = useEnterpriseStore();
  const [actionLoading, setActionLoading] = useState(null);

  useEffect(() => {
    fetchRunbookExecutions();
    const interval = setInterval(fetchRunbookExecutions, 10000);
    return () => clearInterval(interval);
  }, []);

  async function handleApprove(execId, stepId) {
    setActionLoading(`${execId}_${stepId}_approve`);
    try { await approveRunbookStep(execId, stepId); } catch { /* store */ }
    setActionLoading(null);
  }

  async function handleReject(execId, stepId) {
    const reason = prompt('Rejection reason:');
    if (!reason) return;
    setActionLoading(`${execId}_${stepId}_reject`);
    try { await rejectRunbookStep(execId, stepId, reason); } catch { /* store */ }
    setActionLoading(null);
  }

  async function handleCancel(execId) {
    if (!confirm('Cancel this runbook execution?')) return;
    setActionLoading(`${execId}_cancel`);
    try { await cancelRunbook(execId); } catch { /* store */ }
    setActionLoading(null);
  }

  function stepStatusIcon(status) {
    if (status === 'completed' || status === 'approved') return <CheckCircle2 size={14} className="text-green-400" />;
    if (status === 'failed' || status === 'rejected') return <XCircle size={14} className="text-red-400" />;
    if (status === 'running') return <Loader2 size={14} className="animate-spin text-blue-400" />;
    if (status === 'pending_approval') return <AlertTriangle size={14} className="text-yellow-400" />;
    return <Clock size={14} className="text-dark-muted" />;
  }

  if (runbooksLoading && runbookExecutions.length === 0) {
    return <div className="flex justify-center py-12"><Loader2 size={24} className="animate-spin text-blue-400" /></div>;
  }

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} onDismiss={clearError} />

      {runbookExecutions.length === 0 ? (
        <EmptyState icon={BookOpen} title="No active runbook executions" description="Runbooks will appear here when agents execute them" />
      ) : (
        <div className="space-y-4">
          {runbookExecutions.map((exec) => (
            <div key={exec.id} className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-3">
              {/* Header */}
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-medium text-dark-text">{exec.runbook_name || exec.runbook_id}</h3>
                  <p className="text-xs text-dark-muted mt-0.5">
                    {exec.incident_id && <span className="font-mono">{exec.incident_id}</span>}
                    {exec.started_at && <span className="ml-2">Started {new Date(exec.started_at).toLocaleString()}</span>}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium capitalize ${
                    exec.status === 'completed' ? 'text-green-400 bg-green-500/10 border-green-500/20' :
                    exec.status === 'failed' ? 'text-red-400 bg-red-500/10 border-red-500/20' :
                    exec.status === 'running' ? 'text-blue-400 bg-blue-500/10 border-blue-500/20' :
                    'text-dark-muted bg-dark-border/50 border-dark-border'
                  }`}>
                    {exec.status}
                  </span>
                  {(exec.status === 'running' || exec.status === 'pending_approval') && (
                    <button
                      onClick={() => handleCancel(exec.id)}
                      disabled={actionLoading === `${exec.id}_cancel`}
                      className="p-1 text-dark-muted hover:text-red-400 transition-colors"
                      aria-label="Cancel execution"
                    >
                      {actionLoading === `${exec.id}_cancel` ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />}
                    </button>
                  )}
                </div>
              </div>

              {/* Steps */}
              {exec.steps && exec.steps.length > 0 && (
                <div className="space-y-1.5">
                  {exec.steps.map((step, i) => (
                    <div key={step.id || i} className="flex items-start gap-2.5 p-2 rounded-lg bg-dark-bg/50">
                      {stepStatusIcon(step.status)}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-dark-text">Step {i + 1}: {step.name || step.action}</span>
                          <span className="text-[10px] text-dark-muted capitalize">{step.status}</span>
                        </div>
                        {step.output && (
                          <p className="text-[11px] text-dark-muted mt-0.5 truncate">{step.output}</p>
                        )}
                      </div>
                      {step.status === 'pending_approval' && (
                        <div className="flex gap-1.5 shrink-0">
                          <button
                            onClick={() => handleApprove(exec.id, step.id)}
                            disabled={actionLoading === `${exec.id}_${step.id}_approve`}
                            className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded bg-green-500/10 text-green-400 border border-green-500/20 hover:bg-green-500/20 transition-colors disabled:opacity-50"
                          >
                            {actionLoading === `${exec.id}_${step.id}_approve` ? <Loader2 size={10} className="animate-spin" /> : <CheckCircle2 size={10} />}
                            Approve
                          </button>
                          <button
                            onClick={() => handleReject(exec.id, step.id)}
                            disabled={actionLoading === `${exec.id}_${step.id}_reject`}
                            className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-colors disabled:opacity-50"
                          >
                            {actionLoading === `${exec.id}_${step.id}_reject` ? <Loader2 size={10} className="animate-spin" /> : <XCircle size={10} />}
                            Reject
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Progress bar */}
              {exec.steps && exec.steps.length > 0 && (
                <div className="w-full h-1.5 bg-dark-border rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all"
                    style={{
                      width: `${Math.round(
                        (exec.steps.filter((s) => s.status === 'completed' || s.status === 'approved').length / exec.steps.length) * 100
                      )}%`,
                    }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Reports Tab ──────────────────────────────
function ReportsTab() {
  const {
    reports, reportsLoading, fetchReports,
    generateReport, downloadReportCsv,
    error, clearError,
  } = useEnterpriseStore();
  const [generating, setGenerating] = useState(false);
  const [reportType, setReportType] = useState('incident_summary');
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().split('T')[0];
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [selectedReport, setSelectedReport] = useState(null);

  useEffect(() => { fetchReports(); }, []);

  async function handleGenerate(e) {
    e.preventDefault();
    setGenerating(true);
    try {
      const report = await generateReport(reportType, startDate, endDate);
      setSelectedReport(report);
    } catch { /* store */ }
    setGenerating(false);
  }

  async function handleDownload(id) {
    try { await downloadReportCsv(id); } catch { /* store */ }
  }

  return (
    <div className="space-y-5">
      <ErrorBanner message={error} onDismiss={clearError} />

      {/* Generate form */}
      <div className="bg-dark-card border border-dark-border rounded-xl p-4">
        <h3 className="text-sm font-medium text-dark-text mb-3 flex items-center gap-2">
          <FileText size={14} />
          Generate Report
        </h3>
        <form onSubmit={handleGenerate} className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label htmlFor="report-type" className="block text-xs font-medium text-dark-muted mb-1">Report Type</label>
              <select id="report-type" value={reportType} onChange={(e) => setReportType(e.target.value)} className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-sm text-dark-text">
                <option value="incident_summary">Incident Summary</option>
                <option value="mttr_analysis">MTTR Analysis</option>
                <option value="sla_compliance">SLA Compliance</option>
                <option value="team_performance">Team Performance</option>
                <option value="service_health">Service Health</option>
              </select>
            </div>
            <div>
              <label htmlFor="report-start" className="block text-xs font-medium text-dark-muted mb-1">Start Date</label>
              <input id="report-start" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-sm text-dark-text" />
            </div>
            <div>
              <label htmlFor="report-end" className="block text-xs font-medium text-dark-muted mb-1">End Date</label>
              <input id="report-end" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-sm text-dark-text" />
            </div>
          </div>
          <button
            type="submit"
            disabled={generating}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
          >
            {generating ? <Loader2 size={14} className="animate-spin" /> : <BarChart3 size={14} />}
            {generating ? 'Generating...' : 'Generate Report'}
          </button>
        </form>
      </div>

      {/* Selected report detail */}
      {selectedReport && (
        <div className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-dark-text">{selectedReport.title || selectedReport.type}</h3>
            <button onClick={() => setSelectedReport(null)} className="p-1 hover:bg-dark-border rounded" aria-label="Close report">
              <X size={14} className="text-dark-muted" />
            </button>
          </div>
          {selectedReport.summary && (
            <p className="text-xs text-dark-muted">{selectedReport.summary}</p>
          )}
          {selectedReport.data && (
            <pre className="text-xs text-dark-text bg-dark-bg p-3 rounded-lg border border-dark-border overflow-auto max-h-64 font-mono">
              {typeof selectedReport.data === 'string' ? selectedReport.data : JSON.stringify(selectedReport.data, null, 2)}
            </pre>
          )}
          <button
            onClick={() => handleDownload(selectedReport.id)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-dark-border hover:bg-dark-border/50 transition-colors"
          >
            <Download size={12} />
            Download CSV
          </button>
        </div>
      )}

      {/* Report list */}
      <div>
        <h3 className="text-xs font-medium text-dark-muted mb-3">Past Reports ({reports.length})</h3>
        {reportsLoading && reports.length === 0 ? (
          <div className="flex justify-center py-8"><Loader2 size={20} className="animate-spin text-blue-400" /></div>
        ) : reports.length === 0 ? (
          <EmptyState icon={FileText} title="No reports yet" description="Generate your first report above" />
        ) : (
          <div className="space-y-2">
            {reports.map((report) => (
              <div key={report.id} className="bg-dark-card border border-dark-border rounded-lg p-3 flex items-center gap-3">
                <FileText size={16} className="text-dark-muted shrink-0" />
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm text-dark-text truncate">{report.title || report.type}</h4>
                  <div className="flex items-center gap-2 mt-0.5 text-xs text-dark-muted">
                    <Calendar size={10} />
                    <span>{new Date(report.created_at || report.generated_at).toLocaleDateString()}</span>
                    <span className="capitalize px-1.5 py-0.5 rounded bg-dark-border/50 text-[10px]">{report.type}</span>
                  </div>
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <button
                    onClick={() => setSelectedReport(report)}
                    className="px-2.5 py-1 text-xs rounded border border-dark-border hover:bg-dark-border/50 text-dark-muted hover:text-dark-text transition-colors"
                  >
                    View
                  </button>
                  <button
                    onClick={() => handleDownload(report.id)}
                    className="p-1.5 rounded border border-dark-border hover:bg-dark-border/50 text-dark-muted hover:text-dark-text transition-colors"
                    aria-label={`Download ${report.title || report.type} as CSV`}
                  >
                    <Download size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Main Enterprise Dashboard ────────────────
const TABS = [
  { id: 'oncall', label: 'On-Call', icon: Phone },
  { id: 'sla', label: 'SLA', icon: Shield },
  { id: 'rules', label: 'Alert Rules', icon: Bell },
  { id: 'runbooks', label: 'Runbooks', icon: BookOpen },
  { id: 'reports', label: 'Reports', icon: FileText },
];

export default function EnterpriseDashboard({ defaultTab }) {
  const [activeTab, setActiveTab] = useState(defaultTab || 'oncall');

  useEffect(() => {
    if (defaultTab && TABS.some((t) => t.id === defaultTab)) {
      setActiveTab(defaultTab);
    }
  }, [defaultTab]);

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-gradient-to-br from-purple-500/20 to-blue-500/20 border border-purple-500/20">
          <Shield size={20} className="text-purple-400" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-dark-text">Enterprise Features</h2>
          <p className="text-xs text-dark-muted">On-call, SLA, alert rules, runbooks, and reports</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 flex-wrap" role="tablist" aria-label="Enterprise feature tabs">
        {TABS.map((tab) => (
          <TabButton
            key={tab.id}
            active={activeTab === tab.id}
            onClick={() => setActiveTab(tab.id)}
            icon={tab.icon}
            label={tab.label}
          />
        ))}
      </div>

      {/* Tab content */}
      <div className="animate-fade-in" role="tabpanel">
        {activeTab === 'oncall' && <OnCallTab />}
        {activeTab === 'sla' && <SlaTab />}
        {activeTab === 'rules' && <AlertRulesTab />}
        {activeTab === 'runbooks' && <RunbooksTab />}
        {activeTab === 'reports' && <ReportsTab />}
      </div>
    </div>
  );
}
