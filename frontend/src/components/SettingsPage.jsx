import React, { useState, useEffect } from 'react';
import {
  Settings,
  Database,
  MessageSquare,
  Webhook,
  Brain,
  CheckCircle2,
  XCircle,
  Loader2,
  Copy,
  RefreshCw,
  Eye,
  EyeOff,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Cloud,
  Ticket,
  BookOpen,
  Send,
} from 'lucide-react';

const API = '/api/settings';

async function fetchSettings() {
  const res = await fetch(API);
  return res.json();
}

async function saveSettings(data) {
  const res = await fetch(API, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return res.json();
}

async function testConnection(type) {
  const res = await fetch(`${API}/test/${type}`, { method: 'POST' });
  return res.json();
}

async function getWebhookUrls() {
  const res = await fetch(`${API}/webhook-urls`);
  return res.json();
}

async function getSetupStatus() {
  const res = await fetch(`${API}/setup-status`);
  return res.json();
}

async function generateSecret() {
  const res = await fetch(`${API}/generate-secret`, { method: 'POST' });
  return res.json();
}

function SecretField({ value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        type={show ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text pr-10 focus:outline-none focus:border-blue-500/50"
      />
      <button
        type="button"
        onClick={() => setShow(!show)}
        className="absolute right-2 top-1/2 -translate-y-1/2 text-dark-muted hover:text-dark-text"
      >
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

function TestButton({ type, label }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);

  async function handleTest() {
    setLoading(true);
    setStatus(null);
    try {
      const result = await testConnection(type);
      setStatus(result);
    } catch (e) {
      setStatus({ status: 'error', message: e.message });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={handleTest}
        disabled={loading}
        className="px-3 py-1.5 text-xs font-medium rounded border border-dark-border hover:bg-dark-border transition-colors disabled:opacity-50 flex items-center gap-1.5"
      >
        {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
        Test {label}
      </button>
      {status && (
        <span className={`text-xs flex items-center gap-1 ${status.status === 'ok' ? 'text-green-400' : 'text-red-400'}`}>
          {status.status === 'ok' ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
          {status.message}
        </span>
      )}
    </div>
  );
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className="p-1 hover:bg-dark-border rounded text-dark-muted hover:text-dark-text"
      title="Copy"
    >
      {copied ? <CheckCircle2 size={12} className="text-green-400" /> : <Copy size={12} />}
    </button>
  );
}

function Section({ icon: Icon, title, children, defaultOpen = false, badge }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bg-dark-card rounded-lg border border-dark-border">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 p-4 text-left hover:bg-dark-border/30 transition-colors"
      >
        <Icon size={18} className="text-blue-400" />
        <span className="text-sm font-medium text-dark-text flex-1">{title}</span>
        {badge && <span className="text-[10px] px-1.5 py-0.5 bg-purple-500/20 text-purple-400 rounded font-medium">{badge}</span>}
        {open ? <ChevronDown size={16} className="text-dark-muted" /> : <ChevronRight size={16} className="text-dark-muted" />}
      </button>
      {open && <div className="px-4 pb-4 space-y-4 border-t border-dark-border pt-4">{children}</div>}
    </div>
  );
}

function Field({ label, description, children }) {
  return (
    <div>
      <label className="block text-xs font-medium text-dark-muted mb-1">{label}</label>
      {description && <p className="text-xs text-dark-muted/70 mb-2">{description}</p>}
      {children}
    </div>
  );
}

function Input({ value, onChange, placeholder, type = 'text' }) {
  return (
    <input
      type={type}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text focus:outline-none focus:border-blue-500/50"
    />
  );
}

function TextArea({ value, onChange, placeholder, rows = 3 }) {
  return (
    <textarea
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={rows}
      className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 font-mono"
    />
  );
}

export default function SettingsPage() {
  const [settings, setSettings] = useState(null);
  const [setupStatus, setSetupStatus] = useState(null);
  const [webhookUrls, setWebhookUrls] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  useEffect(() => {
    fetchSettings().then(setSettings).catch(console.error);
    getSetupStatus().then(setSetupStatus).catch(console.error);
    getWebhookUrls().then(setWebhookUrls).catch(console.error);
  }, []);

  if (!settings) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-blue-400" />
      </div>
    );
  }

  function update(section, field, value) {
    setSettings((prev) => ({
      ...prev,
      [section]: { ...prev[section], [field]: value },
    }));
  }

  function updateNested(section, subsection, field, value) {
    setSettings((prev) => ({
      ...prev,
      [section]: {
        ...prev[section],
        [subsection]: { ...prev[section]?.[subsection], [field]: value },
      },
    }));
  }

  async function handleSave() {
    setSaving(true);
    setSaveMsg('');
    try {
      const updated = await saveSettings(settings);
      setSettings(updated);
      setSaveMsg('Settings saved!');
      getSetupStatus().then(setSetupStatus);
      setTimeout(() => setSaveMsg(''), 3000);
    } catch (e) {
      setSaveMsg(`Error: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  const s = settings;

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* Setup Progress */}
      {setupStatus && (
        <div className={`rounded-lg border p-4 ${setupStatus.ready ? 'border-green-500/30 bg-green-500/5' : 'border-yellow-500/30 bg-yellow-500/5'}`}>
          <div className="flex items-center justify-between mb-3">
            <h3 className={`text-sm font-medium ${setupStatus.ready ? 'text-green-400' : 'text-yellow-400'}`}>
              {setupStatus.ready ? 'All required integrations configured' : 'Setup in progress'}
            </h3>
            <span className="text-xs text-dark-muted">
              {setupStatus.configured_count}/{setupStatus.total_count} configured
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {Object.entries(setupStatus.steps).map(([key, step]) => (
              <div key={key} className="flex items-center gap-2 text-xs">
                {step.configured ? (
                  <CheckCircle2 size={14} className="text-green-400 shrink-0" />
                ) : (
                  <XCircle size={14} className={`shrink-0 ${step.required ? 'text-red-400' : 'text-dark-muted'}`} />
                )}
                <span className={step.configured ? 'text-dark-text' : 'text-dark-muted'}>
                  {step.name}
                  {step.required && !step.configured && <span className="text-red-400 ml-1">*</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Notion MCP */}
      <Section icon={Database} title="Notion MCP Server" defaultOpen={!setupStatus?.steps?.notion_mcp?.configured}>
        <Field label="MCP Server URL" description="URL of your Notion MCP server (e.g., http://localhost:3100/mcp)">
          <Input value={s.notion_mcp?.mcp_url} onChange={(v) => update('notion_mcp', 'mcp_url', v)} placeholder="http://localhost:3100/mcp" />
        </Field>
        <Field label="Auth Token" description="MCP authentication token from your server config">
          <SecretField value={s.notion_mcp?.auth_token} onChange={(v) => update('notion_mcp', 'auth_token', v)} placeholder="Your MCP auth token" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Incidents Database ID">
            <Input value={s.notion_mcp?.incidents_db_id} onChange={(v) => update('notion_mcp', 'incidents_db_id', v)} placeholder="Database ID" />
          </Field>
          <Field label="Runbooks Database ID">
            <Input value={s.notion_mcp?.runbooks_db_id} onChange={(v) => update('notion_mcp', 'runbooks_db_id', v)} placeholder="Database ID" />
          </Field>
          <Field label="Postmortems Database ID">
            <Input value={s.notion_mcp?.postmortems_db_id} onChange={(v) => update('notion_mcp', 'postmortems_db_id', v)} placeholder="Database ID" />
          </Field>
          <Field label="Services Database ID">
            <Input value={s.notion_mcp?.services_db_id} onChange={(v) => update('notion_mcp', 'services_db_id', v)} placeholder="Database ID" />
          </Field>
        </div>
        <Field label="Poll Interval (seconds)" description="How often to check Notion for human edits">
          <Input type="number" value={s.notion_mcp?.poll_interval_seconds} onChange={(v) => update('notion_mcp', 'poll_interval_seconds', parseInt(v) || 30)} />
        </Field>
        <TestButton type="notion" label="Connection" />
        <div className="text-xs text-dark-muted space-y-1 bg-dark-bg p-3 rounded">
          <p className="font-medium text-dark-text mb-1">How to set up:</p>
          <p>1. Install: <code className="bg-dark-border px-1 rounded">npx @notionhq/notion-mcp-server</code></p>
          <p>2. Create a Notion integration at <a href="https://www.notion.so/my-integrations" target="_blank" rel="noopener" className="text-blue-400 hover:underline">notion.so/my-integrations</a></p>
          <p>3. Share your workspace pages with the integration</p>
          <p>4. Run: <code className="bg-dark-border px-1 rounded">npx @notionhq/notion-mcp-server --transport http --port 3100</code></p>
        </div>
      </Section>

      {/* AI Agents */}
      <Section icon={Brain} title="AI Agents" defaultOpen={!setupStatus?.steps?.ai_agents?.configured}>
        <Field label="Primary LLM Provider" description="Which AI provider to use for agent reasoning">
          <select
            value={s.ai?.llm_provider || 'gemini'}
            onChange={(e) => update('ai', 'llm_provider', e.target.value)}
            className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text"
          >
            <option value="gemini">Google Gemini</option>
            <option value="anthropic">Anthropic Claude</option>
          </select>
        </Field>
        <Field label="Fallback Provider" description="If the primary provider fails, try this one (optional)">
          <select
            value={s.ai?.llm_fallback_provider || ''}
            onChange={(e) => update('ai', 'llm_fallback_provider', e.target.value)}
            className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text"
          >
            <option value="">None</option>
            <option value="anthropic">Anthropic Claude</option>
            <option value="gemini">Google Gemini</option>
          </select>
        </Field>

        {/* Gemini Config */}
        <div className="bg-dark-bg rounded p-3 space-y-3">
          <h4 className="text-xs font-medium text-dark-text flex items-center gap-2">
            Google Gemini
            {s.ai?.llm_provider === 'gemini' && <span className="text-[10px] px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">PRIMARY</span>}
            {s.ai?.llm_fallback_provider === 'gemini' && <span className="text-[10px] px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded">FALLBACK</span>}
          </h4>
          <Field label="Gemini API Key" description="Get your key from aistudio.google.com">
            <SecretField value={s.ai?.gemini_api_key} onChange={(v) => update('ai', 'gemini_api_key', v)} placeholder="AIza..." />
          </Field>
          <Field label="Model">
            <select
              value={s.ai?.gemini_model || 'gemini-2.0-flash'}
              onChange={(e) => update('ai', 'gemini_model', e.target.value)}
              className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text"
            >
              <option value="gemini-2.0-flash">Gemini 2.0 Flash (recommended)</option>
              <option value="gemini-2.5-flash-preview-05-20">Gemini 2.5 Flash (latest)</option>
              <option value="gemini-2.5-pro-preview-05-06">Gemini 2.5 Pro (most capable)</option>
            </select>
          </Field>
        </div>

        {/* Anthropic Config */}
        <div className="bg-dark-bg rounded p-3 space-y-3">
          <h4 className="text-xs font-medium text-dark-text flex items-center gap-2">
            Anthropic Claude
            {s.ai?.llm_provider === 'anthropic' && <span className="text-[10px] px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">PRIMARY</span>}
            {s.ai?.llm_fallback_provider === 'anthropic' && <span className="text-[10px] px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded">FALLBACK</span>}
          </h4>
          <Field label="Anthropic API Key" description="Get your key from console.anthropic.com">
            <SecretField value={s.ai?.anthropic_api_key} onChange={(v) => update('ai', 'anthropic_api_key', v)} placeholder="sk-ant-..." />
          </Field>
          <Field label="Model">
            <select
              value={s.ai?.anthropic_model || 'claude-sonnet-4-20250514'}
              onChange={(e) => update('ai', 'anthropic_model', e.target.value)}
              className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text"
            >
              <option value="claude-sonnet-4-20250514">Claude Sonnet 4 (recommended)</option>
              <option value="claude-opus-4-6">Claude Opus 4.6 (most capable)</option>
              <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5 (fastest)</option>
            </select>
          </Field>
        </div>

        <Field label="Max Concurrent Agents">
          <Input type="number" value={s.ai?.max_concurrent_agents} onChange={(v) => update('ai', 'max_concurrent_agents', parseInt(v) || 5)} />
        </Field>
        <TestButton type="ai" label="Connection" />
      </Section>

      {/* Slack Deep Integration */}
      <Section icon={MessageSquare} title="Slack Integration" badge="ENTERPRISE">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={s.slack?.enabled || false}
              onChange={(e) => update('slack', 'enabled', e.target.checked)}
              className="rounded border-dark-border"
            />
            <span className="text-sm text-dark-text">Enable Slack integration</span>
          </label>
        </div>
        {s.slack?.enabled && (
          <>
            <Field label="Bot Token (recommended)" description="For war rooms, interactive buttons, and thread updates. Create a Slack App at api.slack.com/apps">
              <SecretField value={s.slack?.bot_token} onChange={(v) => update('slack', 'bot_token', v)} placeholder="xoxb-..." />
            </Field>
            <Field label="Webhook URL (fallback)" description="Simple webhook for basic notifications if no bot token">
              <SecretField value={s.slack?.webhook_url} onChange={(v) => update('slack', 'webhook_url', v)} placeholder="https://hooks.slack.com/services/..." />
            </Field>
            <Field label="Default Channel">
              <Input value={s.slack?.channel} onChange={(v) => update('slack', 'channel', v)} placeholder="#incidents" />
            </Field>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={s.slack?.create_war_rooms ?? true}
                  onChange={(e) => update('slack', 'create_war_rooms', e.target.checked)}
                  className="rounded border-dark-border"
                />
                <span className="text-sm text-dark-text">Auto-create war room channels for P0/P1 incidents</span>
              </label>
            </div>
            <TestButton type="slack" label="Slack" />
            <div className="text-xs text-dark-muted space-y-1 bg-dark-bg p-3 rounded">
              <p className="font-medium text-dark-text mb-1">Bot Token Setup:</p>
              <p>1. Create a Slack App at api.slack.com/apps</p>
              <p>2. Add Bot Token Scopes: <code className="bg-dark-border px-1 rounded">channels:manage, chat:write, pins:write, commands</code></p>
              <p>3. Install the app to your workspace</p>
              <p>4. Copy the Bot User OAuth Token (xoxb-...)</p>
            </div>
          </>
        )}
      </Section>

      {/* GitHub Integration */}
      <Section icon={GitBranch} title="GitHub Integration" badge="ENTERPRISE">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={s.github?.enabled || false}
              onChange={(e) => update('github', 'enabled', e.target.checked)}
              className="rounded border-dark-border"
            />
            <span className="text-sm text-dark-text">Enable GitHub integration</span>
          </label>
        </div>
        {s.github?.enabled && (
          <>
            <Field label="Personal Access Token" description="GitHub PAT with repo, workflow, and deployments scopes">
              <SecretField value={s.github?.token} onChange={(v) => update('github', 'token', v)} placeholder="ghp_..." />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Organization" description="GitHub org name (optional if repos include owner)">
                <Input value={s.github?.org} onChange={(v) => update('github', 'org', v)} placeholder="my-org" />
              </Field>
              <Field label="Default Branch">
                <Input value={s.github?.default_branch} onChange={(v) => update('github', 'default_branch', v)} placeholder="main" />
              </Field>
            </div>
            <TestButton type="github" label="GitHub" />
            <div className="text-xs text-dark-muted space-y-1 bg-dark-bg p-3 rounded">
              <p className="font-medium text-dark-text mb-1">Features:</p>
              <p>- Deploy correlation: detect deployments before incidents</p>
              <p>- Commit linking: auto-link recent commits to timeline</p>
              <p>- Rollback PRs: create rollback PRs from remediation suggestions</p>
              <p>- CI/CD triggers: trigger GitHub Actions workflows</p>
            </div>
          </>
        )}
      </Section>

      {/* Jira Integration */}
      <Section icon={Ticket} title="Jira Integration" badge="ENTERPRISE">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={s.jira?.enabled || false}
              onChange={(e) => update('jira', 'enabled', e.target.checked)}
              className="rounded border-dark-border"
            />
            <span className="text-sm text-dark-text">Enable Jira integration</span>
          </label>
        </div>
        {s.jira?.enabled && (
          <>
            <Field label="Jira Cloud URL" description="Your Atlassian instance URL">
              <Input value={s.jira?.base_url} onChange={(v) => update('jira', 'base_url', v)} placeholder="https://your-org.atlassian.net" />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Email">
                <Input value={s.jira?.email} onChange={(v) => update('jira', 'email', v)} placeholder="you@company.com" />
              </Field>
              <Field label="API Token">
                <SecretField value={s.jira?.api_token} onChange={(v) => update('jira', 'api_token', v)} placeholder="Jira API token" />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Project Key">
                <Input value={s.jira?.project_key} onChange={(v) => update('jira', 'project_key', v)} placeholder="OPS" />
              </Field>
              <Field label="Default Issue Type">
                <select
                  value={s.jira?.default_issue_type || 'Task'}
                  onChange={(e) => update('jira', 'default_issue_type', e.target.value)}
                  className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text"
                >
                  <option value="Task">Task</option>
                  <option value="Bug">Bug</option>
                  <option value="Story">Story</option>
                </select>
              </Field>
            </div>
            <TestButton type="jira" label="Jira" />
          </>
        )}
      </Section>

      {/* Linear Integration */}
      <Section icon={Ticket} title="Linear Integration" badge="ENTERPRISE">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={s.linear?.enabled || false}
              onChange={(e) => update('linear', 'enabled', e.target.checked)}
              className="rounded border-dark-border"
            />
            <span className="text-sm text-dark-text">Enable Linear integration</span>
          </label>
        </div>
        {s.linear?.enabled && (
          <>
            <Field label="API Key" description="Generate at linear.app/settings/api">
              <SecretField value={s.linear?.api_key} onChange={(v) => update('linear', 'api_key', v)} placeholder="lin_api_..." />
            </Field>
            <Field label="Team ID" description="Linear team to create issues in">
              <Input value={s.linear?.team_id} onChange={(v) => update('linear', 'team_id', v)} placeholder="Team UUID" />
            </Field>
            <TestButton type="linear" label="Linear" />
          </>
        )}
      </Section>

      {/* AWS */}
      <Section icon={Cloud} title="AWS Cloud Integration" badge="ENTERPRISE">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={s.aws?.enabled || false}
              onChange={(e) => update('aws', 'enabled', e.target.checked)}
              className="rounded border-dark-border"
            />
            <span className="text-sm text-dark-text">Enable AWS integration</span>
          </label>
        </div>
        {s.aws?.enabled && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Access Key ID">
                <SecretField value={s.aws?.access_key_id} onChange={(v) => update('aws', 'access_key_id', v)} placeholder="AKIA..." />
              </Field>
              <Field label="Secret Access Key">
                <SecretField value={s.aws?.secret_access_key} onChange={(v) => update('aws', 'secret_access_key', v)} placeholder="Secret key" />
              </Field>
            </div>
            <Field label="Region">
              <Input value={s.aws?.region} onChange={(v) => update('aws', 'region', v)} placeholder="us-east-1" />
            </Field>
            <TestButton type="aws" label="AWS" />
            <div className="text-xs text-dark-muted space-y-1 bg-dark-bg p-3 rounded">
              <p className="font-medium text-dark-text mb-1">Features:</p>
              <p>- CloudWatch alarm monitoring</p>
              <p>- ECS service health & auto-restart</p>
              <p>- EC2 instance status checks</p>
              <p>- Auto-scaling actions</p>
            </div>
          </>
        )}
      </Section>

      {/* GCP */}
      <Section icon={Cloud} title="GCP Cloud Integration" badge="ENTERPRISE">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={s.gcp?.enabled || false}
              onChange={(e) => update('gcp', 'enabled', e.target.checked)}
              className="rounded border-dark-border"
            />
            <span className="text-sm text-dark-text">Enable GCP integration</span>
          </label>
        </div>
        {s.gcp?.enabled && (
          <>
            <Field label="Project ID">
              <Input value={s.gcp?.project_id} onChange={(v) => update('gcp', 'project_id', v)} placeholder="my-project-123" />
            </Field>
            <Field label="Service Account JSON" description="Paste the full service account key JSON">
              <TextArea value={s.gcp?.credentials_json} onChange={(v) => update('gcp', 'credentials_json', v)} placeholder='{"type": "service_account", ...}' rows={4} />
            </Field>
            <Field label="Region">
              <Input value={s.gcp?.region} onChange={(v) => update('gcp', 'region', v)} placeholder="us-central1" />
            </Field>
            <TestButton type="gcp" label="GCP" />
          </>
        )}
      </Section>

      {/* Azure */}
      <Section icon={Cloud} title="Azure Cloud Integration" badge="ENTERPRISE">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={s.azure?.enabled || false}
              onChange={(e) => update('azure', 'enabled', e.target.checked)}
              className="rounded border-dark-border"
            />
            <span className="text-sm text-dark-text">Enable Azure integration</span>
          </label>
        </div>
        {s.azure?.enabled && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Tenant ID">
                <Input value={s.azure?.tenant_id} onChange={(v) => update('azure', 'tenant_id', v)} placeholder="Azure AD tenant ID" />
              </Field>
              <Field label="Subscription ID">
                <Input value={s.azure?.subscription_id} onChange={(v) => update('azure', 'subscription_id', v)} placeholder="Subscription UUID" />
              </Field>
              <Field label="Client ID (App ID)">
                <Input value={s.azure?.client_id} onChange={(v) => update('azure', 'client_id', v)} placeholder="App registration client ID" />
              </Field>
              <Field label="Client Secret">
                <SecretField value={s.azure?.client_secret} onChange={(v) => update('azure', 'client_secret', v)} placeholder="Client secret" />
              </Field>
            </div>
            <TestButton type="azure" label="Azure" />
          </>
        )}
      </Section>

      {/* Webhook Sources */}
      <Section icon={Webhook} title="Alert Sources (Inbound Webhooks)">
        <p className="text-xs text-dark-muted mb-3">
          Configure your monitoring tools to send alerts to OpsLens. Copy the webhook URL below and paste it into your tool's webhook configuration.
        </p>

        {webhookUrls && Object.entries(webhookUrls.endpoints || {}).map(([key, ep]) => (
          <div key={key} className="bg-dark-bg rounded p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-dark-text capitalize">{key.replace('_', ' ')}</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs bg-dark-border/50 px-2 py-1 rounded text-blue-300 truncate">{ep.url}</code>
              <CopyButton text={ep.url} />
            </div>
            <p className="text-xs text-dark-muted">{ep.docs}</p>
            {['alertmanager', 'grafana', 'pagerduty'].includes(key) && (
              <div className="flex items-center gap-2">
                <SecretField
                  value={s.webhooks?.[key]?.secret || ''}
                  onChange={(v) => updateNested('webhooks', key, 'secret', v)}
                  placeholder={`${key} webhook secret`}
                />
                <button
                  onClick={async () => {
                    const { secret } = await generateSecret();
                    updateNested('webhooks', key, 'secret', secret);
                  }}
                  className="px-2 py-2 text-xs border border-dark-border rounded hover:bg-dark-border shrink-0"
                  title="Generate random secret"
                >
                  <RefreshCw size={12} />
                </button>
              </div>
            )}
          </div>
        ))}
      </Section>

      {/* Operational */}
      <Section icon={Settings} title="Operational Settings">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Dedup Window (seconds)" description="Ignore duplicate alerts within this window">
            <Input type="number" value={s.operational?.dedup_window_seconds} onChange={(v) => update('operational', 'dedup_window_seconds', parseInt(v) || 300)} />
          </Field>
          <Field label="Auto-Escalation (minutes)" description="Escalate unacknowledged incidents after this time">
            <Input type="number" value={s.operational?.auto_escalation_minutes} onChange={(v) => update('operational', 'auto_escalation_minutes', parseInt(v) || 30)} />
          </Field>
        </div>
        <Field label="Ticket Provider" description="Which ticket system to use for postmortem action items">
          <select
            value={s.operational?.ticket_provider || ''}
            onChange={(e) => update('operational', 'ticket_provider', e.target.value)}
            className="w-full bg-dark-bg border border-dark-border rounded px-3 py-2 text-sm text-dark-text"
          >
            <option value="">None</option>
            <option value="jira">Jira</option>
            <option value="linear">Linear</option>
          </select>
        </Field>
      </Section>

      {/* Save Button */}
      <div className="flex items-center gap-3 sticky bottom-0 bg-dark-bg border-t border-dark-border py-4 -mx-4 px-4">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : null}
          Save Settings
        </button>
        {saveMsg && (
          <span className={`text-sm ${saveMsg.startsWith('Error') ? 'text-red-400' : 'text-green-400'}`}>
            {saveMsg}
          </span>
        )}
      </div>
    </div>
  );
}
