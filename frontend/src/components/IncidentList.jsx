import React from 'react';
import { AlertTriangle, Clock, Server, ExternalLink } from 'lucide-react';
import { SeverityBadge, StatusBadge } from './StatusBadge';

function timeSince(dateStr) {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h ago`;
}

function durationStr(seconds) {
  if (!seconds) return null;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m > 60) return `${Math.floor(m / 60)}h ${m % 60}m`;
  return `${m}m ${s}s`;
}

export default function IncidentList({ incidents, onSelect, selectedId }) {
  if (!incidents.length) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-dark-muted">
        <div className="w-16 h-16 rounded-2xl bg-green-500/5 border border-green-500/10 flex items-center justify-center mb-4">
          <AlertTriangle size={28} className="text-green-500/40" />
        </div>
        <p className="text-sm font-medium text-dark-text/60">No incidents</p>
        <p className="text-xs mt-1">All systems operational</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {incidents.map((inc) => (
        <div
          key={inc.incident_id}
          onClick={() => onSelect(inc.incident_id)}
          className={`p-4 rounded-xl border cursor-pointer transition-all hover:border-dark-muted/40 ${
            selectedId === inc.incident_id
              ? 'bg-dark-card border-blue-500/40 shadow-sm shadow-blue-500/5'
              : 'bg-dark-card/50 border-dark-border hover:bg-dark-card/70'
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs text-dark-muted font-mono">{inc.incident_id}</span>
                <SeverityBadge severity={inc.severity} />
                <StatusBadge status={inc.status} />
              </div>
              <h3 className="text-sm font-medium text-dark-text truncate">{inc.title}</h3>
              <div className="flex items-center gap-3 mt-2 text-xs text-dark-muted">
                <span className="flex items-center gap-1">
                  <Server size={12} />
                  {inc.service}
                </span>
                <span className="flex items-center gap-1">
                  <Clock size={12} />
                  {inc.duration_seconds
                    ? durationStr(inc.duration_seconds)
                    : timeSince(inc.triggered_at)}
                </span>
                {inc.source && (
                  <span className="capitalize">{inc.source}</span>
                )}
              </div>
            </div>
            {inc.agent_actions_count > 0 && (
              <span className="text-xs bg-purple-500/20 text-purple-400 px-1.5 py-0.5 rounded">
                {inc.agent_actions_count} actions
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
