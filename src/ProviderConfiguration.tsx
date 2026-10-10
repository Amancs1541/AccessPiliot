import { useEffect, useState } from 'react';
import { Activity, Cloud, Download, KeyRound, Pencil, Plus, RefreshCw, Save, X } from 'lucide-react';
import { useAuth } from './auth';

type ProviderStatus = 'NOT_CONFIGURED' | 'CONFIGURED' | 'CONNECTED' | 'ERROR' | 'DISABLED';
type Provider = { id: string; name: string; provider_type: string; status: ProviderStatus; tenant_id: string; client_id?: string; authority?: string; api_audience?: string; api_scope?: string; redirect_uri_metadata?: Record<string, string>; organization_url?: string | null; graph_client_id?: string | null; credential_configured: boolean; provisioning_domain?: string | null; username_convention?: string | null; agent_configured?: boolean; agent_connected?: boolean; agent_last_seen_at?: string | null; last_sync_at?: string | null; sync_interval_minutes?: number | null; };
type SyncRun = { id: string; status: string; started_at: string; completed_at: string | null; users_processed: number; groups_processed: number; roles_processed: number; errors_count: number; };
type Domain = { name: string; is_verified: boolean; is_default: boolean };
type ProviderForm = Omit<Provider, 'id' | 'status' | 'credential_configured' | 'graph_client_id'>;
const createEmptyForm = (): ProviderForm => ({ name: 'Microsoft Entra ID', provider_type: 'ENTRA', tenant_id: '', client_id: '', authority: '', api_audience: '', api_scope: '', redirect_uri_metadata: { development: 'http://localhost:5173' } });
const toForm = (provider: Provider): ProviderForm => ({ name: provider.name, provider_type: 'ENTRA', tenant_id: provider.tenant_id, client_id: provider.client_id || '', authority: provider.authority || '', api_audience: provider.api_audience || '', api_scope: provider.api_scope || '', redirect_uri_metadata: provider.redirect_uri_metadata || {} });

function ProvisioningMappingPanel({ provider, onSaved }: { provider: Provider; onSaved: () => void }) {
  const auth = useAuth();
  const [domain, setDomain] = useState(provider.provisioning_domain || '');
  const [convention, setConvention] = useState(provider.username_convention || '');
  const [domains, setDomains] = useState<Domain[]>([]);
  const [domainsLoading, setDomainsLoading] = useState(false);
  const [domainsMessage, setDomainsMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { setDomain(provider.provisioning_domain || ''); setConvention(provider.username_convention || ''); }, [provider.id, provider.provisioning_domain, provider.username_convention]);
  const fetchDomains = async () => {
    setDomainsLoading(true); setDomainsMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}/domains`);
      if (response.ok) setDomains(await response.json());
      else { const error = await response.json().catch(() => null); setDomainsMessage(error?.error?.message || 'Unable to fetch domains — check the connector has Domain.Read.All granted.'); }
    } catch { setDomainsMessage('Unable to fetch domains.'); } finally { setDomainsLoading(false); }
  };
  const save = async () => {
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}`, { method: 'PATCH', body: JSON.stringify({ provisioning_domain: domain || null, username_convention: convention || null }) });
      if (response.ok) { setMessage('Provisioning mapping saved.'); onSaved(); }
      else { const error = await response.json().catch(() => null); setMessage(error?.error?.message || 'Unable to save provisioning mapping.'); }
    } catch { setMessage('Unable to save provisioning mapping.'); } finally { setSaving(false); }
  };
  const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '');
  const previewLocal = (() => {
    const first = slug('Jane'), last = slug('Doe');
    if (!convention) return 'jane.doe';
    const result = convention.replace(/\{first\}/g, first).replace(/\{last\}/g, last).replace(/\{f\}/g, first[0] || '').replace(/\{l\}/g, last[0] || '');
    return result.trim() || 'jane.doe';
  })();
  return <div className="detail-section">
    <div className="detail-title"><h2 style={{fontSize:14}}>Provisioning mapping</h2></div>
    <p className="subtitle" style={{marginBottom:14}}>Controls how CSV/HR onboarding provisions new accounts into this connector — pick a KNOWN VERIFIED domain (fetched live from the connector) instead of trusting a CSV row's raw email domain, and set a consistent username naming convention. Leave both blank to use each CSV row's own email exactly as given (unchanged behavior).</p>
    <div style={{display:'flex',gap:8,alignItems:'center',marginBottom:10,flexWrap:'wrap'}}>
      <button type="button" className="btn" disabled={domainsLoading} onClick={() => void fetchDomains()}><RefreshCw size={14}/> {domainsLoading ? 'Fetching...' : 'Fetch domains from connector'}</button>
      {domains.length > 0 && <span className="footer-note">{domains.length} domain{domains.length === 1 ? '' : 's'} found</span>}
    </div>
    {domainsMessage && <div className="notice" style={{marginBottom:10}}>{domainsMessage}</div>}
    <div className="key-grid">
      <label className="key"><span>Provisioning domain</span>{domains.length > 0 ? <select className="select" value={domain} onChange={event => setDomain(event.target.value)}><option value="">Use each CSV row's own email domain</option>{domains.map(d => <option key={d.name} value={d.name}>{d.name}{d.is_verified ? ' (verified)' : ' (unverified)'}{d.is_default ? ' — default' : ''}</option>)}</select> : <input className="select" placeholder="e.g. contoso.onmicrosoft.com" value={domain} onChange={event => setDomain(event.target.value)}/>}</label>
      <label className="key"><span>Naming convention preset</span><select className="select" value="" onChange={event => { if (event.target.value) setConvention(event.target.value); }}><option value="">Choose a preset...</option><option value="{first}.{last}">firstname.lastname</option><option value="{f}{last}">flastname</option><option value="{first}{last}">firstnamelastname</option><option value="{first}_{last}">firstname_lastname</option></select></label>
      <label className="key"><span>Naming convention (custom)</span><input className="select" placeholder="{first}.{last}" value={convention} onChange={event => setConvention(event.target.value)}/></label>
    </div>
    <p className="footer-note" style={{marginTop:10}}>Preview for "Jane Doe": <strong>{previewLocal}@{domain || 'company-domain.com'}</strong></p>
    <div style={{display:'flex',justifyContent:'flex-end',gap:8,marginTop:12}}><button type="button" className="btn btn-primary" disabled={saving} onClick={() => void save()}><Save size={14}/> {saving ? 'Saving...' : 'Save mapping'}</button></div>
    {message && <div className="notice" style={{marginTop:12}}>{message}</div>}
  </div>;
}
// Active Directory, Phase 1 of the connector plan: connectivity only — proving a small on-prem agent can reach
// AccessPilot over HTTPS. No LDAP config (host/base DN/bind credentials) exists yet; that's a later phase, so
// this panel deliberately asks for nothing beyond a name today.
function AdAgentPanel({ provider, onChanged }: { provider: Provider; onChanged: () => void }) {
  const auth = useAuth();
  const [generating, setGenerating] = useState(false);
  const [newKey, setNewKey] = useState('');
  const [message, setMessage] = useState('');
  const generateKey = async () => {
    if (provider.agent_configured && !window.confirm('Generate a new agent key? Any agent process still using the current key will stop working until you update it.')) return;
    setGenerating(true); setMessage(''); setNewKey('');
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}/agent/generate-key`, { method: 'POST' });
      if (response.ok) { setNewKey((await response.json()).api_key); onChanged(); }
      else { const error = await response.json().catch(() => null); setMessage(error?.error?.message || 'Unable to generate an agent key.'); }
    } catch { setMessage('Unable to generate an agent key.'); } finally { setGenerating(false); }
  };
  const downloadAgent = async () => {
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}/agent/download`);
      if (!response.ok) { setMessage('Unable to download the agent script.'); return; }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = 'accesspilot_agent.py';
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    } catch { setMessage('Unable to reach the backend.'); }
  };
  const lastSeenLabel = provider.agent_last_seen_at ? new Date(provider.agent_last_seen_at).toLocaleString() : null;
  return <div className="detail-section">
    <div className="detail-title"><h2 style={{fontSize:14}}>Agent connectivity</h2>
      <span className={`badge ${provider.agent_connected ? 'success' : 'neutral'}`}>{provider.agent_connected ? 'Agent connected' : provider.agent_configured ? 'Agent not responding' : 'No agent set up yet'}</span>
    </div>
    <p className="subtitle" style={{marginBottom:14}}>On-premises Active Directory's LDAP port usually isn't reachable from the internet, unlike Entra/Okta — so a small agent runs inside your network and calls <strong>out</strong> to AccessPilot instead. This phase proves that connection works; it doesn't read or write anything in Active Directory yet.</p>
    {lastSeenLabel && <p className="footer-note" style={{marginBottom:12}}>Last heartbeat: {lastSeenLabel}</p>}
    <div style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:12}}>
      <button type="button" className="btn btn-primary" disabled={generating} onClick={() => void generateKey()}><KeyRound size={14}/> {generating ? 'Generating...' : provider.agent_configured ? 'Regenerate agent key' : 'Generate agent key'}</button>
      <button type="button" className="btn" onClick={() => void downloadAgent()}><Download size={14}/> Download agent script</button>
    </div>
    {newKey && <div className="notice" style={{marginBottom:12}}>
      <strong>Agent key (shown once — copy it now):</strong>
      <div style={{fontFamily:'monospace',wordBreak:'break-all',background:'#f6f7f9',padding:'8px 10px',borderRadius:6,margin:'8px 0'}}>{newKey}</div>
      <div>Provider ID: <span style={{fontFamily:'monospace'}}>{provider.id}</span></div>
      <div style={{marginTop:6}}>Set <code>ACCESSPILOT_AGENT_KEY</code>, <code>ACCESSPILOT_PROVIDER_ID</code>, and <code>ACCESSPILOT_URL</code> on the machine running the agent — see the README in the downloaded script's folder for exact steps.</div>
    </div>}
    {message && <div className="notice" style={{marginBottom:12}}>{message}</div>}
  </div>;
}
// Dynamic LDAP connection config — URL, base DN, bind username/password — stored through the exact same generic
// provider endpoints Entra's own config already uses (PATCH /providers/{id} for url/base DN, PATCH .../credentials
// for username/password, Fernet-encrypted the same way). Runs directly from the backend — no agent involved.
function AdLdapConfigPanel({ provider, onChanged }: { provider: Provider; onChanged: () => void }) {
  const auth = useAuth();
  const [url, setUrl] = useState(provider.organization_url || '');
  const [baseDn, setBaseDn] = useState(provider.tenant_id || '');
  const [bindUser, setBindUser] = useState(provider.graph_client_id || '');
  const [bindPassword, setBindPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { setUrl(provider.organization_url || ''); setBaseDn(provider.tenant_id || ''); setBindUser(provider.graph_client_id || ''); }, [provider.id, provider.organization_url, provider.tenant_id, provider.graph_client_id]);
  const save = async () => {
    setSaving(true); setMessage('');
    try {
      const configResponse = await auth.apiRequest(`/api/v1/providers/${provider.id}`, { method: 'PATCH', body: JSON.stringify({ organization_url: url.trim() || null, tenant_id: baseDn.trim() }) });
      if (!configResponse.ok) { const error = await configResponse.json().catch(() => null); setMessage(error?.error?.message || 'Unable to save the LDAP URL/base DN.'); return; }
      if (bindPassword.trim()) {
        const credResponse = await auth.apiRequest(`/api/v1/providers/${provider.id}/credentials`, { method: 'PATCH', body: JSON.stringify({ graph_client_id: bindUser.trim() || undefined, graph_client_secret: bindPassword.trim() }) });
        if (!credResponse.ok) { const error = await credResponse.json().catch(() => null); setMessage(error?.error?.message || 'Unable to save the bind username/password.'); return; }
        setBindPassword('');
      } else if (bindUser.trim() !== (provider.graph_client_id || '')) {
        // Username changed but no new password typed — the credentials endpoint always requires a password, so
        // there's nothing to save for just the username alone without also re-entering the password.
        setMessage('Enter the bind password too when changing the bind username.');
        return;
      }
      setMessage('Saved.'); onChanged();
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };
  const test = async () => {
    setTesting(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}/test-connection`, { method: 'POST' });
      if (response.ok) setMessage('Connection successful — bind succeeded.');
      else { const error = await response.json().catch(() => null); setMessage(error?.error?.message || 'Connection failed.'); }
      onChanged();
    } catch { setMessage('Connection failed. Please try again.'); } finally { setTesting(false); }
  };
  return <div className="detail-section">
    <div className="detail-title"><h2 style={{fontSize:14}}>LDAP connection</h2></div>
    <div className="key-grid">
      <label className="key"><span>LDAP URL</span><input className="select" placeholder="ldaps://10.0.0.5:636" value={url} onChange={event => setUrl(event.target.value)}/></label>
      <label className="key"><span>Base DN</span><input className="select" placeholder="DC=example,DC=local" value={baseDn} onChange={event => setBaseDn(event.target.value)}/></label>
      <label className="key"><span>Bind username / DN</span><input className="select" placeholder="svc-accesspilot@example.local" value={bindUser} onChange={event => setBindUser(event.target.value)}/></label>
      <label className="key"><span>Bind password</span><input type="password" autoComplete="off" className="select" placeholder={provider.credential_configured ? '**************' : 'Enter bind password'} value={bindPassword} onChange={event => setBindPassword(event.target.value)}/></label>
    </div>
    <div style={{display:'flex',gap:8,marginTop:12}}>
      <button type="button" className="btn btn-primary" disabled={saving} onClick={() => void save()}><Save size={14}/> {saving ? 'Saving...' : 'Save connection'}</button>
      <button type="button" className="btn" disabled={testing} onClick={() => void test()}><Activity size={14}/> {testing ? 'Testing...' : 'Test connection'}</button>
    </div>
    {message && <div className="notice" style={{marginTop:12}}>{message}</div>}
  </div>;
}
// Phase 2's real read-only sync, triggered directly from this card — the shared Sync page only ever shows
// Entra-or-first-provider (providers?.find(p => p.provider_type === 'ENTRA') || providers?.[0]), so a second
// provider like this one has no path to trigger a sync from there. Rather than change that shared page's
// established behavior for every other provider type, this is a self-contained "Sync now" scoped to this card —
// calls the exact same POST /providers/{id}/sync endpoint Entra's own sync already uses, unchanged.
function AdSyncPanel({ provider, onChanged }: { provider: Provider; onChanged: () => void }) {
  const auth = useAuth();
  const [syncing, setSyncing] = useState(false);
  const [lastRun, setLastRun] = useState<SyncRun | null>(null);
  const [message, setMessage] = useState('');
  const [intervalValue, setIntervalValue] = useState(provider.sync_interval_minutes ? String(provider.sync_interval_minutes) : '');
  const [intervalSaving, setIntervalSaving] = useState(false);
  const [intervalMessage, setIntervalMessage] = useState('');
  useEffect(() => { setIntervalValue(provider.sync_interval_minutes ? String(provider.sync_interval_minutes) : ''); }, [provider.id, provider.sync_interval_minutes]);
  const runSync = async () => {
    setSyncing(true); setMessage(''); setLastRun(null);
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}/sync`, { method: 'POST' });
      const body = await response.json().catch(() => null);
      if (response.ok) { setLastRun(body); onChanged(); }
      else setMessage(body?.error?.message || 'Sync failed.');
    } catch { setMessage('Sync failed. Please try again.'); } finally { setSyncing(false); }
  };
  const saveInterval = async () => {
    setIntervalSaving(true); setIntervalMessage('');
    try {
      const minutes = intervalValue.trim() ? Number(intervalValue.trim()) : null;
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}`, { method: 'PATCH', body: JSON.stringify({ sync_interval_minutes: minutes }) });
      if (response.ok) { setIntervalMessage(minutes ? `Saved — will sync automatically every ${minutes} minute${minutes === 1 ? '' : 's'}.` : 'Saved — automatic sync turned off; use "Sync now" manually.'); onChanged(); }
      else { const error = await response.json().catch(() => null); setIntervalMessage(error?.error?.message || 'Unable to save the sync schedule.'); }
    } catch { setIntervalMessage('Unable to save the sync schedule.'); } finally { setIntervalSaving(false); }
  };
  const lastSyncLabel = provider.last_sync_at ? new Date(provider.last_sync_at).toLocaleString() : 'Never';
  return <div className="detail-section">
    <div className="detail-title"><h2 style={{fontSize:14}}>Directory sync</h2></div>
    <p className="subtitle" style={{marginBottom:14}}>Real users and groups, read directly from this directory and synced into AccessPilot — direct group membership only, no roles/applications yet (see the connector's own notes for exactly what's covered).</p>
    <p className="footer-note" style={{marginBottom:12}}>Last sync: {lastSyncLabel}</p>
    <button type="button" className="btn btn-primary" disabled={syncing || !provider.credential_configured} onClick={() => void runSync()}><RefreshCw size={14}/> {syncing ? 'Syncing...' : 'Sync now'}</button>
    {!provider.credential_configured && <p className="footer-note" style={{marginTop:8}}>Save a bind username and password above first.</p>}
    {lastRun && <div className="notice" style={{marginTop:12}}>
      Sync {lastRun.status === 'COMPLETED' ? 'completed' : lastRun.status.toLowerCase()}: {lastRun.users_processed} users, {lastRun.groups_processed} groups, {lastRun.roles_processed} roles{lastRun.errors_count > 0 ? `, ${lastRun.errors_count} error${lastRun.errors_count === 1 ? '' : 's'}` : ''}.
    </div>}
    {message && <div className="notice" style={{marginTop:12}}>{message}</div>}
    <div style={{marginTop:18,paddingTop:14,borderTop:'1px solid #e4e7e9'}}>
      <label className="key" style={{display:'block',maxWidth:320}}><span>Sync automatically every (minutes)</span><input className="select" style={{width:'100%'}} type="number" min={1} max={10080} placeholder="Off — manual sync only" value={intervalValue} onChange={event => setIntervalValue(event.target.value)}/></label>
      <p className="footer-note" style={{marginTop:6}}>Leave blank to turn automatic sync off. A background worker checks every minute and re-syncs this directory once this interval has elapsed since the last run.</p>
      <button type="button" className="btn" style={{marginTop:8}} disabled={intervalSaving} onClick={() => void saveInterval()}><Save size={14}/> {intervalSaving ? 'Saving...' : 'Save schedule'}</button>
      {intervalMessage && <div className="notice" style={{marginTop:10}}>{intervalMessage}</div>}
    </div>
  </div>;
}
function ActiveDirectoryCard({ provider, onChanged }: { provider: Provider | undefined; onChanged: () => void }) {
  const auth = useAuth();
  const [name, setName] = useState('Corporate Active Directory');
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState('');
  const create = async () => {
    if (!name.trim()) { setMessage('Enter a name for this directory.'); return; }
    setCreating(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/providers', { method: 'POST', body: JSON.stringify({ name: name.trim(), provider_type: 'ACTIVE_DIRECTORY', tenant_id: name.trim() }) });
      if (response.ok) onChanged();
      else { const error = await response.json().catch(() => null); setMessage(error?.error?.message || 'Unable to add this directory.'); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setCreating(false); }
  };
  return <section className="panel">
    <div className="panel-head"><h2>Active Directory</h2>{provider && <StatusBadge status={provider.status}/>}</div>
    {!provider ? <div className="empty">
      <Cloud size={27} style={{marginBottom:10,color:'#a0afb3'}}/>
      <div>On-premises Active Directory, via a connectivity agent (Phase 1 — no LDAP sync yet).</div>
      <label className="key" style={{display:'block',margin:'15px auto 0',maxWidth:320,textAlign:'left'}}><span>Directory name</span><input className="select" style={{width:'100%'}} value={name} onChange={event => setName(event.target.value)}/></label>
      <button className="btn btn-primary" disabled={creating} onClick={() => void create()} style={{marginTop:12}}><Plus size={14}/> {creating ? 'Adding...' : 'Add Active Directory'}</button>
      {message && <div className="notice" style={{marginTop:12}}>{message}</div>}
    </div> : <><AdLdapConfigPanel provider={provider} onChanged={onChanged}/><AdSyncPanel provider={provider} onChanged={onChanged}/><AdAgentPanel provider={provider} onChanged={onChanged}/></>}
  </section>;
}
export default function ProviderConfiguration() {
  const auth = useAuth(); const [providers, setProviders] = useState<Provider[]>([]); const [form, setForm] = useState<ProviderForm>(createEmptyForm); const [editing, setEditing] = useState<Provider | null>(null); const [open, setOpen] = useState(false); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [testing, setTesting] = useState<string | null>(null); const [message, setMessage] = useState('');
  const [credentialForm, setCredentialForm] = useState({ graph_client_id: '', graph_client_secret: '' }); const [credentialSaving, setCredentialSaving] = useState(false); const [credentialMessage, setCredentialMessage] = useState('');
  const entraProvider = providers.find(provider => provider.provider_type === 'ENTRA');
  const adProvider = providers.find(provider => provider.provider_type === 'ACTIVE_DIRECTORY');
  const load = async () => { setLoading(true); try { const response = await auth.apiRequest('/api/v1/providers'); if (response.ok) setProviders(await response.json()); else setMessage(response.status === 401 ? 'Your session has expired. Please sign in again.' : response.status === 403 ? 'You do not have permission to manage identity providers.' : 'Unable to load provider configuration.'); } catch (error) { setMessage(error instanceof Error && error.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to manage identity providers.' : 'Unable to load provider configuration.'); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const save = async (event?: React.FormEvent) => { event?.preventDefault(); const required = ['name', 'tenant_id', 'client_id', 'authority', 'api_audience', 'api_scope'] as const; if (required.some(key => !String(form[key] || '').trim())) { setMessage('Please complete all required provider fields.'); return; } setSaving(true); setMessage(''); try { const response = await auth.apiRequest(editing ? `/api/v1/providers/${editing.id}` : '/api/v1/providers', { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(form) }); if (!response.ok) { const error = await response.json().catch(() => null); setMessage(response.status === 401 ? 'Your session has expired. Please sign in again.' : response.status === 403 ? 'You do not have permission to configure identity providers.' : error?.error?.message || 'Unable to save provider configuration. Please try again.'); return; } setOpen(false); setMessage('Configuration saved.'); await load(); } catch (error) { setMessage(error instanceof Error && error.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to configure identity providers.' : 'Unable to save provider configuration. Please try again.'); } finally { setSaving(false); } };
  const test = async (provider: Provider) => { setTesting(provider.id); setMessage(''); try { const response = await auth.apiRequest(`/api/v1/providers/${provider.id}/test-connection`, { method: 'POST' }); if (response.ok) { setMessage('Connection successful.'); } else { const error = await response.json().catch(() => null); setMessage(error?.error?.message || 'Connection failed.'); } await load(); } catch { setMessage('Connection failed. Please try again.'); await load(); } finally { setTesting(null); } };
  const startEdit = (provider?: Provider) => { setEditing(provider || null); setForm(provider ? toForm(provider) : createEmptyForm()); setOpen(true); setMessage(''); };
  const saveCredentials = async (event: React.FormEvent) => { event.preventDefault(); if (!entraProvider) return; if (!credentialForm.graph_client_secret.trim()) { setCredentialMessage('Enter the Graph client secret value.'); return; } setCredentialSaving(true); setCredentialMessage(''); try { const payload: Record<string, string> = { graph_client_secret: credentialForm.graph_client_secret }; if (credentialForm.graph_client_id.trim()) payload.graph_client_id = credentialForm.graph_client_id.trim(); const response = await auth.apiRequest(`/api/v1/providers/${entraProvider.id}/credentials`, { method: 'PATCH', body: JSON.stringify(payload) }); if (response.ok) { setCredentialForm({ graph_client_id: '', graph_client_secret: '' }); setCredentialMessage('Graph connector credentials saved.'); await load(); } else { const error = await response.json().catch(() => null); setCredentialMessage(response.status === 401 ? 'Your session has expired. Please sign in again.' : response.status === 403 ? 'You do not have permission to configure identity providers.' : error?.error?.message || 'Unable to save Graph connector credentials.'); } } catch (error) { setCredentialMessage(error instanceof Error && error.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to save Graph connector credentials.'); } finally { setCredentialSaving(false); } };
  return <Page title="Providers" subtitle="Connected identity providers and synchronization health." eyebrow="SYSTEM" action={<button className="btn btn-primary" onClick={() => startEdit()}><Plus size={14}/> Add provider</button>}><div className="grid-2"><section className="panel"><div className="panel-head"><h2>Microsoft Entra ID</h2><StatusBadge status={entraProvider?.status || 'NOT_CONFIGURED'}/></div>{loading ? <div className="empty">Loading provider configuration...</div> : !entraProvider ? <div className="empty"><Cloud size={27} style={{marginBottom:10,color:'#a0afb3'}}/><div>Microsoft Entra ID is not configured.</div><button className="btn btn-primary" onClick={() => startEdit()} style={{marginTop:15}}>Configure</button></div> : <><div className="detail-section"><div className="key-grid"><div className="key"><span>Tenant</span><strong>{entraProvider.tenant_id}</strong></div><div className="key"><span>Provider type</span><strong>Microsoft Entra ID</strong></div><div className="key"><span>Authority</span><strong>{entraProvider.authority || 'Not provided'}</strong></div><div className="key"><span>API audience</span><strong>{entraProvider.api_audience || 'Not provided'}</strong></div><div className="key"><span>API scope</span><strong>{entraProvider.api_scope || 'Not provided'}</strong></div><div className="key"><span>Last status</span><strong>{entraProvider.status}</strong></div><div className="key"><span>Graph client ID</span><strong>{entraProvider.graph_client_id || 'Not configured'}</strong></div><div className="key"><span>Graph client secret</span><strong>{entraProvider.credential_configured ? 'Configured' : 'Not configured'}</strong></div></div></div><div className="detail-section" style={{display:'flex',gap:8}}><button className="btn" onClick={() => startEdit(entraProvider)}><Pencil size={14}/> Edit</button><button className="btn btn-primary" disabled={testing === entraProvider.id} onClick={() => void test(entraProvider)}><Activity size={14}/> {testing === entraProvider.id ? 'Testing connection...' : 'Test connection'}</button></div><form className="detail-section" onSubmit={saveCredentials}><div className="detail-title"><h2 style={{fontSize:14}}>Graph connector credentials</h2></div><div className="key-grid"><label className="key"><span>Graph client ID</span><input className="select" placeholder={entraProvider.graph_client_id || 'Application (client) ID with Graph permissions'} value={credentialForm.graph_client_id} onChange={event => setCredentialForm({...credentialForm, graph_client_id: event.target.value})}/></label><label className="key"><span>Graph client secret</span><input type="password" autoComplete="off" className="select" placeholder="**************" value={credentialForm.graph_client_secret} onChange={event => setCredentialForm({...credentialForm, graph_client_secret: event.target.value})}/></label></div><div style={{display:'flex',justifyContent:'flex-end',gap:8,marginTop:12}}><button type="submit" className="btn btn-primary" disabled={credentialSaving}><Save size={14}/>{credentialSaving ? 'Saving...' : 'Save credentials'}</button></div>{credentialMessage && <div className="notice" style={{marginTop:12}}>{credentialMessage}</div>}</form><ProvisioningMappingPanel provider={entraProvider} onSaved={load}/></>}{message && <div className="detail-section"><div className="notice">{message}</div></div>}</section><section className="panel"><div className="panel-head"><h2>Okta</h2><StatusBadge status="COMING SOON"/></div><div className="empty"><Cloud size={27} style={{marginBottom:10,color:'#a0afb3'}}/><div>Additional provider connectors are planned for a future release.</div></div></section><ActiveDirectoryCard provider={adProvider} onChanged={load}/></div>{open && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:720,marginTop:18}} onSubmit={save}><div className="panel-head"><h2>{editing ? 'Edit provider' : 'Configure provider'}</h2><button type="button" className="btn" aria-label="Close" onClick={() => setOpen(false)}><X size={14}/></button></div><div className="detail-section"><div className="key-grid">{([['name','Provider name'],['tenant_id','Tenant ID'],['client_id','Client ID'],['authority','Authority'],['api_audience','API audience'],['api_scope','API scope']] as const).map(([key,label]) => <label className="key" key={key}><span>{label}</span><input className="select" value={form[key] || ''} onChange={event => setForm({...form, [key]: event.target.value})}/></label>)}</div><label className="key" style={{display:'block',marginTop:18}}><span>Redirect URI metadata</span><input className="select" style={{width:'100%'}} value={form.redirect_uri_metadata?.development || ''} onChange={event => setForm({...form, redirect_uri_metadata: { development: event.target.value }})}/></label></div><div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}><Save size={14}/>{saving ? 'Saving...' : 'Save configuration'}</button></div></form>}</Page>;
}
function Page({ eyebrow, title, subtitle, action, children }: { eyebrow: string; title: string; subtitle: string; action: React.ReactNode; children: React.ReactNode }) { return <div className="content"><div className="page-head"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p className="subtitle">{subtitle}</p></div>{action}</div>{children}</div>; }
function StatusBadge({ status }: { status: string }) { const cls = ['CONNECTED','CONFIGURED'].includes(status) ? 'success' : ['ERROR','DISABLED'].includes(status) ? 'danger' : 'neutral'; return <span className={`badge ${cls}`}>{status}</span>; }
