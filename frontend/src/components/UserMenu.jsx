import React, { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { User, LogOut, Shield, Key, Users, ChevronDown } from 'lucide-react';
import { useAuthStore } from '../stores/authStore';

const ROLE_COLORS = {
  admin: 'bg-red-500/20 text-red-400 border-red-500/30',
  commander: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  responder: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  viewer: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
};

export default function UserMenu() {
  const { user, logout, hasRole } = useAuthStore();
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);
  const navigate = useNavigate();

  useEffect(() => {
    function handleClickOutside(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function handleEscape(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, []);

  if (!user) return null;

  const initials = (user.name || user.email || '?')
    .split(' ')
    .map((w) => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  const roleStyle = ROLE_COLORS[user.role] || ROLE_COLORS.viewer;

  function handleLogout() {
    logout();
    navigate('/login', { replace: true });
  }

  function handleNavigation(path) {
    setOpen(false);
    navigate(path);
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-dark-border/50 transition-colors"
        aria-label="User menu"
        aria-expanded={open}
        aria-haspopup="true"
      >
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-[10px] font-bold">
          {initials}
        </div>
        <span className="text-sm text-dark-text hidden sm:block max-w-[120px] truncate">
          {user.name || user.email}
        </span>
        <ChevronDown size={14} className={`text-dark-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-56 bg-dark-card border border-dark-border rounded-lg shadow-xl shadow-black/40 py-1 z-50 animate-fade-in"
          role="menu"
          aria-label="User options"
        >
          {/* User info */}
          <div className="px-3 py-2 border-b border-dark-border">
            <p className="text-sm font-medium text-dark-text truncate">{user.name || 'User'}</p>
            <p className="text-xs text-dark-muted truncate">{user.email}</p>
            <span className={`inline-block mt-1.5 text-[10px] px-1.5 py-0.5 rounded border font-medium capitalize ${roleStyle}`}>
              {user.role || 'viewer'}
            </span>
          </div>

          {/* Menu items */}
          <div className="py-1">
            <button
              onClick={() => handleNavigation('/profile')}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-dark-muted hover:text-dark-text hover:bg-dark-border/30 transition-colors"
              role="menuitem"
            >
              <User size={14} />
              Profile
            </button>
            <button
              onClick={() => handleNavigation('/change-password')}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-dark-muted hover:text-dark-text hover:bg-dark-border/30 transition-colors"
              role="menuitem"
            >
              <Key size={14} />
              Change Password
            </button>
            {hasRole('admin') && (
              <button
                onClick={() => handleNavigation('/users')}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-dark-muted hover:text-dark-text hover:bg-dark-border/30 transition-colors"
                role="menuitem"
              >
                <Users size={14} />
                User Management
              </button>
            )}
          </div>

          <div className="border-t border-dark-border py-1">
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-red-400 hover:bg-red-500/10 transition-colors"
              role="menuitem"
            >
              <LogOut size={14} />
              Sign Out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
