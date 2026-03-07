import React from 'react';

const severityStyles = {
  'P0-Critical': 'bg-red-500/20 text-red-400 border-red-500/30',
  'P1-High': 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  'P2-Medium': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  'P3-Low': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
};

const statusStyles = {
  Triggered: 'bg-red-500/20 text-red-400 border-red-500/30',
  Triaged: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  Investigating: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  Mitigated: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  Resolved: 'bg-green-500/20 text-green-400 border-green-500/30',
  Postmortem: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
};

export function SeverityBadge({ severity }) {
  const style = severityStyles[severity] || 'bg-gray-500/20 text-gray-400 border-gray-500/30';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${style}`}>
      {severity}
    </span>
  );
}

export function StatusBadge({ status }) {
  const style = statusStyles[status] || 'bg-gray-500/20 text-gray-400 border-gray-500/30';
  const isActive = status === 'Triggered';
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium border ${style}`}>
      {isActive && (
        <span className="w-1.5 h-1.5 rounded-full bg-red-400" style={{ animation: 'pulse-dot 1.5s ease-in-out infinite' }} />
      )}
      {status}
    </span>
  );
}
