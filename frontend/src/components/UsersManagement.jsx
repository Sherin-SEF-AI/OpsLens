import React, { useState, useEffect } from 'react';
import {
  Users, Loader2, Shield, Mail, Clock, Search,
  UserPlus, ChevronDown, CheckCircle2, XCircle, X, AlertCircle,
} from 'lucide-react';
import { api } from '../api/client';

const ROLES = ['viewer', 'responder', 'commander', 'admin'];

const ROLE_COLORS = {
  admin: 'text-red-400 bg-red-500/10 border-red-500/20',
  commander: 'text-purple-400 bg-purple-500/10 border-purple-500/20',
  responder: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
  viewer: 'text-gray-400 bg-gray-500/10 border-gray-500/20',
};

function formatDate(dateStr) {
  if (!dateStr) return 'Never';
  return new Date(dateStr).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function UsersManagement() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [inviteRole, setInviteRole] = useState('responder');
  const [inviteLoading, setInviteLoading] = useState(false);
  const [error, setError] = useState('');
  const [actionLoading, setActionLoading] = useState(null);

  useEffect(() => {
    loadUsers();
  }, []);

  async function loadUsers() {
    setLoading(true);
    try {
      const data = await api.getUsers();
      setUsers(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleRoleChange(userId, newRole) {
    setActionLoading(userId);
    try {
      await api.updateUserRole(userId, newRole);
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, role: newRole } : u))
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleToggleStatus(userId, currentActive) {
    setActionLoading(userId);
    try {
      await api.updateUserStatus(userId, !currentActive);
      setUsers((prev) =>
        prev.map((u) =>
          u.id === userId ? { ...u, active: !currentActive } : u
        )
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleInvite(e) {
    e.preventDefault();
    setInviteLoading(true);
    setError('');
    try {
      await api.inviteUser(inviteEmail, inviteName, inviteRole);
      setShowInvite(false);
      setInviteEmail('');
      setInviteName('');
      setInviteRole('responder');
      await loadUsers();
    } catch (err) {
      setError(err.message);
    } finally {
      setInviteLoading(false);
    }
  }

  const filteredUsers = users.filter((u) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (u.name || '').toLowerCase().includes(q) ||
      (u.email || '').toLowerCase().includes(q) ||
      (u.role || '').toLowerCase().includes(q)
    );
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" aria-label="Loading users">
        <Loader2 size={24} className="animate-spin text-blue-400" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-gradient-to-br from-purple-500/20 to-pink-500/20 border border-purple-500/20">
            <Users size={20} className="text-purple-400" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-dark-text">User Management</h2>
            <p className="text-xs text-dark-muted">{users.length} user{users.length !== 1 ? 's' : ''}</p>
          </div>
        </div>
        <button
          onClick={() => setShowInvite(true)}
          className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
          aria-label="Invite new user"
        >
          <UserPlus size={14} />
          Invite User
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs" role="alert">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <span>{error}</span>
          <button onClick={() => setError('')} className="ml-auto" aria-label="Dismiss error">
            <X size={12} />
          </button>
        </div>
      )}

      {/* Search */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-dark-muted" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search users..."
          className="w-full bg-dark-bg border border-dark-border rounded-lg pl-10 pr-3 py-2.5 text-sm text-dark-text placeholder:text-dark-muted/50 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
          aria-label="Search users"
        />
      </div>

      {/* Users table */}
      <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="Users list">
            <thead>
              <tr className="border-b border-dark-border">
                <th className="text-left px-4 py-3 text-xs font-medium text-dark-muted">User</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-dark-muted">Role</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-dark-muted">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-dark-muted">Last Login</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-dark-muted">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-dark-border">
              {filteredUsers.map((u) => {
                const roleStyle = ROLE_COLORS[u.role] || ROLE_COLORS.viewer;
                const isActive = u.active !== false;
                return (
                  <tr key={u.id} className="hover:bg-dark-border/20 transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-[10px] font-bold shrink-0">
                          {(u.name || u.email || '?')
                            .split(' ')
                            .map((w) => w[0])
                            .join('')
                            .toUpperCase()
                            .slice(0, 2)}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-dark-text truncate">{u.name || 'Unnamed'}</p>
                          <p className="text-xs text-dark-muted truncate flex items-center gap-1">
                            <Mail size={10} />
                            {u.email}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={u.role || 'viewer'}
                        onChange={(e) => handleRoleChange(u.id, e.target.value)}
                        disabled={actionLoading === u.id}
                        className={`text-[11px] px-2 py-1 rounded border font-medium capitalize bg-transparent cursor-pointer ${roleStyle} disabled:opacity-50`}
                        aria-label={`Change role for ${u.name || u.email}`}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r} className="bg-dark-bg text-dark-text">
                            {r}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`flex items-center gap-1.5 text-xs ${isActive ? 'text-green-400' : 'text-red-400'}`}>
                        {isActive ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                        {isActive ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-dark-muted flex items-center gap-1">
                        <Clock size={10} />
                        {formatDate(u.last_login)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleToggleStatus(u.id, isActive)}
                        disabled={actionLoading === u.id}
                        className={`text-xs px-2.5 py-1 rounded border transition-colors disabled:opacity-50 ${
                          isActive
                            ? 'text-red-400 border-red-500/20 hover:bg-red-500/10'
                            : 'text-green-400 border-green-500/20 hover:bg-green-500/10'
                        }`}
                        aria-label={isActive ? `Deactivate ${u.name || u.email}` : `Activate ${u.name || u.email}`}
                      >
                        {actionLoading === u.id ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : isActive ? 'Deactivate' : 'Activate'}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {filteredUsers.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-sm text-dark-muted">
                    {searchQuery ? 'No users match your search' : 'No users found'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Invite modal */}
      {showInvite && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div
            className="bg-dark-card border border-dark-border rounded-xl w-full max-w-md p-6 shadow-2xl shadow-black/40 animate-fade-in"
            role="dialog"
            aria-label="Invite user"
            aria-modal="true"
          >
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-base font-semibold text-dark-text">Invite User</h2>
              <button onClick={() => setShowInvite(false)} className="p-1 hover:bg-dark-border rounded-lg" aria-label="Close">
                <X size={16} className="text-dark-muted" />
              </button>
            </div>
            <form onSubmit={handleInvite} className="space-y-4">
              <div>
                <label htmlFor="invite-name" className="block text-xs font-medium text-dark-muted mb-1">Name</label>
                <input
                  id="invite-name"
                  type="text"
                  value={inviteName}
                  onChange={(e) => setInviteName(e.target.value)}
                  required
                  placeholder="Full name"
                  className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                />
              </div>
              <div>
                <label htmlFor="invite-email" className="block text-xs font-medium text-dark-muted mb-1">Email</label>
                <input
                  id="invite-email"
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  required
                  placeholder="user@company.com"
                  className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                />
              </div>
              <div>
                <label htmlFor="invite-role" className="block text-xs font-medium text-dark-muted mb-1">Role</label>
                <select
                  id="invite-role"
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2.5 text-sm text-dark-text"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</option>
                  ))}
                </select>
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button
                  type="button"
                  onClick={() => setShowInvite(false)}
                  className="px-4 py-2 text-sm text-dark-muted hover:text-dark-text rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={inviteLoading}
                  className="flex items-center gap-2 px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
                >
                  {inviteLoading && <Loader2 size={14} className="animate-spin" />}
                  Send Invite
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
