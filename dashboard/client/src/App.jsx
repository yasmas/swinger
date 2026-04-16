import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from './hooks/useWebSocket.js';
import { apiFetch, getToken, setToken, setStoredUser, clearAuth, setAuthExpiredHandler } from './lib/api.js';
import TradingDashboard from './TradingDashboard.jsx';
import LoginPage from './LoginPage.jsx';

export default function App() {
  // Do not trust user from localStorage until /api/auth/me succeeds (avoids racing
  // apiFetch('/api/bots') + WebSocket against validation with a stale token).
  const [user, setUser] = useState(null);
  const [token, setTokenState] = useState(getToken);
  const [bots, setBots] = useState([]);
  const [loading, setLoading] = useState(() => !!getToken());
  const [tradeTick, setTradeTick] = useState(0);
  /** 'checking' | 'anon' | 'auth' */
  const [bootstrap, setBootstrap] = useState(() => (getToken() ? 'checking' : 'anon'));

  const isAuthenticated = bootstrap === 'auth' && !!token && !!user;

  const handleLogout = useCallback(() => {
    if (token) {
      fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
      }).catch(() => {});
    }
    clearAuth();
    setUser(null);
    setTokenState(null);
    setBots([]);
    setBootstrap('anon');
    setLoading(false);
  }, [token]);

  // Register global 401 handler
  useEffect(() => {
    setAuthExpiredHandler(handleLogout);
  }, [handleLogout]);

  const handleLogin = useCallback((newToken, newUser) => {
    setToken(newToken);
    setStoredUser(newUser);
    setTokenState(newToken);
    setUser(newUser);
    setBootstrap('auth');
    setLoading(true);
  }, []);

  // Validate existing session on mount (single source of truth before bots + WS)
  useEffect(() => {
    let cancelled = false;
    const t = getToken();
    if (!t) {
      setTokenState(null);
      setBootstrap('anon');
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setBootstrap('checking');
    fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${t}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error('expired');
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
        setTokenState(t);
        setUser({ username: data.username, email: data.email, admin: data.admin === true });
        setStoredUser({ username: data.username, email: data.email, admin: data.admin === true });
        setBootstrap('auth');
      })
      .catch(() => {
        if (cancelled) return;
        clearAuth();
        setUser(null);
        setTokenState(null);
        setBootstrap('anon');
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch bots when authenticated
  useEffect(() => {
    if (!isAuthenticated) return;

    apiFetch('/api/bots')
      .then(r => r.json())
      .then(data => {
        setBots(data);
        setLoading(false);
      })
      .catch(err => {
        console.error('Failed to fetch bots:', err);
        setLoading(false);
      });
  }, [isAuthenticated]);

  // WebSocket handler
  const handleWsMessage = useCallback((msg) => {
    switch (msg.event) {
      case 'init':
        setBots(msg.data);
        break;

      case 'bot_added':
        setBots(prev => prev.some(b => b.name === msg.bot)
          ? prev
          : [...prev, msg.data]
        );
        break;

      case 'bot_update':
      case 'bot_connected':
        setBots(prev => {
          const exists = prev.some(b => b.name === msg.bot);
          if (exists) return prev.map(b => b.name === msg.bot ? { ...b, ...msg.data } : b);
          return [...prev, msg.data];
        });
        break;

      case 'bot_disconnected':
        setBots(prev => prev.map(b =>
          b.name === msg.bot ? { ...b, status: 'stopped' } : b
        ));
        break;

      case 'trade_entry':
      case 'trade_exit':
        setTradeTick(t => t + 1);
        break;
    }
  }, []);

  useWebSocket('/ws', handleWsMessage, isAuthenticated ? token : null);

  if (bootstrap === 'checking') {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: '#94a3b8', fontSize: 16, background: '#0f172a' }}>
        Validating session...
      </div>
    );
  }

  if (bootstrap === 'anon') {
    return <LoginPage onLogin={handleLogin} />;
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: '#94a3b8', fontSize: 16, background: '#0f172a' }}>
        Loading dashboard...
      </div>
    );
  }

  return (
    <TradingDashboard
      bots={bots}
      setBots={setBots}
      tradeTick={tradeTick}
      user={user}
      onLogout={handleLogout}
    />
  );
}
