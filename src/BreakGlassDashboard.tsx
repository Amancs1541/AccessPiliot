import { useEffect, useState } from 'react';
import { AlertTriangle, Cloud, KeyRound, LogOut, ShieldCheck } from 'lucide-react';
import { useAuth } from './auth';

interface PortalAuthConfig {
  id: string;
  idp_type: string;
  tenant_id: string | null;
  client_id: string | null;
  authority: string | null;
  issuer: string | null;
  audience: string | null;
  scope: string | null;
  redirect_uri: string | null;
  is_active: boolean;
}

const EMPTY_FORM: Omit<PortalAuthConfig, 'id' | 'is_active'> = { idp_type: 'ENTRA', tenant_id: '', client_id: '', authority: '', issuer: '', audience: '', scope: '', redirect_uri: '' };

function Notice({ tone, children }: { tone: 'error' | 'success'; children: React.ReactNode }) {
  const style = tone === 'error' ? { background: '#fbe7e5', color: '#ae4949', border: '1px solid #f2c9c5' } : { background: '#e5f5ef', color: '#277b67', border: '1px solid #c7e9dc' };
  return <div className="notice" style={{ ...style, marginBottom: 14 }}>{children}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>{label}</span>{children}</label>;
}

// The default landing view for a Break-Glass session (AccessPilot.BreakGlassAdmin role) — deliberately does NOT
// use Shell/nav/the app's normal <Routes>. It can do exactly two things: fix the broken IDP configuration, and
// rotate its own password — everything else in the app is unreachable from here (backend-enforced via
// require_permission, not just hidden UI) until the explicit "Enter Admin Console" elevation below. Given a
// visibly distinct "restricted emergency session" treatment so it can never be mistaken for the normal console.
export function BreakGlassDashboard() {
  const auth = useAuth();
  const [form, setForm] = useState(EMPTY_FORM);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);

  const [elevateError, setElevateError] = useState<string | null>(null);
  const [elevating, setElevating] = useState(false);

  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const response = await auth.apiRequest('/api/v1/auth/portal-auth-config');
        if (ignore) return;
        if (!response.ok) { setLoadError('Could not load the current identity provider configuration.'); return; }
        const data = await response.json() as PortalAuthConfig;
        setForm({ idp_type: data.idp_type, tenant_id: data.tenant_id || '', client_id: data.client_id || '', authority: data.authority || '', issuer: data.issuer || '', audience: data.audience || '', scope: data.scope || '', redirect_uri: data.redirect_uri || '' });
      } catch {
        if (!ignore) setLoadError('Could not reach the backend.');
      }
    })();
    return () => { ignore = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveConfig = async () => {
    setSaving(true);
    setSaveMessage(null);
    setSaveError(null);
    try {
      const response = await auth.apiRequest('/api/v1/auth/portal-auth-config', { method: 'PATCH', body: JSON.stringify(form) });
      if (!response.ok) {
        let message = 'Could not save the configuration.';
        try { const data = await response.json(); message = data?.error?.message || message; } catch { /* ignore */ }
        setSaveError(message);
        return;
      }
      setSaveMessage('Saved. This takes effect immediately for future sign-ins.');
    } catch {
      setSaveError('Could not reach the backend.');
    } finally {
      setSaving(false);
    }
  };

  const rotatePassword = async () => {
    setPasswordMessage(null);
    setPasswordError(null);
    if (newPassword.length < 12) { setPasswordError('Password must be at least 12 characters.'); return; }
    if (newPassword !== confirmPassword) { setPasswordError('Passwords do not match.'); return; }
    setRotating(true);
    try {
      const response = await auth.apiRequest('/api/v1/auth/breakglass-credential/rotate', { method: 'POST', body: JSON.stringify({ new_password: newPassword }) });
      if (!response.ok) {
        let message = 'Could not update the password.';
        try { const data = await response.json(); message = data?.error?.message || message; } catch { /* ignore */ }
        setPasswordError(message);
        return;
      }
      setPasswordMessage('Password updated.');
      setNewPassword('');
      setConfirmPassword('');
    } catch {
      setPasswordError('Could not reach the backend.');
    } finally {
      setRotating(false);
    }
  };

  const enterAdminConsole = async () => {
    setElevating(true);
    setElevateError(null);
    const result = await auth.elevateBreakglass();
    setElevating(false);
    if (!result.ok) setElevateError(result.error || 'Could not elevate this session.');
    // On success, App() re-evaluates auth.breakglassElevated on the next render and falls through to the normal
    // Admin Shell — no manual navigation needed here.
  };

  return (
    <div style={{ minHeight: '100vh', background: '#f4f7f9' }}>
      <div style={{ background: '#3a1f1f', color: '#f6d9d3', padding: '10px 24px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9, fontSize: 12, fontWeight: 700, letterSpacing: '.4px' }}>
        <AlertTriangle size={14} color="#e2a190" />
        RESTRICTED — BREAK-GLASS EMERGENCY SESSION
      </div>
      <div style={{ maxWidth: 620, margin: '0 auto', padding: '44px 24px 60px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 28 }}>
          <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <span style={{ width: 42, height: 42, flex: 'none', borderRadius: 10, background: '#fbe7e5', color: '#ae4949', display: 'grid', placeItems: 'center' }}><AlertTriangle size={20} /></span>
            <div>
              <h1 style={{ marginBottom: 4 }}>Break-Glass Emergency Access</h1>
              <p className="subtitle" style={{ maxWidth: 440 }}>You can fix the sign-in configuration or rotate this account's password. Nothing else in AccessPilot is reachable from this session.</p>
            </div>
          </div>
          <button type="button" className="btn" onClick={() => auth.signOut()} style={{ flex: 'none' }}><LogOut size={13} /> Sign out</button>
        </div>

        <div className="panel" style={{ marginBottom: 18 }}>
          <div className="panel-head"><h2 style={{ display: 'flex', alignItems: 'center', gap: 9 }}><Cloud size={15} color="#087f82" /> Identity provider configuration</h2></div>
          <div style={{ padding: 20 }}>
            {loadError && <Notice tone="error">{loadError}</Notice>}
            {saveError && <Notice tone="error">{saveError}</Notice>}
            {saveMessage && <Notice tone="success">{saveMessage}</Notice>}
            <Field label="Identity provider">
              <select className="select" style={{ width: '100%' }} value={form.idp_type} onChange={event => setForm({ ...form, idp_type: event.target.value })}>
                <option value="ENTRA">Microsoft Entra ID</option>
                <option value="OKTA">Okta</option>
              </select>
            </Field>
            {form.idp_type === 'ENTRA' ? (
              <>
                <Field label="Tenant ID"><input className="select" style={{ width: '100%' }} value={form.tenant_id || ''} onChange={event => setForm({ ...form, tenant_id: event.target.value })} /></Field>
                <Field label="Client (application) ID"><input className="select" style={{ width: '100%' }} value={form.client_id || ''} onChange={event => setForm({ ...form, client_id: event.target.value })} /></Field>
                <Field label="Authority"><input className="select" style={{ width: '100%' }} value={form.authority || ''} onChange={event => setForm({ ...form, authority: event.target.value })} /></Field>
              </>
            ) : (
              <>
                <Field label="Issuer"><input className="select" style={{ width: '100%' }} value={form.issuer || ''} onChange={event => setForm({ ...form, issuer: event.target.value })} /></Field>
                <Field label="Client ID"><input className="select" style={{ width: '100%' }} value={form.client_id || ''} onChange={event => setForm({ ...form, client_id: event.target.value })} /></Field>
              </>
            )}
            <Field label="Scope (optional)"><input className="select" style={{ width: '100%' }} value={form.scope || ''} onChange={event => setForm({ ...form, scope: event.target.value })} /></Field>
            <button className="btn btn-primary" disabled={saving} onClick={saveConfig} style={{ marginTop: 4 }}>{saving ? 'Saving...' : 'Save'}</button>
          </div>
        </div>

        <div className="panel" style={{ marginBottom: 18 }}>
          <div className="panel-head"><h2 style={{ display: 'flex', alignItems: 'center', gap: 9 }}><KeyRound size={15} color="#087f82" /> Change Break-Glass password</h2></div>
          <div style={{ padding: 20 }}>
            {passwordError && <Notice tone="error">{passwordError}</Notice>}
            {passwordMessage && <Notice tone="success">{passwordMessage}</Notice>}
            <Field label="New password (min 12 characters)"><input className="select" style={{ width: '100%' }} type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} /></Field>
            <Field label="Confirm password"><input className="select" style={{ width: '100%' }} type="password" value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} /></Field>
            <button className="btn btn-primary" disabled={rotating || !newPassword} onClick={rotatePassword} style={{ marginTop: 4 }}>{rotating ? 'Updating...' : 'Update password'}</button>
          </div>
        </div>

        <div className="panel" style={{ borderColor: '#8ab7b7', boxShadow: '0 2px 10px #123c4214' }}>
          <div style={{ padding: 20 }}>
            {elevateError && <Notice tone="error">{elevateError}</Notice>}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 13, color: '#17212b', display: 'flex', alignItems: 'center', gap: 8 }}><ShieldCheck size={16} color="#087f82" /> Enter Admin Console</div>
                <p style={{ fontSize: 12, color: '#687782', marginTop: 5, marginBottom: 0 }}>Grants this session full Administrator access to perform normal tasks.</p>
              </div>
              <button className="btn btn-primary" disabled={elevating} onClick={enterAdminConsole}>{elevating ? 'Entering...' : 'Enter Admin Console'}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
