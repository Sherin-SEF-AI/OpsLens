import React from 'react';
import { Circle, Bot, User, AlertCircle, ArrowRight, MessageSquare } from 'lucide-react';

const typeConfig = {
  created: { icon: AlertCircle, color: 'text-red-400', bg: 'bg-red-500/20' },
  status_change: { icon: ArrowRight, color: 'text-blue-400', bg: 'bg-blue-500/20' },
  agent_triage: { icon: Bot, color: 'text-yellow-400', bg: 'bg-yellow-500/20' },
  agent_correlation: { icon: Bot, color: 'text-cyan-400', bg: 'bg-cyan-500/20' },
  agent_remediation: { icon: Bot, color: 'text-green-400', bg: 'bg-green-500/20' },
  agent_postmortem: { icon: Bot, color: 'text-purple-400', bg: 'bg-purple-500/20' },
  comment: { icon: MessageSquare, color: 'text-gray-400', bg: 'bg-gray-500/20' },
  escalation: { icon: AlertCircle, color: 'text-red-400', bg: 'bg-red-500/20' },
  manual_action: { icon: User, color: 'text-blue-400', bg: 'bg-blue-500/20' },
};

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export default function Timeline({ events }) {
  if (!events || !events.length) {
    return <p className="text-dark-muted text-sm py-4">No timeline events yet.</p>;
  }

  return (
    <div className="relative">
      <div className="absolute left-4 top-0 bottom-0 w-px bg-dark-border" />
      <div className="space-y-4">
        {events.map((event, i) => {
          const config = typeConfig[event.event_type] || typeConfig.comment;
          const Icon = config.icon;
          return (
            <div key={i} className="relative flex items-start gap-3 pl-1">
              <div className={`relative z-10 flex items-center justify-center w-7 h-7 rounded-full ${config.bg}`}>
                <Icon size={14} className={config.color} />
              </div>
              <div className="flex-1 min-w-0 pt-0.5">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-xs text-dark-muted font-mono">
                    {formatTime(event.timestamp)}
                  </span>
                  <span className="text-xs text-dark-muted">by {event.actor}</span>
                </div>
                <p className="text-sm text-dark-text">{event.message}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
