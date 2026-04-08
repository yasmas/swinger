import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from './hooks/useWebSocket.js';
import { apiFetch, getToken, setToken, getStoredUser, setStoredUser, clearAuth, setAuthExpiredHandler } from './lib/api.js';
import TradingDashboard from './TradingDashboard.jsx';
import LoginPage from './LoginPage.jsx';

export default function App() {
  const [user, setUser] = useState(getStoredUser);
  const [token, setTokenState] = useState(getToken);
  const [bots, setBots] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tradeTick, setTradeTick] = useState(0);

  const isAuthenticated = !!(token && user);

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
    setLoading(true);
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
    setLoading(true);
  }, []);

  // Validate existing session on mount
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }

    fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` },
    })
      .then(r => {
        if (!r.ok) throw new Error('expired');
        return r.json();
      })
      .then(data => {
        setUser({ username: data.username, email: data.email });
        setStoredUser({ username: data.username, email: data.email });
      })
      .catch(() => {
        clearAuth();
        setUser(null);
        setTokenState(null);
        setLoading(false);
      });
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

  useWebSocket('/ws', handleWsMessage, token);

  if (!isAuthenticated) {
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
