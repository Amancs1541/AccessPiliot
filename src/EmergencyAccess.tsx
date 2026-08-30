import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { apiBaseUrl, BREAKGLASS_STORAGE_KEY } from './auth';

// Renders OUTSIDE AuthProvider entirely (see src/main.tsx) — deliberately never calls useAuth(). This is the
// hidden, unlisted URL: never linked from anywhere in the app, its token generated only by the console command
// `python -m app.cli emergency-url` (backend/app/cli.py). A wrong or missing token renders a generic not-found
// message that is content-identical to a real 404 (backed by GET /api/v1/auth/emergency-access/:token/verify,
// which reuses the exact same generic error handler as any genuinely unmatched route) — nothing here should ever
// hint to a normal visitor that a Break-Glass login form exists.
export function EmergencyAccessPage() {
  const { token } = useParams<{ token: string }>();
  const [state, setState] = useState<'checking' | 'valid' | 'invalid'>('checking');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/auth/emergency-access/${encodeURIComponent(token || '')}/verify`);
        if (!ignore) setState(response.ok ? 'valid' : 'invalid');
      } catch {
        if (!ignore) setState('invalid'); // fail closed — a network error must never fall back to showing the form
      }
    })();
    return () => { ignore = true; };
  }, [token]);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/auth/breakglass-login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password, emergency_token: token }),
      });
      if (!response.ok) {
        let message = 'Incorrect username or password.';
        try { const data = await response.json(); message = data?.error?.message || message; } catch { /* ignore */ }
        setError(message);
        return;
      }
      const data = await response.json();
      try { sessionStorage.setItem(BREAKGLASS_STORAGE_KEY, data.access_token); } catch { /* private browsing */ }
      window.location.href = '/';
    } catch {
      setError('Could not reach the backend.');
    } finally {
      setSubmitting(false);
    }
  };

  if (state === 'checking') return null;

  if (state === 'invalid') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', fontFamily: 'sans-serif', color: '#444' }}>
        <div style={{ textAlign: 'center' }}>
          <h1 style={{ fontSize: 22, fontWeight: 600 }}>404</h1>
          <p>Not Found</p>
        </div>
      </div>
    );
  }

  return (
    <div className="empty" style={{ flexDirection: 'column', alignItems: 'stretch', maxWidth: 320, margin: '80px auto', textAlign: 'left' }}>
      <h1 style={{ marginBottom: 18 }}>Emergency Access</h1>
      {error && <div className="notice" style={{ background: '#fdecea', color: '#8c2b21', marginBottom: 12 }}>{error}</div>}
      <label className="key" style={{ display: 'block', marginBottom: 10 }}><span>Username</span><input className="select" style={{ width: '100%' }} value={username} onChange={event => setUsername(event.target.value)} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 14 }}><span>Password</span><input className="select" style={{ width: '100%' }} type="password" value={password} onChange={event => setPassword(event.target.value)} onKeyDown={event => event.key === 'Enter' && submit()} /></label>
      <button className="btn btn-primary" disabled={submitting || !username || !password} onClick={submit}>{submitting ? 'Signing in...' : 'Sign in'}</button>
    </div>
  );
}
