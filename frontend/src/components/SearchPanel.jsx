import React, { useState, useRef, useEffect } from 'react';
import {
  Search,
  Loader2,
  AlertTriangle,
  Database,
  BookOpen,
  ExternalLink,
  Server,
  Clock,
  X,
  Copy,
  Check,
  ChevronDown,
  ChevronUp,
  Sparkles,
  ArrowRight,
  History,
} from 'lucide-react';
import { api } from '../api/client';

const SCOPES = [
  { id: 'all', label: 'All Sources' },
  { id: 'incidents', label: 'Incidents' },
  { id: 'notion', label: 'Notion' },
  { id: 'knowledge_base', label: 'Knowledge Base' },
];

const QUICK_SEARCHES = [
  'high CPU usage',
  'database connection timeout',
  'deployment failure',
  'memory leak',
  'API latency spike',
];

const severityColor = {
  'P0-Critical': 'text-red-400 bg-red-500/10 border-red-500/20',
  'P1-High': 'text-orange-400 bg-orange-500/10 border-orange-500/20',
  'P2-Medium': 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
  'P3-Low': 'text-blue-400 bg-blue-500/10 border-blue-500/20',
};

function ScoreBar({ score }) {
  const pct = Math.round(score * 100);
  const color = pct >= 80 ? 'bg-emerald-400' : pct >= 50 ? 'bg-yellow-400' : 'bg-dark-muted';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1 rounded-full bg-dark-border overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-dark-muted">{pct}%</span>
    </div>
  );
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="p-0.5 hover:bg-dark-border/50 rounded transition-colors"
      title="Copy ID"
    >
      {copied ? <Check size={10} className="text-emerald-400" /> : <Copy size={10} className="text-dark-muted" />}
    </button>
  );
}

function IncidentResult({ item, onSelect, onFindSimilar }) {
  return (
    <div className="bg-dark-card/50 border border-dark-border rounded-lg p-3 hover:border-dark-muted/40 transition-all group">
      <div className="flex items-center gap-2 mb-1">
        <AlertTriangle size={12} className="text-dark-muted" />
        <span className="text-[10px] font-mono text-dark-muted">{item.incident_id}</span>
        <CopyButton text={item.incident_id} />
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${
          severityColor[item.severity] || 'text-dark-muted border-dark-border'
        }`}>{item.severity?.split('-')[0]}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-dark-border text-dark-muted">
          {item.status}
        </span>
        <div className="ml-auto">
          <ScoreBar score={item.score} />
        </div>
      </div>
      <h4 className="text-xs font-medium text-dark-text truncate">{item.title}</h4>
      <p className="text-[11px] text-dark-muted mt-1 truncate">{item.description}</p>
      <div className="flex items-center gap-2 mt-2 text-[10px] text-dark-muted">
        <Server size={10} />
        <span>{item.service}</span>
        <div className="ml-auto flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={() => onFindSimilar(item.title)}
            className="flex items-center gap-1 px-2 py-0.5 rounded bg-dark-border/50 hover:bg-dark-border text-dark-muted hover:text-dark-text transition-colors"
          >
            <Sparkles size={9} /> Find Similar
          </button>
          <button
            onClick={() => onSelect?.(item.incident_id)}
            className="flex items-center gap-1 px-2 py-0.5 rounded bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 transition-colors"
          >
            View <ArrowRight size={9} />
          </button>
        </div>
      </div>
    </div>
  );
}

function NotionResult({ item }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="bg-dark-card/50 border border-dark-border rounded-lg p-3">
      <div className="flex items-center gap-2 mb-1">
        <Database size={12} className="text-blue-400" />
        <span className="text-[10px] text-blue-400 uppercase tracking-wider">Notion</span>
        {item.type && item.type !== 'raw' && (
          <span className="text-[10px] text-dark-muted capitalize">{item.parent_type}</span>
        )}
        {item.url && (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300 transition-colors"
          >
            <ExternalLink size={10} /> Open
          </a>
        )}
      </div>
      <h4 className="text-xs font-medium text-dark-text">{item.title}</h4>
      {item.last_edited && (
        <div className="flex items-center gap-1 mt-1 text-[10px] text-dark-muted">
          <Clock size={10} />
          {timeAgo(item.last_edited)}
        </div>
      )}
      {item.raw_preview && (
        <>
          <p className={`text-[11px] text-dark-muted mt-1.5 ${expanded ? '' : 'line-clamp-2'}`}>{item.raw_preview}</p>
          {item.raw_preview.length > 120 && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-0.5 mt-1 text-[10px] text-blue-400 hover:text-blue-300"
            >
              {expanded ? <><ChevronUp size={10} /> Less</> : <><ChevronDown size={10} /> More</>}
            </button>
          )}
        </>
      )}
      {item.error && (
        <p className="text-[11px] text-red-400 mt-1">{item.error}</p>
      )}
    </div>
  );
}

function KBResult({ item }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="bg-dark-card/50 border border-dark-border rounded-lg p-3">
      <div className="flex items-center gap-2 mb-1">
        <BookOpen size={12} className="text-emerald-400" />
        <span className="text-[10px] text-emerald-400 uppercase tracking-wider">Knowledge Base</span>
        {item.doc_type && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400/80 capitalize">{item.doc_type}</span>
        )}
        <div className="ml-auto">
          <ScoreBar score={item.score} />
        </div>
      </div>
      <h4 className="text-xs font-medium text-dark-text">{item.title}</h4>
      {item.content && (
        <>
          <p className={`text-[11px] text-dark-muted mt-1.5 ${expanded ? 'whitespace-pre-wrap' : 'line-clamp-2'}`}>{item.content}</p>
          {item.content.length > 120 && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-0.5 mt-1 text-[10px] text-emerald-400 hover:text-emerald-300"
            >
              {expanded ? <><ChevronUp size={10} /> Less</> : <><ChevronDown size={10} /> More</>}
            </button>
          )}
        </>
      )}
      {item.error && (
        <p className="text-[11px] text-red-400 mt-1">{item.error}</p>
      )}
    </div>
  );
}

function timeAgo(dateStr) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

const HISTORY_KEY = 'opslens_search_history';

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
  } catch { return []; }
}

function saveHistory(history) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 8)));
}

export default function SearchPanel({ onSelectIncident }) {
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState('all');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState(loadHistory);
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function doSearch(q, s) {
    const trimmed = (q || query).trim();
    const sc = s || scope;
    if (!trimmed) return;
    setQuery(trimmed);
    setLoading(true);
    setResults(null);
    try {
      const res = await api.search(trimmed, sc);
      setResults(res);
      // Save to history
      const updated = [trimmed, ...history.filter((h) => h !== trimmed)].slice(0, 8);
      setHistory(updated);
      saveHistory(updated);
    } catch (err) {
      setResults({ error: err.message, total_results: 0, incidents: [], notion: [], knowledge_base: [] });
    } finally {
      setLoading(false);
    }
  }

  function handleSearch(e) {
    e?.preventDefault();
    doSearch();
  }

  function handleClear() {
    setQuery('');
    setResults(null);
    inputRef.current?.focus();
  }

  function handleFindSimilar(title) {
    setQuery(title);
    doSearch(title);
  }

  function removeHistoryItem(item) {
    const updated = history.filter((h) => h !== item);
    setHistory(updated);
    saveHistory(updated);
  }

  const hasResults = results && results.total_results > 0;
  const hasError = results?.error;
  const totalBySource = {
    incidents: results?.incidents?.length || 0,
    notion: results?.notion?.length || 0,
    kb: results?.knowledge_base?.length || 0,
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-gradient-to-br from-emerald-500/20 to-cyan-500/20 border border-emerald-500/20">
          <Search size={20} className="text-emerald-400" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-dark-text">Semantic Search</h2>
          <p className="text-xs text-dark-muted">Search across incidents, Notion workspace, and knowledge base</p>
        </div>
      </div>

      {/* Search input */}
      <form onSubmit={handleSearch} className="space-y-3">
        <div className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-dark-muted" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search for incidents, runbooks, services..."
            className="w-full bg-dark-bg border border-dark-border rounded-lg pl-10 pr-10 py-2.5 text-sm text-dark-text placeholder:text-dark-muted/60 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
          />
          {query && (
            <button
              type="button"
              onClick={handleClear}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-dark-muted hover:text-dark-text"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Scope tabs + search button */}
        <div className="flex items-center gap-2">
          {SCOPES.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setScope(s.id)}
              className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
                scope === s.id
                  ? 'bg-blue-600/20 text-blue-400'
                  : 'text-dark-muted hover:text-dark-text hover:bg-dark-border/50'
              }`}
            >
              {s.label}
            </button>
          ))}
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="ml-auto px-4 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-xs font-medium transition-colors disabled:opacity-40 flex items-center gap-1.5"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
            Search
          </button>
        </div>
      </form>

      {/* Results */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={24} className="animate-spin text-blue-400" />
        </div>
      )}

      {hasError && (
        <div className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg text-xs text-red-400">
          {results.error}
        </div>
      )}

      {results && !loading && (
        <div className="space-y-4">
          {/* Summary bar */}
          <div className="flex items-center gap-2 p-2.5 bg-dark-card/30 rounded-lg border border-dark-border">
            <span className="text-xs text-dark-text font-medium">{results.total_results} result{results.total_results !== 1 ? 's' : ''}</span>
            <span className="text-[10px] text-dark-muted">for &ldquo;{query}&rdquo;</span>
            <div className="ml-auto flex items-center gap-1.5">
              {totalBySource.incidents > 0 && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-orange-500/10 text-[10px] text-orange-400">
                  <AlertTriangle size={9} /> {totalBySource.incidents}
                </span>
              )}
              {totalBySource.notion > 0 && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-500/10 text-[10px] text-blue-400">
                  <Database size={9} /> {totalBySource.notion}
                </span>
              )}
              {totalBySource.kb > 0 && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/10 text-[10px] text-emerald-400">
                  <BookOpen size={9} /> {totalBySource.kb}
                </span>
              )}
            </div>
          </div>

          {/* Incident results */}
          {results.incidents?.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium text-dark-muted flex items-center gap-2">
                <AlertTriangle size={12} /> Incidents
              </h3>
              {results.incidents.map((item) => (
                <IncidentResult
                  key={item.incident_id}
                  item={item}
                  onSelect={onSelectIncident}
                  onFindSimilar={handleFindSimilar}
                />
              ))}
            </div>
          )}

          {/* Notion results */}
          {results.notion?.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium text-dark-muted flex items-center gap-2">
                <Database size={12} /> Notion Workspace
              </h3>
              {results.notion.map((item, i) => (
                <NotionResult key={item.page_id || i} item={item} />
              ))}
            </div>
          )}

          {/* KB results */}
          {results.knowledge_base?.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium text-dark-muted flex items-center gap-2">
                <BookOpen size={12} /> Knowledge Base
              </h3>
              {results.knowledge_base.map((item, i) => (
                <KBResult key={i} item={item} />
              ))}
            </div>
          )}

          {/* Empty state */}
          {!hasResults && !hasError && (
            <div className="text-center py-8 text-dark-muted">
              <Search size={28} className="mx-auto mb-2 opacity-20" />
              <p className="text-sm">No results found for &ldquo;{query}&rdquo;</p>
              <p className="text-xs mt-1">Try different keywords or broaden the search scope</p>
            </div>
          )}
        </div>
      )}

      {/* Initial empty state with quick searches and history */}
      {!results && !loading && (
        <div className="space-y-5">
          {/* Quick searches */}
          <div>
            <p className="text-[10px] font-semibold text-dark-muted/60 uppercase tracking-widest mb-2">Quick searches</p>
            <div className="flex flex-wrap gap-1.5">
              {QUICK_SEARCHES.map((q) => (
                <button
                  key={q}
                  onClick={() => { setQuery(q); doSearch(q); }}
                  className="px-2.5 py-1 text-[11px] rounded-md bg-dark-card/50 border border-dark-border text-dark-muted hover:text-dark-text hover:border-dark-muted/40 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>

          {/* Recent searches */}
          {history.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-dark-muted/60 uppercase tracking-widest mb-2">Recent</p>
              <div className="space-y-0.5">
                {history.map((h) => (
                  <div key={h} className="flex items-center gap-2 group">
                    <button
                      onClick={() => { setQuery(h); doSearch(h); }}
                      className="flex-1 flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs text-dark-muted hover:text-dark-text hover:bg-dark-card/40 transition-colors text-left"
                    >
                      <History size={11} className="shrink-0" />
                      <span className="truncate">{h}</span>
                    </button>
                    <button
                      onClick={() => removeHistoryItem(h)}
                      className="p-1 opacity-0 group-hover:opacity-100 text-dark-muted hover:text-red-400 transition-all"
                    >
                      <X size={10} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Empty hint */}
          <div className="text-center pt-4 text-dark-muted">
            <div className="w-14 h-14 rounded-2xl bg-emerald-500/5 border border-emerald-500/10 flex items-center justify-center mx-auto mb-3">
              <Search size={24} className="text-emerald-400/40" />
            </div>
            <p className="text-sm font-medium text-dark-text/60">Search across all sources</p>
            <p className="text-xs mt-1">Incidents, Notion pages, runbooks, and past resolutions</p>
          </div>
        </div>
      )}
    </div>
  );
}
