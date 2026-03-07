import React, { useState, useEffect, useMemo } from 'react';
import {
  Bot,
  Search,
  Wrench,
  FileText,
  MessageSquare,
  AlertCircle,
  ArrowRight,
  User,
  Clock,
  ChevronRight,
  Loader2,
  Filter,
  Play,
  Database,
  GitBranch,
  BookOpen,
  Zap,
} from 'lucide-react';
import { api } from '../api/client';

const phaseConfig = {
  triage: { color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/20', icon: Search, label: 'Triage' },
  correlation: { color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', icon: GitBranch, label: 'Correlation' },
  remediation: { color: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/20', icon: Wrench, label: 'Remediation' },
  postmortem: { color: 'text-purple-400', bg: 'bg-purple-500/10', border: 'border-purple-500/20', icon: FileText, label: 'Postmortem' },
  communications: { color: 'text-pink-400', bg: 'bg-pink-500/10', border: 'border-pink-500/20', icon: MessageSquare, label: 'Comms' },
  enrichment: { color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20', icon: BookOpen, label: 'Enrichment' },
  transition: { color: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/20', icon: ArrowRight, label: 'Transition' },
  system: { color: 'text-gray-400', bg: 'bg-gray-500/10', border: 'border-gray-500/20', icon: Zap, label: 'System' },
};

const actorConfig = {
  'triage-agent': { icon: Search, color: 'text-yellow-400' },
  'correlation-agent': { icon: GitBranch, color: 'text-cyan-400' },
  'remediation-agent': { icon: Wrench, color: 'text-green-400' },
  'postmortem-agent': { icon: FileText, color: 'text-purple-400' },
  'comms-agent': { icon: MessageSquare, color: 'text-pink-400' },
  'orchestrator': { icon: Bot, color: 'text-blue-400' },
  'github-integration': { icon: GitBranch, color: 'text-gray-300' },
  'knowledge-base': { icon: BookOpen, color: 'text-emerald-400' },
  'notion-watcher': { icon: Database, color: 'text-orange-400' },
  'system': { icon: Zap, color: 'text-gray-400' },
  'dashboard': { icon: User, color: 'text-blue-300' },
};

function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

// Replay view for a single incident
function ReplayView({ incidentId, onBack }) {
  const [replay, setReplay] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeStep, setActiveStep] = useState(null);

  useEffect(() => {
    setLoading(true);
    api.getIncidentReplay(incidentId)
      .then(setReplay)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [incidentId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader2 size={24} className="animate-spin text-blue-400" />
      </div>
    );
  }

  if (!replay || !replay.steps?.length) {
    return (
      <div className="text-center py-8 text-dark-muted">
        <Bot size={32} className="mx-auto mb-2 opacity-30" />
        <p className="text-sm">No agent actions recorded for this incident</p>
        <button onClick={onBack} className="mt-3 text-xs text-blue-400 hover:text-blue-300">
          Back to trail
        </button>
      </div>
    );
  }

  // Group steps by phase
  const phases = [];
  let currentPhase = null;
  for (const step of replay.steps) {
    if (!currentPhase || currentPhase.phase !== step.phase) {
      currentPhase = { phase: step.phase, steps: [], startTime: step.timestamp };
      phases.push(currentPhase);
    }
    currentPhase.steps.push(step);
    currentPhase.endTime = step.timestamp;
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
          <ChevronRight size={12} className="rotate-180" />
          Back to audit trail
        </button>
        <div className="flex items-center gap-3 text-xs text-dark-muted">
          <span>{replay.total_steps} steps</span>
          <span className="flex items-center gap-1">
            <Clock size={12} />
            {formatDuration(replay.total_duration_ms)}
          </span>
        </div>
      </div>

      {/* Incident info */}
      <div className="bg-dark-card border border-dark-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-mono text-dark-muted">{replay.incident_id}</span>
          <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${
            replay.severity?.includes('P0') ? 'text-red-400 bg-red-500/10 border-red-500/20' :
            replay.severity?.includes('P1') ? 'text-orange-400 bg-orange-500/10 border-orange-500/20' :
            'text-yellow-400 bg-yellow-500/10 border-yellow-500/20'
          }`}>{replay.severity}</span>
          <span className="text-[10px] px-2 py-0.5 rounded-full border text-dark-muted border-dark-border">
            {replay.status}
          </span>
        </div>
        <h3 className="text-sm font-medium text-dark-text">{replay.incident_title}</h3>
      </div>

      {/* Phase timeline bar */}
      {replay.total_duration_ms > 0 && (
        <div className="bg-dark-card border border-dark-border rounded-lg p-3">
          <label className="text-[10px] text-dark-muted uppercase tracking-wider mb-2 block">Pipeline Timeline</label>
          <div className="flex h-6 rounded-md overflow-hidden gap-px">
            {phases.map((p, i) => {
              const cfg = phaseConfig[p.phase] || phaseConfig.system;
              const totalMs = p.steps.reduce((sum, s) => sum + (s.duration_ms || 0), 0);
              const pct = Math.max(((totalMs / replay.total_duration_ms) * 100), 3);
              return (
                <div
                  key={i}
                  className={`${cfg.bg} ${cfg.border} border flex items-center justify-center cursor-pointer hover:opacity-80 transition-opacity`}
                  style={{ width: `${pct}%` }}
                  title={`${cfg.label}: ${formatDuration(totalMs)}`}
                >
                  <span className={`text-[9px] font-medium ${cfg.color} truncate px-1`}>
                    {cfg.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Step-by-step replay */}
      <div className="relative">
        <div className="absolute left-[18px] top-0 bottom-0 w-px bg-dark-border" />
        <div className="space-y-1">
          {replay.steps.map((step) => {
            const cfg = phaseConfig[step.phase] || phaseConfig.system;
            const actor = actorConfig[step.actor] || actorConfig.system;
            const Icon = actor.icon;
            const isExpanded = activeStep === step.step;

            return (
              <div
                key={step.step}
                onClick={() => setActiveStep(isExpanded ? null : step.step)}
                className={`relative flex items-start gap-3 pl-1 py-2 px-2 rounded-lg cursor-pointer transition-all ${
                  isExpanded ? 'bg-dark-card/80 border border-dark-border' : 'hover:bg-dark-card/40'
                }`}
              >
                {/* Icon */}
                <div className={`relative z-10 flex items-center justify-center w-8 h-8 rounded-full shrink-0 ${cfg.bg} ${cfg.border} border`}>
                  <Icon size={14} className={cfg.color} />
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0 pt-0.5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] font-semibold uppercase tracking-wider ${cfg.color}`}>
                      {cfg.label}
                    </span>
                    <span className="text-[10px] text-dark-muted">by</span>
                    <span className={`text-[10px] font-medium ${actor.color}`}>
                      {step.actor}
                    </span>
                    <span className="text-[10px] text-dark-muted font-mono ml-auto">
                      {formatTime(step.timestamp)}
                    </span>
                  </div>
                  <p className={`text-xs text-dark-text/80 mt-0.5 ${isExpanded ? '' : 'truncate'}`}>
                    {step.message}
                  </p>
                  {isExpanded && (
                    <div className="mt-2 flex items-center gap-4 text-[10px] text-dark-muted">
                      <span className="flex items-center gap-1">
                        <Clock size={10} />
                        +{formatDuration(step.elapsed_ms)} from start
                      </span>
                      {step.duration_ms > 0 && (
                        <span className="flex items-center gap-1">
                          Step took {formatDuration(step.duration_ms)}
                        </span>
                      )}
                      <span className="px-1.5 py-0.5 rounded bg-dark-border/50">
                        Step {step.step}/{replay.total_steps}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// Main audit trail view
export default function AuditTrail() {
  const [trail, setTrail] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all'); // all, agents, mcp, manual
  const [replayId, setReplayId] = useState(null);
  const [incidents, setIncidents] = useState([]);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.getAuditTrail(),
      api.getIncidents(),
    ])
      .then(([trailData, incData]) => {
        setTrail(trailData);
        setIncidents(incData);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const filteredTrail = useMemo(() => {
    if (filter === 'agents') return trail.filter((e) => e.is_agent);
    if (filter === 'mcp') return trail.filter((e) => e.is_mcp_call);
    if (filter === 'manual') return trail.filter((e) => !e.is_agent);
    return trail;
  }, [trail, filter]);

  // Group by incident for the summary view
  const incidentSummaries = useMemo(() => {
    const byIncident = {};
    for (const event of trail) {
      if (!byIncident[event.incident_id]) {
        byIncident[event.incident_id] = {
          incident_id: event.incident_id,
          title: event.incident_title,
          severity: event.severity,
          total_events: 0,
          agent_events: 0,
          mcp_calls: 0,
          actors: new Set(),
        };
      }
      const s = byIncident[event.incident_id];
      s.total_events++;
      if (event.is_agent) s.agent_events++;
      if (event.is_mcp_call) s.mcp_calls++;
      s.actors.add(event.actor);
    }
    return Object.values(byIncident).map((s) => ({
      ...s,
      actors: [...s.actors],
    }));
  }, [trail]);

  if (replayId) {
    return <ReplayView incidentId={replayId} onBack={() => setReplayId(null)} />;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader2 size={24} className="animate-spin text-blue-400" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-gradient-to-br from-purple-500/20 to-pink-500/20 border border-purple-500/20">
          <Bot size={20} className="text-purple-400" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-dark-text">Agent Audit Trail</h2>
          <p className="text-xs text-dark-muted">Step-by-step replay of every agent action</p>
        </div>
      </div>

      {/* Incident cards — clickable for replay */}
      {incidentSummaries.length > 0 && (
        <div>
          <label className="text-xs font-medium text-dark-muted mb-2 block">
            Incidents with Agent Activity ({incidentSummaries.length})
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {incidentSummaries.map((s) => (
              <button
                key={s.incident_id}
                onClick={() => setReplayId(s.incident_id)}
                className="bg-dark-card border border-dark-border rounded-lg p-3 text-left hover:border-dark-muted/50 transition-all group"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] font-mono text-dark-muted">{s.incident_id}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${
                    s.severity?.includes('P0') ? 'text-red-400 bg-red-500/10 border-red-500/20' :
                    s.severity?.includes('P1') ? 'text-orange-400 bg-orange-500/10 border-orange-500/20' :
                    s.severity?.includes('P2') ? 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20' :
                    'text-blue-400 bg-blue-500/10 border-blue-500/20'
                  }`}>{s.severity?.split('-')[0]}</span>
                </div>
                <h4 className="text-xs font-medium text-dark-text truncate mb-2">{s.title}</h4>
                <div className="flex items-center gap-3 text-[10px] text-dark-muted">
                  <span>{s.agent_events} agent actions</span>
                  <span>{s.total_events} total events</span>
                  <Play size={10} className="ml-auto text-blue-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Filter tabs */}
      <div className="flex items-center gap-2">
        <Filter size={14} className="text-dark-muted" />
        {[
          { id: 'all', label: 'All Events' },
          { id: 'agents', label: 'Agent Only' },
          { id: 'mcp', label: 'MCP Calls' },
          { id: 'manual', label: 'Human' },
        ].map((f) => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
              filter === f.id
                ? 'bg-blue-600/20 text-blue-400'
                : 'text-dark-muted hover:text-dark-text hover:bg-dark-border/50'
            }`}
          >
            {f.label}
          </button>
        ))}
        <span className="text-xs text-dark-muted ml-auto">{filteredTrail.length} events</span>
      </div>

      {/* Event stream */}
      {filteredTrail.length === 0 ? (
        <div className="text-center py-8 text-dark-muted">
          <Bot size={32} className="mx-auto mb-2 opacity-30" />
          <p className="text-sm">No audit events yet</p>
          <p className="text-xs mt-1">Events will appear as agents process incidents</p>
        </div>
      ) : (
        <div className="space-y-1">
          {filteredTrail.slice(0, 50).map((event, i) => {
            const actor = actorConfig[event.actor] || actorConfig.system;
            const Icon = actor.icon;
            return (
              <div
                key={i}
                className="flex items-start gap-2.5 p-2.5 rounded-lg hover:bg-dark-card/40 transition-colors"
              >
                <Icon size={14} className={`mt-0.5 shrink-0 ${actor.color}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-xs font-medium ${actor.color}`}>{event.actor}</span>
                    <span className="text-[10px] font-mono text-dark-muted">{event.incident_id}</span>
                    {event.is_mcp_call && (
                      <span className="text-[9px] px-1 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/15">
                        MCP
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-dark-text/80 mt-0.5 truncate">{event.message}</p>
                </div>
                <span className="text-[10px] text-dark-muted whitespace-nowrap mt-0.5">
                  {formatTime(event.timestamp)}
                </span>
              </div>
            );
          })}
          {filteredTrail.length > 50 && (
            <p className="text-center text-xs text-dark-muted py-2">
              Showing 50 of {filteredTrail.length} events
            </p>
          )}
        </div>
      )}
    </div>
  );
}
