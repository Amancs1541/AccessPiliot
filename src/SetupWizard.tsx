import { useEffect, useState, type ReactNode } from 'react';
import { PublicClientApplication } from '@azure/msal-browser';
import { apiBaseUrl } from './auth';

// Gates the whole app behind the backend's one-time portal setup flow. `GET /setup/status` reflects
// portal_setup_is_needed() — it is False the instant EITHER env-var Entra is configured (today's default, every
// existing deployment including this one) OR a PortalAuthConfig has already been activated. So for the current
// working environment this component's fetch always resolves `needs_setup: false` on the very first check, and
// `children` (AuthProvider + App, completely unmodified) render exactly as they do today — this file adds one
// cheap status check and otherwise never runs its own code path here. If the status check itself fails (network
// hiccup, backend still starting) we fail OPEN to the normal app rather than ever blocking existing usage on a
// new, unproven check.
export function SetupGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'checking' | 'needed' | 'ready'>('checking');
  useEffect(() => {
    let ignore = false;
    fetch(`${apiBaseUrl}/api/v1/setup/status`)
      .then(response => response.json())
      .then(data => { if (!ignore) setState(data?.needs_setup ? 'needed' : 'ready'); })
      .catch(() => { if (!ignore) setState('ready'); });
    return () => { ignore = true; };
  }, []);
  if (state === 'checking') return <div className="empty">Loading AccessPilot...</div>;
  if (state === 'needed') return <SetupWizard />;
  return <>{children}</>;
}

type IdpType = 'ENTRA' | 'OKTA';
type Step = 'bootstrap' | 'configure' | 'test-login' | 'done';

interface PendingConfig {
  id: string;
  idp_type: IdpType;
  tenant_id: string | null;
  client_id: string | null;
  authority: string | null;
  issuer: string | null;
  audience: string | null;
  scope: string | null;
  redirect_uri: string | null;
}

async function readError(response: Response): Promise<string> {
  try {
    const data = await response.json();
    return data?.error?.message || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function SetupWizard() {
  const [step, setStep] = useState<Step>('bootstrap');
  const [setupToken, setSetupToken] = useState('');
  const [pendingConfig, setPendingConfig] = useState<PendingConfig | null>(null);
  const [breakglassUsername, setBreakglassUsername] = useState('');

  return (
    <div className="empty" style={{ flexDirection: 'column', alignItems: 'stretch', maxWidth: 520, margin: '40px auto', textAlign: 'left' }}>
      <h1 style={{ marginBottom: 4 }}>AccessPilot first-time setup</h1>
      <p className="subtitle" style={{ marginBottom: 24 }}>No sign-in IDP is configured yet. Complete this one-time setup to bring the portal online.</p>
      <StepIndicator step={step} />
      {step === 'bootstrap' && <BootstrapStep onSuccess={token => { setSetupToken(token); setStep('configure'); }} />}
      {step === 'configure' && <ConfigureStep setupToken={setupToken} onSuccess={(config, bgUsername) => { setPendingConfig(config); setBreakglassUsername(bgUsername); setStep('test-login'); }} onSessionExpired={() => setStep('bootstrap')} />}
      {step === 'test-login' && pendingConfig && <TestLoginStep setupToken={setupToken} config={pendingConfig} onSuccess={() => setStep('done')} onSessionExpired={() => setStep('bootstrap')} />}
      {step === 'done' && pendingConfig && <DoneStep idpType={pendingConfig.idp_type} breakglassUsername={breakglassUsername} />}
    </div>
  );
}

function StepIndicator({ step }: { step: Step }) {
  const steps: { key: Step; label: string }[] = [
    { key: 'bootstrap', label: '1. Bootstrap login' },
    { key: 'configure', label: '2. Configure IDP' },
    { key: 'test-login', label: '3. Verify it works' },
    { key: 'done', label: '4. Done' },
  ];
  const activeIndex = steps.findIndex(s => s.key === step);
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 24, flexWrap: 'wrap' }}>
      {steps.map((s, index) => (
        <span key={s.key} style={{ fontSize: 12, padding: '4px 10px', borderRadius: 999, background: index === activeIndex ? '#14606b' : index < activeIndex ? '#dcecee' : '#f1f3f4', color: index === activeIndex ? '#fff' : index < activeIndex ? '#14606b' : '#8a9296' }}>
          {s.label}
        </span>
      ))}
    </div>
  );
}

function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="notice" style={{ background: '#fdecea', color: '#8c2b21', marginBottom: 14 }}>{message}</div>;
}

function BootstrapStep({ onSuccess }: { onSuccess: (setupToken: string) => void }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/setup/bootstrap-login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
      });
      if (!response.ok) { setError(await readError(response)); return; }
      const data = await response.json();
      onSuccess(data.setup_token);
    } catch {
      setError('Could not reach the backend. Confirm it is running and try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel">
      <p style={{ marginTop: 0 }}>Sign in with the one-time bootstrap credential printed to the backend's startup log the first time it ran with no IDP configured.</p>
      <ErrorBanner message={error} />
      <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Username</span><input className="select" style={{ width: '100%' }} value={username} onChange={event => setUsername(event.target.value)} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 16 }}><span>Bootstrap password</span><input className="select" style={{ width: '100%' }} type="password" autoFocus value={password} onChange={event => setPassword(event.target.value)} onKeyDown={event => event.key === 'Enter' && submit()} /></label>
      <button className="btn btn-primary" disabled={submitting || !password} onClick={submit}>{submitting ? 'Signing in...' : 'Continue'}</button>
    </div>
  );
}

function ConfigureStep({ setupToken, onSuccess, onSessionExpired }: { setupToken: string; onSuccess: (config: PendingConfig, breakglassUsername: string) => void; onSessionExpired: () => void }) {
  const [idpType, setIdpType] = useState<IdpType>('ENTRA');
  const [tenantId, setTenantId] = useState('');
  const [clientId, setClientId] = useState('');
  const [authority, setAuthority] = useState('');
  const [issuer, setIssuer] = useState('');
  const [scope, setScope] = useState('');
  const [breakglassUsername, setBreakglassUsername] = useState('');
  const [breakglassPassword, setBreakglassPassword] = useState('');
  const [breakglassConfirm, setBreakglassConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    if (idpType === 'ENTRA' && tenantId && !authority) setAuthority(`https://login.microsoftonline.com/${tenantId}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  const validationError = (): string | null => {
    if (idpType === 'ENTRA' && (!tenantId || !clientId)) return 'Tenant ID and Client ID are required for Entra.';
    if (idpType === 'OKTA' && (!issuer || !clientId)) return 'Issuer and Client ID are required for Okta.';
    if (breakglassUsername.length < 3) return 'Break-glass username must be at least 3 characters.';
    if (breakglassPassword.length < 12) return 'Break-glass password must be at least 12 characters.';
    if (breakglassPassword !== breakglassConfirm) return 'Break-glass password and confirmation do not match.';
    return null;
  };

  const submit = async () => {
    const validation = validationError();
    if (validation) { setError(validation); return; }
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/setup/configure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${setupToken}` },
        body: JSON.stringify({
          idp_type: idpType,
          tenant_id: idpType === 'ENTRA' ? tenantId : undefined,
          client_id: clientId,
          authority: idpType === 'ENTRA' ? authority : undefined,
          issuer: idpType === 'OKTA' ? issuer : undefined,
          scope: scope || undefined,
          redirect_uri: window.location.origin,
          breakglass_username: breakglassUsername,
          breakglass_password: breakglassPassword,
        }),
      });
      if (response.status === 401) { onSessionExpired(); return; }
      if (!response.ok) { setError(await readError(response)); return; }
      const data = await response.json();
      onSuccess(data, breakglassUsername);
    } catch {
      setError('Could not reach the backend. Confirm it is running and try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel">
      <p style={{ marginTop: 0 }}>Configure the real identity provider AccessPilot should use going forward, plus a Break-Glass account for emergency recovery if that IDP is ever unreachable.</p>
      <ErrorBanner message={error} />
      <label className="key" style={{ display: 'block', marginBottom: 12 }}>
        <span>Identity provider</span>
        <select className="select" style={{ width: '100%' }} value={idpType} onChange={event => setIdpType(event.target.value as IdpType)}>
          <option value="ENTRA">Microsoft Entra ID</option>
          <option value="OKTA">Okta</option>
        </select>
      </label>
      {idpType === 'ENTRA' && (
        <>
          <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Tenant ID</span><input className="select" style={{ width: '100%' }} value={tenantId} onChange={event => setTenantId(event.target.value)} placeholder="00000000-0000-0000-0000-000000000000" /></label>
          <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Client (application) ID</span><input className="select" style={{ width: '100%' }} value={clientId} onChange={event => setClientId(event.target.value)} /></label>
          <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Authority</span><input className="select" style={{ width: '100%' }} value={authority} onChange={event => setAuthority(event.target.value)} /></label>
        </>
      )}
      {idpType === 'OKTA' && (
        <>
          <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Issuer</span><input className="select" style={{ width: '100%' }} value={issuer} onChange={event => setIssuer(event.target.value)} placeholder="https://your-org.okta.com/oauth2/default" /></label>
          <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Client ID</span><input className="select" style={{ width: '100%' }} value={clientId} onChange={event => setClientId(event.target.value)} /></label>
        </>
      )}
      <button type="button" className="btn" style={{ marginBottom: 12 }} onClick={() => setShowAdvanced(v => !v)}>{showAdvanced ? 'Hide' : 'Show'} advanced (optional scope)</button>
      {showAdvanced && (
        <label className="key" style={{ display: 'block', marginBottom: 12 }}>
          <span>API scope — leave blank to verify with a basic sign-in token instead</span>
          <input className="select" style={{ width: '100%' }} value={scope} onChange={event => setScope(event.target.value)} placeholder="api://your-app-id/access_as_user" />
        </label>
      )}
      <hr style={{ margin: '16px 0', border: 'none', borderTop: '1px solid #e2e6e7' }} />
      <p style={{ fontWeight: 600, marginBottom: 8 }}>Break-Glass emergency account</p>
      <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Username</span><input className="select" style={{ width: '100%' }} value={breakglassUsername} onChange={event => setBreakglassUsername(event.target.value)} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Password (min 12 characters)</span><input className="select" style={{ width: '100%' }} type="password" value={breakglassPassword} onChange={event => setBreakglassPassword(event.target.value)} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 16 }}><span>Confirm password</span><input className="select" style={{ width: '100%' }} type="password" value={breakglassConfirm} onChange={event => setBreakglassConfirm(event.target.value)} /></label>
      <button className="btn btn-primary" disabled={submitting} onClick={submit}>{submitting ? 'Saving...' : 'Save and continue'}</button>
    </div>
  );
}

function TestLoginStep({ setupToken, config, onSuccess, onSessionExpired }: { setupToken: string; config: PendingConfig; onSuccess: () => void; onSessionExpired: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manualToken, setManualToken] = useState('');

  const activate = async (testToken: string) => {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/setup/activate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${setupToken}` },
        body: JSON.stringify({ config_id: config.id, test_token: testToken }),
      });
      if (response.status === 401) { onSessionExpired(); return; }
      if (!response.ok) { setError(await readError(response)); return; }
      onSuccess();
    } catch {
      setError('Could not reach the backend. Confirm it is running and try again.');
    } finally {
      setBusy(false);
    }
  };

  const testEntraLogin = async () => {
    setBusy(true);
    setError(null);
    try {
      const instance = new PublicClientApplication({
        auth: { clientId: config.client_id || '', authority: config.authority || undefined, redirectUri: config.redirect_uri || window.location.origin },
        cache: { cacheLocation: 'sessionStorage' },
      });
      await instance.initialize();
      const result = await instance.loginPopup({ scopes: config.scope ? [config.scope] : ['openid', 'profile'] });
      const testToken = config.scope ? result.accessToken : result.idToken;
      await activate(testToken);
    } catch (loginError) {
      const message = (loginError as { errorMessage?: string; message?: string })?.errorMessage || (loginError as Error)?.message || 'Sign-in failed or was cancelled.';
      setError(message);
      setBusy(false);
    }
  };

  if (config.idp_type === 'OKTA') {
    return (
      <div className="panel">
        <p style={{ marginTop: 0 }}>Okta sign-in isn't wired into this wizard yet. Obtain a real ID token from your Okta app (e.g. via Okta's own test tool, or a Postman OAuth2 flow against your Okta issuer) and paste it below to verify and activate this configuration.</p>
        <ErrorBanner message={error} />
        <label className="key" style={{ display: 'block', marginBottom: 12 }}><span>Okta ID token</span><textarea className="select" style={{ width: '100%', minHeight: 100, fontFamily: 'monospace', fontSize: 11 }} value={manualToken} onChange={event => setManualToken(event.target.value)} /></label>
        <button className="btn btn-primary" disabled={busy || !manualToken} onClick={() => activate(manualToken)}>{busy ? 'Verifying...' : 'Activate with this token'}</button>
      </div>
    );
  }

  return (
    <div className="panel">
      <p style={{ marginTop: 0 }}>Sign in with the Microsoft account you just configured. This proves the configuration actually works before anything is switched on and the bootstrap credential is deleted.</p>
      <ErrorBanner message={error} />
      <button className="btn btn-primary" disabled={busy} onClick={testEntraLogin}>{busy ? 'Waiting for sign-in...' : 'Test sign-in with Microsoft'}</button>
      <p style={{ fontSize: 12, color: '#8a9296', marginTop: 10 }}>A popup window will open. Allow popups for this site if it doesn't appear.</p>
    </div>
  );
}

function DoneStep({ idpType, breakglassUsername }: { idpType: IdpType; breakglassUsername: string }) {
  return (
    <div className="panel">
      <p style={{ marginTop: 0, fontWeight: 600 }}>Setup complete.</p>
      <p>{idpType === 'ENTRA' ? 'Microsoft Entra ID' : 'Okta'} is now the active sign-in provider for AccessPilot. The bootstrap credential has been permanently deleted and can never be used again.</p>
      <p>Your Break-Glass account (<strong>{breakglassUsername}</strong>) is now active as an emergency-only fallback — use it only if the configured IDP itself becomes unreachable, via <code>POST /api/v1/auth/breakglass-login</code>.</p>
      <button className="btn btn-primary" onClick={() => window.location.reload()}>Continue to AccessPilot</button>
    </div>
  );
}
