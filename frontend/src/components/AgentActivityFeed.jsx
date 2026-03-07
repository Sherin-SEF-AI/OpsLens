import React from 'react';
import { Bot, Search, FileText, Wrench } from 'lucide-react';

const agentIcons = {
  'triage-agent': { icon: Search, color: 'text-yellow-400' },
  'correlation-agent': { icon: FileText, color: 'text-cyan-400' },
  'remediation-agent': { icon: Wrench, color: 'text-green-400' },
  'postmortem-agent': { icon: FileText, color: 'text-purple-400' },
  orchestrator: { icon: Bot, color: 'text-blue-400' },
  system: { icon: Bot, color: 'text-gray-400' },
};

export default function AgentActivityFeed({ events }) {
  const agentEvents = events.filter(
    (e) =>
      e.actor !== 'dashboard' &&
      e.actor !== 'system' &&
      (e.event_type?.startsWith('agent_') || ['triage-agent', 'correlation-agent', 'remediation-agent', 'postmortem-agent', 'orchestrator'].includes(e.actor))
  );

  if (!agentEvents.length) {
    return (
      <div className="text-center py-16 text-dark-muted">
        <div className="w-14 h-14 rounded-2xl bg-purple-500/5 border border-purple-500/10 flex items-center justify-center mx-auto mb-3">
          <Bot size={24} className="text-purple-400/40" />
        </div>
        <p className="text-sm font-medium text-dark-text/60">No agent activity yet</p>
        <p className="text-xs mt-1">Activity will appear as agents process incidents</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {agentEvents.slice(-20).reverse().map((event, i) => {
        const config = agentIcons[event.actor] || agentIcons.system;
        const Icon = config.icon;
        return (
          <div key={i} className="flex items-start gap-2.5 p-2.5 rounded-lg bg-dark-bg/50 hover:bg-dark-card/40 transition-colors">
            <Icon size={14} className={`mt-0.5 ${config.color}`} />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={`text-xs font-medium ${config.color}`}>{event.actor}</span>
                {event.incident_id && (
                  <span className="text-xs text-dark-muted font-mono">{event.incident_id}</span>
                )}
              </div>
              <p className="text-xs text-dark-text mt-0.5 truncate">{event.message}</p>
            </div>
            <span className="text-xs text-dark-muted whitespace-nowrap">
              {new Date(event.timestamp).toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })}
            </span>
          </div>
        );
      })}
    </div>
  );
}
