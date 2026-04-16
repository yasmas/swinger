import { useState } from 'react';

export default function LoginPage({ onLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [needsSetPassword, setNeedsSetPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();

      if (res.ok) {
        onLogin(data.token, { username: data.username, email: data.email, admin: data.admin === true });
        return;
      }

      if (data.error === 'password_not_set') {
        setNeedsSetPassword(true);
        setError('');
      } else {
        setError(data.error || 'Login failed');
      }
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleSetPassword = async (e) => {
    e.preventDefault();
    setError('');

    if (newPassword.length < 6) {
      setError('Password must be at least 6 characters');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setLoading(true);

    try {
      const res = await fetch('/api/auth/set-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password: newPassword }),
      });
      const data = await res.json();

      if (res.ok) {
        onLogin(data.token, { username: data.username, email: data.email, admin: data.admin === true });
        return;
      }

      setError(data.error || 'Failed to set password');
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.backdrop}>
      <div style={styles.card}>
        <h1 style={styles.title}>Swinger Dashboard</h1>
        <p style={styles.subtitle}>
          {needsSetPassword ? 'Set your password to get started' : 'Sign in to continue'}
        </p>

        {error && <div style={styles.error}>{error}</div>}

        {!needsSetPassword ? (
          <form onSubmit={handleLogin}>
            <label style={styles.label}>Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={styles.input}
              placeholder="you@example.com"
              autoFocus
              required
            />
            <label style={styles.label}>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={styles.input}
              placeholder="Enter password"
              required
            />
            <button type="submit" style={styles.button} disabled={loading}>
              {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>
        ) : (
          <form onSubmit={handleSetPassword}>
            <div style={styles.infoBox}>
              First login for <strong>{email}</strong>. Please create a password.
            </div>
            <label style={styles.label}>New Password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              style={styles.input}
              placeholder="At least 6 characters"
              autoFocus
              required
            />
            <label style={styles.label}>Confirm Password</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              style={styles.input}
              placeholder="Confirm password"
              required
            />
            <button type="submit" style={styles.button} disabled={loading}>
              {loading ? 'Setting password...' : 'Set Password & Sign In'}
            </button>
            <button
              type="button"
              style={styles.linkButton}
              onClick={() => { setNeedsSetPassword(false); setError(''); }}
            >
              Back to login
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

const styles = {
  backdrop: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    minHeight: '100vh',
    background: '#0f172a',
  },
  card: {
    background: '#1e293b',
    borderRadius: 12,
    padding: '40px 36px',
    width: 380,
    maxWidth: '90vw',
    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
  },
  title: {
    color: '#f1f5f9',
    fontSize: 24,
    fontWeight: 700,
    margin: 0,
    textAlign: 'center',
  },
  subtitle: {
    color: '#94a3b8',
    fontSize: 14,
    margin: '8px 0 24px',
    textAlign: 'center',
  },
  label: {
    display: 'block',
    color: '#94a3b8',
    fontSize: 13,
    fontWeight: 500,
    marginBottom: 6,
    marginTop: 16,
  },
  input: {
    width: '100%',
    padding: '10px 12px',
    fontSize: 14,
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: 6,
    color: '#f1f5f9',
    outline: 'none',
    boxSizing: 'border-box',
  },
  button: {
    width: '100%',
    padding: '11px 0',
    marginTop: 24,
    fontSize: 14,
    fontWeight: 600,
    background: '#3b82f6',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
  },
  linkButton: {
    width: '100%',
    padding: '8px 0',
    marginTop: 8,
    fontSize: 13,
    background: 'none',
    color: '#64748b',
    border: 'none',
    cursor: 'pointer',
    textDecoration: 'underline',
  },
  error: {
    background: '#ef444420',
    color: '#ef4444',
    border: '1px solid #ef444440',
    borderRadius: 6,
    padding: '8px 12px',
    fontSize: 13,
    marginTop: 12,
  },
  infoBox: {
    background: '#3b82f620',
    color: '#93c5fd',
    border: '1px solid #3b82f640',
    borderRadius: 6,
    padding: '8px 12px',
    fontSize: 13,
    marginBottom: 4,
  },
};
