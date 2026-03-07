import React, { useState, useRef, useEffect } from 'react';
import {
  Terminal, Send, Trash2, Loader2, ChevronUp, ChevronDown,
  Search, ArrowRight, AlertTriangle, Bell, BookOpen, MessageCircle,
} from 'lucide-react';
import { api } from '../api/client';

// --- Action parser: extract [ACTION:type:detail] markers ---

function parseActions(text) {
  const actionRegex = /\[ACTION:(\w+)(?::([^\]]*))?\]\s*(.*)/g;
  const actions = [];
  let match;
  while ((match = actionRegex.exec(text)) !== null) {
    actions.push({
      type: match[1],
      param: match[2] || '',
      label: match[3].trim(),
    });
  }
  // Strip action lines from display text
  const cleanText = text.replace(/\[ACTION:\w+(?::[^\]]*)?\]\s*.*/g, '').trim();
  return { cleanText, actions };
}

const ACTION_STYLES = {
  search: { icon: Search, color: 'blue', bg: 'bg-blue-500/10 border-blue-500/30 text-blue-300 hover:bg-blue-500/20' },
  transition: { icon: ArrowRight, color: 'amber', bg: 'bg-amber-500/10 border-amber-500/30 text-amber-300 hover:bg-amber-500/20' },
  escalate: { icon: AlertTriangle, color: 'red', bg: 'bg-red-500/10 border-red-500/30 text-red-300 hover:bg-red-500/20' },
  notify: { icon: Bell, color: 'purple', bg: 'bg-purple-500/10 border-purple-500/30 text-purple-300 hover:bg-purple-500/20' },
  runbook: { icon: BookOpen, color: 'emerald', bg: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20' },
  ask: { icon: MessageCircle, color: 'cyan', bg: 'bg-cyan-500/10 border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/20' },
};

function ActionButtons({ actions, onAction }) {
  if (!actions.length) return null;
  return (
    <div className="mt-2 pt-2 border-t border-dark-border/50 space-y-1.5">
      <p className="text-[10px] font-semibold text-dark-muted uppercase tracking-wider">Actions</p>
      <div className="flex flex-wrap gap-1.5">
        {actions.map((action, i) => {
          const style = ACTION_STYLES[action.type] || ACTION_STYLES.search;
          const Icon = style.icon;
          return (
            <button
              key={i}
              onClick={() => onAction(action)}
              className={`flex items-center gap-1.5 px-2 py-1 rounded-md border text-[10px] font-medium transition-colors ${style.bg}`}
            >
              <Icon size={10} />
              {action.label || action.param || action.type}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// --- Markdown renderer ---

function MarkdownText({ text }) {
  const lines = text.split('\n');
  return (
    <div className="space-y-1">
      {lines.map((line, i) => {
        // Warning/caution lines
        if (/^[!⚠️🚨]/.test(line.trim())) {
          return (
            <div key={i} className="flex items-start gap-1.5 text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-1 mt-1">
              <AlertTriangle size={10} className="mt-0.5 shrink-0" />
              <span>{line.replace(/^[!⚠️🚨]\s*/, '')}</span>
            </div>
          );
        }
        if (line.startsWith('### ')) {
          return <h4 key={i} className="text-xs font-semibold text-emerald-400 mt-2">{line.slice(4)}</h4>;
        }
        if (line.startsWith('## ')) {
          return <h3 key={i} className="text-sm font-semibold text-dark-text mt-2">{line.slice(3)}</h3>;
        }
        if (line.startsWith('# ')) {
          return <h2 key={i} className="text-sm font-bold text-dark-text mt-2">{line.slice(2)}</h2>;
        }
        if (line.startsWith('- ') || line.startsWith('* ')) {
          return <li key={i} className="text-xs text-dark-text ml-3 list-disc">{renderInline(line.slice(2))}</li>;
        }
        if (/^\d+\.\s/.test(line)) {
          const content = line.replace(/^\d+\.\s/, '');
          return <li key={i} className="text-xs text-dark-text ml-3 list-decimal">{renderInline(content)}</li>;
        }
        if (line.startsWith('```')) return null;
        if (line.startsWith('>')) {
          return <blockquote key={i} className="text-xs text-dark-muted border-l-2 border-emerald-500/30 pl-2 ml-1">{line.slice(1).trim()}</blockquote>;
        }
        if (line.trim() === '') return <div key={i} className="h-1" />;
        return <p key={i} className="text-xs text-dark-text leading-relaxed">{renderInline(line)}</p>;
      })}
    </div>
  );
}

function renderInline(text) {
  const parts = [];
  let remaining = text;
  let key = 0;
  while (remaining.length > 0) {
    const boldMatch = remaining.match(/\*\*(.*?)\*\*/);
    const codeMatch = remaining.match(/`([^`]+)`/);

    let firstMatch = null;
    let matchType = null;
    if (boldMatch && (!codeMatch || boldMatch.index <= codeMatch.index)) {
      firstMatch = boldMatch;
      matchType = 'bold';
    } else if (codeMatch) {
      firstMatch = codeMatch;
      matchType = 'code';
    }

    if (!firstMatch) {
      parts.push(<span key={key++}>{remaining}</span>);
      break;
    }

    if (firstMatch.index > 0) {
      parts.push(<span key={key++}>{remaining.slice(0, firstMatch.index)}</span>);
    }

    if (matchType === 'bold') {
      parts.push(<strong key={key++} className="font-semibold text-dark-text">{firstMatch[1]}</strong>);
    } else {
      parts.push(<code key={key++} className="px-1 py-0.5 bg-dark-border rounded text-[10px] font-mono text-emerald-300">{firstMatch[1]}</code>);
    }

    remaining = remaining.slice(firstMatch.index + firstMatch[0].length);
  }
  return parts;
}

// --- Main component ---

const SUGGESTIONS = [
  { text: 'What should I do next?', icon: ArrowRight },
  { text: 'What is the likely root cause?', icon: Search },
  { text: 'Find the runbook for this service', icon: BookOpen },
  { text: 'Compare with past incidents', icon: MessageCircle },
  { text: 'Who should I escalate to?', icon: Bell },
  { text: 'What changed recently in this service?', icon: AlertTriangle },
];

export default function CommandPanel({ incidentId }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState('');
  const [expanded, setExpanded] = useState(true);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (expanded) inputRef.current?.focus();
  }, [expanded]);

  useEffect(() => {
    setMessages([]);
    setConversationId('');
  }, [incidentId]);

  async function sendMessage(msg) {
    if (!msg.trim() || loading) return;
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: msg }]);
    setLoading(true);

    try {
      const res = await api.commanderQuery(incidentId, msg, conversationId);
      setConversationId(res.conversation_id);
      const { cleanText, actions } = parseActions(res.response);
      setMessages((prev) => [...prev, {
        role: 'assistant',
        content: cleanText,
        actions,
      }]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${err.message}`, error: true, actions: [] },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e?.preventDefault();
    sendMessage(input);
  }

  function handleAction(action) {
    if (action.type === 'search' || action.type === 'ask') {
      sendMessage(action.param || action.label);
    } else if (action.type === 'transition') {
      // Suggest but don't execute — send as follow-up question
      sendMessage(`How do I safely transition to ${action.param}? What should I verify first?`);
    } else if (action.type === 'escalate') {
      sendMessage('Walk me through the escalation process. Who should I contact and what context should I provide?');
    } else if (action.type === 'notify') {
      sendMessage(`What context should I include when notifying ${action.param}?`);
    } else if (action.type === 'runbook') {
      sendMessage(`Give me the detailed steps for: ${action.param || action.label}`);
    }
  }

  function handleClear() {
    setMessages([]);
    setConversationId('');
    api.clearCommanderHistory(incidentId).catch(() => {});
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div className="border-t border-dark-border bg-dark-card">
      {/* Toggle bar */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-2 hover:bg-dark-border/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Terminal size={14} className="text-emerald-400" />
          <span className="text-xs font-semibold text-dark-text">Incident Commander</span>
          <span className="text-[10px] text-dark-muted">AI co-pilot</span>
          {messages.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400">
              {messages.filter((m) => m.role === 'user').length} queries
            </span>
          )}
        </div>
        {expanded ? <ChevronDown size={14} className="text-dark-muted" /> : <ChevronUp size={14} className="text-dark-muted" />}
      </button>

      {expanded && (
        <div className="flex flex-col" style={{ maxHeight: '400px' }}>
          {/* Messages area */}
          <div className="flex-1 overflow-y-auto px-4 py-2 space-y-3" style={{ maxHeight: '300px' }}>
            {messages.length === 0 && (
              <div className="py-3">
                <p className="text-[11px] text-dark-muted mb-1">
                  I have full context of this incident and all agent analyses.
                </p>
                <p className="text-[11px] text-dark-muted mb-3">
                  Ask operational questions — I'll search Notion, runbooks, and past incidents.
                </p>
                <div className="grid grid-cols-2 gap-1.5">
                  {SUGGESTIONS.map((s) => {
                    const Icon = s.icon;
                    return (
                      <button
                        key={s.text}
                        onClick={() => sendMessage(s.text)}
                        className="flex items-center gap-1.5 text-[10px] px-2.5 py-1.5 rounded-md border border-dark-border text-dark-muted hover:border-emerald-500/40 hover:text-emerald-400 transition-colors text-left"
                      >
                        <Icon size={10} className="shrink-0" />
                        {s.text}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[90%] rounded-lg px-3 py-2 ${
                    msg.role === 'user'
                      ? 'bg-blue-600/20 border border-blue-500/30'
                      : msg.error
                      ? 'bg-red-500/10 border border-red-500/30'
                      : 'bg-dark-bg border border-dark-border'
                  }`}
                >
                  {msg.role === 'user' ? (
                    <p className="text-xs text-blue-200">{msg.content}</p>
                  ) : (
                    <>
                      <MarkdownText text={msg.content} />
                      <ActionButtons actions={msg.actions || []} onAction={handleAction} />
                    </>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="bg-dark-bg border border-dark-border rounded-lg px-3 py-2 flex items-center gap-2">
                  <Loader2 size={12} className="animate-spin text-emerald-400" />
                  <span className="text-xs text-dark-muted">Searching Notion & analyzing...</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input bar */}
          <div className="px-4 py-2 border-t border-dark-border">
            <form onSubmit={handleSubmit} className="flex gap-2">
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask your incident co-pilot..."
                disabled={loading}
                className="flex-1 bg-dark-bg border border-dark-border rounded px-3 py-1.5 text-xs text-dark-text placeholder:text-dark-muted focus:outline-none focus:border-emerald-500/50 disabled:opacity-50"
              />
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={handleClear}
                  className="p-1.5 text-dark-muted hover:text-red-400 transition-colors"
                  title="Clear conversation"
                >
                  <Trash2 size={14} />
                </button>
              )}
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded text-xs font-medium transition-colors flex items-center gap-1"
              >
                <Send size={12} />
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
