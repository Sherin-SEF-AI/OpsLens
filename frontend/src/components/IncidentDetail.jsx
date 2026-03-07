import React, { useState, useEffect } from 'react';
import { X, ExternalLink, Send, ArrowRight, Database } from 'lucide-react';
import { SeverityBadge, StatusBadge } from './StatusBadge';
import Timeline from './Timeline';
import CommandPanel from './CommandPanel';
import { api } from '../api/client';

const transitions = {
  Triggered: ['Triaged'],
  Triaged: ['Investigating'],
  Investigating: ['Mitigated', 'Resolved'],
  Mitigated: ['Resolved', 'Investigating'],
  Resolved: ['Investigating'],
  Postmortem: [],
};

export default function IncidentDetail({ incidentId, onClose, onUpdate }) {
  const [incident, setIncident] = useState(null);
  const [comment, setComment] = useState('');
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    if (!incidentId) return;
    setLoading(true);
    api.getIncident(incidentId)
      .then(setIncident)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [incidentId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    );
  }

  if (!incident) return null;

  const allowedTransitions = transitions[incident.status] || [];

  async function handleTransition(newStatus) {
    setActionLoading(true);
    try {
      const reason = prompt(`Reason for transition to ${newStatus}:`) || '';
      await api.transitionIncident(incident.incident_id, newStatus, reason);
      const updated = await api.getIncident(incidentId);
      setIncident(updated);
      onUpdate?.();
    } catch (e) {
      alert(`Error: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  async function handleComment(e) {
    e.preventDefault();
    if (!comment.trim()) return;
    try {
      await api.addComment(incident.incident_id, comment);
      setComment('');
      const updated = await api.getIncident(incidentId);
      setIncident(updated);
    } catch (e) {
      alert(`Error: ${e.message}`);
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-dark-border">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-mono text-dark-muted">{incident.incident_id}</span>
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
          </div>
          <h2 className="text-lg font-semibold text-dark-text">{incident.title}</h2>
        </div>
        <button onClick={onClose} className="p-1 hover:bg-dark-border rounded">
          <X size={18} className="text-dark-muted" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {/* Info grid */}
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span className="text-dark-muted">Service</span>
            <p className="text-dark-text font-medium">{incident.service}</p>
          </div>
          <div>
            <span className="text-dark-muted">Source</span>
            <p className="text-dark-text capitalize">{incident.source}</p>
          </div>
          <div>
            <span className="text-dark-muted">Triggered</span>
            <p className="text-dark-text">{new Date(incident.triggered_at).toLocaleString()}</p>
          </div>
          <div>
            <span className="text-dark-muted">Duration</span>
            <p className="text-dark-text">
              {incident.duration_seconds
                ? `${Math.floor(incident.duration_seconds / 60)}m ${incident.duration_seconds % 60}s`
                : 'Ongoing'}
            </p>
          </div>
          {incident.root_cause && (
            <div className="col-span-2">
              <span className="text-dark-muted">Root Cause</span>
              <p className="text-dark-text">{incident.root_cause}</p>
            </div>
          )}
        </div>

        {/* Description */}
        <div>
          <h3 className="text-sm font-medium text-dark-muted mb-1">Description</h3>
          <p className="text-sm text-dark-text bg-dark-bg p-3 rounded-lg border border-dark-border">
            {incident.description}
          </p>
        </div>

        {/* Links */}
        <div className="flex gap-2 flex-wrap">
          {incident.notion_page_id && (
            <a
              href={`https://notion.so/${incident.notion_page_id.replace(/-/g, '')}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-dark-bg border border-dark-border text-dark-text hover:border-blue-500/30 hover:text-blue-400 transition-colors"
            >
              <Database size={12} /> View in Notion
            </a>
          )}
          {incident.source_url && (
            <a href={incident.source_url} target="_blank" rel="noopener noreferrer"
               className="text-xs flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-dark-bg border border-dark-border text-dark-text hover:border-blue-500/30 hover:text-blue-400 transition-colors">
              <ExternalLink size={12} /> Alert Source
            </a>
          )}
          {incident.dashboard_url && (
            <a href={incident.dashboard_url} target="_blank" rel="noopener noreferrer"
               className="text-xs flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-dark-bg border border-dark-border text-dark-text hover:border-blue-500/30 hover:text-blue-400 transition-colors">
              <ExternalLink size={12} /> Dashboard
            </a>
          )}
        </div>

        {/* Actions */}
        {allowedTransitions.length > 0 && (
          <div>
            <h3 className="text-sm font-medium text-dark-muted mb-2">Actions</h3>
            <div className="flex gap-2 flex-wrap">
              {allowedTransitions.map((status) => (
                <button
                  key={status}
                  onClick={() => handleTransition(status)}
                  disabled={actionLoading}
                  className="px-3 py-1.5 text-xs font-medium rounded border border-dark-border hover:bg-dark-border transition-colors disabled:opacity-50 flex items-center gap-1"
                >
                  <ArrowRight size={12} /> {status}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Timeline */}
        <div>
          <h3 className="text-sm font-medium text-dark-muted mb-3">Timeline</h3>
          <Timeline events={incident.timeline} />
        </div>
      </div>

      {/* Incident Commander */}
      <CommandPanel incidentId={incident.incident_id} />

      {/* Comment input */}
      <form onSubmit={handleComment} className="p-4 border-t border-dark-border">
        <div className="flex gap-2">
          <input
            type="text"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Add a comment..."
            className="flex-1 bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text placeholder:text-dark-muted focus:outline-none focus:border-blue-500/50"
          />
          <button
            type="submit"
            className="px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors"
          >
            <Send size={14} />
          </button>
        </div>
      </form>
    </div>
  );
}
