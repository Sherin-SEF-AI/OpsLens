import React, { useState } from 'react';
import {
  Play,
  Copy,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  Loader2,
  Zap,
  Server,
  Shield,
  Send,
} from 'lucide-react';
import { api } from '../api/client';

const SOURCES = [
  { id: 'alertmanager', label: 'Prometheus AlertManager', color: 'text-red-400' },
  { id: 'grafana', label: 'Grafana', color: 'text-orange-400' },
  { id: 'pagerduty', label: 'PagerDuty', color: 'text-green-400' },
  { id: 'generic', label: 'Generic Alert', color: 'text-blue-400' },
  { id: 'manual', label: 'Manual Incident', color: 'text-purple-400' },
];

const TEMPLATES = {
  alertmanager: {
    status: 'firing',
    alerts: [
      {
        status: 'firing',
        labels: {
          alertname: 'HighCPUUsage',
          severity: 'critical',
          instance: 'api-server-01:9090',
          service: 'API Gateway',
        },
        annotations: {
          summary: 'CPU usage above 90% for 5 minutes',
          description: 'The API Gateway server api-server-01 has been using more than 90% CPU for the last 5 minutes.',
          runbook_url: 'https://runbooks.example.com/high-cpu',
        },
        startsAt: new Date().toISOString(),
        generatorURL: 'http://prometheus:9090/graph?g0.expr=cpu_usage',
      },
    ],
    groupLabels: { alertname: 'HighCPUUsage' },
    commonLabels: { severity: 'critical' },
    commonAnnotations: {},
    externalURL: 'http://alertmanager:9093',
  },
  grafana: {
    status: 'alerting',
    alerts: [
      {
        status: 'firing',
        labels: {
          alertname: 'DatabaseLatencyHigh',
          grafana_folder: 'Infrastructure',
          severity: 'warning',
        },
        annotations: {
          summary: 'Database query latency exceeds 500ms',
          description: 'P95 query latency on Database Cluster has exceeded 500ms threshold.',
        },
        startsAt: new Date().toISOString(),
        panelURL: 'http://grafana:3000/d/abc123/db-dashboard?viewPanel=1',
        dashboardURL: 'http://grafana:3000/d/abc123/db-dashboard',
      },
    ],
    title: 'Database Latency Alert',
    message: 'Database queries are slow',
  },
  pagerduty: {
    event: {
      id: 'evt-' + Date.now(),
      event_type: 'incident.triggered',
      occurred_at: new Date().toISOString(),
      data: {
        id: 'PD-' + Math.random().toString(36).slice(2, 8).toUpperCase(),
        title: 'Payment Service: 500 errors spike',
        urgency: 'high',
        status: 'triggered',
        service: { summary: 'Payment Service' },
        assignees: [{ summary: 'On-Call Engineer' }],
        html_url: 'https://pagerduty.com/incidents/PD123',
      },
    },
  },
  generic: {
    title: 'Memory usage critical on worker-node-03',
    description: 'Memory usage on worker-node-03 has exceeded 95% threshold. OOM killer may activate.',
    severity: 'P1',
    service: 'Notification Service',
    source_url: 'https://monitoring.example.com/alerts/mem-critical',
    labels: { host: 'worker-node-03', cluster: 'production-east' },
  },
  manual: {
    title: 'Customer reports checkout failures',
    description: 'Multiple customer reports of payment failures during checkout. Approximately 15% of transactions affected in the last 30 minutes.',
    severity: 'P1',
    service: 'Payment Service',
  },
};

const severityColor = {
  P0: 'text-red-400 bg-red-500/10 border-red-500/30',
  P1: 'text-orange-400 bg-orange-500/10 border-orange-500/30',
  P2: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
  P3: 'text-blue-400 bg-blue-500/10 border-blue-500/30',
  critical: 'text-red-400 bg-red-500/10 border-red-500/30',
  high: 'text-orange-400 bg-orange-500/10 border-orange-500/30',
  warning: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
  low: 'text-blue-400 bg-blue-500/10 border-blue-500/30',
};

export default function WebhookPlayground() {
  const [source, setSource] = useState('alertmanager');
  const [payload, setPayload] = useState(JSON.stringify(TEMPLATES.alertmanager, null, 2));
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState(null);

  function handleSourceChange(newSource) {
    setSource(newSource);
    setPayload(JSON.stringify(TEMPLATES[newSource], null, 2));
    setResult(null);
    setError('');
    setSendResult(null);
  }

  async function handleTest() {
    setLoading(true);
    setResult(null);
    setError('');
    try {
      const parsed = JSON.parse(payload);
      const res = await api.testPlayground(source, parsed);
      setResult(res);
    } catch (e) {
      if (e instanceof SyntaxError) {
        setError(`Invalid JSON: ${e.message}`);
      } else {
        setError(e.message);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleSendLive() {
    setSending(true);
    setSendResult(null);
    setError('');
    try {
      const parsed = JSON.parse(payload);
      const res = await api.sendWebhook(source, parsed);
      setSendResult(res);
    } catch (e) {
      if (e instanceof SyntaxError) {
        setError(`Invalid JSON: ${e.message}`);
      } else {
        setError(e.message);
      }
    } finally {
      setSending(false);
    }
  }

  function handleCopy() {
    navigator.clipboard.writeText(payload);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-gradient-to-br from-blue-500/20 to-purple-500/20 border border-blue-500/20">
          <Zap size={20} className="text-blue-400" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-dark-text">Webhook Playground</h2>
          <p className="text-xs text-dark-muted">Test alert payloads without creating incidents</p>
        </div>
      </div>

      {/* Source selector */}
      <div>
        <label className="block text-xs font-medium text-dark-muted mb-2">Alert Source Format</label>
        <div className="flex flex-wrap gap-2">
          {SOURCES.map((s) => (
            <button
              key={s.id}
              onClick={() => handleSourceChange(s.id)}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-all ${
                source === s.id
                  ? 'bg-blue-600/20 text-blue-400 border-blue-500/40'
                  : 'bg-dark-bg text-dark-muted border-dark-border hover:border-dark-muted/50 hover:text-dark-text'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* Editor + Result split */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Payload editor */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-dark-muted">JSON Payload</label>
            <div className="flex items-center gap-2">
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 px-2 py-1 text-xs text-dark-muted hover:text-dark-text rounded border border-dark-border hover:border-dark-muted/50"
              >
                {copied ? <CheckCircle2 size={12} className="text-green-400" /> : <Copy size={12} />}
                {copied ? 'Copied' : 'Copy'}
              </button>
              <button
                onClick={() => handleSourceChange(source)}
                className="px-2 py-1 text-xs text-dark-muted hover:text-dark-text rounded border border-dark-border hover:border-dark-muted/50"
              >
                Reset
              </button>
            </div>
          </div>
          <textarea
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            spellCheck={false}
            className="w-full h-80 bg-dark-bg border border-dark-border rounded-lg px-4 py-3 text-sm text-dark-text font-mono leading-relaxed focus:outline-none focus:border-blue-500/50 resize-none"
          />
          <div className="flex gap-2">
            <button
              onClick={handleTest}
              disabled={loading || sending}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-500 hover:to-blue-600 rounded-lg text-sm font-medium transition-all disabled:opacity-50"
            >
              {loading ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Play size={16} />
              )}
              {loading ? 'Parsing...' : 'Test Payload'}
            </button>
            <button
              onClick={handleSendLive}
              disabled={loading || sending}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-emerald-600 to-emerald-700 hover:from-emerald-500 hover:to-emerald-600 rounded-lg text-sm font-medium transition-all disabled:opacity-50"
            >
              {sending ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Send size={16} />
              )}
              {sending ? 'Sending...' : 'Send Live'}
            </button>
          </div>
        </div>

        {/* Result panel */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-dark-muted">Analysis Result</label>
          <div className="h-80 bg-dark-bg border border-dark-border rounded-lg p-4 overflow-y-auto">
            {sendResult ? (
              <div className="flex items-start gap-3 text-emerald-400">
                <CheckCircle2 size={18} className="mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium">Webhook Sent Successfully</p>
                  <p className="text-xs mt-1 text-emerald-300/80">
                    {sendResult.alerts || sendResult.status === 'accepted' ? `${sendResult.alerts || 1} alert(s) accepted` : JSON.stringify(sendResult)}
                  </p>
                  <p className="text-xs mt-2 text-dark-muted">Check Active incidents for the new entry</p>
                </div>
              </div>
            ) : error ? (
              <div className="flex items-start gap-3 text-red-400">
                <XCircle size={18} className="mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium">Validation Failed</p>
                  <p className="text-xs mt-1 text-red-300/80 font-mono">{error}</p>
                </div>
              </div>
            ) : result ? (
              <div className="space-y-4">
                {/* Status header */}
                <div className={`flex items-center gap-2 p-2 rounded-lg border ${
                  result.status === 'ok'
                    ? 'bg-green-500/5 border-green-500/20'
                    : 'bg-red-500/5 border-red-500/20'
                }`}>
                  {result.status === 'ok' ? (
                    <CheckCircle2 size={16} className="text-green-400" />
                  ) : (
                    <XCircle size={16} className="text-red-400" />
                  )}
                  <span className={`text-sm font-medium ${result.status === 'ok' ? 'text-green-400' : 'text-red-400'}`}>
                    {result.status === 'ok' ? 'Validation Passed' : 'Validation Failed'}
                  </span>
                  <span className="text-xs text-dark-muted ml-auto">
                    {result.alerts_parsed} alert{result.alerts_parsed !== 1 ? 's' : ''} parsed
                  </span>
                </div>

                {result.error && (
                  <div className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                    <p className="text-xs text-red-400 font-mono">{result.error_type}: {result.error}</p>
                  </div>
                )}

                {/* Parsed alerts */}
                {result.alerts?.map((alert, i) => (
                  <div key={i} className="bg-dark-card/50 border border-dark-border rounded-lg p-3 space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-medium text-dark-text truncate flex-1">
                        {alert.title}
                      </h4>
                      {alert.is_duplicate && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 ml-2 shrink-0">
                          DUPLICATE
                        </span>
                      )}
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <span className="text-[10px] text-dark-muted uppercase tracking-wider">Severity</span>
                        <p className={`text-xs font-medium mt-0.5 px-2 py-0.5 rounded border inline-block ${
                          severityColor[alert.severity] || 'text-dark-text bg-dark-bg border-dark-border'
                        }`}>
                          {alert.severity}
                        </p>
                      </div>
                      <div>
                        <span className="text-[10px] text-dark-muted uppercase tracking-wider">Service</span>
                        <p className="text-xs text-dark-text mt-0.5 flex items-center gap-1">
                          <Server size={10} className="text-dark-muted" />
                          {alert.service}
                        </p>
                      </div>
                      <div>
                        <span className="text-[10px] text-dark-muted uppercase tracking-wider">Source</span>
                        <p className="text-xs text-dark-text mt-0.5 capitalize">{alert.source}</p>
                      </div>
                      <div>
                        <span className="text-[10px] text-dark-muted uppercase tracking-wider">Status</span>
                        <p className="text-xs text-dark-text mt-0.5 capitalize">{alert.status}</p>
                      </div>
                    </div>

                    <div>
                      <span className="text-[10px] text-dark-muted uppercase tracking-wider">Description</span>
                      <p className="text-xs text-dark-text/80 mt-0.5">{alert.description}</p>
                    </div>

                    {alert.is_duplicate && (
                      <div className="flex items-center gap-2 p-2 bg-yellow-500/5 border border-yellow-500/15 rounded text-xs text-yellow-400">
                        <Shield size={12} />
                        Would be deduplicated (active incident exists for this service + title)
                      </div>
                    )}

                    {Object.keys(alert.labels || {}).length > 0 && (
                      <div>
                        <span className="text-[10px] text-dark-muted uppercase tracking-wider">Labels</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {Object.entries(alert.labels).map(([k, v]) => (
                            <span key={k} className="text-[10px] px-1.5 py-0.5 bg-dark-border/50 text-dark-muted rounded font-mono">
                              {k}={v}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-dark-muted">
                <Zap size={32} className="mb-3 opacity-20" />
                <p className="text-sm">Click "Test Payload" to analyze</p>
                <p className="text-xs mt-1">Payloads are validated and parsed without creating incidents</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
