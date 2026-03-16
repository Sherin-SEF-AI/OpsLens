import { create } from 'zustand';

const TOKEN_KEY = 'opslens_token';
const REFRESH_KEY = 'opslens_refresh_token';
const USER_KEY = 'opslens_user';

export const useAuthStore = create((set, get) => ({
  user: null,
  token: null,
  refreshToken: null,
  isAuthenticated: false,
  isLoading: true,
  error: null,
  _refreshInterval: null,

  _setTokens(token, refreshToken) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
    if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
    else localStorage.removeItem(REFRESH_KEY);
    set({ token, refreshToken });
  },

  _setUser(user) {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);
    set({ user, isAuthenticated: !!user });
  },

  _startRefreshTimer() {
    const state = get();
    if (state._refreshInterval) clearInterval(state._refreshInterval);
    // Refresh token every 14 minutes (assuming 15 min expiry)
    const interval = setInterval(() => {
      const s = get();
      if (s.token && s.refreshToken) {
        s.refreshAccessToken().catch(() => {});
      }
    }, 14 * 60 * 1000);
    set({ _refreshInterval: interval });
  },

  _stopRefreshTimer() {
    const state = get();
    if (state._refreshInterval) {
      clearInterval(state._refreshInterval);
      set({ _refreshInterval: null });
    }
  },

  async login(email, password) {
    set({ isLoading: true, error: null });
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `Login failed: ${res.status}`);
      }
      const data = await res.json();
      get()._setTokens(data.access_token, data.refresh_token);
      get()._setUser(data.user);
      get()._startRefreshTimer();
      set({ isLoading: false });
      return data.user;
    } catch (err) {
      set({ isLoading: false, error: err.message });
      throw err;
    }
  },

  async register(email, password, name) {
    set({ isLoading: true, error: null });
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, name }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `Registration failed: ${res.status}`);
      }
      const data = await res.json();
      get()._setTokens(data.access_token, data.refresh_token);
      get()._setUser(data.user);
      get()._startRefreshTimer();
      set({ isLoading: false });
      return data.user;
    } catch (err) {
      set({ isLoading: false, error: err.message });
      throw err;
    }
  },

  logout() {
    get()._stopRefreshTimer();
    get()._setTokens(null, null);
    get()._setUser(null);
    set({ isLoading: false, error: null });
  },

  async refreshAccessToken() {
    const { refreshToken } = get();
    if (!refreshToken) {
      get().logout();
      return;
    }
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        get().logout();
        return;
      }
      const data = await res.json();
      get()._setTokens(data.access_token, data.refresh_token || refreshToken);
      if (data.user) get()._setUser(data.user);
    } catch {
      get().logout();
    }
  },

  setUser(user) {
    get()._setUser(user);
  },

  async oauthLogin(provider) {
    try {
      const res = await fetch(`/api/auth/oauth/${provider}/authorize`);
      if (!res.ok) throw new Error('Failed to get OAuth URL');
      const data = await res.json();
      return data.url;
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  async initAuth() {
    const token = localStorage.getItem(TOKEN_KEY);
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    const savedUser = localStorage.getItem(USER_KEY);

    if (!token) {
      set({ isLoading: false, isAuthenticated: false });
      return;
    }

    set({ token, refreshToken });

    // Try to load cached user immediately for fast UI
    if (savedUser) {
      try {
        const user = JSON.parse(savedUser);
        set({ user, isAuthenticated: true });
      } catch { /* ignore */ }
    }

    // Validate token by fetching current user
    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const user = await res.json();
        get()._setUser(user);
        get()._startRefreshTimer();
        set({ isLoading: false });
      } else if (res.status === 401 && refreshToken) {
        await get().refreshAccessToken();
        // Retry getMe with new token
        const newToken = get().token;
        if (newToken) {
          const retryRes = await fetch('/api/auth/me', {
            headers: { Authorization: `Bearer ${newToken}` },
          });
          if (retryRes.ok) {
            const user = await retryRes.json();
            get()._setUser(user);
            get()._startRefreshTimer();
          } else {
            get().logout();
          }
        }
        set({ isLoading: false });
      } else {
        get().logout();
        set({ isLoading: false });
      }
    } catch {
      // Network error - use cached user if available
      if (get().user) {
        set({ isLoading: false, isAuthenticated: true });
      } else {
        set({ isLoading: false, isAuthenticated: false });
      }
    }
  },

  clearError() {
    set({ error: null });
  },

  hasRole(minimumRole) {
    const roles = ['viewer', 'responder', 'commander', 'admin'];
    const user = get().user;
    if (!user) return false;
    const userLevel = roles.indexOf(user.role || 'viewer');
    const requiredLevel = roles.indexOf(minimumRole);
    return userLevel >= requiredLevel;
  },
}));
