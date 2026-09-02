import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Activity, AlertTriangle, ArrowRight, BarChart3, Bell, BookOpen, Box, Check, ChevronRight, Clock3, Cloud, Copy, Database, ExternalLink, FileCheck2, FolderKanban, Gauge, Image, KeyRound, LayoutDashboard, LifeBuoy, ListChecks, Lock, Menu, Network, Plus, RefreshCw, Search, Settings2, Shield, ShieldAlert, ShieldCheck, SlidersHorizontal, UploadCloud, UserRound, Users, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { currentUser, policies, type RequestStatus, type Role } from './mock';
import { mockService, useMockState } from './mockService';
import { apiBaseUrl, useAuth } from './auth';
import ProviderConfiguration from './ProviderConfiguration';
import { BreakGlassDashboard } from './BreakGlassDashboard';
import { IdleGuard, useRefreshSecuritySettings } from './IdleGuard';
import logo from './assets/logo.png';

interface ApiUser { id: string; provider_id: string; external_id: string; email: string; display_name: string; given_name: string | null; surname: string | null; department: string | null; job_title: string | null; status: string; employee_id: string | null; source: string | null; last_synced_at: string | null; }
interface ApiGroup { id: string; external_id: string; name: string; description: string | null; is_privileged: boolean; status: string; last_synced_at: string | null; }
interface ApiRole { id: string; external_id: string; name: string; description: string | null; role_type: string; is_privileged: boolean; status: string; }
interface ApiApplicationRole { id: string; name: string; description: string | null; }
interface ApiApplication { id: string; external_id: string; name: string; status: string; app_roles: ApiApplicationRole[] | null; last_synced_at: string | null; }
interface ApiPackageItem { id: string; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; }
interface ApiPackageEligiblePrincipal { principal_type: string; principal_id: string; display_name: string | null; }
interface ApiPackage { id: string; name: string; description: string | null; status: string; items: ApiPackageItem[]; default_approver_id: string | null; default_fallback_approver_id: string | null; eligible_principals: ApiPackageEligiblePrincipal[]; created_at: string; }
interface ApiUserAccessItem { id: string | null; resource_type: string; resource_display_name: string | null; status: string; assignment_type: string; expiration_time: string | null; package_name: string | null; source: string; }
interface ApiUserLicense { sku_id: string; name: string; }
interface ApiUserAccessSummary { assignments: ApiUserAccessItem[]; licenses: ApiUserLicense[]; }
interface ApiPackageBatch { package_assignment_id: string; package_id: string; package_name: string; user_id: string; assignment_ids: string[]; }
interface DashboardAdmin { users: number; groups: number; roles: number; privilegedRoles: number; activeSessions: number; pendingRequests: number; expiringAccess: number; provider: { id: string; name: string; status: string; lastSyncAt: string | null } | null; lastSync: { id: string; status: string; startedAt: string; completedAt: string | null; usersProcessed: number; groupsProcessed: number; rolesProcessed: number; errorsCount: number } | null; }
interface ApiActivationTimeline { days: number; series: { date: string; count: number }[]; }
interface ApiUserAccessSegments { permanentActive: number; eligible: number; }
interface ApiSegmentMember { id: string; display_name: string; email: string; }
interface ApiOnboardingImport { id: string; filename: string; status: string; total_records: number; created_count: number; updated_count: number; disabled_count: number; no_change_count: number; failed_count: number; access_revoked_count: number; access_revoke_failed_count: number; real_accounts_provisioned_count: number; birthright_assignments_created_count: number; error_summary: Record<string, unknown> | null; created_at: string; completed_at: string | null; }
interface ApiOnboardingImportRecord { row_number: number; employee_id: string; action: string; error_message: string | null; raw_data: Record<string, string> | null; }
interface ApiSecuritySettings { blur_enabled: boolean; blur_after_minutes: number; lock_enabled: boolean; lock_after_minutes: number; logout_enabled: boolean; logout_after_minutes: number; }
interface ApiCurrentUser { id: string; displayName: string; email: string | null; tenantId: string; roles: string[]; department: string | null; jobTitle: string | null; employeeId: string | null; }
interface ApiSodEntity { id: string; conflict_side: string; entity_type: string; entity_id: string; entity_display_name: string | null; app_role_external_id: string | null; entity_resolved: boolean; }
interface ApiSodPolicy { id: string; name: string; description: string | null; severity: string; status: string; entities: ApiSodEntity[]; created_at: string; updated_at: string; }
interface ApiSodViolationHolding { assignment_id: string | null; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; source: string; }
interface ApiSodViolation { policy_id: string; policy_name: string; severity: string; user_id: string; user_display_name: string | null; side_a_holdings: ApiSodViolationHolding[]; side_b_holdings: ApiSodViolationHolding[]; exception_active: boolean; exception_expires_at: string | null; }
interface ApiSodAdmin { id: string; user_id: string; user_display_name: string | null; user_email: string | null; granted_by: string | null; granted_by_display_name: string | null; created_at: string; }
interface ApiSodException { id: string; sod_policy_id: string; policy_name: string | null; user_id: string; user_display_name: string | null; user_email: string | null; justification: string; granted_by: string | null; granted_by_display_name: string | null; expires_at: string; revoked_at: string | null; is_active: boolean; created_at: string; }
interface ApiSodNotificationSettings { notify_on_new_violation: boolean; notify_on_exception_expiring: boolean; exception_expiring_warning_days: number; }
interface ApiSodNotification { id: string; notification_type: string; sod_policy_id: string | null; policy_name: string | null; user_id: string | null; user_display_name: string | null; message: string; read_at: string | null; resolved_at: string | null; created_at: string; }
interface ApiSodActivityEntry { id: string; timestamp: string; actor_display_name: string | null; action: string; target_user_display_name: string | null; result: string; metadata: Record<string, unknown> | null; }
function summarizeSodActivity(entry: ApiSodActivityEntry): string {
  const m = entry.metadata || {};
  const parts: string[] = [];
  if (typeof m.name === 'string') parts.push(`"${m.name}"`);
  if (typeof m.severity === 'string') parts.push(String(m.severity));
  if (Array.isArray(m.conflicting_policies) && m.conflicting_policies.length > 0) parts.push(`conflicts: ${(m.conflicting_policies as string[]).join(', ')}`);
  if (m.sod_override) parts.push('overridden by admin');
  return parts.join(' · ') || '—';
}

function useApiResource<T>(path: string, enabled = true) {
  const auth = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(enabled);
  const [reloadToken, setReloadToken] = useState(0);
  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    let cancelled = false;
    const load = async () => {
      setLoading(true); setError('');
      try {
        const response = await auth.apiRequest(path);
        if (cancelled) return;
        if (response.ok) setData(await response.json());
        else setError(response.status === 401 ? 'Your session has expired. Please sign in again.' : response.status === 403 ? 'You do not have permission to view this.' : 'Unable to load data.');
      } catch (err) {
        if (!cancelled) setError(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to load data.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled, reloadToken]);
  return { data, error, loading, reload: () => setReloadToken(token => token + 1) };
}

interface ApiBranding { sign_in_logo: string | null; internal_logo: string | null; powered_by_text: string | null; }
function useBranding() {
  // Deliberately a raw, unauthenticated fetch — GET /branding is public (the sign-in screen needs it before
  // anyone has logged in), so this must never go through apiRequest()'s "requires an account" logic.
  const [branding, setBranding] = useState<ApiBranding | null>(null);
  useEffect(() => {
    let ignore = false;
    fetch(`${apiBaseUrl}/api/v1/branding`).then(response => response.ok ? response.json() : null).then(data => { if (!ignore && data) setBranding(data); }).catch(() => {});
    return () => { ignore = true; };
  }, []);
  return branding;
}

const nav = [
  { label: 'Dashboard', icon: LayoutDashboard, to: '/dashboard', roles: ['user','admin'] },
  { label: 'My Access', icon: KeyRound, to: '/my-access', roles: ['user'] },
  { label: 'Request Access', icon: Plus, to: '/request-access', roles: ['user'] },
  { label: 'Request Packages', icon: Box, to: '/request-packages', roles: ['user'] },
  { label: 'My Requests', icon: ListChecks, to: '/my-requests', roles: ['user'] },
  { label: 'Approvals', icon: Check, to: '/approvals', roles: ['user','admin'] },
  { label: 'Profile', icon: UserRound, to: '/profile', roles: ['user','admin'] },
  { label: 'Users', icon: Users, to: '/admin/users', roles: ['admin'], section: 'ADMINISTRATION' },
  { label: 'Groups', icon: Network, to: '/admin/groups', roles: ['admin'] },
  { label: 'Roles', icon: Shield, to: '/admin/roles', roles: ['admin'] },
  { label: 'Access Requests', icon: FolderKanban, to: '/admin/access-requests', roles: ['admin'], section: 'ACCESS MANAGEMENT' },
  { label: 'Assignments', icon: KeyRound, to: '/admin/assignments', roles: ['admin'] },
  { label: 'Access Packages', icon: Box, to: '/admin/access-packages', roles: ['admin'] },
  { label: 'Policies', icon: SlidersHorizontal, to: '/admin/policies', roles: ['admin'], section: 'GOVERNANCE' },
  { label: 'Audit Logs', icon: BookOpen, to: '/admin/audit', roles: ['admin'] },
  // Its own sidebar section, not folded into GOVERNANCE — also visible to a plain end-user who holds the
  // DB-driven AccessPilot.SoDAdmin flag (see Shell's nav filter, which additionally checks auth.isSodAdmin for
  // items marked extra: 'sod') — 'admin' alone is neither sufficient nor necessary for these two.
  { label: 'Separation of Duties', icon: ShieldAlert, to: '/admin/sod', roles: ['admin'], extra: 'sod', section: 'SEPARATION OF DUTIES' },
  { label: 'SoD Configuration', icon: Settings2, to: '/admin/sod/configuration', roles: ['admin'], extra: 'sod' },
  { label: 'Providers', icon: Cloud, to: '/admin/providers', roles: ['admin'], section: 'SYSTEM' },
  { label: 'Sync', icon: RefreshCw, to: '/admin/sync', roles: ['admin'] },
  { label: 'Onboarding', icon: UploadCloud, to: '/admin/onboarding', roles: ['admin'] },
  { label: 'Security', icon: Lock, to: '/admin/security', roles: ['admin'] },
  { label: 'Branding', icon: Image, to: '/admin/branding', roles: ['admin'] },
];

function App() {
  const auth = useAuth();
  const [mockRole, setMockRole] = useState<Role>(() => (localStorage.getItem('accesspilot.mockRole') as Role) || 'admin');
  if (auth.authConfigured && auth.loading) return <div className="empty">Loading AccessPilot authentication...</div>;
  if (auth.authConfigured && !auth.account && !auth.breakglassActive) return <SignInScreen />;
  if (auth.breakglassActive && !auth.breakglassElevated) return <BreakGlassDashboard />;
  const role = auth.authConfigured ? auth.role : mockRole;
  const changeRole = (nextRole: Role) => { localStorage.setItem('accesspilot.mockRole', nextRole); setMockRole(nextRole); };
  return <IdleGuard><Shell role={role} setRole={changeRole}><Routes><Route path="/" element={<Navigate to="/dashboard" replace />} /><Route path="/dashboard" element={<Dashboard role={role} />} /><Route path="/my-access" element={<MyAccess />} /><Route path="/request-access" element={<RequestAccess />} /><Route path="/request-packages" element={<RequestPackagesPage />} /><Route path="/my-requests" element={<Requests mine />} /><Route path="/approvals" element={<MyApprovalsPage />} /><Route path="/profile" element={<Profile />} /><Route path="/admin/users" element={<AdminOnly role={role}><UsersPage /></AdminOnly>} /><Route path="/admin/users/:id" element={<AdminOnly role={role}><UserDetail /></AdminOnly>} /><Route path="/admin/groups" element={<AdminOnly role={role}><GroupsPage /></AdminOnly>} /><Route path="/admin/roles" element={<AdminOnly role={role}><RolesPage /></AdminOnly>} /><Route path="/admin/access-requests" element={<AdminOnly role={role}><Requests /></AdminOnly>} /><Route path="/admin/access-requests/:id" element={<AdminOnly role={role}><RequestDetailInteractive /></AdminOnly>} /><Route path="/admin/assignments" element={<AdminOnly role={role}><AssignmentsInteractive /></AdminOnly>} /><Route path="/admin/access-packages" element={<AdminOnly role={role}><AccessPackagesInteractive /></AdminOnly>} /><Route path="/admin/policies" element={<AdminOnly role={role}><PoliciesPage /></AdminOnly>} /><Route path="/admin/audit" element={<AdminOnly role={role}><AuditPage /></AdminOnly>} /><Route path="/admin/providers" element={<AdminOnly role={role}><ProvidersPage /></AdminOnly>} /><Route path="/admin/sync" element={<AdminOnly role={role}><SyncPage /></AdminOnly>} /><Route path="/admin/onboarding" element={<AdminOnly role={role}><OnboardingPage /></AdminOnly>} /><Route path="/admin/security" element={<AdminOnly role={role}><SecurityPage /></AdminOnly>} /><Route path="/admin/branding" element={<AdminOnly role={role}><BrandingPage /></AdminOnly>} /><Route path="/admin/sod" element={role === 'admin' || auth.isSodAdmin ? <SodPage /> : <Navigate to="/dashboard" replace />} /><Route path="/admin/sod/configuration" element={role === 'admin' || auth.isSodAdmin ? <SodConfigurationPage /> : <Navigate to="/dashboard" replace />} /><Route path="*" element={<Navigate to="/dashboard" replace />} /></Routes></Shell></IdleGuard>;
}
function SignInScreen() {
  const auth = useAuth();
  const branding = useBranding();
  // Deliberately no mention of Break-Glass anywhere on this screen, for any user — it's reachable only via the
  // hidden /emergency-access/:token URL (src/EmergencyAccess.tsx), generated solely by a console command
  // (backend/app/cli.py). An IDP outage shows a generic notice here, never an actionable recovery hint.
  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: '#f4f7f9', padding: 24 }}>
      <img src={branding?.sign_in_logo || logo} alt="AccessPilot" style={{ width: 140, height: 140, objectFit: 'contain', marginBottom: 26 }} />
      <h1>Sign in to AccessPilot</h1>
      <p className="subtitle">Use your Microsoft Entra account to continue.</p>
      {auth.idpUnreachable && <div className="notice" style={{ background: '#fdecea', color: '#8c2b21', marginTop: 14, maxWidth: 380 }}>The identity provider is currently unavailable. Please contact your administrator.</div>}
      <button className="btn btn-primary" onClick={auth.signIn} style={{ marginTop: 18 }}>Sign in</button>
      <div style={{ position: 'fixed', right: 24, bottom: 20, fontSize: 11, color: '#8a9296' }}>Powered by <strong style={{ color: '#52656d' }}>{branding?.powered_by_text || 'Clover-X'}</strong></div>
    </div>
  );
}
function SecurityPage() {
  const auth = useAuth();
  const { data, loading, reload } = useApiResource<ApiSecuritySettings>('/api/v1/security-settings');
  const [form, setForm] = useState<ApiSecuritySettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const refreshIdleGuard = useRefreshSecuritySettings();
  useEffect(() => { if (data) setForm(data); }, [data]);
  const save = async () => {
    if (!form) return;
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/security-settings', { method: 'PATCH', body: JSON.stringify(form) });
      if (response.ok) { setMessage('Saved.'); reload(); void refreshIdleGuard?.(); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to save.'); }
    } catch {
      setMessage('Unable to reach the backend.');
    } finally { setSaving(false); }
  };
  return <Page eyebrow="SYSTEM" title="Security" subtitle="Idle-session behavior applied to every signed-in user, admin and end-user alike.">
    {loading || !form ? <div className="empty">Loading...</div> : <div className="panel"><div className="detail-section">
      <label style={{display:'flex',alignItems:'center',gap:10,marginBottom:12}}><input type="checkbox" checked={form.blur_enabled} onChange={event => setForm({...form, blur_enabled: event.target.checked})}/><span>Blur the screen after inactivity</span></label>
      <label className="key" style={{display:'block',marginBottom:22,maxWidth:220}}><span>Blur after (minutes)</span><input className="select" style={{width:'100%'}} type="number" min={1} max={120} disabled={!form.blur_enabled} value={form.blur_after_minutes} onChange={event => setForm({...form, blur_after_minutes: Number(event.target.value)})}/></label>
      <label style={{display:'flex',alignItems:'center',gap:10,marginBottom:12}}><input type="checkbox" checked={form.lock_enabled} onChange={event => setForm({...form, lock_enabled: event.target.checked})}/><span>Lock the screen after inactivity — requires clicking "Continue" to resume; never signs the user out</span></label>
      <label className="key" style={{display:'block',marginBottom:22,maxWidth:220}}><span>Lock after (minutes)</span><input className="select" style={{width:'100%'}} type="number" min={1} max={120} disabled={!form.lock_enabled} value={form.lock_after_minutes} onChange={event => setForm({...form, lock_after_minutes: Number(event.target.value)})}/></label>
      <label style={{display:'flex',alignItems:'center',gap:10,marginBottom:12}}><input type="checkbox" checked={form.logout_enabled} onChange={event => setForm({...form, logout_enabled: event.target.checked})}/><span>Automatically sign out after inactivity — ends the session; the user must sign in again</span></label>
      <label className="key" style={{display:'block',marginBottom:22,maxWidth:220}}><span>Sign out after (minutes)</span><input className="select" style={{width:'100%'}} type="number" min={1} max={480} disabled={!form.logout_enabled} value={form.logout_after_minutes} onChange={event => setForm({...form, logout_after_minutes: Number(event.target.value)})}/></label>
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      <button className="btn btn-primary" disabled={saving} onClick={save}>{saving ? 'Saving...' : 'Save'}</button>
    </div></div>}
  </Page>;
}
function BrandingPage() {
  const auth = useAuth();
  const { data, loading } = useApiResource<ApiBranding>('/api/v1/branding');
  const [form, setForm] = useState<{ sign_in_logo: string | null; internal_logo: string | null; powered_by_text: string }>({ sign_in_logo: null, internal_logo: null, powered_by_text: '' });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { if (data) setForm({ sign_in_logo: data.sign_in_logo, internal_logo: data.internal_logo, powered_by_text: data.powered_by_text || '' }); }, [data]);

  const readFile = (file: File, key: 'sign_in_logo' | 'internal_logo') => {
    setMessage('');
    if (file.size > 2_000_000) { setMessage('Image must be under 2MB.'); return; }
    if (!['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(file.type)) { setMessage('Only PNG, JPEG, GIF, or WEBP images are supported.'); return; }
    const reader = new FileReader();
    reader.onload = () => setForm(current => ({ ...current, [key]: reader.result as string }));
    reader.readAsDataURL(file);
  };

  const save = async () => {
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/branding', { method: 'PATCH', body: JSON.stringify({ sign_in_logo: form.sign_in_logo, internal_logo: form.internal_logo, powered_by_text: form.powered_by_text.trim() || null }) });
      if (response.ok) {
        // A full reload picks up the new branding everywhere at once (sidebar, sign-in screen) rather than
        // wiring a live-refresh channel for something admins change rarely.
        window.location.reload();
      } else {
        const body = await response.json().catch(() => null);
        setMessage(body?.error?.message || 'Unable to save.');
      }
    } catch {
      setMessage('Unable to reach the backend.');
    } finally { setSaving(false); }
  };

  return <Page eyebrow="SYSTEM" title="Branding" subtitle="Customize the logo shown on the public sign-in screen, the logo inside the app, and the attribution text.">
    {loading ? <div className="empty">Loading...</div> : <div className="panel"><div className="detail-section">
      <div className="key" style={{marginBottom:10}}><span>Sign-in page logo</span></div>
      <div style={{display:'flex',alignItems:'center',gap:16,marginBottom:26}}>
        <img src={form.sign_in_logo || logo} alt="Sign-in logo preview" style={{width:64,height:64,objectFit:'contain',background:'#f4f7f9',borderRadius:8,padding:6}}/>
        <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={event => event.target.files?.[0] && readFile(event.target.files[0], 'sign_in_logo')}/>
        {form.sign_in_logo && <button type="button" className="btn" onClick={() => setForm(current => ({...current, sign_in_logo: null}))}>Reset to default</button>}
      </div>
      <div className="key" style={{marginBottom:10}}><span>Internal (sidebar) logo</span></div>
      <div style={{display:'flex',alignItems:'center',gap:16,marginBottom:26}}>
        <img src={form.internal_logo || logo} alt="Internal logo preview" style={{width:64,height:64,objectFit:'contain',background:'#123944',borderRadius:8,padding:6}}/>
        <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={event => event.target.files?.[0] && readFile(event.target.files[0], 'internal_logo')}/>
        {form.internal_logo && <button type="button" className="btn" onClick={() => setForm(current => ({...current, internal_logo: null}))}>Reset to default</button>}
      </div>
      <label className="key" style={{display:'block',marginBottom:24,maxWidth:280}}><span>Powered by text</span><input className="select" style={{width:'100%'}} value={form.powered_by_text} onChange={event => setForm(current => ({...current, powered_by_text: event.target.value}))} placeholder="Clover-X"/></label>
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      <button className="btn btn-primary" disabled={saving} onClick={save}>{saving ? 'Saving...' : 'Save'}</button>
    </div></div>}
  </Page>;
}
interface SodEntityRow { entity_type: string; entity_id: string; app_role_external_id: string }
const emptySodEntity: SodEntityRow = { entity_type: 'GROUP', entity_id: '', app_role_external_id: '' };
function SodEntityPicker({ rows, groups, roles, applications, packages, onAdd, onRemove, onUpdate }: {
  rows: SodEntityRow[]; groups: ApiGroup[] | null; roles: ApiRole[] | null; applications: ApiApplication[] | null; packages: ApiPackage[] | null;
  onAdd: () => void; onRemove: (index: number) => void; onUpdate: (index: number, patch: Partial<SodEntityRow>) => void;
}) {
  return <div>
    {rows.map((row, index) => {
      const options: { id: string; name: string }[] = row.entity_type === 'GROUP' ? (groups || []) : row.entity_type === 'ROLE' ? (roles || []) : row.entity_type === 'APPLICATION' ? (applications || []) : (packages || []);
      const selectedApplication = row.entity_type === 'APPLICATION' ? (applications || []).find(a => a.id === row.entity_id) : null;
      return <div key={index} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
        <select className="select" value={row.entity_type} onChange={event => onUpdate(index, { entity_type: event.target.value, entity_id: '', app_role_external_id: '' })}>
          <option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option><option value="PACKAGE">Access Package</option>
        </select>
        <select className="select" value={row.entity_id} onChange={event => onUpdate(index, { entity_id: event.target.value })}>
          <option value="">Select...</option>
          {options.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
        {row.entity_type === 'APPLICATION' && <select className="select" value={row.app_role_external_id} onChange={event => onUpdate(index, { app_role_external_id: event.target.value })} disabled={!selectedApplication}>
          <option value="">Select a role</option>
          {(selectedApplication?.app_roles || []).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>}
        <button type="button" className="btn" onClick={() => onRemove(index)}>Remove</button>
      </div>;
    })}
    <button type="button" className="btn" onClick={onAdd}>+ Add entity</button>
  </div>;
}
const emptySodForm = { name: '', description: '', severity: 'MEDIUM', sideA: [] as SodEntityRow[], sideB: [] as SodEntityRow[] };
function SodPage() {
  const auth = useAuth();
  const canManageRules = auth.isSodAdmin;
  const canManageRoster = auth.role === 'admin';
  const { data: policies, loading, reload } = useApiResource<ApiSodPolicy[]>('/api/v1/sod/policies');
  const { data: violations, reload: reloadViolations } = useApiResource<ApiSodViolation[]>('/api/v1/sod/violations');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const { data: packages } = useApiResource<ApiPackage[]>('/api/v1/packages');
  const { data: admins, reload: reloadAdmins } = useApiResource<ApiSodAdmin[]>('/api/v1/sod/admins', canManageRoster);
  const { data: directoryUsers } = useApiResource<ApiUser[]>('/api/v1/users', canManageRoster);
  const { data: activity } = useApiResource<ApiSodActivityEntry[]>('/api/v1/sod/activity');
  const { data: exceptions, reload: reloadExceptions } = useApiResource<ApiSodException[]>('/api/v1/sod/exceptions');
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptySodForm);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [newAdminUserId, setNewAdminUserId] = useState('');
  const [exceptionForm, setExceptionForm] = useState<{ policyId: string; policyName: string; userId: string; userLabel: string; justification: string; expiresAt: string } | null>(null);
  const [exceptionSaving, setExceptionSaving] = useState(false);
  const [exceptionMessage, setExceptionMessage] = useState('');
  const defaultExpiry = () => { const d = new Date(); d.setDate(d.getDate() + 30); return d.toISOString().slice(0, 16); };
  const openExceptionForm = (policyId: string, policyName: string, userId: string, userLabel: string) => { setExceptionForm({ policyId, policyName, userId, userLabel, justification: '', expiresAt: defaultExpiry() }); setExceptionMessage(''); };
  const submitException = async () => {
    if (!exceptionForm) return;
    if (exceptionForm.justification.trim().length < 3) { setExceptionMessage('A justification (at least 3 characters) is required.'); return; }
    if (!exceptionForm.expiresAt) { setExceptionMessage('Pick an expiry date.'); return; }
    setExceptionSaving(true); setExceptionMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/sod/exceptions', { method: 'POST', body: JSON.stringify({ sod_policy_id: exceptionForm.policyId, user_id: exceptionForm.userId, justification: exceptionForm.justification.trim(), expires_at: new Date(exceptionForm.expiresAt).toISOString() }) });
      if (response.ok) { setExceptionForm(null); reloadExceptions(); reloadViolations(); }
      else { const body = await response.json().catch(() => null); setExceptionMessage(body?.error?.message || 'Unable to grant this exception.'); }
    } catch { setExceptionMessage('Unable to reach the backend.'); }
    finally { setExceptionSaving(false); }
  };
  const revokeException = async (id: string) => {
    if (!window.confirm('Revoke this exception now? The conflict will be blocked again immediately for any new grant.')) return;
    const response = await auth.apiRequest(`/api/v1/sod/exceptions/${id}`, { method: 'DELETE' });
    if (response.ok) { reloadExceptions(); reloadViolations(); }
  };

  const startCreate = () => { setForm(emptySodForm); setEditingId(null); setShowForm(true); setMessage(''); };
  const startEdit = (policy: ApiSodPolicy) => {
    setForm({
      name: policy.name, description: policy.description || '', severity: policy.severity,
      sideA: policy.entities.filter(e => e.conflict_side === 'A').map(e => ({ entity_type: e.entity_type, entity_id: e.entity_id, app_role_external_id: e.app_role_external_id || '' })),
      sideB: policy.entities.filter(e => e.conflict_side === 'B').map(e => ({ entity_type: e.entity_type, entity_id: e.entity_id, app_role_external_id: e.app_role_external_id || '' })),
    });
    setEditingId(policy.id); setShowForm(true); setMessage('');
  };
  const addEntity = (side: 'sideA' | 'sideB') => setForm({ ...form, [side]: [...form[side], { ...emptySodEntity }] });
  const removeEntity = (side: 'sideA' | 'sideB', index: number) => setForm({ ...form, [side]: form[side].filter((_, i) => i !== index) });
  const updateEntity = (side: 'sideA' | 'sideB', index: number, patch: Partial<SodEntityRow>) => setForm({ ...form, [side]: form[side].map((row, i) => i === index ? { ...row, ...patch } : row) });

  const submit = async () => {
    setMessage('');
    if (!form.name.trim()) { setMessage('Name is required.'); return; }
    if (form.sideA.length === 0 || form.sideB.length === 0) { setMessage('A policy needs at least one entity on each side.'); return; }
    if ([...form.sideA, ...form.sideB].some(e => !e.entity_id || (e.entity_type === 'APPLICATION' && !e.app_role_external_id))) { setMessage('Complete every entity (select a target, and an application role where needed).'); return; }
    setSaving(true);
    try {
      const entities = [
        ...form.sideA.map(e => ({ conflict_side: 'A', entity_type: e.entity_type, entity_id: e.entity_id, app_role_external_id: e.entity_type === 'APPLICATION' ? e.app_role_external_id : undefined })),
        ...form.sideB.map(e => ({ conflict_side: 'B', entity_type: e.entity_type, entity_id: e.entity_id, app_role_external_id: e.entity_type === 'APPLICATION' ? e.app_role_external_id : undefined })),
      ];
      const payload: Record<string, unknown> = { name: form.name.trim(), description: form.description.trim() || undefined, severity: form.severity, entities };
      if (editingId) payload.status = 'ACTIVE';
      const response = await auth.apiRequest(editingId ? `/api/v1/sod/policies/${editingId}` : '/api/v1/sod/policies', { method: editingId ? 'PATCH' : 'POST', body: JSON.stringify(payload) });
      if (response.ok) { setShowForm(false); reload(); reloadViolations(); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to save this policy.'); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };

  const toggleStatus = async (policy: ApiSodPolicy) => {
    const payload = { name: policy.name, description: policy.description || undefined, severity: policy.severity, status: policy.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE', entities: policy.entities.map(e => ({ conflict_side: e.conflict_side, entity_type: e.entity_type, entity_id: e.entity_id, app_role_external_id: e.app_role_external_id || undefined })) };
    const response = await auth.apiRequest(`/api/v1/sod/policies/${policy.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
    if (response.ok) { reload(); reloadViolations(); }
  };

  const remove = async (policy: ApiSodPolicy) => {
    if (!window.confirm(`Delete the SoD policy "${policy.name}"? This cannot be undone.`)) return;
    const response = await auth.apiRequest(`/api/v1/sod/policies/${policy.id}`, { method: 'DELETE' });
    if (response.ok) { reload(); reloadViolations(); }
  };

  const addAdmin = async () => {
    if (!newAdminUserId) return;
    const response = await auth.apiRequest('/api/v1/sod/admins', { method: 'POST', body: JSON.stringify({ user_id: newAdminUserId }) });
    if (response.ok) { setNewAdminUserId(''); reloadAdmins(); }
  };
  const removeAdmin = async (userId: string) => {
    const response = await auth.apiRequest(`/api/v1/sod/admins/${userId}`, { method: 'DELETE' });
    if (response.ok) reloadAdmins();
  };

  const summarize = (policy: ApiSodPolicy, side: string) => policy.entities.filter(e => e.conflict_side === side).map(e => e.entity_display_name || 'Unresolved').join(', ') || '—';

  return <Page eyebrow="GOVERNANCE" title="Separation of Duties" subtitle="Admin-configurable rules preventing any user from holding two conflicting entitlements at once — enforced live, at the moment access actually becomes real." action={canManageRules && <button className="btn btn-primary" onClick={startCreate}>+ Add SoD policy</button>}>
    {!canManageRules && <div className="notice" style={{ marginBottom: 18 }}>You can view rules and violations. Only an AccessPilot.SoDAdmin can create, edit, or disable rules — ask an Admin to grant that if you need it.</div>}
    {showForm && canManageRules && <div className="panel" style={{ marginBottom: 24 }}><div className="detail-section">
      <div className="detail-title"><h2>{editingId ? 'Edit SoD policy' : 'New SoD policy'}</h2></div>
      <label className="key" style={{ display: 'block', marginBottom: 14, maxWidth: 420 }}><span>Name</span><input className="select" style={{ width: '100%' }} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 14, maxWidth: 420 }}><span>Description</span><input className="select" style={{ width: '100%' }} value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 20, maxWidth: 220 }}><span>Severity</span><select className="select" style={{ width: '100%' }} value={form.severity} onChange={event => setForm({ ...form, severity: event.target.value })}><option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option><option value="CRITICAL">Critical</option></select></label>
      <div className="key" style={{ marginBottom: 8 }}><span>Side A — holding anything here...</span></div>
      <SodEntityPicker rows={form.sideA} groups={groups} roles={roles} applications={applications} packages={packages} onAdd={() => addEntity('sideA')} onRemove={i => removeEntity('sideA', i)} onUpdate={(i, patch) => updateEntity('sideA', i, patch)} />
      <div className="key" style={{ margin: '18px 0 8px' }}><span>...conflicts with holding anything here (Side B)</span></div>
      <SodEntityPicker rows={form.sideB} groups={groups} roles={roles} applications={applications} packages={packages} onAdd={() => addEntity('sideB')} onRemove={i => removeEntity('sideB', i)} onUpdate={(i, patch) => updateEntity('sideB', i, patch)} />
      {message && <div className="notice" style={{ margin: '14px 0' }}>{message}</div>}
      <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
        <button className="btn btn-primary" disabled={saving} onClick={submit}>{saving ? 'Saving...' : 'Save policy'}</button>
        <button className="btn" onClick={() => setShowForm(false)}>Cancel</button>
      </div>
    </div></div>}

    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Policies</h2></div>
      {loading ? <div className="empty">Loading...</div> : !policies || policies.length === 0 ? <div className="empty">No SoD policies defined yet.</div> : <table className="table"><thead><tr><th>Name</th><th>Severity</th><th>Status</th><th>Side A</th><th>Side B</th>{canManageRules && <th>Actions</th>}</tr></thead><tbody>
        {policies.map(policy => <tr key={policy.id}>
          <td>{policy.name}</td><td>{policy.severity}</td><td><StatusBadge status={policy.status} /></td>
          <td>{summarize(policy, 'A')}</td><td>{summarize(policy, 'B')}</td>
          {canManageRules && <td style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={() => startEdit(policy)}>Edit</button>
            <button className="btn" onClick={() => toggleStatus(policy)}>{policy.status === 'ACTIVE' ? 'Disable' : 'Enable'}</button>
            <button className="btn" onClick={() => remove(policy)}>Delete</button>
          </td>}
        </tr>)}
      </tbody></table>}
    </div>

    {exceptionForm && <form role="dialog" aria-modal="true" className="panel" style={{ maxWidth: 480, marginBottom: 24 }} onSubmit={event => { event.preventDefault(); void submitException(); }}>
      <div className="panel-head"><h2>Grant an exception</h2><button type="button" className="btn" aria-label="Close" onClick={() => setExceptionForm(null)}><X size={14} /></button></div>
      <div className="detail-section">
        <p className="subtitle" style={{ marginTop: 0 }}>Formally accept this specific conflict for <strong>{exceptionForm.userLabel}</strong> on policy <strong>{exceptionForm.policyName}</strong>, for a bounded period. New grants for this user on this rule won't be blocked while the exception is active — it can be revoked early at any time.</p>
        <label className="key" style={{ display: 'block', marginBottom: 14 }}><span>Justification — why is this risk acceptable? (required)</span><input className="select" style={{ width: '100%' }} required value={exceptionForm.justification} onChange={event => setExceptionForm({ ...exceptionForm, justification: event.target.value })} /></label>
        <label className="key" style={{ display: 'block' }}><span>Expires</span><input className="select" style={{ width: '100%' }} type="datetime-local" required value={exceptionForm.expiresAt} onChange={event => setExceptionForm({ ...exceptionForm, expiresAt: event.target.value })} /></label>
        {exceptionMessage && <div className="notice" style={{ marginTop: 14 }}>{exceptionMessage}</div>}
      </div>
      <div className="detail-section" style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}><button type="button" className="btn" onClick={() => setExceptionForm(null)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={exceptionSaving}>{exceptionSaving ? 'Granting...' : 'Grant exception'}</button></div>
    </form>}

    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Violations</h2></div>
      {!violations ? <div className="empty">Loading...</div> : violations.length === 0 ? <div className="empty">No current violations — nobody holds both sides of an active rule.</div> : <table className="table"><thead><tr><th>User</th><th>Policy</th><th>Severity</th><th>Side A holdings</th><th>Side B holdings</th><th>Risk status</th></tr></thead><tbody>
        {violations.map((v, i) => <tr key={i}>
          <td>{v.user_display_name || v.user_id}</td><td>{v.policy_name}</td><td>{v.severity}</td>
          <td>{v.side_a_holdings.map(h => `${h.resource_display_name || h.resource_type}${h.source === 'DIRECT_IN_ENTRA' ? ' (direct in Entra)' : ''}`).join(', ')}</td>
          <td>{v.side_b_holdings.map(h => `${h.resource_display_name || h.resource_type}${h.source === 'DIRECT_IN_ENTRA' ? ' (direct in Entra)' : ''}`).join(', ')}</td>
          <td>{v.exception_active
            ? <span className="badge success">Accepted until {v.exception_expires_at ? new Date(v.exception_expires_at).toLocaleDateString() : '—'}</span>
            : canManageRules ? <button className="btn" onClick={() => openExceptionForm(v.policy_id, v.policy_name, v.user_id, v.user_display_name || v.user_id)}>Grant exception</button> : <span className="badge danger">Open</span>}
          </td>
        </tr>)}
      </tbody></table>}
    </div>

    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Active Exceptions</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{ marginTop: 0, marginBottom: 16 }}>Time-boxed risk acceptances — while one is active, new grants for that user on that rule aren't blocked. Every grant, revoke, and expiry is on the record in SoD Activity below.</p>
        {!exceptions || exceptions.length === 0 ? <div className="empty">No exceptions have ever been granted.</div> : <table className="table"><thead><tr><th>User</th><th>Policy</th><th>Justification</th><th>Granted by</th><th>Status</th><th>Actions</th></tr></thead><tbody>
          {exceptions.map(exception => <tr key={exception.id}>
            <td>{exception.user_display_name || exception.user_id}</td>
            <td>{exception.policy_name || '—'}</td>
            <td>{exception.justification}</td>
            <td>{exception.granted_by_display_name || '—'}</td>
            <td>{exception.revoked_at ? <span className="badge neutral">Revoked</span> : exception.is_active ? <span className="badge success">Active until {new Date(exception.expires_at).toLocaleString()}</span> : <span className="badge neutral">Expired</span>}</td>
            <td>{canManageRules && exception.is_active && <button className="btn" onClick={() => revokeException(exception.id)}>Revoke</button>}</td>
          </tr>)}
        </tbody></table>}
      </div>
    </div>

    <div className="panel" style={{ marginBottom: canManageRoster ? 24 : 0 }}>
      <div className="panel-head"><h2>SoD Activity</h2></div>
      {!activity ? <div className="empty">Loading...</div> : activity.length === 0 ? <div className="empty">No SoD activity yet — rule changes, roster changes, and any blocked or overridden grant will show up here.</div> : <table className="table"><thead><tr><th>When</th><th>Action</th><th>Actor</th><th>Target user</th><th>Result</th><th>Details</th></tr></thead><tbody>
        {activity.map(entry => <tr key={entry.id}>
          <td>{new Date(entry.timestamp).toLocaleString()}</td>
          <td>{entry.action.replace(/_/g, ' ')}</td>
          <td>{entry.actor_display_name || 'System'}</td>
          <td>{entry.target_user_display_name || '—'}</td>
          <td><StatusBadge status={entry.result} /></td>
          <td>{summarizeSodActivity(entry)}</td>
        </tr>)}
      </tbody></table>}
    </div>

    {canManageRoster && <div className="panel"><div className="detail-section">
      <div className="detail-title"><h2>SoD Administrators</h2></div>
      <p className="subtitle" style={{ marginBottom: 16 }}>Only these directory users (plus you, as an Admin, for oversight and roster management) can create or edit SoD rules — a genuine separation between managing access and governing its conflict rules.</p>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <select className="select" style={{ minWidth: 260 }} value={newAdminUserId} onChange={event => setNewAdminUserId(event.target.value)}>
          <option value="">Select a user...</option>
          {(directoryUsers || []).filter(u => !(admins || []).some(a => a.user_id === u.id)).map(u => <option key={u.id} value={u.id}>{u.display_name} ({u.email})</option>)}
        </select>
        <button className="btn btn-primary" disabled={!newAdminUserId} onClick={addAdmin}>Grant SoDAdmin</button>
      </div>
      {!admins || admins.length === 0 ? <div className="empty">No one holds AccessPilot.SoDAdmin yet.</div> : <table className="table"><thead><tr><th>User</th><th>Granted by</th><th>Since</th><th>Actions</th></tr></thead><tbody>
        {admins.map(a => <tr key={a.id}>
          <td>{a.user_display_name} ({a.user_email})</td><td>{a.granted_by_display_name || '—'}</td><td>{new Date(a.created_at).toLocaleDateString()}</td>
          <td><button className="btn" onClick={() => removeAdmin(a.user_id)}>Revoke</button></td>
        </tr>)}
      </tbody></table>}
    </div></div>}
  </Page>;
}
function SodConfigurationPage() {
  const auth = useAuth();
  const canManage = auth.isSodAdmin;
  const { data: settings, reload: reloadSettings } = useApiResource<ApiSodNotificationSettings>('/api/v1/sod/notification-settings');
  const { data: notifications, reload: reloadNotifications } = useApiResource<ApiSodNotification[]>('/api/v1/sod/notifications');
  const [form, setForm] = useState<ApiSodNotificationSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { if (settings) setForm(settings); }, [settings]);

  const save = async () => {
    if (!form) return;
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/sod/notification-settings', { method: 'PATCH', body: JSON.stringify(form) });
      if (response.ok) { setMessage('Saved.'); reloadSettings(); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to save.'); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };

  const markRead = async (id: string) => { await auth.apiRequest(`/api/v1/sod/notifications/${id}/read`, { method: 'POST' }); reloadNotifications(); };
  const markAllRead = async () => { await auth.apiRequest('/api/v1/sod/notifications/read-all', { method: 'POST' }); reloadNotifications(); };

  const unreadCount = (notifications || []).filter(n => !n.read_at && !n.resolved_at).length;

  return <Page eyebrow="SEPARATION OF DUTIES" title="SoD Configuration" subtitle="Control when the SoD engine notifies you, and review everything it has ever reported.">
    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Notification settings</h2></div>
      {!canManage && <div className="notice" style={{ margin: '0 18px 18px' }}>You can view these settings and the notification log. Only an AccessPilot.SoDAdmin can change them.</div>}
      {!form ? <div className="empty">Loading...</div> : <div className="detail-section">
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}><input type="checkbox" disabled={!canManage} checked={form.notify_on_new_violation} onChange={event => setForm({ ...form, notify_on_new_violation: event.target.checked })} /><span>Notify when a new SoD violation is found</span></label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}><input type="checkbox" disabled={!canManage} checked={form.notify_on_exception_expiring} onChange={event => setForm({ ...form, notify_on_exception_expiring: event.target.checked })} /><span>Notify before an accepted-risk exception expires</span></label>
        <label className="key" style={{ display: 'block', marginBottom: 22, maxWidth: 260 }}><span>Warn this many days before an exception expires</span><input className="select" style={{ width: '100%' }} type="number" min={1} max={90} disabled={!canManage || !form.notify_on_exception_expiring} value={form.exception_expiring_warning_days} onChange={event => setForm({ ...form, exception_expiring_warning_days: Number(event.target.value) })} /></label>
        {message && <div className="notice" style={{ marginBottom: 14 }}>{message}</div>}
        {canManage && <button className="btn btn-primary" disabled={saving} onClick={save}>{saving ? 'Saving...' : 'Save'}</button>}
      </div>}
    </div>

    <div className="panel">
      <div className="panel-head"><h2>Notification Log</h2><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>{unreadCount > 0 && <span className="badge danger">{unreadCount} unread</span>}<button className="btn" onClick={markAllRead} disabled={!notifications || unreadCount === 0}>Mark all as read</button></div></div>
      {!notifications ? <div className="empty">Loading...</div> : notifications.length === 0 ? <div className="empty">Nothing has been reported yet — a new violation or a soon-expiring exception will show up here.</div> : <table className="table"><thead><tr><th>When</th><th>Type</th><th>Message</th><th>Status</th><th>Actions</th></tr></thead><tbody>
        {notifications.map(n => <tr key={n.id} style={{ opacity: n.read_at ? 0.7 : 1 }}>
          <td>{new Date(n.created_at).toLocaleString()}</td>
          <td>{n.notification_type.replace(/_/g, ' ')}</td>
          <td>{n.message}</td>
          <td>{n.resolved_at ? <span className="badge neutral">Resolved</span> : n.read_at ? <span className="badge neutral">Read</span> : <span className="badge danger">Unread</span>}</td>
          <td>{!n.read_at && <button className="btn" onClick={() => markRead(n.id)}>Mark read</button>}</td>
        </tr>)}
      </tbody></table>}
    </div>
  </Page>;
}
function AdminOnly({ role, children }: { role: Role; children: React.ReactNode }) { return role === 'admin' ? children : <Navigate to="/dashboard" replace />; }
function Shell({ role, setRole, children }: { role: Role; setRole: (r: Role) => void; children: React.ReactNode }) {
  const location = useLocation(); const navigate = useNavigate();
  const auth = useAuth();
  const branding = useBranding();
  const visible = nav.filter(item => item.roles.includes(role) || (item.extra === 'sod' && auth.isSodAdmin));
  const path = location.pathname;
  const seesSodBell = role === 'admin' || auth.isSodAdmin;
  const { data: sodNotifications } = useApiResource<ApiSodNotification[]>('/api/v1/sod/notifications', seesSodBell);
  const unreadSodCount = (sodNotifications || []).filter(n => !n.read_at && !n.resolved_at).length;
  return <div className="app"><aside className="sidebar"><Link to="/dashboard" className="brand"><span className="brand-mark"><img src={branding?.internal_logo || logo} alt="AccessPilot" /></span> AccessPilot</Link>{visible.map((item, index) => { const I = item.icon; const previous = visible[index - 1]; return <div key={item.to}>{item.section && item.section !== previous?.section && <div className="nav-label">{item.section}</div>}<Link className={`nav-item ${path === item.to || (item.to !== '/dashboard' && path.startsWith(item.to)) ? 'active' : ''}`} to={item.to}><I />{item.label}</Link></div> })}<div className="sidebar-foot"><div>ACCESSPILOT CONSOLE</div><div style={{marginTop:5}}>v0.1.0 · Mock environment</div><div className="sidebar-credit">by <span>{branding?.powered_by_text || 'Clover‑X'}</span></div></div></aside><main className="main"><header className="topbar"><button className="mobile-menu" aria-label="Open navigation"><Menu size={20}/></button><div className="crumb">Workspace / <strong>{role === 'admin' ? 'Administration' : 'Self-service'}</strong></div><div className="top-actions">{auth.authConfigured ? <button className="btn" onClick={() => (auth.account || auth.breakglassActive) ? auth.signOut() : auth.signIn()}>{(auth.account || auth.breakglassActive) ? 'Sign out' : 'Sign in'}</button> : <div className="role-switch" aria-label="Development role switcher"><button className={role === 'user' ? 'active' : ''} onClick={() => { setRole('user'); navigate('/dashboard'); }}>User</button><button className={role === 'admin' ? 'active' : ''} onClick={() => { setRole('admin'); navigate('/dashboard'); }}>Admin</button></div>}{seesSodBell ? <Link to="/admin/sod/configuration" aria-label="SoD notifications" style={{position:'relative',display:'inline-flex'}}><Bell size={17} color="#718088"/>{unreadSodCount > 0 && <span style={{position:'absolute',top:-6,right:-6,background:'#c0392b',color:'#fff',borderRadius:9,fontSize:10,fontWeight:700,padding:'0 5px',lineHeight:'16px',minWidth:16,textAlign:'center'}}>{unreadSodCount}</span>}</Link> : <Bell size={17} color="#718088"/>}<div className="profile"><span>{auth.account?.name || (auth.breakglassActive ? `Break-Glass (${auth.breakglassUsername})` : currentUser.name)}</span><span className="avatar">{currentUser.initials}</span></div></div></header>{children}</main></div>;
}
function Page({ eyebrow, title, subtitle, action, children }: { eyebrow?: string; title: string; subtitle?: string; action?: React.ReactNode; children: React.ReactNode }) { return <div className="content"><div className="page-head"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{subtitle && <p className="subtitle">{subtitle}</p>}</div>{action}</div>{children}</div>; }
interface UserDashboardStats { active: number; eligible: number; pending: number; expiringSoon: number; }
function StatCards({ admin = false, dashboard, userStats }: { admin?: boolean; dashboard?: DashboardAdmin | null; userStats?: UserDashboardStats | null }) { const na = '—'; const stats: Array<[string, string, string, LucideIcon, string?]> = admin ? [['Total users', dashboard ? String(dashboard.users) : na, 'Synced from Microsoft Entra ID', Users, '/admin/users'],['Groups', dashboard ? String(dashboard.groups) : na, 'Synced from Microsoft Entra ID', Network, '/admin/groups'],['Privileged roles', dashboard ? String(dashboard.privilegedRoles) : na, `${dashboard ? dashboard.roles : na} directory roles total`, ShieldCheck, '/admin/roles?privileged=true'],['Active JIT sessions', dashboard ? String(dashboard.activeSessions) : na, 'Currently active, real access grants', Clock3, '/admin/assignments?status=ACTIVE'],['Pending requests', dashboard ? String(dashboard.pendingRequests) : na, 'Awaiting approver decision', FolderKanban, '/admin/assignments?status=PENDING_APPROVAL'],['Expiring access', dashboard ? String(dashboard.expiringAccess) : na, 'Active access expiring within 24 hours', AlertTriangle, '/admin/assignments?status=ACTIVE&expiring=24h'],['Provider health', dashboard?.provider?.status || na, dashboard?.provider ? dashboard.provider.name : 'No provider configured', Cloud, '/admin/providers'],['Policy coverage', na, 'Not available in this release', FileCheck2, '/admin/policies']] : [['Active access', userStats ? String(userStats.active) : na, 'Currently real, granted access', KeyRound, '/my-access'],['Eligible access', userStats ? String(userStats.eligible) : na, 'Ready for you to activate', Shield, '/my-access'],['Pending requests', userStats ? String(userStats.pending) : na, 'Awaiting approver decision', Clock3, '/my-requests'],['Expiring soon', userStats ? String(userStats.expiringSoon) : na, 'Active access expiring within 24 hours', AlertTriangle, '/my-access']]; return <div className={`stats ${admin ? 'admin-stats' : ''}`}>{stats.map(([label,value,foot,I,to]) => { const body = <><div className="stat-top"><span>{label}</span><span className="stat-icon"><I size={15}/></span></div><div className="stat-value">{value}</div><div className="stat-foot">{foot}</div></>; return to ? <Link to={to} className="stat stat-link" key={String(label)}>{body}</Link> : <div className="stat" key={String(label)}>{body}</div>; })}</div>; }
function niceAxisMax(value: number): number {
  if (value <= 5) return 5;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  const normalized = value / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * magnitude;
}
function ActivationTimelineChart({ series }: { series: { date: string; count: number }[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  if (series.length === 0) return <div className="empty">No privileged role activations recorded yet.</div>;
  const width = 800, height = 300, marginLeft = 44, marginRight = 16, marginTop = 16, marginBottom = 48;
  const plotWidth = width - marginLeft - marginRight;
  const plotHeight = height - marginTop - marginBottom;
  const axisMax = niceAxisMax(Math.max(...series.map(d => d.count)));
  const gridSteps = 4;
  const n = series.length;
  const xFor = (i: number) => marginLeft + (n === 1 ? plotWidth / 2 : (i / (n - 1)) * plotWidth);
  const yFor = (count: number) => marginTop + plotHeight - (count / axisMax) * plotHeight;
  const points = series.map((d, i) => ({ x: xFor(i), y: yFor(d.count), ...d }));
  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const labelStride = Math.max(1, Math.ceil(n / 8));
  const hovered = hoverIndex !== null ? points[hoverIndex] : null;
  return <div style={{position:'relative'}}>
    <svg viewBox={`0 0 ${width} ${height}`} style={{width:'100%',height:300,display:'block'}}>
      {Array.from({ length: gridSteps + 1 }, (_, step) => {
        const value = (axisMax / gridSteps) * step;
        const y = yFor(value);
        return <g key={step}>
          <line x1={marginLeft} y1={y} x2={width - marginRight} y2={y} stroke="#e2e8ea" strokeDasharray={step === 0 ? undefined : '4 4'}/>
          <text x={marginLeft - 10} y={y + 4} textAnchor="end" fontSize="11" fill="#94a3ab">{Math.round(value)}</text>
        </g>;
      })}
      {points.map((p, i) => (i % labelStride === 0 || i === n - 1) && <text key={p.date} x={p.x} y={height - marginBottom + 20} textAnchor="middle" fontSize="11" fill="#94a3ab">{new Date(p.date).toLocaleDateString(undefined, { month:'short', day:'numeric' })}</text>)}
      <text x={marginLeft + plotWidth / 2} y={height - 6} textAnchor="middle" fontSize="11" fontWeight={700} fill="#687782">Date</text>
      <text x={14} y={marginTop + plotHeight / 2} textAnchor="middle" fontSize="11" fontWeight={700} fill="#687782" transform={`rotate(-90 14 ${marginTop + plotHeight / 2})`}>Users activated</text>
      <path d={linePath} fill="none" stroke="#087f82" strokeWidth={2}/>
      {hoverIndex !== null && <line x1={points[hoverIndex].x} y1={marginTop} x2={points[hoverIndex].x} y2={height - marginBottom} stroke="#087f82" strokeDasharray="3 3" opacity={0.4}/>}
      {points.map((p, i) => <circle key={p.date} cx={p.x} cy={p.y} r={i === hoverIndex ? 6 : 4} fill="#fff" stroke="#087f82" strokeWidth={2} style={{cursor:'pointer'}} onMouseEnter={() => setHoverIndex(i)} onMouseLeave={() => setHoverIndex(null)}/>)}
    </svg>
    {hovered && <div className="notice" style={{position:'absolute', top:0, left:`${(hovered.x / width) * 100}%`, transform:'translate(-50%, -100%)', whiteSpace:'nowrap', pointerEvents:'none', padding:'6px 10px'}}>
      <strong>{new Date(hovered.date).toLocaleDateString(undefined, { month:'short', day:'numeric' })}</strong>: {hovered.count} user{hovered.count === 1 ? '' : 's'} activated
    </div>}
  </div>;
}
function PieChart({ segments, onSliceClick }: { segments: { key: string; label: string; value: number; color: string }[]; onSliceClick?: (key: string) => void }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const size = 180, cx = size / 2, cy = size / 2, r = 74;
  const nonZero = segments.filter(s => s.value > 0);
  let angleStart = -90;
  const slices = segments.map((s, i) => {
    const fraction = total > 0 ? s.value / total : 0;
    const angleEnd = angleStart + fraction * 360;
    const largeArc = angleEnd - angleStart > 180 ? 1 : 0;
    const startRad = (angleStart * Math.PI) / 180, endRad = (angleEnd * Math.PI) / 180;
    const x1 = cx + r * Math.cos(startRad), y1 = cy + r * Math.sin(startRad);
    const x2 = cx + r * Math.cos(endRad), y2 = cy + r * Math.sin(endRad);
    const path = `M${cx},${cy} L${x1.toFixed(2)},${y1.toFixed(2)} A${r},${r} 0 ${largeArc} 1 ${x2.toFixed(2)},${y2.toFixed(2)} Z`;
    angleStart = angleEnd;
    return { ...s, path, fraction, index: i };
  });
  return <div style={{display:'flex',alignItems:'center',gap:28,flexWrap:'wrap'}}>
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {total === 0 ? <circle cx={cx} cy={cy} r={r} fill="#edf1f3"/> : nonZero.length === 1 ? <circle cx={cx} cy={cy} r={r} fill={nonZero[0].color} style={{cursor: onSliceClick ? 'pointer' : undefined}} onClick={() => onSliceClick?.(nonZero[0].key)}/> : slices.filter(s => s.value > 0).map(s => <path key={s.key} d={s.path} fill={s.color} opacity={hoverIndex === null || hoverIndex === s.index ? 1 : 0.4} style={{cursor:'pointer',transition:'opacity .12s'}} onMouseEnter={() => setHoverIndex(s.index)} onMouseLeave={() => setHoverIndex(null)} onClick={() => onSliceClick?.(s.key)}/>)}
      <circle cx={cx} cy={cy} r={r * 0.55} fill="#fff" style={{pointerEvents:'none'}}/>
      <text x={cx} y={cy - 3} textAnchor="middle" fontSize="20" fontWeight={700} fill="#17212b" style={{pointerEvents:'none'}}>{total}</text>
      <text x={cx} y={cy + 14} textAnchor="middle" fontSize="10" fill="#94a3ab" style={{pointerEvents:'none'}}>users</text>
    </svg>
    <div style={{display:'flex',flexDirection:'column',gap:10}}>
      {segments.map((s, i) => <div key={s.key} style={{display:'flex',alignItems:'center',gap:8,fontSize:12,cursor:s.value > 0 ? 'pointer' : 'default',opacity:hoverIndex === null || hoverIndex === i ? 1 : 0.5}} onMouseEnter={() => setHoverIndex(i)} onMouseLeave={() => setHoverIndex(null)} onClick={() => s.value > 0 && onSliceClick?.(s.key)}>
        <span style={{width:10,height:10,borderRadius:3,background:s.color,display:'inline-block',flex:'none'}}/>
        <span style={{color:'#52656d'}}>{s.label}</span>
        <strong>{s.value}</strong>
        <span style={{color:'#94a3ab'}}>({total > 0 ? Math.round((s.value / total) * 100) : 0}%)</span>
      </div>)}
    </div>
  </div>;
}
function Dashboard({ role }: { role: Role }) {
  const admin = role === 'admin';
  const auth = useAuth();
  const navigate = useNavigate();
  const { data: dashboard, error, loading, reload: reloadDashboard } = useApiResource<DashboardAdmin>('/api/v1/dashboard/admin', admin);
  const { data: recentAudit, reload: reloadAudit } = useApiResource<ApiAuditLog[]>('/api/v1/audit-logs', admin);
  const { data: timeline, reload: reloadTimeline } = useApiResource<ApiActivationTimeline>('/api/v1/dashboard/privileged-role-activations?days=30', admin);
  const { data: segments, reload: reloadSegments } = useApiResource<ApiUserAccessSegments>('/api/v1/dashboard/user-access-segments', admin);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const { data: segmentMembers, loading: membersLoading } = useApiResource<ApiSegmentMember[]>(`/api/v1/dashboard/user-access-segments/${selectedSegment}`, Boolean(selectedSegment));
  const segmentTitle = selectedSegment === 'permanent-active' ? 'Permanent & Active' : selectedSegment === 'eligible' ? 'Eligible (not yet activated)' : '';
  const { data: myAssignments, reload: reloadMine } = useApiResource<ApiAssignment[]>('/api/v1/assignments/mine', !admin);
  const seesSod = admin || auth.isSodAdmin;
  const { data: sodViolations } = useApiResource<ApiSodViolation[]>('/api/v1/sod/violations', seesSod);
  const { data: sodActivity } = useApiResource<ApiSodActivityEntry[]>('/api/v1/sod/activity', seesSod);
  const greetingName = auth.account?.name || (auth.authConfigured ? '' : currentUser.name);
  const lastSyncLabel = dashboard?.lastSync?.completedAt ? new Date(dashboard.lastSync.completedAt).toLocaleString() : dashboard?.lastSync ? 'In progress' : 'Never synced';

  const userStats: UserDashboardStats | null = myAssignments ? {
    active: myAssignments.filter(a => a.status === 'ACTIVE').length,
    eligible: myAssignments.filter(a => a.status === 'ELIGIBLE').length,
    pending: myAssignments.filter(a => a.status === 'PENDING_APPROVAL').length,
    expiringSoon: myAssignments.filter(a => a.status === 'ACTIVE' && a.expiration_time && new Date(a.expiration_time).getTime() - Date.now() <= 24 * 60 * 60 * 1000).length,
  } : null;
  const myActiveAccess = (myAssignments || []).filter(a => a.status === 'ACTIVE');
  const myRecentActivity = [...(myAssignments || [])].sort((a, b) => new Date(b.activated_at || b.created_at).getTime() - new Date(a.activated_at || a.created_at).getTime()).slice(0, 6);

  useEffect(() => {
    if (!admin) return;
    const id = setInterval(() => { reloadDashboard(); reloadAudit(); reloadTimeline(); reloadSegments(); }, 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admin]);

  useEffect(() => {
    if (admin) return;
    const id = setInterval(() => reloadMine(), 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admin]);

  return <Page eyebrow={admin ? 'ADMINISTRATION' : 'SELF-SERVICE'} title={admin ? (greetingName ? `Good morning, ${greetingName.split(' ')[0]}` : 'Good morning') : 'Your access overview'} subtitle={admin ? 'Here is what is happening across your identity environment.' : 'Review your current access and request what you need.'} action={<button className="btn btn-primary" onClick={() => navigate(admin ? '/admin/assignments?status=PENDING_APPROVAL' : '/request-packages')}><ArrowRight size={15}/> {admin ? 'Review requests' : 'Request access'}</button>}><StatCards admin={admin} dashboard={dashboard} userStats={userStats}/>{admin && loading && <div className="empty">Loading dashboard...</div>}{admin && error && <div className="empty">{error}</div>}
    {admin && <div className="grid-2" style={{marginBottom:18}}>
      <section className="panel"><div className="panel-head"><h2>Privileged role activations</h2><span className="panel-link">{timeline ? `Last ${timeline.days} days` : ''}</span></div><div className="detail-section">{timeline ? <ActivationTimelineChart series={timeline.series}/> : <div className="empty">Loading timeline...</div>}</div></section>
      <section className="panel"><div className="panel-head"><h2>User access mix</h2><span className="panel-link">Click a slice for the list</span></div><div className="detail-section">{segments ? <PieChart segments={[{key:'permanent-active', label:'Permanent & Active', value: segments.permanentActive, color:'#087f82'},{key:'eligible', label:'Eligible (not yet activated)', value: segments.eligible, color:'#f4b35d'}]} onSliceClick={setSelectedSegment}/> : <div className="empty">Loading...</div>}</div></section>
    </div>}
    {seesSod && <div className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-head"><h2>Separation of Duties</h2><Link to="/admin/sod" className="panel-link">Manage <ChevronRight size={12}/></Link></div>
      <div className="detail-section"><div className="user-cell"><span className="stat-icon" style={{background: sodViolations && sodViolations.length > 0 ? '#fdecea' : '#e8f6ec', color: sodViolations && sodViolations.length > 0 ? '#8c2b21' : '#1c7c3f'}}><ShieldAlert size={16}/></span><div><div className="user-name">{sodViolations ? `${sodViolations.length} current violation${sodViolations.length === 1 ? '' : 's'}` : 'Loading...'}</div><div className="user-email">{sodViolations && sodViolations.length > 0 ? 'One or more users hold both sides of a conflicting-access rule.' : 'No user currently holds both sides of an active rule.'}</div></div></div>
        {sodActivity && sodActivity.length > 0 && <div style={{marginTop:16,paddingTop:14,borderTop:'1px solid #eef1f2'}}>
          <div className="subtitle" style={{marginBottom:8,fontWeight:600}}>Recent SoD activity</div>
          {sodActivity.slice(0,3).map(entry => <div key={entry.id} className="activity-row" style={{padding:'6px 0'}}><span className="activity-dot"/><div className="activity-copy"><strong>{entry.action.replace(/_/g,' ')}</strong><small>{entry.actor_display_name || 'System'}{entry.target_user_display_name ? ` · ${entry.target_user_display_name}` : ''} · {summarizeSodActivity(entry)} · {new Date(entry.timestamp).toLocaleString()}</small></div></div>)}
        </div>}
      </div>
    </div>}
    {selectedSegment && <div className="overlay-backdrop" onClick={() => setSelectedSegment(null)}>
      <div className="overlay-card" onClick={event => event.stopPropagation()}>
        <div className="panel-head"><h2>{segmentTitle}</h2><button type="button" className="btn" aria-label="Close" onClick={() => setSelectedSegment(null)}><X size={14}/></button></div>
        <div className="table-wrap">{membersLoading ? <div className="empty">Loading users...</div> : !segmentMembers || segmentMembers.length === 0 ? <div className="empty">No users in this segment.</div> : <table><thead><tr><th>User</th><th>Email</th></tr></thead><tbody>{segmentMembers.map(m => <tr key={m.id}><td className="user-name">{m.display_name}</td><td>{m.email}</td></tr>)}</tbody></table>}</div>
      </div>
    </div>}
    <div className="grid-2"><section className="panel"><div className="panel-head"><h2>{admin ? 'Recent access requests' : 'Recent activity'}</h2><Link to={admin ? '/admin/audit' : '/my-requests'} className="panel-link">View all <ChevronRight size={12}/></Link></div>{admin ? (!recentAudit || recentAudit.length === 0 ? <div className="empty">No recent activity.</div> : recentAudit.slice(0,6).map(entry => <div className="activity" key={entry.id}><div className="activity-row"><span className="activity-dot"/><div className="activity-copy"><strong>{entry.action}</strong><small>{entry.actor_display_name || 'System'}{entry.target_user_display_name ? ` · ${entry.target_user_display_name}` : ''} · {new Date(entry.timestamp).toLocaleString()}</small></div><StatusBadge status={entry.result}/></div></div>)) : (myRecentActivity.length === 0 ? <div className="empty">No activity yet.</div> : myRecentActivity.map(item => <div className="activity" key={item.id}><div className="activity-row"><span className="activity-dot"/><div className="activity-copy"><strong>{item.resource_display_name || item.resource_type}{item.package_name ? ` (${item.package_name})` : ''}</strong><small>{item.resource_type} · {new Date(item.activated_at || item.created_at).toLocaleString()}</small></div><StatusBadge status={item.status}/></div></div>))}</section><section className="panel"><div className="panel-head"><h2>{admin ? 'Provider status' : 'Current active access'}</h2>{admin && <StatusBadge status={dashboard?.provider?.status || 'NOT_CONFIGURED'}/>}</div>{admin ? <div className="detail-section"><div className="user-cell"><span className="avatar" style={{background:'#e4f1f5',color:'#33758a'}}><Cloud size={15}/></span><div><div className="user-name">{dashboard?.provider?.name || 'No provider configured'}</div><div className="user-email">{dashboard?.provider ? `${dashboard.provider.status} · Last sync ${lastSyncLabel}` : 'Configure a provider to begin syncing.'}</div></div></div><div className="key-grid" style={{marginTop:24}}><div className="key"><span>Users synced</span><strong>{dashboard ? dashboard.users : '—'}</strong></div><div className="key"><span>Groups synced</span><strong>{dashboard ? dashboard.groups : '—'}</strong></div><div className="key"><span>Directory roles</span><strong>{dashboard ? dashboard.roles : '—'}</strong></div><div className="key"><span>Last sync</span><strong>{lastSyncLabel}</strong></div></div></div> : (myActiveAccess.length === 0 ? <div className="detail-section"><div className="empty">No active access right now. Check My Access for anything eligible to activate.</div></div> : <div className="table-wrap"><table><thead><tr><th>Resource</th><th>Type</th><th>Expires</th></tr></thead><tbody>{myActiveAccess.map(item => <tr key={item.id}><td className="user-name">{item.resource_display_name || item.resource_type}{item.package_name ? <div className="user-email">{item.package_name}</div> : null}</td><td>{item.resource_type}</td><td>{item.expiration_time ? new Date(item.expiration_time).toLocaleString() : 'Permanent'}</td></tr>)}</tbody></table></div>)}</section></div></Page>; }
function StatusBadge({ status }: { status: string }) { const cls = ['APPROVED','ACTIVE','COMPLETED','CONNECTED','ELIGIBLE','SUCCESS','Healthy','Active'].includes(status) ? 'success' : ['PENDING','PENDING_APPROVAL','SCHEDULED','RUNNING','PARTIAL','Medium'].includes(status) ? 'warning' : ['REJECTED','EXPIRED','REVOKED','FAILED','Disabled','High'].includes(status) ? 'danger' : 'neutral'; return <span className={`badge ${cls}`}>{status}</span>; }
function TablePanel({ children, toolbar }: { children: React.ReactNode; toolbar?: React.ReactNode }) { return <><div className="toolbar">{toolbar}</div><section className="panel"><div className="table-wrap">{children}</div></section></>; }
interface FilterOption { value: string; label: string; }
function Toolbar({ placeholder = 'Search', searchValue, onSearchChange, filterLabel = 'All statuses', filterValue = '', onFilterChange, filterOptions }: { placeholder?: string; searchValue?: string; onSearchChange?: (value: string) => void; filterLabel?: string; filterValue?: string; onFilterChange?: (value: string) => void; filterOptions?: FilterOption[]; }) {
  return <><div className="toolbar-left">{onSearchChange && <div className="search-box"><Search size={15}/><input className="search" placeholder={placeholder} value={searchValue ?? ''} onChange={event => onSearchChange(event.target.value)}/></div>}{filterOptions && <select className="select" value={filterValue} onChange={event => onFilterChange?.(event.target.value)}><option value="">{filterLabel}</option>{filterOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</div></>;
}
function initialsFor(name: string) { const parts = name.trim().split(/\s+/); return ((parts[0]?.[0] || '') + (parts[parts.length - 1]?.[0] || '')).toUpperCase() || '?'; }
function sourceLabel(user: ApiUser, providers: ApiProvider[] | null): { label: string; detail: string } {
  const provider = providers?.find(p => p.id === user.provider_id);
  const connector = provider ? `${provider.provider_type === 'ENTRA' ? 'Microsoft Entra ID' : provider.provider_type === 'OKTA' ? 'Okta' : provider.name} · ${user.external_id}` : user.external_id;
  if (user.source === 'CSV_ONBOARDING') return { label: 'CSV Onboarding', detail: `Employee ID ${user.employee_id} — ${connector}` };
  return { label: provider?.provider_type === 'ENTRA' ? 'Microsoft Entra ID' : provider?.provider_type === 'OKTA' ? 'Okta' : provider?.name || 'Connector', detail: connector };
}
function UsersPage() {
  const auth = useAuth();
  const { data: users, error, loading, reload } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: providers } = useApiResource<ApiProvider[]>('/api/v1/providers');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const statusFilter = searchParams.get('status') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setStatusFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('status', value); else next.delete('status'); return next; });
  const statusOptions = useMemo(() => Array.from(new Set((users || []).map(u => u.status))).sort().map(s => ({ value: s, label: s })), [users]);
  const filteredUsers = (users || []).filter(u => (!statusFilter || u.status === statusFilter) && (!search || u.display_name.toLowerCase().includes(search.toLowerCase()) || u.email.toLowerCase().includes(search.toLowerCase())));
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ display_name: '', user_principal_name: '', department: '', job_title: '' });
  const [saving, setSaving] = useState(false);
  const [formMessage, setFormMessage] = useState('');
  const [createdPassword, setCreatedPassword] = useState<string | null>(null);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.display_name.trim() || !form.user_principal_name.trim()) { setFormMessage('Display name and email are required.'); return; }
    setSaving(true); setFormMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/users', { method: 'POST', body: JSON.stringify(form) });
      if (response.status === 201) {
        const body = await response.json();
        setCreatedPassword(body.temporary_password || null);
        setForm({ display_name: '', user_principal_name: '', department: '', job_title: '' });
        setOpen(false);
        reload();
      } else if (response.status === 409) {
        setFormMessage('A user with this email already exists.');
      } else {
        const errorBody = await response.json().catch(() => null);
        setFormMessage(errorBody?.error?.message || 'Unable to create user. Please try again.');
      }
    } catch (err) {
      setFormMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to create user. Please try again.');
    } finally { setSaving(false); }
  };
  return <Page eyebrow="ADMINISTRATION" title="Users" subtitle="Directory identities and their AccessPilot entitlements." action={<button className="btn btn-primary" onClick={() => { setOpen(true); setFormMessage(''); }}><Plus size={14}/> Add user</button>}>
    {createdPassword && <div className="notice" style={{marginBottom:14}}>User created. Temporary password (shown once, share it securely): <strong>{createdPassword}</strong></div>}
    {open && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:640,marginBottom:18}} onSubmit={submit}><div className="panel-head"><h2>Add user</h2><button type="button" className="btn" aria-label="Close" onClick={() => setOpen(false)}><X size={14}/></button></div><div className="detail-section"><div className="key-grid">{([['display_name','Display name'],['user_principal_name','Email / UPN'],['department','Department'],['job_title','Job title']] as const).map(([key,label]) => <label className="key" key={key}><span>{label}</span><input className="select" value={form[key]} onChange={event => setForm({...form, [key]: event.target.value})}/></label>)}</div>{formMessage && <div className="notice" style={{marginTop:14}}>{formMessage}</div>}</div><div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Creating...' : 'Create user'}</button></div></form>}
    <TablePanel toolbar={<Toolbar placeholder="Search users by name or email" searchValue={search} onSearchChange={setSearch} filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={statusOptions}/>}>{loading ? <div className="empty">Loading users...</div> : error ? <div className="empty">{error}</div> : !users || users.length === 0 ? <div className="empty">No users found.</div> : filteredUsers.length === 0 ? <div className="empty">No users match this filter.</div> : <table><thead><tr><th>User</th><th>Department</th><th>Job title</th><th>Source</th><th>Status</th><th>Last synced</th><th></th></tr></thead><tbody>{filteredUsers.map(u => { const source = sourceLabel(u, providers); return <tr key={u.id}><td><Link to={`/admin/users/${u.id}`} className="user-cell"><span className="avatar">{initialsFor(u.display_name)}</span><span><span className="user-name">{u.display_name}</span><span className="user-email">{u.email}</span></span></Link></td><td>{u.department || '—'}</td><td>{u.job_title || '—'}</td><td><span className="badge neutral" title={source.detail}>{source.label}</span></td><td><StatusBadge status={u.status}/></td><td>{u.last_synced_at ? new Date(u.last_synced_at).toLocaleString() : 'Never'}</td><td><ChevronRight size={15} color="#829198"/></td></tr>; })}</tbody></table>}</TablePanel>
    {users && users.length > 0 && <p className="footer-note">Showing {filteredUsers.length} of {users.length} users</p>}
  </Page>;
}
function UserDetail() {
  const { id } = useParams();
  const { data: user, error, loading } = useApiResource<ApiUser>(`/api/v1/users/${id}`);
  const { data: providers } = useApiResource<ApiProvider[]>('/api/v1/providers');
  const { data: access, error: accessError, loading: accessLoading, reload: reloadAccess } = useApiResource<ApiUserAccessSummary>(`/api/v1/users/${id}/access-summary`);
  const groupItems = (access?.assignments || []).filter(item => item.resource_type === 'GROUP');
  const applicationItems = (access?.assignments || []).filter(item => item.resource_type === 'APPLICATION');
  const roleItems = (access?.assignments || []).filter(item => item.resource_type === 'ROLE');
  const groupCount = groupItems.filter(item => item.status === 'ACTIVE').length;
  const applicationCount = applicationItems.filter(item => item.status === 'ACTIVE').length;
  const packageGroups = useMemo(() => {
    const map = new Map<string, ApiUserAccessItem[]>();
    (access?.assignments || []).forEach(item => {
      if (!item.package_name) return;
      if (!map.has(item.package_name)) map.set(item.package_name, []);
      map.get(item.package_name)!.push(item);
    });
    return Array.from(map.entries());
  }, [access]);
  const renderAccessItem = (item: ApiUserAccessItem, index: number) => <div key={item.id || `${item.resource_type}-${index}`} className="timeline-item"><strong>{item.resource_display_name || item.resource_type}</strong><small>{item.source === 'DIRECT_IN_ENTRA' ? 'Added directly in Entra' : item.assignment_type}{item.expiration_time ? ` · expires ${new Date(item.expiration_time).toLocaleString()}` : ''}</small><div style={{marginTop:5}}><StatusBadge status={item.status}/></div></div>;
  const [copied, setCopied] = useState(false);
  const copyEmail = async () => {
    if (!user) return;
    try { await navigator.clipboard.writeText(user.email); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard unavailable */ }
  };
  if (loading) return <Page eyebrow="USER DIRECTORY" title="Loading..." subtitle=""><div className="empty">Loading user...</div></Page>;
  if (error || !user) return <Page eyebrow="USER DIRECTORY" title="User" subtitle=""><div className="empty">{error || 'User not found.'}</div></Page>;
  const provider = providers?.find(p => p.id === user.provider_id);
  const connectorName = provider ? (provider.provider_type === 'ENTRA' ? 'Microsoft Entra ID' : provider.provider_type === 'OKTA' ? 'Okta' : provider.name) : 'Unknown connector';
  const isCsvOnly = provider?.provider_type === 'CSV';
  return <Page eyebrow="USER DIRECTORY" title={user.display_name} subtitle={user.email} action={<button className="btn" aria-label="Refresh" onClick={() => reloadAccess()}><RefreshCw size={14}/></button>}><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="user-cell"><span className="avatar" style={{width:45,height:45}}>{initialsFor(user.display_name)}</span><div><h2>{user.job_title || 'No job title on file'}</h2><p className="subtitle">{user.department || 'No department on file'} · {user.status}</p></div></div></div><div className="detail-section"><div className="detail-title"><h2>Overview</h2><StatusBadge status={user.status}/></div><div className="key-grid"><div className="key"><span>Email</span><strong style={{display:'flex',alignItems:'center',gap:8}}>{user.email}<button type="button" className="btn" aria-label="Copy email" onClick={() => void copyEmail()} style={{padding:'2px 7px'}}><Copy size={12}/></button>{copied && <span className="footer-note">Copied</span>}</strong></div><div className="key"><span>Given name</span><strong>{user.given_name || '—'}</strong></div><div className="key"><span>Surname</span><strong>{user.surname || '—'}</strong></div><div className="key"><span>Last synced</span><strong>{user.last_synced_at ? new Date(user.last_synced_at).toLocaleString() : 'Never'}</strong></div><div className="key"><span>Groups</span><strong>{accessLoading ? '…' : groupCount}</strong></div><div className="key"><span>Applications</span><strong>{accessLoading ? '…' : applicationCount}</strong></div></div></div><div className="detail-section"><div className="detail-title"><h2>Identity source</h2>{isCsvOnly && <span className="badge warning">No real account yet</span>}</div><div className="key-grid">
    <div className="key"><span>Onboarded via</span><strong>{user.source === 'CSV_ONBOARDING' ? 'CSV Onboarding' : 'Directory sync'}</strong></div>
    {user.employee_id && <div className="key"><span>Employee ID (from CSV)</span><strong>{user.employee_id}</strong></div>}
    <div className="key"><span>Connector</span><strong>{connectorName}</strong></div>
    <div className="key"><span>Connector external ID</span><strong>{user.external_id}</strong></div>
  </div>{isCsvOnly && <p className="subtitle" style={{marginTop:12}}>This identity has no real {providers?.some(p => p.provider_type === 'ENTRA') ? 'Entra' : providers?.some(p => p.provider_type === 'OKTA') ? 'Okta' : 'connector'} account yet — group/role membership shown below is AccessPilot-local (eligible) only. Re-uploading its CSV row after a real connector is available will provision one automatically.</p>}</div></section><aside className="panel">
    <div className="panel-head"><h2>Groups</h2></div>
    <div className="detail-section">{accessLoading ? <div className="empty">Loading groups...</div> : accessError ? <div className="notice">{accessError}</div> : groupItems.length === 0 ? <div className="notice">Not a member of any group.</div> : <div className="timeline" style={{padding:0}}>{groupItems.map(renderAccessItem)}</div>}</div>
    <div className="panel-head"><h2>Applications</h2></div>
    <div className="detail-section">{accessLoading ? <div className="empty">Loading applications...</div> : applicationItems.length === 0 ? <div className="notice">No application role assignments.</div> : <div className="timeline" style={{padding:0}}>{applicationItems.map(renderAccessItem)}</div>}</div>
    <div className="panel-head"><h2>Roles</h2></div>
    <div className="detail-section">{accessLoading ? <div className="empty">Loading roles...</div> : roleItems.length === 0 ? <div className="notice">No directory role assignments.</div> : <div className="timeline" style={{padding:0}}>{roleItems.map(renderAccessItem)}</div>}</div>
    <div className="panel-head"><h2>Access Packages</h2></div>
    <div className="detail-section">{accessLoading ? <div className="empty">Loading packages...</div> : packageGroups.length === 0 ? <div className="notice">Not enrolled in any access package.</div> : <div className="timeline" style={{padding:0}}>{packageGroups.map(([name, items]) => <div key={name} className="timeline-item"><strong>📦 {name}</strong><small>{items.length} item{items.length === 1 ? '' : 's'}</small></div>)}</div>}</div>
    <div className="panel-head"><h2>Licenses</h2></div>
    <div className="detail-section">{accessLoading ? <div className="empty">Loading licenses...</div> : !access || access.licenses.length === 0 ? <div className="notice">No licenses found for this user.</div> : <ul style={{margin:0,paddingLeft:18,lineHeight:1.9}}>{access.licenses.map(lic => <li key={lic.sku_id}>{lic.name}</li>)}</ul>}</div>
  </aside></div></Page>;
}
function Requests({ mine = false }: { mine?: boolean }) {
  const { requests: items } = useMockState();
  const update = (id: string, status: RequestStatus) => { if (status === 'REJECTED' && !window.confirm('Reject this access request?')) return; mockService.transitionRequest(id, status); };

  // "My requests" (mine=true) shows the caller's REAL package-request history — every package they've personally
  // self-requested and what happened to it, including rejections (which otherwise have nowhere visible to show up
  // at all). The admin "Access requests" page (mine=false) is untouched, still mock — these hooks simply don't
  // fire for that path (enabled: mine).
  const { data: myAssignments, error: historyError, loading: historyLoading, reload: reloadHistory } = useApiResource<ApiAssignment[]>('/api/v1/assignments/mine', mine);
  const { data: myBatches } = useApiResource<ApiPackageBatch[]>('/api/v1/packages/my-package-batches', mine);
  const batchByAssignmentId = useMemo(() => { const map = new Map<string, ApiPackageBatch>(); (myBatches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [myBatches]);
  const requestHistory = useMemo(() => {
    const rows: { batchId: string; packageName: string; requestedAt: string; status: string }[] = [];
    const seen = new Set<string>();
    (myAssignments || []).forEach(a => {
      if (!a.requested_by || a.requested_by !== a.user_id) return;
      const batch = batchByAssignmentId.get(a.id);
      if (!batch || seen.has(batch.package_assignment_id)) return;
      seen.add(batch.package_assignment_id);
      const batchItems = (myAssignments || []).filter(x => batchByAssignmentId.get(x.id)?.package_assignment_id === batch.package_assignment_id);
      const statuses = new Set(batchItems.map(x => x.status));
      rows.push({ batchId: batch.package_assignment_id, packageName: batch.package_name, requestedAt: batchItems[0].created_at, status: statuses.size === 1 ? batchItems[0].status : 'MIXED' });
    });
    return rows.sort((a, b) => new Date(b.requestedAt).getTime() - new Date(a.requestedAt).getTime());
  }, [myAssignments, batchByAssignmentId]);

  if (mine) {
    return <Page eyebrow="SELF-SERVICE" title="My requests" subtitle="Every package you've requested, and what happened to it — including rejections." action={<><button className="btn" aria-label="Refresh" onClick={() => reloadHistory()}><RefreshCw size={14}/></button><Link to="/request-packages" className="btn btn-primary"><Plus size={14}/> New request</Link></>}>
      <TablePanel toolbar={undefined}>{historyLoading ? <div className="empty">Loading your requests...</div> : historyError ? <div className="empty">{historyError}</div> : requestHistory.length === 0 ? <div className="empty">You haven't requested any packages yet.</div> : <table><thead><tr><th>Package</th><th>Requested</th><th>Status</th></tr></thead><tbody>{requestHistory.map(row => <tr key={row.batchId}><td className="user-name">{row.packageName}</td><td>{new Date(row.requestedAt).toLocaleString()}</td><td><StatusBadge status={row.status}/></td></tr>)}</tbody></table>}</TablePanel>
    </Page>;
  }

  return <Page eyebrow="ACCESS MANAGEMENT" title="Access requests" subtitle="Review and govern access requests across the environment."><TablePanel toolbar={<Toolbar placeholder="Search requests"/>}><table><thead><tr><th>Requester</th><th>Resource</th><th>Type</th><th>Provider</th><th>Duration</th><th>Risk</th><th>Status</th><th>Created</th><th>Approval</th><th></th></tr></thead><tbody>{items.map(r => <tr key={r.id}><td className="user-name">{r.requester}</td><td><Link to={`/admin/access-requests/${r.id}`} className="user-name">{r.resource}</Link></td><td>{r.type}</td><td>{r.provider}</td><td>{r.duration}</td><td><span className={`risk risk-${r.risk.toLowerCase()}`}>{r.risk}</span></td><td><StatusBadge status={r.status}/></td><td>{r.created}</td><td>{r.approval}</td><td>{r.status === 'PENDING' ? <span style={{display:'flex',gap:5}}><button className="btn" onClick={() => update(r.id,'APPROVED')} aria-label="Approve"><Check size={14}/></button><button className="btn" onClick={() => update(r.id,'REJECTED')} aria-label="Reject"><X size={14}/></button></span> : <ChevronRight size={15} color="#829198"/>}</td></tr>)}</tbody></table></TablePanel></Page>;
}
function RequestDetailInteractive() { const { id } = useParams(); const { requests: items } = useMockState(); const req = items.find(item => item.id === id) || items[0]; const update = (status: RequestStatus) => { if ((status === 'REJECTED' || status === 'CANCELLED') && !window.confirm(`${status === 'REJECTED' ? 'Reject' : 'Cancel'} this access request?`)) return; mockService.transitionRequest(req.id, status); }; return <Page eyebrow="ACCESS REQUEST" title={req.id} subtitle="Request details and approval history" action={<div style={{display:'flex',gap:8}}>{req.status === 'PENDING' && <><button className="btn btn-primary" onClick={() => update('APPROVED')}><Check size={14}/> Approve</button><button className="btn" onClick={() => update('REJECTED')}><X size={14}/> Reject</button></>}</div>}><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="detail-title"><h2>{req.resource}</h2><StatusBadge status={req.status}/></div><div className="key-grid"><div className="key"><span>Requester</span><strong>{req.requester}</strong></div><div className="key"><span>Provider</span><strong>{req.provider}</strong></div><div className="key"><span>Resource type</span><strong>{req.type}</strong></div><div className="key"><span>Requested duration</span><strong>{req.duration}</strong></div><div className="key"><span>Risk assessment</span><strong className={`risk risk-${req.risk.toLowerCase()}`}>{req.risk} risk</strong></div><div className="key"><span>Ticket number</span><strong>INC-48291</strong></div></div></div><div className="detail-section"><div className="detail-title"><h2>Justification</h2></div><p className="subtitle" style={{lineHeight:1.7,color:'#39525c'}}>{req.justification}</p></div><div className="detail-section"><div className="detail-title"><h2>Policy evaluation</h2><StatusBadge status="SUCCESS"/></div><div className="notice">MFA and a valid ticket are required before activation. The requested duration is within the policy maximum of 4 hours.</div></div></section><aside className="panel"><div className="panel-head"><h2>Request timeline</h2></div><div className="timeline"><div className="timeline-item"><strong>Request created</strong><small>{req.requester} · {req.created}</small></div><div className="timeline-item"><strong>Policy evaluated</strong><small>Passed · Today, 10:14</small></div><div className="timeline-item"><strong>{req.status === 'PENDING' ? 'Awaiting approval' : `Request ${req.status.toLowerCase()}`}</strong><small>{req.approval}</small></div></div></aside></div></Page>; }
function formatRemaining(expirationTime: string | null): string {
  if (!expirationTime) return '—';
  const ms = new Date(expirationTime).getTime() - Date.now();
  if (ms <= 0) return 'Expired';
  const totalMinutes = Math.floor(ms / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}
type MyAccessRow = { kind: 'single'; assignment: ApiAssignment } | { kind: 'batch'; batch: ApiPackageBatch; assignments: ApiAssignment[] };
function MyAccess() {
  const auth = useAuth();
  const { data: assignments, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments/mine');
  const { data: policy } = useApiResource<ApiActivationPolicy>('/api/v1/assignments/activation-policy');
  const { data: batches } = useApiResource<ApiPackageBatch[]>('/api/v1/packages/my-package-batches');
  const maxHours = policy?.max_self_activation_hours ?? 8;
  const [activateTarget, setActivateTarget] = useState<{ ids: string[]; label: string } | null>(null);
  const [durationHours, setDurationHours] = useState('');
  const [activateJustification, setActivateJustification] = useState('');
  const [activateMessage, setActivateMessage] = useState('');
  const [activateSaving, setActivateSaving] = useState(false);
  const [busyIds, setBusyIds] = useState<string | null>(null);
  const now = Date.now();

  const batchByAssignmentId = useMemo(() => { const map = new Map<string, ApiPackageBatch>(); (batches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [batches]);
  // Grouped by package_id (not package_assignment_id/batch): package names are globally unique, so if the same
  // package was assigned to this user more than once (e.g. re-requested), every batch's items are merged into one
  // row — the user should only ever see one entry per package, never duplicates of the same name.
  const groupRows = (list: ApiAssignment[]): MyAccessRow[] => {
    const rows: MyAccessRow[] = [];
    const seenPackages = new Set<string>();
    list.forEach(a => {
      const batch = batchByAssignmentId.get(a.id);
      if (!batch) { rows.push({ kind: 'single', assignment: a }); return; }
      if (seenPackages.has(batch.package_id)) return;
      seenPackages.add(batch.package_id);
      rows.push({ kind: 'batch', batch, assignments: list.filter(x => batchByAssignmentId.get(x.id)?.package_id === batch.package_id) });
    });
    return rows;
  };
  // An eligible row whose activation deadline has already passed must disappear immediately, not linger until the
  // backend's periodic sweep (up to 60s later) flips its status to EXPIRED.
  const eligible = (assignments || []).filter(a => a.status === 'ELIGIBLE' && (!a.start_time || new Date(a.start_time).getTime() <= now) && (!a.expiration_time || new Date(a.expiration_time).getTime() > now));
  const active = (assignments || []).filter(a => a.status === 'ACTIVE');
  const eligibleRows = useMemo(() => groupRows(eligible), [eligible, batchByAssignmentId]);
  const activeRows = useMemo(() => groupRows(active), [active, batchByAssignmentId]);

  // Separation-of-Duties is only ever ENFORCED at the moment access actually becomes real (i.e. when Activate is
  // clicked) — an eligible row that would conflict is still listed here, exactly like anything else eligible but
  // not yet granted. This soft, non-blocking pre-check (reusing the same /sod/check the backend itself never
  // trusts as the real gate) surfaces that risk on the list itself instead of only after clicking Activate.
  const eligibleIds = eligible.map(a => a.id).join(',');
  const [sodWarnings, setSodWarnings] = useState<Record<string, string[]>>({});
  useEffect(() => {
    if (eligible.length === 0) { setSodWarnings({}); return; }
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(eligible.map(async a => {
        try {
          const response = await auth.apiRequest('/api/v1/sod/check', { method: 'POST', body: JSON.stringify({ resource_type: a.resource_type, resource_id: a.resource_id, app_role_external_id: a.app_role_external_id || undefined }) });
          if (!response.ok) return [a.id, []] as const;
          const body = await response.json();
          return [a.id, ((body.conflicts || []) as ApiSodPolicy[]).map(p => p.name)] as const;
        } catch { return [a.id, []] as const; }
      }));
      if (!cancelled) setSodWarnings(Object.fromEntries(entries));
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eligibleIds]);

  const openActivate = (ids: string[], label: string) => { setActivateTarget({ ids, label }); setDurationHours(String(maxHours)); setActivateJustification(''); setActivateMessage(''); };

  const submitActivate = async () => {
    if (!activateTarget) return;
    const hours = Number(durationHours);
    if (!hours || hours <= 0) { setActivateMessage('Enter a duration greater than zero.'); return; }
    if (hours > maxHours) { setActivateMessage(`The maximum self-activation duration is ${maxHours} hours.`); return; }
    if (activateJustification.trim().length < 3) { setActivateMessage('A justification (at least 3 characters) is required to activate.'); return; }
    setActivateSaving(true); setActivateMessage('');
    try {
      const responses = await Promise.all(activateTarget.ids.map(id => auth.apiRequest(`/api/v1/assignments/${id}/activate`, { method: 'POST', body: JSON.stringify({ duration_hours: hours, justification: activateJustification.trim() }) })));
      const failedResponse = responses.find(r => !r.ok);
      if (!failedResponse) { setActivateTarget(null); reload(); }
      else { const body = await failedResponse.json().catch(() => null); setActivateMessage(body?.error?.message || 'Unable to activate this access.'); reload(); }
    } catch { setActivateMessage('Unable to activate this access.'); } finally { setActivateSaving(false); }
  };

  const deactivate = async (ids: string[], label: string) => {
    if (!window.confirm(`Deactivate ${label} now? You can activate it again later.`)) return;
    setBusyIds(ids.join(','));
    try {
      await Promise.all(ids.map(id => auth.apiRequest(`/api/v1/assignments/${id}/deactivate`, { method: 'POST' })));
      reload();
    } finally { setBusyIds(null); }
  };

  return <Page eyebrow="SELF-SERVICE" title="My access" subtitle="Your active and eligible access across connected providers." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    <div className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>Eligible access</h2><span className="panel-link">{eligible.length} available</span></div>
      <div className="detail-section">
        {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : eligibleRows.length === 0 ? <div className="notice">Nothing eligible to activate right now.</div> : eligibleRows.map(row => {
          const warnings = row.kind === 'single' ? (sodWarnings[row.assignment.id] || []) : Array.from(new Set(row.assignments.flatMap(a => sodWarnings[a.id] || [])));
          const warningTitle = warnings.length > 0 ? `Activating this may conflict with Separation-of-Duties polic${warnings.length === 1 ? 'y' : 'ies'}: ${warnings.join(', ')}` : undefined;
          return row.kind === 'single'
          ? <div key={row.assignment.id} className="activity-row"><span className="avatar"><Shield size={14}/></span><div className="activity-copy"><strong>{row.assignment.resource_display_name || row.assignment.resource_id}</strong><small>{row.assignment.resource_type}{row.assignment.package_name ? ` · ${row.assignment.package_name}` : ''} · {row.assignment.assignment_type === 'TEMPORARY' && row.assignment.expiration_time ? `Activate by ${new Date(row.assignment.expiration_time).toLocaleString()}` : 'No activation deadline'}</small></div>{warnings.length > 0 && <span className="badge danger" title={warningTitle} style={{marginRight:8}}>⚠ SoD conflict</span>}<button className="btn btn-primary" onClick={() => openActivate([row.assignment.id], row.assignment.resource_display_name || 'this access')}>Activate <ArrowRight size={13}/></button></div>
          : <div key={row.batch.package_id} className="activity-row"><span className="avatar">📦</span><div className="activity-copy"><strong>{row.batch.package_name}</strong><small>PACKAGE · {row.assignments.length} items</small></div>{warnings.length > 0 && <span className="badge danger" title={warningTitle} style={{marginRight:8}}>⚠ SoD conflict</span>}<button className="btn btn-primary" onClick={() => openActivate(row.assignments.map(a => a.id), `"${row.batch.package_name}" (${row.assignments.length} items)`)}>Activate all <ArrowRight size={13}/></button></div>;
        })}
      </div>
    </div>
    {activateTarget && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:480,marginBottom:18}} onSubmit={event => { event.preventDefault(); void submitActivate(); }}>
      <div className="panel-head"><h2>Activate {activateTarget.label}</h2><button type="button" className="btn" aria-label="Close" onClick={() => setActivateTarget(null)}><X size={14}/></button></div>
      <div className="detail-section">
        <label className="key" style={{display:'block'}}><span>Duration (hours) — up to {maxHours}</span><input className="select" style={{width:'100%'}} type="number" min={1} max={maxHours} step={0.5} value={durationHours} onChange={event => setDurationHours(event.target.value)}/></label>
        <label className="key" style={{display:'block',marginTop:14}}><span>Justification — why do you need this now? (required)</span><input className="select" style={{width:'100%'}} required value={activateJustification} onChange={event => setActivateJustification(event.target.value)}/></label>
        {activateMessage && <div className="notice" style={{marginTop:14}}>{activateMessage}</div>}
      </div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setActivateTarget(null)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={activateSaving}>{activateSaving ? 'Activating...' : 'Activate'}</button></div>
    </form>}
    <TablePanel toolbar={undefined}>{loading ? <div className="empty">Loading access...</div> : error ? <div className="empty">{error}</div> : activeRows.length === 0 ? <div className="empty">No active access.</div> : <table><thead><tr><th>Resource</th><th>Type</th><th>Package</th><th>Activated</th><th>Expires</th><th>Remaining</th><th></th></tr></thead><tbody>{activeRows.map(row => row.kind === 'single'
      ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.resource_display_name || row.assignment.resource_id}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.package_name || '—'}</td><td>{row.assignment.activated_at ? new Date(row.assignment.activated_at).toLocaleString() : '—'}</td><td>{row.assignment.expiration_time ? new Date(row.assignment.expiration_time).toLocaleString() : 'Never'}</td><td>{row.assignment.expiration_time ? formatRemaining(row.assignment.expiration_time) : '—'}</td><td>{row.assignment.bypass_activation ? <span className="footer-note">Assigned by admin</span> : <button className="btn" disabled={busyIds === row.assignment.id} onClick={() => void deactivate([row.assignment.id], row.assignment.resource_display_name || 'this access')}>Deactivate</button>}</td></tr>
      : <tr key={row.batch.package_id}><td className="user-name">📦 {row.batch.package_name}</td><td>PACKAGE ({row.assignments.length})</td><td>{row.batch.package_name}</td><td>{row.assignments[0]?.activated_at ? new Date(row.assignments[0].activated_at!).toLocaleString() : '—'}</td><td>{row.assignments[0]?.expiration_time ? new Date(row.assignments[0].expiration_time!).toLocaleString() : 'Never'}</td><td>{row.assignments[0]?.expiration_time ? formatRemaining(row.assignments[0].expiration_time) : '—'}</td><td><button className="btn" disabled={busyIds === row.assignments.map(a => a.id).join(',')} onClick={() => void deactivate(row.assignments.map(a => a.id), `"${row.batch.package_name}" (${row.assignments.length} items)`)}>Deactivate all</button></td></tr>
    )}</tbody></table>}</TablePanel>
  </Page>;
}
function RequestAccess() {
  // Real self-service access requests go through Access Packages — every resource an end user can request must
  // be named in a package's eligibility list, matching real, granted access to real business justification
  // requirements, fallback approvers, etc. Rebuilding a parallel free-text "any resource" request flow here
  // would duplicate that entire working system against the dormant, pre-Assignment-model AccessRequest/
  // ApprovalStep tables — real tables in the schema, but superseded by AccessAssignment years before this UI
  // page was ever wired up. Rather than fake a second system, this page now honestly points at the real one.
  const { data: packages, loading } = useApiResource<ApiPackage[]>('/api/v1/packages/requestable');
  return <Page eyebrow="SELF-SERVICE" title="Request access" subtitle="Self-service access requests go through Access Packages.">
    <div className="panel">
      <div className="detail-section">
        <p style={{marginTop:0}}>AccessPilot's real self-service request flow lives under <strong>Request Packages</strong> — every package there is something you (individually, or via a group you belong to) are specifically eligible to request, with the same real approval and activation workflow as anything an Admin assigns directly.</p>
        {loading ? <div className="empty">Checking what you're eligible for...</div> : packages && packages.length > 0 ? <div className="notice" style={{marginBottom:16}}>You currently have <strong>{packages.length}</strong> {packages.length === 1 ? 'package' : 'packages'} you can request.</div> : <div className="notice" style={{marginBottom:16}}>You aren't currently eligible for any packages — ask an Admin to add you (or a group you're in) to a package's eligibility list.</div>}
        <Link to="/request-packages" className="btn btn-primary"><ArrowRight size={15}/> Go to Request Packages</Link>
      </div>
    </div>
  </Page>;
}
const emptyPackageRequestForm = { assignment_type: 'PERMANENT', start_date: '', start_clock: '', end_date: '', end_clock: '', justification: '' };
function RequestPackagesPage() {
  const auth = useAuth();
  const { data: packages, error, loading, reload } = useApiResource<ApiPackage[]>('/api/v1/packages/requestable');
  const [requestingPackage, setRequestingPackage] = useState<ApiPackage | null>(null);
  const [form, setForm] = useState(emptyPackageRequestForm);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [successMessage, setSuccessMessage] = useState('');
  const today = todayDateValue(new Date());

  const openRequest = (pkg: ApiPackage) => { setRequestingPackage(pkg); setForm(emptyPackageRequestForm); setMessage(''); };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!requestingPackage) return;
    if (form.assignment_type === 'TEMPORARY' && !form.end_date && !form.end_clock) { setMessage('Set an end date/time for a time-bound request.'); return; }
    if (form.justification.trim().length < 3) { setMessage('A justification (at least 3 characters) is required.'); return; }
    setSaving(true); setMessage('');
    try {
      const payload: Record<string, unknown> = { assignment_type: form.assignment_type, justification: form.justification.trim() };
      if (form.start_date || form.start_clock) payload.start_time = new Date(`${form.start_date || today}T${form.start_clock || '00:00'}`).toISOString();
      if (form.assignment_type === 'TEMPORARY' && (form.end_date || form.end_clock)) payload.expiration_time = new Date(`${form.end_date || today}T${form.end_clock || '23:59'}`).toISOString();
      const response = await auth.apiRequest(`/api/v1/packages/${requestingPackage.id}/request`, { method: 'POST', body: JSON.stringify(payload) });
      if (response.status === 201) {
        const body = await response.json();
        const results: { status: string; assignment?: { status: string } }[] = body.results || [];
        const failed = results.filter(r => r.status === 'FAILED').length;
        const pending = results.some(r => r.assignment?.status === 'PENDING_APPROVAL');
        const name = requestingPackage.name;
        setRequestingPackage(null);
        setSuccessMessage(failed > 0 ? `Requested "${name}" — ${failed} item(s) failed, contact an admin.` : pending ? `Requested "${name}" — awaiting approval.` : `"${name}" is now eligible — activate it from My Access.`);
        reload();
      } else { const errorBody = await response.json().catch(() => null); setMessage(errorBody?.error?.message || 'Unable to submit this request.'); }
    } catch (err) {
      setMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to submit this request.');
    } finally { setSaving(false); }
  };

  return <Page eyebrow="SELF-SERVICE" title="Request Packages" subtitle="Access packages you're eligible to request for yourself." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    {successMessage && <div className="detail-section" style={{marginBottom:14}}><div className="notice">{successMessage}</div></div>}
    {requestingPackage && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:640,marginBottom:18}} onSubmit={submit}>
      <div className="panel-head"><h2>Request "{requestingPackage.name}"</h2><button type="button" className="btn" aria-label="Close" onClick={() => setRequestingPackage(null)}><X size={14}/></button></div>
      <div className="detail-section">
        <label className="key"><span>Duration</span><select className="select" value={form.assignment_type} onChange={event => setForm({...form, assignment_type: event.target.value})}><option value="PERMANENT">Permanent</option><option value="TEMPORARY">Time-bound</option></select></label>
        <div style={{marginTop:18}}>
          <div className="key" style={{marginBottom:8}}><span>Start (optional) — leave blank to start now</span></div>
          <div style={{display:'flex',gap:10}}>
            <input className="select" style={{flex:1}} type="date" min={today} value={form.start_date} onChange={event => setForm({...form, start_date: event.target.value})}/>
            <input className="select" style={{flex:1}} type="time" value={form.start_clock} onChange={event => setForm({...form, start_clock: event.target.value})}/>
          </div>
        </div>
        {form.assignment_type === 'TEMPORARY' && <div style={{marginTop:18}}>
          <div className="key" style={{marginBottom:8}}><span>Ends</span></div>
          <div style={{display:'flex',gap:10}}>
            <input className="select" style={{flex:1}} type="date" min={form.start_date || today} value={form.end_date} onChange={event => setForm({...form, end_date: event.target.value})}/>
            <input className="select" style={{flex:1}} type="time" value={form.end_clock} onChange={event => setForm({...form, end_clock: event.target.value})}/>
          </div>
        </div>}
        <label className="key" style={{display:'block',marginTop:14}}><span>Justification (required)</span><input className="select" style={{width:'100%'}} required value={form.justification} onChange={event => setForm({...form, justification: event.target.value})}/></label>
        {message && <div className="notice" style={{marginTop:14}}>{message}</div>}
      </div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setRequestingPackage(null)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Requesting...' : 'Submit request'}</button></div>
    </form>}
    <TablePanel toolbar={undefined}>{loading ? <div className="empty">Loading packages...</div> : error ? <div className="empty">{error}</div> : !packages || packages.length === 0 ? <div className="empty">No access packages are available for you to request.</div> : <table><thead><tr><th>Name</th><th>Description</th><th>Includes</th><th></th></tr></thead><tbody>{packages.map(p => <tr key={p.id}><td className="user-name">{p.name}</td><td>{p.description || '—'}</td><td>{p.items.map(i => i.resource_display_name || i.resource_id).join(', ')}</td><td><button className="btn btn-primary" onClick={() => openRequest(p)}>Request</button></td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
interface ApiAssignment { id: string; user_id: string; user_display_name: string | null; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; assignment_type: string; status: string; start_time: string | null; expiration_time: string | null; justification: string | null; requested_by: string | null; approved_by: string | null; bypass_activation: boolean; activated_at: string | null; created_at: string; package_name: string | null; }
interface ApiActivationPolicy { max_self_activation_hours: number; }
function todayDateValue(date: Date) { const pad = (n: number) => String(n).padStart(2, '0'); return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`; }
const emptyAssignmentForm = { user_id: '', resource_type: 'GROUP', resource_id: '', app_role_external_id: '', assignment_type: 'PERMANENT', start_date: '', start_clock: '', end_date: '', end_clock: '', approver_id: '', bypass_activation: false, justification: '' };
function AssignmentsInteractive() {
  const auth = useAuth();
  const { data: assignmentList, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments');
  const { data: users, reload: reloadUsers } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: groups, reload: reloadGroups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles, reload: reloadRoles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications, reload: reloadApplications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const { data: packages, reload: reloadPackages } = useApiResource<ApiPackage[]>('/api/v1/packages');
  const { data: batches } = useApiResource<ApiPackageBatch[]>('/api/v1/packages/assignment-batches');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const statusFilter = searchParams.get('status') || '';
  const expiringFilter = searchParams.get('expiring') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setStatusFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('status', value); else next.delete('status'); return next; });
  const setExpiringFilter = (value: boolean) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('expiring', '24h'); else next.delete('expiring'); return next; });
  const statusOptions = useMemo(() => Array.from(new Set((assignmentList || []).map(a => a.status))).sort().map(s => ({ value: s, label: s })), [assignmentList]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyAssignmentForm);
  const [saving, setSaving] = useState(false);
  const [formMessage, setFormMessage] = useState('');
  const [actioningId, setActioningId] = useState<string | null>(null);
  const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set());
  const targets = form.resource_type === 'GROUP' ? (groups || []) : form.resource_type === 'ROLE' ? (roles || []) : form.resource_type === 'APPLICATION' ? (applications || []) : (packages || []).filter(p => p.status === 'ACTIVE');
  const selectedApplication = form.resource_type === 'APPLICATION' ? (applications || []).find(a => a.id === form.resource_id) : undefined;
  const today = todayDateValue(new Date());
  const batchByAssignmentId = useMemo(() => { const map = new Map<string, ApiPackageBatch>(); (batches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [batches]);
  const filteredAssignments = useMemo(() => {
    const now = Date.now();
    return (assignmentList || []).filter(a => {
      if (statusFilter && a.status !== statusFilter) return false;
      if (expiringFilter === '24h') {
        if (a.status !== 'ACTIVE' || !a.expiration_time) return false;
        const expiresAt = new Date(a.expiration_time).getTime();
        if (expiresAt < now || expiresAt > now + 24 * 60 * 60 * 1000) return false;
      }
      if (search) {
        const haystack = `${a.user_display_name || ''} ${a.resource_display_name || ''}`.toLowerCase();
        if (!haystack.includes(search.toLowerCase())) return false;
      }
      return true;
    });
  }, [assignmentList, statusFilter, expiringFilter, search]);
  const groupedRows = useMemo(() => {
    const rows: Array<{ kind: 'single'; assignment: ApiAssignment } | { kind: 'batch'; batch: ApiPackageBatch; assignments: ApiAssignment[] }> = [];
    const seenBatches = new Set<string>();
    filteredAssignments.forEach(a => {
      const batch = batchByAssignmentId.get(a.id);
      if (!batch) { rows.push({ kind: 'single', assignment: a }); return; }
      if (seenBatches.has(batch.package_assignment_id)) return;
      seenBatches.add(batch.package_assignment_id);
      const assignments = filteredAssignments.filter(x => batchByAssignmentId.get(x.id)?.package_assignment_id === batch.package_assignment_id);
      rows.push({ kind: 'batch', batch, assignments });
    });
    return rows;
  }, [filteredAssignments, batchByAssignmentId]);
  const toggleBatch = (id: string) => setExpandedBatches(prev => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const isRevocable = (status: string) => !['REJECTED', 'REVOKED', 'EXPIRED'].includes(status);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.user_id || !form.resource_id) { setFormMessage('Select a user and a target.'); return; }
    if (form.resource_type === 'APPLICATION' && !form.app_role_external_id) { setFormMessage('Select an application role.'); return; }
    if (form.assignment_type === 'TEMPORARY' && !form.end_date && !form.end_clock) { setFormMessage('Set an end date/time for a time-bound assignment.'); return; }
    if (form.justification.trim().length < 3) { setFormMessage('A justification (at least 3 characters) for this assignment is required.'); return; }
    setSaving(true); setFormMessage('');
    try {
      const isPackage = form.resource_type === 'PACKAGE';
      const payload: Record<string, unknown> = isPackage
        ? { user_id: form.user_id, assignment_type: form.assignment_type, justification: form.justification.trim() }
        : { user_id: form.user_id, resource_type: form.resource_type, resource_id: form.resource_id, assignment_type: form.assignment_type, justification: form.justification.trim() };
      if (!isPackage && form.resource_type === 'APPLICATION') payload.app_role_external_id = form.app_role_external_id;
      if (form.start_date || form.start_clock) payload.start_time = new Date(`${form.start_date || today}T${form.start_clock || '00:00'}`).toISOString();
      if (form.assignment_type === 'TEMPORARY' && (form.end_date || form.end_clock)) payload.expiration_time = new Date(`${form.end_date || today}T${form.end_clock || '23:59'}`).toISOString();
      if (!isPackage && form.bypass_activation) payload.bypass_activation = true;
      else if (form.approver_id) payload.approver_id = form.approver_id;
      const endpoint = isPackage ? `/api/v1/packages/${form.resource_id}/assign` : '/api/v1/assignments';
      const response = await auth.apiRequest(endpoint, { method: 'POST', body: JSON.stringify(payload) });
      if (response.status === 201) { setOpen(false); setForm(emptyAssignmentForm); reload(); }
      else { const errorBody = await response.json().catch(() => null); setFormMessage(errorBody?.error?.message || 'Unable to create assignment.'); }
    } catch (err) {
      setFormMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to create assignment.');
    } finally { setSaving(false); }
  };

  const decide = async (assignmentId: string, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm('Reject this assignment request?')) return;
    let justification: string | null = null;
    if (decision === 'approve') {
      justification = window.prompt('Justification for approving this request (required):');
      if (justification === null) return;
      if (justification.trim().length < 3) { window.alert('A justification (at least 3 characters) is required to approve.'); return; }
    }
    setActioningId(assignmentId);
    try {
      const response = await auth.apiRequest(`/api/v1/assignments/${assignmentId}/${decision}`, { method: 'POST', body: decision === 'approve' ? JSON.stringify({ justification: justification!.trim() }) : undefined });
      if (response.ok) reload();
    } finally { setActioningId(null); }
  };

  const decideBatch = async (batch: ApiPackageBatch, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm(`Reject all ${batch.assignment_ids.length} items in "${batch.package_name}"?`)) return;
    let justification: string | null = null;
    if (decision === 'approve') {
      justification = window.prompt(`Justification for approving all ${batch.assignment_ids.length} items in "${batch.package_name}" (required):`);
      if (justification === null) return;
      if (justification.trim().length < 3) { window.alert('A justification (at least 3 characters) is required to approve.'); return; }
    }
    setActioningId(batch.package_assignment_id);
    try {
      await Promise.all(batch.assignment_ids.map(id => auth.apiRequest(`/api/v1/assignments/${id}/${decision}`, { method: 'POST', body: decision === 'approve' ? JSON.stringify({ justification: justification!.trim() }) : undefined })));
      reload();
    } finally { setActioningId(null); }
  };

  const revoke = async (assignmentId: string, label: string) => {
    const justification = window.prompt(`Revoke "${label}"? This works no matter its current status. Justification (required):`);
    if (justification === null) return;
    if (justification.trim().length < 3) { window.alert('A justification (at least 3 characters) is required to revoke.'); return; }
    setActioningId(assignmentId);
    try {
      const response = await auth.apiRequest(`/api/v1/assignments/${assignmentId}/revoke`, { method: 'POST', body: JSON.stringify({ justification: justification.trim() }) });
      if (response.ok) reload();
      else { const body = await response.json().catch(() => null); window.alert(body?.error?.message || 'Unable to revoke this assignment.'); }
    } finally { setActioningId(null); }
  };

  const revokeBatch = async (batch: ApiPackageBatch) => {
    const justification = window.prompt(`Revoke all ${batch.assignment_ids.length} items in "${batch.package_name}"? Justification (required):`);
    if (justification === null) return;
    if (justification.trim().length < 3) { window.alert('A justification (at least 3 characters) is required to revoke.'); return; }
    setActioningId(batch.package_assignment_id);
    try {
      await Promise.all(batch.assignment_ids.map(id => auth.apiRequest(`/api/v1/assignments/${id}/revoke`, { method: 'POST', body: JSON.stringify({ justification: justification.trim() }) })));
      reload();
    } finally { setActioningId(null); }
  };

  return <Page eyebrow="ACCESS MANAGEMENT" title="Assignments" subtitle="Assign group, role, application, or package access to users, with optional approval and time-bound expiration." action={<><button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button><button className="btn btn-primary" onClick={() => { setOpen(true); setFormMessage(''); reloadUsers(); reloadGroups(); reloadRoles(); reloadApplications(); reloadPackages(); }}><Plus size={14}/> Add assignment</button></>}>
    {open && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:720,marginBottom:18}} onSubmit={submit}>
      <div className="panel-head"><h2>Add assignment</h2><button type="button" className="btn" aria-label="Close" onClick={() => setOpen(false)}><X size={14}/></button></div>
      <div className="detail-section"><div className="key-grid">
        <label className="key"><span>User</span><select className="select" value={form.user_id} onChange={event => setForm({...form, user_id: event.target.value})}><option value="">Select a user</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name} ({u.email})</option>)}</select></label>
        <label className="key"><span>Target type</span><select className="select" value={form.resource_type} onChange={event => setForm({...form, resource_type: event.target.value, resource_id: '', app_role_external_id: ''})}><option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option><option value="PACKAGE">Package</option></select></label>
        <label className="key"><span>{form.resource_type === 'GROUP' ? 'Group' : form.resource_type === 'ROLE' ? 'Role' : form.resource_type === 'APPLICATION' ? 'Application' : 'Package'}</span><select className="select" value={form.resource_id} onChange={event => setForm({...form, resource_id: event.target.value, app_role_external_id: ''})}><option value="">Select {form.resource_type === 'GROUP' ? 'a group' : form.resource_type === 'ROLE' ? 'a role' : form.resource_type === 'APPLICATION' ? 'an application' : 'a package'}</option>{targets.map((t: ApiGroup | ApiRole | ApiApplication | ApiPackage) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
        {form.resource_type === 'APPLICATION' && <label className="key"><span>Application role</span><select className="select" value={form.app_role_external_id} onChange={event => setForm({...form, app_role_external_id: event.target.value})} disabled={!selectedApplication}><option value="">Select a role</option>{(selectedApplication?.app_roles || []).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}
        <label className="key"><span>Duration</span><select className="select" value={form.assignment_type} onChange={event => setForm({...form, assignment_type: event.target.value})}><option value="PERMANENT">Permanent</option><option value="TEMPORARY">Time-bound</option></select></label>
        <label className="key"><span>Approver (optional)</span><select className="select" value={form.approver_id} disabled={form.bypass_activation} onChange={event => setForm({...form, approver_id: event.target.value})}><option value="">No approval required</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
      </div>
      {form.resource_type !== 'PACKAGE' && <label className="key" style={{display:'flex',alignItems:'center',gap:8,marginTop:16,cursor:'pointer'}}><input type="checkbox" checked={form.bypass_activation} onChange={event => setForm({...form, bypass_activation: event.target.checked, approver_id: event.target.checked ? '' : form.approver_id})}/><span style={{textTransform:'none',fontSize:12,fontWeight:600,color:'#37515c'}}>Assign immediately (Admin only) — skip activation, grant real access now; the user won't be able to deactivate it themselves</span></label>}
      <div className="notice" style={{marginTop:14}}>{form.bypass_activation ? 'This grants real access immediately, with no approval or self-activation step — the end user cannot deactivate it themselves; only an Admin can.' : form.approver_id ? 'Once approved, this becomes eligible, not active — the user still activates it themselves (up to the admin-configured limit) from their My Access page.' : 'This lands as eligible, not active — the user activates it themselves (up to the admin-configured limit) from their My Access page.'}</div>
      <div style={{marginTop:18}}>
        <div className="key" style={{marginBottom:8}}><span>{form.assignment_type === 'TEMPORARY' ? 'Start (optional) — deadline to activate by is set below' : 'Start (optional) — leave blank to start now'}</span></div>
        <div style={{display:'flex',gap:10}}>
          <input className="select" style={{flex:1}} type="date" min={today} value={form.start_date} onChange={event => setForm({...form, start_date: event.target.value})}/>
          <input className="select" style={{flex:1}} type="time" value={form.start_clock} onChange={event => setForm({...form, start_clock: event.target.value})}/>
        </div>
      </div>
      {form.assignment_type === 'TEMPORARY' && <div style={{marginTop:18}}>
        <div className="key" style={{marginBottom:8}}><span>Deadline to activate by</span></div>
        <div style={{display:'flex',gap:10}}>
          <input className="select" style={{flex:1}} type="date" min={form.start_date || today} value={form.end_date} onChange={event => setForm({...form, end_date: event.target.value})}/>
          <input className="select" style={{flex:1}} type="time" value={form.end_clock} onChange={event => setForm({...form, end_clock: event.target.value})}/>
        </div>
      </div>}
      <label className="key" style={{display:'block',marginTop:14}}><span>Justification — why are you assigning this? (required)</span><input className="select" style={{width:'100%'}} required value={form.justification} onChange={event => setForm({...form, justification: event.target.value})}/></label>
      {formMessage && <div className="notice" style={{marginTop:14}}>{formMessage}</div>}</div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Creating...' : 'Create assignment'}</button></div>
    </form>}
    <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:12}}><label style={{display:'flex',alignItems:'center',gap:6,fontSize:12,color:'#52656d',cursor:'pointer'}}><input type="checkbox" checked={expiringFilter === '24h'} onChange={event => setExpiringFilter(event.target.checked)}/> Expiring within 24 hours</label></div>
    <TablePanel toolbar={<Toolbar placeholder="Search assignments" searchValue={search} onSearchChange={setSearch} filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={statusOptions}/>}>{loading ? <div className="empty">Loading assignments...</div> : error ? <div className="empty">{error}</div> : !assignmentList || assignmentList.length === 0 ? <div className="empty">No assignments found.</div> : groupedRows.length === 0 ? <div className="empty">No assignments match this filter.</div> : <table><thead><tr><th>User</th><th>Resource</th><th>Type</th><th>Duration</th><th>Status</th><th>Start</th><th>Expiration</th><th></th></tr></thead><tbody>{groupedRows.map(row => row.kind === 'single' ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.user_display_name || row.assignment.user_id}</td><td>{row.assignment.resource_display_name || row.assignment.resource_id}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.assignment_type}</td><td><StatusBadge status={row.assignment.status}/></td><td>{row.assignment.start_time ? new Date(row.assignment.start_time).toLocaleString() : '—'}</td><td>{row.assignment.expiration_time ? new Date(row.assignment.expiration_time).toLocaleString() : '—'}</td><td>{isRevocable(row.assignment.status) ? <span style={{display:'flex',gap:5}}>{row.assignment.status === 'PENDING_APPROVAL' && <><button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'approve')} aria-label="Approve"><Check size={14}/></button><button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'reject')} aria-label="Reject"><X size={14}/></button></>}<button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void revoke(row.assignment.id, row.assignment.resource_display_name || 'this assignment')} aria-label="Revoke">Revoke</button></span> : <span className="footer-note">No actions</span>}</td></tr> : <>
      <tr key={row.batch.package_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.package_assignment_id)}>
        <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
        <td>📦 {row.batch.package_name} <span className="footer-note">({row.assignments.length} items)</span></td>
        <td>PACKAGE</td>
        <td>{row.assignments[0]?.assignment_type}</td>
        <td><StatusBadge status={new Set(row.assignments.map(a => a.status)).size === 1 ? row.assignments[0].status : 'MIXED'}/></td>
        <td>{row.assignments[0]?.start_time ? new Date(row.assignments[0].start_time!).toLocaleString() : '—'}</td>
        <td>{row.assignments[0]?.expiration_time ? new Date(row.assignments[0].expiration_time!).toLocaleString() : '—'}</td>
        <td>{row.assignments.some(a => isRevocable(a.status)) ? <span style={{display:'flex',gap:5}} onClick={event => event.stopPropagation()}>{row.assignments.some(a => a.status === 'PENDING_APPROVAL') && <><button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'approve')} aria-label="Approve all"><Check size={14}/></button><button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'reject')} aria-label="Reject all"><X size={14}/></button></>}<button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void revokeBatch(row.batch)} aria-label="Revoke all">Revoke all</button></span> : <span className="footer-note">No actions</span>}</td>
      </tr>
      {expandedBatches.has(row.batch.package_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{a.start_time ? new Date(a.start_time).toLocaleString() : '—'}</td><td>{a.expiration_time ? new Date(a.expiration_time).toLocaleString() : '—'}</td><td>{isRevocable(a.status) ? <span style={{display:'flex',gap:5}}>{a.status === 'PENDING_APPROVAL' && <><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'approve')} aria-label="Approve"><Check size={14}/></button><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'reject')} aria-label="Reject"><X size={14}/></button></>}<button className="btn" disabled={actioningId === a.id} onClick={() => void revoke(a.id, a.resource_display_name || 'this assignment')} aria-label="Revoke">Revoke</button></span> : <span className="footer-note">No actions</span>}</td></tr>)}
    </>)}</tbody></table>}</TablePanel>
  </Page>;
}
function AssignmentsPage() { return <AssignmentsInteractive />; }
const emptyPackageForm = { name: '', description: '', items: [] as { resource_type: string; resource_id: string; app_role_external_id: string }[], principals: [] as { principal_type: string; principal_id: string }[], default_approver_id: '', default_fallback_approver_id: '', fallback_unlock_hours: '' };
const emptyPackageAssignForm = { target_type: 'USER', user_id: '', group_id: '', assignment_type: 'PERMANENT', start_date: '', start_clock: '', end_date: '', end_clock: '', approver_id: '', justification: '' };
function AccessPackagesInteractive() {
  const auth = useAuth();
  const { data: packageList, error, loading, reload } = useApiResource<ApiPackage[]>('/api/v1/packages');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const statusFilter = searchParams.get('status') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setStatusFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('status', value); else next.delete('status'); return next; });
  const filteredPackages = (packageList || []).filter(p => (!statusFilter || p.status === statusFilter) && (!search || p.name.toLowerCase().includes(search.toLowerCase())));
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyPackageForm);
  const [saving, setSaving] = useState(false);
  const [formMessage, setFormMessage] = useState('');
  const [editingPackageId, setEditingPackageId] = useState<string | null>(null);
  const [assigningPackage, setAssigningPackage] = useState<ApiPackage | null>(null);
  const [assignForm, setAssignForm] = useState(emptyPackageAssignForm);
  const [assigning, setAssigning] = useState(false);
  const [assignMessage, setAssignMessage] = useState('');
  const [archivingId, setArchivingId] = useState<string | null>(null);
  const [eligibilityPackage, setEligibilityPackage] = useState<ApiPackage | null>(null);
  const [viewingEligibility, setViewingEligibility] = useState<ApiPackage | null>(null);
  const [eligibilityForm, setEligibilityForm] = useState<{ principals: { principal_type: string; principal_id: string }[]; default_approver_id: string; default_fallback_approver_id: string }>({ principals: [], default_approver_id: '', default_fallback_approver_id: '' });
  const [eligibilitySaving, setEligibilitySaving] = useState(false);
  const [eligibilityMessage, setEligibilityMessage] = useState('');
  const today = todayDateValue(new Date());

  const targetsFor = (resourceType: string) => resourceType === 'GROUP' ? (groups || []) : resourceType === 'ROLE' ? (roles || []) : (applications || []);
  const addItem = () => setForm({ ...form, items: [...form.items, { resource_type: 'GROUP', resource_id: '', app_role_external_id: '' }] });
  const removeItem = (index: number) => setForm({ ...form, items: form.items.filter((_, i) => i !== index) });
  const updateItem = (index: number, patch: Partial<{ resource_type: string; resource_id: string; app_role_external_id: string }>) => setForm({ ...form, items: form.items.map((item, i) => i === index ? { ...item, ...patch } : item) });
  const addFormPrincipal = () => setForm({ ...form, principals: [...form.principals, { principal_type: 'USER', principal_id: '' }] });
  const removeFormPrincipal = (index: number) => setForm({ ...form, principals: form.principals.filter((_, i) => i !== index) });
  const updateFormPrincipal = (index: number, patch: Partial<{ principal_type: string; principal_id: string }>) => setForm({ ...form, principals: form.principals.map((p, i) => i === index ? { ...p, ...patch } : p) });

  const openEligibility = (pkg: ApiPackage) => {
    setEligibilityPackage(pkg);
    setEligibilityForm({ principals: pkg.eligible_principals.map(p => ({ principal_type: p.principal_type, principal_id: p.principal_id })), default_approver_id: pkg.default_approver_id || '', default_fallback_approver_id: pkg.default_fallback_approver_id || '' });
    setEligibilityMessage('');
  };
  const addPrincipal = () => setEligibilityForm({ ...eligibilityForm, principals: [...eligibilityForm.principals, { principal_type: 'USER', principal_id: '' }] });
  const removePrincipal = (index: number) => setEligibilityForm({ ...eligibilityForm, principals: eligibilityForm.principals.filter((_, i) => i !== index) });
  const updatePrincipal = (index: number, patch: Partial<{ principal_type: string; principal_id: string }>) => setEligibilityForm({ ...eligibilityForm, principals: eligibilityForm.principals.map((p, i) => i === index ? { ...p, ...patch } : p) });
  const submitEligibility = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!eligibilityPackage) return;
    if (eligibilityForm.principals.some(p => !p.principal_id)) { setEligibilityMessage('Select a target for every entry.'); return; }
    setEligibilitySaving(true); setEligibilityMessage('');
    try {
      const payload = { principals: eligibilityForm.principals, default_approver_id: eligibilityForm.default_approver_id || undefined, default_fallback_approver_id: eligibilityForm.default_fallback_approver_id || undefined };
      const response = await auth.apiRequest(`/api/v1/packages/${eligibilityPackage.id}/eligibility`, { method: 'PUT', body: JSON.stringify(payload) });
      if (response.ok) { setEligibilityPackage(null); reload(); }
      else { const errorBody = await response.json().catch(() => null); setEligibilityMessage(errorBody?.error?.message || 'Unable to update eligibility.'); }
    } catch (err) {
      setEligibilityMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to update eligibility.');
    } finally { setEligibilitySaving(false); }
  };

  const openCreate = () => { setEditingPackageId(null); setForm(emptyPackageForm); setFormMessage(''); setOpen(true); };
  const openEdit = (pkg: ApiPackage) => {
    setEditingPackageId(pkg.id);
    setForm({ name: pkg.name, description: pkg.description || '', items: pkg.items.map(item => ({ resource_type: item.resource_type, resource_id: item.resource_id, app_role_external_id: item.app_role_external_id || '' })), principals: [], default_approver_id: '', default_fallback_approver_id: '', fallback_unlock_hours: '' });
    setFormMessage(''); setOpen(true);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.name.trim()) { setFormMessage('Enter a package name.'); return; }
    if (form.items.length === 0) { setFormMessage('Add at least one item.'); return; }
    if (form.items.some(item => !item.resource_id || (item.resource_type === 'APPLICATION' && !item.app_role_external_id))) { setFormMessage('Complete every item (select a target, and an application role where needed).'); return; }
    if (!editingPackageId) {
      if (form.principals.some(p => !p.principal_id)) { setFormMessage('Select a target for every eligible user/group entry.'); return; }
      if (form.fallback_unlock_hours && !form.default_fallback_approver_id) { setFormMessage('Set a fallback approver before setting how long to wait for the primary approver.'); return; }
    }
    setSaving(true); setFormMessage('');
    try {
      const payload: Record<string, unknown> = { name: form.name.trim(), description: form.description.trim() || undefined, items: form.items.map(item => ({ resource_type: item.resource_type, resource_id: item.resource_id, app_role_external_id: item.resource_type === 'APPLICATION' ? item.app_role_external_id : undefined })) };
      if (!editingPackageId) {
        payload.principals = form.principals;
        if (form.default_approver_id) payload.default_approver_id = form.default_approver_id;
        if (form.default_fallback_approver_id) payload.default_fallback_approver_id = form.default_fallback_approver_id;
        if (form.fallback_unlock_hours) payload.fallback_unlock_hours = Number(form.fallback_unlock_hours);
      }
      const response = editingPackageId
        ? await auth.apiRequest(`/api/v1/packages/${editingPackageId}`, { method: 'PATCH', body: JSON.stringify(payload) })
        : await auth.apiRequest('/api/v1/packages', { method: 'POST', body: JSON.stringify(payload) });
      if (response.status === 200 || response.status === 201) { setOpen(false); setEditingPackageId(null); setForm(emptyPackageForm); reload(); }
      else { const errorBody = await response.json().catch(() => null); setFormMessage(errorBody?.error?.message || `Unable to ${editingPackageId ? 'update' : 'create'} package.`); }
    } catch (err) {
      setFormMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : `Unable to ${editingPackageId ? 'update' : 'create'} package.`);
    } finally { setSaving(false); }
  };

  const submitAssign = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!assigningPackage) return;
    if (assignForm.target_type === 'USER' && !assignForm.user_id) { setAssignMessage('Select a user.'); return; }
    if (assignForm.target_type === 'GROUP' && !assignForm.group_id) { setAssignMessage('Select a group.'); return; }
    if (assignForm.assignment_type === 'TEMPORARY' && !assignForm.end_date && !assignForm.end_clock) { setAssignMessage('Set an end date/time for a time-bound assignment.'); return; }
    if (assignForm.justification.trim().length < 3) { setAssignMessage('A justification (at least 3 characters) for this assignment is required.'); return; }
    setAssigning(true); setAssignMessage('');
    try {
      const payload: Record<string, unknown> = assignForm.target_type === 'USER' ? { user_id: assignForm.user_id, assignment_type: assignForm.assignment_type, justification: assignForm.justification.trim() } : { group_id: assignForm.group_id, assignment_type: assignForm.assignment_type, justification: assignForm.justification.trim() };
      if (assignForm.start_date || assignForm.start_clock) payload.start_time = new Date(`${assignForm.start_date || today}T${assignForm.start_clock || '00:00'}`).toISOString();
      if (assignForm.assignment_type === 'TEMPORARY' && (assignForm.end_date || assignForm.end_clock)) payload.expiration_time = new Date(`${assignForm.end_date || today}T${assignForm.end_clock || '23:59'}`).toISOString();
      if (assignForm.approver_id) payload.approver_id = assignForm.approver_id;
      const response = await auth.apiRequest(`/api/v1/packages/${assigningPackage.id}/assign`, { method: 'POST', body: JSON.stringify(payload) });
      if (response.status === 201) {
        const body = await response.json();
        const members: { results: { status: string }[] }[] = body.members || [];
        const failedCount = members.reduce((count, member) => count + member.results.filter(r => r.status === 'FAILED').length, 0);
        if (failedCount > 0) setAssignMessage(`Assigned to ${members.length} ${members.length === 1 ? 'member' : 'members'} with ${failedCount} item(s) failed — check Assignments for details.`);
        else { setAssigningPackage(null); setAssignForm(emptyPackageAssignForm); }
      } else { const errorBody = await response.json().catch(() => null); setAssignMessage(errorBody?.error?.message || 'Unable to assign this package.'); }
    } catch (err) {
      setAssignMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to assign this package.');
    } finally { setAssigning(false); }
  };

  const deletePackage = async (packageId: string) => {
    if (!window.confirm('Delete this package? If it has never been assigned it will be removed entirely; otherwise it will be archived and no longer assignable.')) return;
    setArchivingId(packageId);
    try {
      const response = await auth.apiRequest(`/api/v1/packages/${packageId}`, { method: 'DELETE' });
      if (response.ok) reload();
    } finally { setArchivingId(null); }
  };

  return <Page eyebrow="ACCESS MANAGEMENT" title="Access Packages" subtitle="Bundle groups, roles, and application roles together and assign them to a user in one action." action={<><button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button><button className="btn btn-primary" onClick={openCreate}><Plus size={14}/> Add package</button></>}>
    {open && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:820,marginBottom:18}} onSubmit={submit}>
      <div className="panel-head"><h2>{editingPackageId ? 'Edit package' : 'Add package'}</h2><button type="button" className="btn" aria-label="Close" onClick={() => setOpen(false)}><X size={14}/></button></div>
      <div className="detail-section">
        <div className="key-grid">
          <label className="key"><span>Name</span><input className="select" style={{width:'100%'}} value={form.name} onChange={event => setForm({...form, name: event.target.value})}/></label>
          <label className="key"><span>Description (optional)</span><input className="select" style={{width:'100%'}} value={form.description} onChange={event => setForm({...form, description: event.target.value})}/></label>
        </div>
        {!editingPackageId && <>
          <div className="key" style={{marginTop:18,marginBottom:8}}><span>1. Approval flow — set this up before adding items</span></div>
          <div className="notice" style={{marginBottom:14}}>Set an approver so requests for this package go through an approval flow. A fallback approver may also decide — immediately, or only after a wait period if the primary hasn't responded.</div>
          <div className="key-grid">
            <label className="key"><span>Approver (optional) — leave blank for no approval required</span><select className="select" style={{width:'100%'}} value={form.default_approver_id} onChange={event => setForm({...form, default_approver_id: event.target.value})}><option value="">No approval required</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
            <label className="key"><span>Fallback approver (optional)</span><select className="select" style={{width:'100%'}} value={form.default_fallback_approver_id} onChange={event => setForm({...form, default_fallback_approver_id: event.target.value})}><option value="">No fallback</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
            <label className="key"><span>Fallback may act after (hours) — optional; blank means immediately</span><input className="select" style={{width:'100%'}} type="number" min={1} disabled={!form.default_fallback_approver_id} value={form.fallback_unlock_hours} onChange={event => setForm({...form, fallback_unlock_hours: event.target.value})}/></label>
          </div>
          <div className="key" style={{marginTop:18,marginBottom:8}}><span>Who can request this package</span></div>
          {form.principals.map((principal, index) => {
            const options = (principal.principal_type === 'USER' ? (users || []) : (groups || [])).filter(o => o.id === principal.principal_id || !form.principals.some((p, i) => i !== index && p.principal_type === principal.principal_type && p.principal_id === o.id));
            return <div key={index} style={{display:'flex',gap:10,alignItems:'flex-end',marginBottom:10}}>
              <label className="key" style={{flex:1}}><span>Type</span><select className="select" style={{width:'100%'}} value={principal.principal_type} onChange={event => updateFormPrincipal(index, { principal_type: event.target.value, principal_id: '' })}><option value="USER">Individual user</option><option value="GROUP">Group</option></select></label>
              <label className="key" style={{flex:1}}><span>{principal.principal_type === 'USER' ? 'User' : 'Group'}</span><select className="select" style={{width:'100%'}} value={principal.principal_id} onChange={event => updateFormPrincipal(index, { principal_id: event.target.value })}><option value="">Select...</option>{options.map((o: ApiUser | ApiGroup) => <option key={o.id} value={o.id}>{principal.principal_type === 'USER' ? (o as ApiUser).display_name : (o as ApiGroup).name}</option>)}</select></label>
              <button type="button" className="btn" aria-label="Remove" onClick={() => removeFormPrincipal(index)}><X size={14}/></button>
            </div>;
          })}
          <button type="button" className="btn" onClick={addFormPrincipal}><Plus size={14}/> Add eligible user/group</button>
          {form.principals.length === 0 && <div className="notice" style={{marginTop:14}}>No one can self-request this package yet — you can still assign it directly, or add eligible users/groups now or later.</div>}
        </>}
        <div className="key" style={{marginTop:18,marginBottom:8}}><span>{editingPackageId ? 'Items' : '2. Items'}</span></div>
        {form.items.map((item, index) => {
          const itemTargets = targetsFor(item.resource_type);
          const selectedApp = item.resource_type === 'APPLICATION' ? (applications || []).find(a => a.id === item.resource_id) : undefined;
          return <div key={index} style={{display:'flex',gap:10,alignItems:'flex-end',marginBottom:10}}>
            <label className="key" style={{flex:1}}><span>Target type</span><select className="select" style={{width:'100%'}} value={item.resource_type} onChange={event => updateItem(index, { resource_type: event.target.value, resource_id: '', app_role_external_id: '' })}><option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option></select></label>
            <label className="key" style={{flex:1}}><span>{item.resource_type === 'GROUP' ? 'Group' : item.resource_type === 'ROLE' ? 'Role' : 'Application'}</span><select className="select" style={{width:'100%'}} value={item.resource_id} onChange={event => updateItem(index, { resource_id: event.target.value, app_role_external_id: '' })}><option value="">Select...</option>{itemTargets.map((t: ApiGroup | ApiRole | ApiApplication) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
            {item.resource_type === 'APPLICATION' && <label className="key" style={{flex:1}}><span>Application role</span><select className="select" style={{width:'100%'}} value={item.app_role_external_id} onChange={event => updateItem(index, { app_role_external_id: event.target.value })} disabled={!selectedApp}><option value="">Select a role</option>{(selectedApp?.app_roles || []).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}
            <button type="button" className="btn" aria-label="Remove item" onClick={() => removeItem(index)}><X size={14}/></button>
          </div>;
        })}
        <button type="button" className="btn" onClick={addItem}><Plus size={14}/> Add item</button>
        {formMessage && <div className="notice" style={{marginTop:14}}>{formMessage}</div>}
      </div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving...' : editingPackageId ? 'Save changes' : 'Create package'}</button></div>
    </form>}
    {assigningPackage && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:720,marginBottom:18}} onSubmit={submitAssign}>
      <div className="panel-head"><h2>Assign "{assigningPackage.name}"</h2><button type="button" className="btn" aria-label="Close" onClick={() => setAssigningPackage(null)}><X size={14}/></button></div>
      <div className="detail-section"><div className="key-grid">
        <label className="key"><span>Assign to</span><select className="select" value={assignForm.target_type} onChange={event => setAssignForm({...assignForm, target_type: event.target.value, user_id: '', group_id: ''})}><option value="USER">Individual user</option><option value="GROUP">Everyone in a group</option></select></label>
        {assignForm.target_type === 'USER' ? <label className="key"><span>User</span><select className="select" value={assignForm.user_id} onChange={event => setAssignForm({...assignForm, user_id: event.target.value})}><option value="">Select a user</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name} ({u.email})</option>)}</select></label> : <label className="key"><span>Group</span><select className="select" value={assignForm.group_id} onChange={event => setAssignForm({...assignForm, group_id: event.target.value})}><option value="">Select a group</option>{(groups || []).map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>}
        <label className="key"><span>Duration</span><select className="select" value={assignForm.assignment_type} onChange={event => setAssignForm({...assignForm, assignment_type: event.target.value})}><option value="PERMANENT">Permanent</option><option value="TEMPORARY">Time-bound</option></select></label>
        <label className="key"><span>Approver (optional)</span><select className="select" value={assignForm.approver_id} onChange={event => setAssignForm({...assignForm, approver_id: event.target.value})}><option value="">No approval required</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
      </div>
      <div style={{marginTop:18}}>
        <div className="key" style={{marginBottom:8}}><span>Start (optional) — leave blank to start now</span></div>
        <div style={{display:'flex',gap:10}}>
          <input className="select" style={{flex:1}} type="date" min={today} value={assignForm.start_date} onChange={event => setAssignForm({...assignForm, start_date: event.target.value})}/>
          <input className="select" style={{flex:1}} type="time" value={assignForm.start_clock} onChange={event => setAssignForm({...assignForm, start_clock: event.target.value})}/>
        </div>
      </div>
      {assignForm.assignment_type === 'TEMPORARY' && <div style={{marginTop:18}}>
        <div className="key" style={{marginBottom:8}}><span>Ends</span></div>
        <div style={{display:'flex',gap:10}}>
          <input className="select" style={{flex:1}} type="date" min={assignForm.start_date || today} value={assignForm.end_date} onChange={event => setAssignForm({...assignForm, end_date: event.target.value})}/>
          <input className="select" style={{flex:1}} type="time" value={assignForm.end_clock} onChange={event => setAssignForm({...assignForm, end_clock: event.target.value})}/>
        </div>
      </div>}
      <label className="key" style={{display:'block',marginTop:14}}><span>Justification — why are you assigning this? (required)</span><input className="select" style={{width:'100%'}} required value={assignForm.justification} onChange={event => setAssignForm({...assignForm, justification: event.target.value})}/></label>
      {assignMessage && <div className="notice" style={{marginTop:14}}>{assignMessage}</div>}</div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setAssigningPackage(null)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={assigning}>{assigning ? 'Assigning...' : 'Assign package'}</button></div>
    </form>}
    {eligibilityPackage && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:720,marginBottom:18}} onSubmit={submitEligibility}>
      <div className="panel-head"><h2>Who can request "{eligibilityPackage.name}"</h2><button type="button" className="btn" aria-label="Close" onClick={() => setEligibilityPackage(null)}><X size={14}/></button></div>
      <div className="detail-section">
        <div style={{display:'flex',gap:14,marginBottom:14}}>
          <label className="key" style={{flex:1}}><span>Default approver (optional) — used automatically when someone eligible requests this package</span><select className="select" style={{width:'100%'}} value={eligibilityForm.default_approver_id} onChange={event => setEligibilityForm({...eligibilityForm, default_approver_id: event.target.value})}><option value="">No approval required</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
          <label className="key" style={{flex:1}}><span>Fallback approver (optional) — may also approve if the default approver hasn't</span><select className="select" style={{width:'100%'}} value={eligibilityForm.default_fallback_approver_id} onChange={event => setEligibilityForm({...eligibilityForm, default_fallback_approver_id: event.target.value})}><option value="">No fallback</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
        </div>
        <div className="key" style={{marginBottom:8}}><span>Eligible users / groups</span></div>
        {eligibilityForm.principals.map((principal, index) => {
          const options = (principal.principal_type === 'USER' ? (users || []) : (groups || [])).filter(o => o.id === principal.principal_id || !eligibilityForm.principals.some((p, i) => i !== index && p.principal_type === principal.principal_type && p.principal_id === o.id));
          return <div key={index} style={{display:'flex',gap:10,alignItems:'flex-end',marginBottom:10}}>
            <label className="key" style={{flex:1}}><span>Type</span><select className="select" style={{width:'100%'}} value={principal.principal_type} onChange={event => updatePrincipal(index, { principal_type: event.target.value, principal_id: '' })}><option value="USER">Individual user</option><option value="GROUP">Group</option></select></label>
            <label className="key" style={{flex:1}}><span>{principal.principal_type === 'USER' ? 'User' : 'Group'}</span><select className="select" style={{width:'100%'}} value={principal.principal_id} onChange={event => updatePrincipal(index, { principal_id: event.target.value })}><option value="">Select...</option>{options.map((o: ApiUser | ApiGroup) => <option key={o.id} value={o.id}>{principal.principal_type === 'USER' ? (o as ApiUser).display_name : (o as ApiGroup).name}</option>)}</select></label>
            <button type="button" className="btn" aria-label="Remove" onClick={() => removePrincipal(index)}><X size={14}/></button>
          </div>;
        })}
        <button type="button" className="btn" onClick={addPrincipal}><Plus size={14}/> Add eligible user/group</button>
        {eligibilityForm.principals.length === 0 && <div className="notice" style={{marginTop:14}}>No one can currently self-request this package — end users only see it under "Request Packages" once eligible.</div>}
        {eligibilityMessage && <div className="notice" style={{marginTop:14}}>{eligibilityMessage}</div>}
      </div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setEligibilityPackage(null)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={eligibilitySaving}>{eligibilitySaving ? 'Saving...' : 'Save eligibility'}</button></div>
    </form>}
    <TablePanel toolbar={<Toolbar placeholder="Search packages" searchValue={search} onSearchChange={setSearch} filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={[{value:'ACTIVE',label:'Active'},{value:'ARCHIVED',label:'Archived'}]}/>}>{loading ? <div className="empty">Loading packages...</div> : error ? <div className="empty">{error}</div> : !packageList || packageList.length === 0 ? <div className="empty">No packages found.</div> : filteredPackages.length === 0 ? <div className="empty">No packages match this filter.</div> : <table><thead><tr><th>Name</th><th>Description</th><th>Items</th><th>Status</th><th>Requestable by</th><th></th></tr></thead><tbody>{filteredPackages.map(p => <tr key={p.id}><td className="user-name">{p.name}</td><td>{p.description || '—'}</td><td>{p.items.map(i => i.resource_display_name || i.resource_id).join(', ')}</td><td><StatusBadge status={p.status}/></td><td>{p.eligible_principals.length === 0 ? '—' : <button type="button" className="btn" style={{padding:'4px 9px',fontSize:11,fontWeight:700,color:'var(--teal-dark)',borderColor:'#c7e3e3',background:'var(--mint)'}} onClick={() => setViewingEligibility(p)} title="Click to see who">{p.eligible_principals.length}</button>}</td><td><span style={{display:'flex',gap:5}}>{p.status === 'ACTIVE' && <button className="btn btn-primary" onClick={() => { setAssigningPackage(p); setAssignForm(emptyPackageAssignForm); setAssignMessage(''); }}>Assign</button>}<button className="btn" onClick={() => openEdit(p)}>Edit</button>{p.status === 'ACTIVE' && <button className="btn" onClick={() => openEligibility(p)}>Eligibility</button>}{p.status === 'ACTIVE' && <button className="btn" disabled={archivingId === p.id} onClick={() => void deletePackage(p.id)}>Delete</button>}</span></td></tr>)}</tbody></table>}</TablePanel>
    {viewingEligibility && <div className="overlay-backdrop" onClick={() => setViewingEligibility(null)}>
      <div className="overlay-card" onClick={event => event.stopPropagation()}>
        <div className="panel-head">
          <div>
            <h2>Who can request this</h2>
            <p className="subtitle" style={{marginTop:3}}>{viewingEligibility.name}</p>
          </div>
          <button type="button" className="btn" aria-label="Close" onClick={() => setViewingEligibility(null)}><X size={14}/></button>
        </div>
        <div className="table-wrap">
          <table><thead><tr><th>Type</th><th>Name</th></tr></thead><tbody>
            {viewingEligibility.eligible_principals.map((principal, index) => <tr key={`${principal.principal_type}-${principal.principal_id}-${index}`}>
              <td><span className={`badge ${principal.principal_type === 'USER' ? 'info' : 'neutral'}`}>{principal.principal_type === 'USER' ? 'User' : 'Group'}</span></td>
              <td className="user-name">{principal.display_name || principal.principal_id}</td>
            </tr>)}
          </tbody></table>
        </div>
        <div className="detail-section" style={{display:'flex',justifyContent:'flex-end'}}>
          <button type="button" className="btn" onClick={() => { setViewingEligibility(null); openEligibility(viewingEligibility); }}>Edit eligibility</button>
        </div>
      </div>
    </div>}
  </Page>;
}
function MyApprovalsPage() {
  const auth = useAuth();
  const { data: items, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments/pending-approval');
  // Self-scoped: returns only batches where the caller is the designated approver, so it works for any
  // authenticated user (not just Admins) — same access model as /assignments/pending-approval above.
  const { data: batches } = useApiResource<ApiPackageBatch[]>('/api/v1/packages/my-assignment-batches');
  const [actioningId, setActioningId] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set());
  const decide = async (assignmentId: string, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm('Reject this access request?')) return;
    let justification: string | null = null;
    if (decision === 'approve') {
      justification = window.prompt('Justification for approving this request (required):');
      if (justification === null) return;
      if (justification.trim().length < 3) { setMessage('A justification (at least 3 characters) is required to approve.'); return; }
    }
    setActioningId(assignmentId); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/assignments/${assignmentId}/${decision}`, { method: 'POST', body: decision === 'approve' ? JSON.stringify({ justification: justification!.trim() }) : undefined });
      if (response.ok) reload();
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to complete this action.'); }
    } catch { setMessage('Unable to complete this action.'); } finally { setActioningId(null); }
  };
  const decideBatch = async (batch: ApiPackageBatch, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm(`Reject all ${batch.assignment_ids.length} items in "${batch.package_name}"?`)) return;
    let justification: string | null = null;
    if (decision === 'approve') {
      justification = window.prompt(`Justification for approving all ${batch.assignment_ids.length} items in "${batch.package_name}" (required):`);
      if (justification === null) return;
      if (justification.trim().length < 3) { setMessage('A justification (at least 3 characters) is required to approve.'); return; }
    }
    setActioningId(batch.package_assignment_id); setMessage('');
    try {
      const responses = await Promise.all(batch.assignment_ids.map(id => auth.apiRequest(`/api/v1/assignments/${id}/${decision}`, { method: 'POST', body: decision === 'approve' ? JSON.stringify({ justification: justification!.trim() }) : undefined })));
      if (responses.every(r => r.ok)) reload();
      else setMessage('Some items in this package could not be processed.');
    } catch { setMessage('Unable to complete this action.'); } finally { setActioningId(null); }
  };
  const toggleBatch = (id: string) => setExpandedBatches(prev => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const batchByAssignmentId = useMemo(() => { const map = new Map<string, ApiPackageBatch>(); (batches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [batches]);
  const groupRows = (list: ApiAssignment[]) => {
    const rows: Array<{ kind: 'single'; assignment: ApiAssignment } | { kind: 'batch'; batch: ApiPackageBatch; assignments: ApiAssignment[] }> = [];
    const seen = new Set<string>();
    list.forEach(a => {
      const batch = batchByAssignmentId.get(a.id);
      if (!batch) { rows.push({ kind: 'single', assignment: a }); return; }
      if (seen.has(batch.package_assignment_id)) return;
      seen.add(batch.package_assignment_id);
      rows.push({ kind: 'batch', batch, assignments: list.filter(x => batchByAssignmentId.get(x.id)?.package_assignment_id === batch.package_assignment_id) });
    });
    return rows;
  };
  const pending = (items || []).filter(a => a.status === 'PENDING_APPROVAL');
  const decided = (items || []).filter(a => a.status !== 'PENDING_APPROVAL');
  const pendingRows = groupRows(pending);
  const decidedRows = groupRows(decided);
  return <Page eyebrow="ACCESS MANAGEMENT" title="Approvals" subtitle="Access assignments where you are the designated approver." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    {message && <div className="detail-section" style={{marginBottom:14}}><div className="notice">{message}</div></div>}
    <TablePanel toolbar={undefined}>{loading ? <div className="empty">Loading approvals...</div> : error ? <div className="empty">{error}</div> : !items || items.length === 0 ? <div className="empty">No assignments are waiting on your approval.</div> : <table><thead><tr><th>User</th><th>Resource</th><th>Type</th><th>Duration</th><th>Status</th><th>Requested</th><th></th></tr></thead><tbody>
      {pendingRows.map(row => row.kind === 'single' ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.user_display_name || row.assignment.user_id}</td><td>{row.assignment.resource_display_name || row.assignment.resource_id}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.assignment_type}</td><td><StatusBadge status={row.assignment.status}/></td><td>{new Date(row.assignment.created_at).toLocaleString()}</td><td><span style={{display:'flex',gap:5}}><button className="btn btn-primary" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'approve')} aria-label="Approve"><Check size={14}/> Approve</button><button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'reject')} aria-label="Reject"><X size={14}/> Reject</button></span></td></tr> : <>
        <tr key={row.batch.package_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.package_assignment_id)}>
          <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
          <td>📦 {row.batch.package_name} <span className="footer-note">({row.assignments.length} items)</span></td>
          <td>PACKAGE</td>
          <td>{row.assignments[0]?.assignment_type}</td>
          <td><StatusBadge status="PENDING_APPROVAL"/></td>
          <td>{new Date(row.assignments[0].created_at).toLocaleString()}</td>
          <td><span style={{display:'flex',gap:5}} onClick={event => event.stopPropagation()}><button className="btn btn-primary" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'approve')} aria-label="Approve all"><Check size={14}/> Approve all</button><button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'reject')} aria-label="Reject all"><X size={14}/> Reject all</button></span></td>
        </tr>
        {expandedBatches.has(row.batch.package_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{new Date(a.created_at).toLocaleString()}</td><td></td></tr>)}
      </>)}
      {decidedRows.map(row => row.kind === 'single' ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.user_display_name || row.assignment.user_id}</td><td>{row.assignment.resource_display_name || row.assignment.resource_id}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.assignment_type}</td><td><StatusBadge status={row.assignment.status}/></td><td>{new Date(row.assignment.created_at).toLocaleString()}</td><td><span className="footer-note">Decided</span></td></tr> : <>
        <tr key={row.batch.package_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.package_assignment_id)}>
          <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
          <td>📦 {row.batch.package_name} <span className="footer-note">({row.assignments.length} items)</span></td>
          <td>PACKAGE</td>
          <td>{row.assignments[0]?.assignment_type}</td>
          <td><StatusBadge status={new Set(row.assignments.map(a => a.status)).size === 1 ? row.assignments[0].status : 'MIXED'}/></td>
          <td>{new Date(row.assignments[0].created_at).toLocaleString()}</td>
          <td><span className="footer-note">Decided</span></td>
        </tr>
        {expandedBatches.has(row.batch.package_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{new Date(a.created_at).toLocaleString()}</td><td></td></tr>)}
      </>)}
    </tbody></table>}</TablePanel>
  </Page>;
}
function GroupsPage() {
  const auth = useAuth();
  const { data: groups, error, loading, reload } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const privilegedFilter = searchParams.get('privileged') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setPrivilegedFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('privileged', value); else next.delete('privileged'); return next; });
  const filteredGroups = (groups || []).filter(g => (!privilegedFilter || String(g.is_privileged) === privilegedFilter) && (!search || g.name.toLowerCase().includes(search.toLowerCase())));
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ display_name: '', description: '' });
  const [saving, setSaving] = useState(false);
  const [formMessage, setFormMessage] = useState('');
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.display_name.trim()) { setFormMessage('Group name is required.'); return; }
    setSaving(true); setFormMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/groups', { method: 'POST', body: JSON.stringify(form) });
      if (response.status === 201) { setForm({ display_name: '', description: '' }); setOpen(false); reload(); }
      else if (response.status === 409) setFormMessage('A group with this name already exists.');
      else { const errorBody = await response.json().catch(() => null); setFormMessage(errorBody?.error?.message || 'Unable to create group. Please try again.'); }
    } catch (err) {
      setFormMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to create group. Please try again.');
    } finally { setSaving(false); }
  };
  return <Page eyebrow="ADMINISTRATION" title="Groups" subtitle="Directory groups and membership governance." action={<button className="btn btn-primary" onClick={() => { setOpen(true); setFormMessage(''); }}><Plus size={14}/> Add group</button>}>
    {open && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:640,marginBottom:18}} onSubmit={submit}><div className="panel-head"><h2>Add group</h2><button type="button" className="btn" aria-label="Close" onClick={() => setOpen(false)}><X size={14}/></button></div><div className="detail-section"><label className="key" style={{display:'block'}}><span>Group name</span><input className="select" style={{width:'100%'}} value={form.display_name} onChange={event => setForm({...form, display_name: event.target.value})}/></label><label className="key" style={{display:'block',marginTop:14}}><span>Description</span><input className="select" style={{width:'100%'}} value={form.description} onChange={event => setForm({...form, description: event.target.value})}/></label>{formMessage && <div className="notice" style={{marginTop:14}}>{formMessage}</div>}</div><div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Creating...' : 'Create group'}</button></div></form>}
    <TablePanel toolbar={<Toolbar placeholder="Search groups" searchValue={search} onSearchChange={setSearch} filterLabel="All groups" filterValue={privilegedFilter} onFilterChange={setPrivilegedFilter} filterOptions={[{value:'true',label:'Privileged'},{value:'false',label:'Standard'}]}/>}>{loading ? <div className="empty">Loading groups...</div> : error ? <div className="empty">{error}</div> : !groups || groups.length === 0 ? <div className="empty">No groups found.</div> : filteredGroups.length === 0 ? <div className="empty">No groups match this filter.</div> : <table><thead><tr><th>Name</th><th>Description</th><th>Privileged</th><th>Status</th><th>Last synced</th></tr></thead><tbody>{filteredGroups.map(g => <tr key={g.id}><td className="user-name">{g.name}</td><td>{g.description || '—'}</td><td><span className={`risk ${g.is_privileged ? 'risk-high' : 'risk-low'}`}>{g.is_privileged ? 'Privileged' : 'Standard'}</span></td><td><StatusBadge status={g.status}/></td><td>{g.last_synced_at ? new Date(g.last_synced_at).toLocaleString() : 'Never'}</td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
function RolesPage() {
  const { data: roles, error, loading } = useApiResource<ApiRole[]>('/api/v1/roles');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const privilegedFilter = searchParams.get('privileged') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setPrivilegedFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('privileged', value); else next.delete('privileged'); return next; });
  const filteredRoles = (roles || []).filter(r => (!privilegedFilter || String(r.is_privileged) === privilegedFilter) && (!search || r.name.toLowerCase().includes(search.toLowerCase())));
  return <Page eyebrow="ADMINISTRATION" title="Directory roles" subtitle="Privileged and standard roles available through AccessPilot."><TablePanel toolbar={<Toolbar placeholder="Search roles" searchValue={search} onSearchChange={setSearch} filterLabel="All roles" filterValue={privilegedFilter} onFilterChange={setPrivilegedFilter} filterOptions={[{value:'true',label:'Privileged'},{value:'false',label:'Standard'}]}/>}>{loading ? <div className="empty">Loading roles...</div> : error ? <div className="empty">{error}</div> : !roles || roles.length === 0 ? <div className="empty">No roles found.</div> : filteredRoles.length === 0 ? <div className="empty">No roles match this filter.</div> : <table><thead><tr><th>Role</th><th>Description</th><th>Provider</th><th>Privileged</th><th>Status</th></tr></thead><tbody>{filteredRoles.map(r => <tr key={r.id}><td className="user-name">{r.name}</td><td>{r.description || '—'}</td><td>Microsoft Entra ID</td><td><span className={`risk ${r.is_privileged ? 'risk-high' : 'risk-low'}`}>{r.is_privileged ? 'Yes' : 'No'}</span></td><td><StatusBadge status={r.status}/></td></tr>)}</tbody></table>}</TablePanel></Page>;
}
interface ApiBirthrightPolicy { id: string; name: string; match_field: string; match_value: string; resource_type: string; resource_id: string; app_role_external_id: string | null; assignment_type: string; status: string; created_at: string; }
function BirthrightPoliciesPanel() {
  const auth = useAuth();
  const { data: birthrightPolicies, error: birthrightError, loading: birthrightLoading, reload: reloadBirthright } = useApiResource<ApiBirthrightPolicy[]>('/api/v1/policies/birthright');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const emptyForm = { name: '', match_field: 'department', match_value: '', resource_type: 'GROUP', resource_id: '', assignment_type: 'PERMANENT' };
  const [form, setForm] = useState(emptyForm);
  const targets: Array<ApiGroup | ApiRole | ApiApplication> = form.resource_type === 'GROUP' ? (groups || []) : form.resource_type === 'ROLE' ? (roles || []) : (applications || []);
  const resourceLabel = (p: ApiBirthrightPolicy) => (p.resource_type === 'GROUP' ? groups : p.resource_type === 'ROLE' ? roles : applications)?.find(t => t.id === p.resource_id)?.name || p.resource_id;

  const create = async () => {
    if (!form.name.trim() || !form.match_value.trim() || !form.resource_id) { setMessage('Complete every field.'); return; }
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/policies/birthright', { method: 'POST', body: JSON.stringify(form) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setOpen(false); setForm(emptyForm); reloadBirthright(); }
      else setMessage(body?.error?.message || 'Unable to create this policy.');
    } catch { setMessage('Unable to create this policy.'); } finally { setSaving(false); }
  };
  const toggleStatus = async (policy: ApiBirthrightPolicy) => {
    await auth.apiRequest(`/api/v1/policies/birthright/${policy.id}`, { method: 'PATCH', body: JSON.stringify({ status: policy.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' }) });
    reloadBirthright();
  };
  const remove = async (policy: ApiBirthrightPolicy) => {
    if (!window.confirm(`Delete birthright policy "${policy.name}"? This does not remove access already granted.`)) return;
    await auth.apiRequest(`/api/v1/policies/birthright/${policy.id}`, { method: 'DELETE' });
    reloadBirthright();
  };

  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Birthright policies</h2><button className="btn btn-primary" onClick={() => { setOpen(true); setMessage(''); }}><Plus size={14}/> Add rule</button></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginBottom:14}}>Attribute-driven auto-assignment: when a joiner or mover's <code>department</code> or <code>job title</code> matches a rule, they're automatically made <strong>eligible</strong> for that Group, Role, or Application — same as any other assignment, still activated by hand. Evaluated automatically whenever an Onboarding CSV import is committed.</p>
      {open && <div className="notice" style={{marginBottom:14}}>
        <div className="key-grid" style={{marginBottom:10}}>
          <label className="key"><span>Rule name</span><input className="select" value={form.name} onChange={event => setForm({...form, name: event.target.value})} placeholder="e.g. Finance department access"/></label>
          <label className="key"><span>Match on</span><select className="select" value={form.match_field} onChange={event => setForm({...form, match_field: event.target.value})}><option value="department">Department</option><option value="job_title">Job title</option></select></label>
          <label className="key"><span>Equals</span><input className="select" value={form.match_value} onChange={event => setForm({...form, match_value: event.target.value})} placeholder="e.g. Finance"/></label>
          <label className="key"><span>Grant</span><select className="select" value={form.resource_type} onChange={event => setForm({...form, resource_type: event.target.value, resource_id: ''})}><option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option></select></label>
          <label className="key"><span>Target</span><select className="select" value={form.resource_id} onChange={event => setForm({...form, resource_id: event.target.value})}><option value="">Select a target</option>{targets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
          <label className="key"><span>Assignment type</span><select className="select" value={form.assignment_type} onChange={event => setForm({...form, assignment_type: event.target.value})}><option value="PERMANENT">Permanent</option><option value="TEMPORARY">Temporary</option></select></label>
        </div>
        <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={saving} onClick={() => void create()}>{saving ? 'Saving...' : 'Create rule'}</button><button className="btn" onClick={() => { setOpen(false); setForm(emptyForm); }}>Cancel</button></div>
      </div>}
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      <div className="table-wrap">{birthrightLoading ? <div className="empty">Loading...</div> : birthrightError ? <div className="empty">{birthrightError}</div> : !birthrightPolicies || birthrightPolicies.length === 0 ? <div className="empty">No birthright policies yet.</div> : <table><thead><tr><th>Rule</th><th>Condition</th><th>Grants</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{birthrightPolicies.map(p => <tr key={p.id}><td className="user-name">{p.name}</td><td>{p.match_field} = {p.match_value}</td><td>{p.resource_type.toLowerCase()}: {resourceLabel(p)}</td><td>{p.assignment_type}</td><td><StatusBadge status={p.status}/></td><td style={{display:'flex',gap:6}}><button className="btn" onClick={() => void toggleStatus(p)}>{p.status === 'ACTIVE' ? 'Disable' : 'Enable'}</button><button className="btn" onClick={() => void remove(p)}>Delete</button></td></tr>)}</tbody></table>}</div>
    </div>
  </section>;
}
function PoliciesPage() {
  const auth = useAuth();
  const { data: providers, reload: reloadProviders } = useApiResource<ApiProvider[]>('/api/v1/providers');
  const provider = providers?.find(p => p.provider_type === 'ENTRA') || providers?.[0] || null;
  const [activationHoursValue, setActivationHoursValue] = useState('');
  const [activationSaving, setActivationSaving] = useState(false);
  const [activationMessage, setActivationMessage] = useState('');
  const saveActivationCap = async (hours: number) => {
    if (!provider) return;
    setActivationSaving(true); setActivationMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}`, { method: 'PATCH', body: JSON.stringify({ max_self_activation_hours: hours }) });
      if (response.ok) { setActivationMessage(`End users may now self-activate eligible access for up to ${hours} hours.`); setActivationHoursValue(''); reloadProviders(); }
      else { const body = await response.json().catch(() => null); setActivationMessage(body?.error?.message || 'Unable to update the self-activation limit.'); }
    } catch { setActivationMessage('Unable to update the self-activation limit.'); } finally { setActivationSaving(false); }
  };
  return <Page eyebrow="GOVERNANCE" title="Policies" subtitle="Rules that govern access duration, approvals, and assurance." action={<button className="btn btn-primary"><Plus size={14}/> Create policy</button>}>
    {provider && <section className="panel" style={{marginBottom:18}}><div className="panel-head"><h2>Self-activation (PIM)</h2><span className="badge neutral">Up to {provider.max_self_activation_hours} hours</span></div><div className="detail-section"><p className="subtitle" style={{marginBottom:14}}>The single, universal maximum duration any end user may self-activate their own eligible access for — Group, Role, Application role, or Access Package alike — from their My Access dashboard, mirroring Entra PIM's activation cap. Raising this takes effect immediately for every eligible assignment across the whole tenant.</p><div style={{display:'flex',gap:8,alignItems:'flex-end',flexWrap:'wrap'}}><label className="key"><span>Maximum self-activation duration (hours)</span><input className="select" type="number" min={1} max={8760} placeholder={String(provider.max_self_activation_hours)} value={activationHoursValue} onChange={event => setActivationHoursValue(event.target.value)}/></label><button className="btn btn-primary" disabled={activationSaving || !activationHoursValue} onClick={() => void saveActivationCap(Number(activationHoursValue))}><Clock3 size={14}/> {activationSaving ? 'Saving...' : 'Save limit'}</button></div>{activationMessage && <div className="notice" style={{marginTop:12}}>{activationMessage}</div>}</div></section>}
    <BirthrightPoliciesPanel/>
    <TablePanel toolbar={<Toolbar placeholder="Search policies"/>}><table><thead><tr><th>Policy name</th><th>Description</th><th>Scope</th><th>Max duration</th><th>Approval</th><th>MFA</th><th>Ticket</th><th>Status</th><th></th></tr></thead><tbody>{policies.map(p => <tr key={p.name}><td className="user-name">{p.name}</td><td>{p.description}</td><td>{p.scope}</td><td>{p.max}</td><td>{p.approval}</td><td>{p.mfa}</td><td>{p.ticket}</td><td><StatusBadge status={p.status}/></td><td><button className="btn">Edit</button></td></tr>)}</tbody></table></TablePanel>
  </Page>;
}
interface ApiAuditLog { id: string; timestamp: string; actor_user_id: string | null; actor_display_name: string | null; action: string; target_type: string; target_id: string | null; provider_id: string | null; provider_name: string | null; request_id: string; result: string; target_user_display_name: string | null; target_user_email: string | null; }
function AuditPage() {
  const { data: logs, error, loading, reload } = useApiResource<ApiAuditLog[]>('/api/v1/audit-logs');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const resultFilter = searchParams.get('result') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setResultFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('result', value); else next.delete('result'); return next; });
  const resultOptions = useMemo(() => Array.from(new Set((logs || []).map(l => l.result))).sort().map(r => ({ value: r, label: r })), [logs]);
  const filteredLogs = (logs || []).filter(l => (!resultFilter || l.result === resultFilter) && (!search || `${l.action} ${l.actor_display_name || ''} ${l.target_type} ${l.target_user_display_name || ''}`.toLowerCase().includes(search.toLowerCase())));
  return <Page eyebrow="GOVERNANCE" title="Audit logs" subtitle="A tamper-evident record of identity and access activity." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}><TablePanel toolbar={<Toolbar placeholder="Search audit events" searchValue={search} onSearchChange={setSearch} filterLabel="All results" filterValue={resultFilter} onFilterChange={setResultFilter} filterOptions={resultOptions}/>}>{loading ? <div className="empty">Loading audit logs...</div> : error ? <div className="empty">{error}</div> : !logs || logs.length === 0 ? <div className="empty">No audit events found.</div> : filteredLogs.length === 0 ? <div className="empty">No audit events match this filter.</div> : <table><thead><tr><th>Timestamp</th><th>Actor</th><th>Action</th><th>Target</th><th>User</th><th>Provider</th><th>Result</th><th>Request ID</th></tr></thead><tbody>{filteredLogs.map(entry => <tr key={entry.id}><td>{new Date(entry.timestamp).toLocaleString()}</td><td className="user-name">{entry.actor_display_name || 'System'}</td><td>{entry.action}</td><td>{entry.target_type}</td><td>{entry.target_user_display_name ? `${entry.target_user_display_name}${entry.target_user_email ? ` (${entry.target_user_email})` : ''}` : '—'}</td><td>{entry.provider_name || '—'}</td><td><StatusBadge status={entry.result}/></td><td>{entry.request_id}</td></tr>)}</tbody></table>}</TablePanel></Page>;
}
function ProvidersPage() { return <ProviderConfiguration />; }
interface ApiProvider { id: string; name: string; provider_type: string; status: string; sync_interval_minutes: number | null; last_sync_at: string | null; max_self_activation_hours: number; }
interface ApiSyncRun { id: string; status: string; started_at: string; completed_at: string | null; users_processed: number; groups_processed: number; roles_processed: number; errors_count: number; }
function SyncPage() {
  const auth = useAuth();
  const { data: providers, loading: providersLoading, reload: reloadProviders } = useApiResource<ApiProvider[]>('/api/v1/providers');
  const provider = providers?.find(p => p.provider_type === 'ENTRA') || providers?.[0] || null;
  const { data: runs, error, loading, reload } = useApiResource<ApiSyncRun[]>(provider ? `/api/v1/providers/${provider.id}/sync-runs` : '', Boolean(provider));
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get('status') || '';
  const setStatusFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('status', value); else next.delete('status'); return next; });
  const statusOptions = useMemo(() => Array.from(new Set((runs || []).map(r => r.status))).sort().map(s => ({ value: s, label: s })), [runs]);
  const filteredRuns = (runs || []).filter(r => !statusFilter || r.status === statusFilter);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState('');
  const [intervalValue, setIntervalValue] = useState('');
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [scheduleMessage, setScheduleMessage] = useState('');
  const runSync = async () => {
    if (!provider) return;
    setSyncing(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}/sync`, { method: 'POST' });
      if (response.ok) { setMessage('Sync completed.'); reload(); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Sync failed.'); reload(); }
    } catch { setMessage('Sync failed. Please try again.'); } finally { setSyncing(false); }
  };
  const saveSchedule = async (minutes: number | null) => {
    if (!provider) return;
    setScheduleSaving(true); setScheduleMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/providers/${provider.id}`, { method: 'PATCH', body: JSON.stringify({ sync_interval_minutes: minutes }) });
      if (response.ok) { setScheduleMessage(minutes ? `Sync scheduled every ${minutes} minutes.` : 'Scheduled sync disabled.'); setIntervalValue(''); reloadProviders(); }
      else { const body = await response.json().catch(() => null); setScheduleMessage(body?.error?.message || 'Unable to update the sync schedule.'); }
    } catch { setScheduleMessage('Unable to update the sync schedule.'); } finally { setScheduleSaving(false); }
  };
  return <Page eyebrow="SYSTEM" title="Sync history" subtitle="Provider synchronization runs and reconciliation results." action={<button className="btn btn-primary" disabled={!provider || syncing} onClick={() => void runSync()}><RefreshCw size={14}/> {syncing ? 'Syncing...' : 'Sync now'}</button>}>
    {message && <div className="detail-section" style={{marginBottom:14}}><div className="notice">{message}</div></div>}
    {provider && <section className="panel" style={{marginBottom:18}}><div className="panel-head"><h2>Scheduled sync</h2><span className="badge neutral">{provider.sync_interval_minutes ? `Every ${provider.sync_interval_minutes} min` : 'Not scheduled'}</span></div><div className="detail-section"><div className="key-grid" style={{marginBottom:14}}><div className="key"><span>Current schedule</span><strong>{provider.sync_interval_minutes ? `Every ${provider.sync_interval_minutes} minutes` : 'Manual only'}</strong></div><div className="key"><span>Last sync</span><strong>{provider.last_sync_at ? new Date(provider.last_sync_at).toLocaleString() : 'Never'}</strong></div></div><div style={{display:'flex',gap:8,alignItems:'flex-end',flexWrap:'wrap'}}><label className="key"><span>Run every (minutes)</span><input className="select" type="number" min={1} max={10080} placeholder="e.g. 60" value={intervalValue} onChange={event => setIntervalValue(event.target.value)}/></label><button className="btn btn-primary" disabled={scheduleSaving || !intervalValue} onClick={() => void saveSchedule(Number(intervalValue))}><Clock3 size={14}/> {scheduleSaving ? 'Saving...' : 'Schedule sync'}</button>{provider.sync_interval_minutes && <button className="btn" disabled={scheduleSaving} onClick={() => void saveSchedule(null)}>Disable schedule</button>}</div>{scheduleMessage && <div className="notice" style={{marginTop:12}}>{scheduleMessage}</div>}</div></section>}
    <div className="notice" style={{marginBottom:18}}>Looking for the self-activation time limit (PIM)? That's now under <strong>Policies</strong> in the Governance section.</div>
    <TablePanel toolbar={<Toolbar placeholder="" filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={statusOptions}/>}>{providersLoading || loading ? <div className="empty">Loading sync history...</div> : !provider ? <div className="empty">No identity provider is configured.</div> : error ? <div className="empty">{error}</div> : !runs || runs.length === 0 ? <div className="empty">No sync runs yet.</div> : filteredRuns.length === 0 ? <div className="empty">No sync runs match this filter.</div> : <table><thead><tr><th>Started</th><th>Completed</th><th>Users</th><th>Groups</th><th>Roles</th><th>Errors</th><th>Status</th></tr></thead><tbody>{filteredRuns.map(run => <tr key={run.id}><td className="user-name">{new Date(run.started_at).toLocaleString()}</td><td>{run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}</td><td>{run.users_processed}</td><td>{run.groups_processed}</td><td>{run.roles_processed}</td><td>{run.errors_count}</td><td><StatusBadge status={run.status}/></td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
function actionBadgeClass(action: string): string { return action === 'CREATE' || action === 'UPDATE' ? 'success' : action === 'DISABLE' ? 'warning' : action === 'ERROR' ? 'danger' : 'neutral'; }
function OnboardingPage() {
  const auth = useAuth();
  const { data: imports, loading: importsLoading, error: importsError, reload: reloadImports } = useApiResource<ApiOnboardingImport[]>('/api/v1/onboarding/imports');
  const [fileName, setFileName] = useState('');
  const [csvContent, setCsvContent] = useState('');
  const [uploading, setUploading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [message, setMessage] = useState('');
  const [currentImport, setCurrentImport] = useState<ApiOnboardingImport | null>(null);
  const [previewRows, setPreviewRows] = useState<ApiOnboardingImportRecord[] | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const onFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name); setMessage(''); setCurrentImport(null); setPreviewRows(null);
    const reader = new FileReader();
    reader.onload = () => setCsvContent(String(reader.result || ''));
    reader.readAsText(file);
  };

  const loadPreview = async (importId: string) => {
    setPreviewLoading(true);
    try { const response = await auth.apiRequest(`/api/v1/onboarding/imports/${importId}/preview`); if (response.ok) setPreviewRows(await response.json()); }
    finally { setPreviewLoading(false); }
  };

  const upload = async () => {
    if (!csvContent || !fileName) return;
    setUploading(true); setMessage(''); setCurrentImport(null); setPreviewRows(null);
    try {
      const response = await auth.apiRequest('/api/v1/onboarding/csv', { method: 'POST', body: JSON.stringify({ filename: fileName, content: csvContent }) });
      const body = await response.json().catch(() => null);
      if (response.ok && body) {
        setCurrentImport(body);
        reloadImports();
        if (body.status === 'VALIDATED') void loadPreview(body.id);
        else setMessage(body.error_summary?.error ? String(body.error_summary.error) : 'Validation failed — see details below.');
      } else setMessage(body?.error?.message || 'Unable to upload the CSV file.');
    } catch { setMessage('Unable to upload the CSV file.'); } finally { setUploading(false); }
  };

  const commit = async () => {
    if (!currentImport) return;
    setCommitting(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/onboarding/imports/${currentImport.id}/commit`, { method: 'POST' });
      const body = await response.json().catch(() => null);
      if (response.ok && body) { setCurrentImport(body); setMessage('Import committed.'); reloadImports(); }
      else setMessage(body?.error?.message || 'Unable to commit this import.');
    } catch { setMessage('Unable to commit this import.'); } finally { setCommitting(false); }
  };

  const reset = () => { setFileName(''); setCsvContent(''); setCurrentImport(null); setPreviewRows(null); setMessage(''); };

  return <Page eyebrow="SYSTEM" title="Onboarding" subtitle="Bring identities into AccessPilot from an HR export or a one-off CSV — separate from, and ahead of, any real Entra provisioning.">
    <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>Upload a CSV</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{marginBottom:14}}>Required columns: <code>employeeId, firstName, lastName, email, department, status</code> (status is <code>ACTIVE</code> or <code>TERMINATED</code>). Optional: <code>jobTitle</code>. On commit, a new/changed row also provisions a <strong>real account</strong> via your configured connector (Microsoft Graph, or the mock connector in dev) and immediately grants any matching <strong>birthright policy</strong> access for real — the <code>email</code> column's domain must be a verified domain on your Entra tenant for the real account to succeed; if it can't be provisioned, the identity still lands locally as before. A <code>TERMINATED</code> row disables the identity and automatically revokes any access it still holds.</p>
        <div style={{display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}>
          <label className="btn"><UploadCloud size={14}/> {fileName || 'Choose CSV file'}<input type="file" accept=".csv,text/csv" style={{display:'none'}} onChange={onFileChange}/></label>
          <button className="btn btn-primary" disabled={!csvContent || uploading} onClick={() => void upload()}>{uploading ? 'Validating...' : 'Upload & validate'}</button>
          {(fileName || currentImport) && <button className="btn" onClick={reset}>Start over</button>}
        </div>
        {message && <div className="notice" style={{marginTop:14}}>{message}</div>}
      </div>
    </section>

    {currentImport && <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>{currentImport.filename}</h2><StatusBadge status={currentImport.status}/></div>
      <div className="detail-section">
        <div className="key-grid" style={{marginBottom:14}}>
          <div className="key"><span>Total rows</span><strong>{currentImport.total_records}</strong></div>
          <div className="key"><span>Create</span><strong>{currentImport.created_count}</strong></div>
          <div className="key"><span>Update</span><strong>{currentImport.updated_count}</strong></div>
          <div className="key"><span>No change</span><strong>{currentImport.no_change_count}</strong></div>
          <div className="key"><span>Disable (leavers)</span><strong>{currentImport.disabled_count}</strong></div>
          <div className="key"><span>Row errors</span><strong>{currentImport.failed_count}</strong></div>
          {currentImport.status === 'COMMITTED' && <><div className="key"><span>Real accounts provisioned</span><strong>{currentImport.real_accounts_provisioned_count}</strong></div><div className="key"><span>Birthright grants</span><strong>{currentImport.birthright_assignments_created_count}</strong></div><div className="key"><span>Access revoked</span><strong>{currentImport.access_revoked_count}</strong></div><div className="key"><span>Revoke failures</span><strong>{currentImport.access_revoke_failed_count}</strong></div></>}
        </div>
        {currentImport.error_summary && <div className="notice" style={{marginBottom:14}}>{String(currentImport.error_summary.error || 'Validation failed.')}{Array.isArray(currentImport.error_summary.missingColumns) && <> Missing: {(currentImport.error_summary.missingColumns as string[]).join(', ')}</>}</div>}
        {currentImport.status === 'VALIDATED' && <button className="btn btn-primary" disabled={committing} onClick={() => void commit()}>{committing ? 'Committing...' : 'Commit import'}</button>}
        {currentImport.status === 'COMMITTED' && <div className="notice">Committed — identities now appear under Users. {currentImport.real_accounts_provisioned_count > 0 && <>{currentImport.real_accounts_provisioned_count} real account{currentImport.real_accounts_provisioned_count === 1 ? '' : 's'} provisioned{currentImport.birthright_assignments_created_count > 0 ? ` with ${currentImport.birthright_assignments_created_count} birthright grant${currentImport.birthright_assignments_created_count === 1 ? '' : 's'} applied immediately. ` : '. '}</>}Any TERMINATED leavers had their access revoked automatically.</div>}
      </div>
      {previewLoading && <div className="empty">Loading preview...</div>}
      {previewRows && <div className="table-wrap"><table><thead><tr><th>Row</th><th>Employee ID</th><th>Action</th><th>Detail</th></tr></thead><tbody>{previewRows.map(row => <tr key={row.row_number}><td>{row.row_number}</td><td className="user-name">{row.employee_id || '—'}</td><td><span className={`badge ${actionBadgeClass(row.action)}`}>{row.action}</span></td><td>{row.error_message || (row.raw_data ? `${row.raw_data.firstName || ''} ${row.raw_data.lastName || ''} · ${row.raw_data.department || ''}` : '—')}</td></tr>)}</tbody></table></div>}
    </section>}

    <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>Past imports</h2></div>
      <div className="table-wrap">{importsLoading ? <div className="empty">Loading...</div> : importsError ? <div className="empty">{importsError}</div> : !imports || imports.length === 0 ? <div className="empty">No imports yet.</div> : <table><thead><tr><th>Filename</th><th>Status</th><th>Rows</th><th>Created</th><th>Updated</th><th>Disabled</th><th>Errors</th><th>Uploaded</th></tr></thead><tbody>{imports.map(imp => <tr key={imp.id}><td className="user-name">{imp.filename}</td><td><StatusBadge status={imp.status}/></td><td>{imp.total_records}</td><td>{imp.created_count}</td><td>{imp.updated_count}</td><td>{imp.disabled_count}</td><td>{imp.failed_count}</td><td>{new Date(imp.created_at).toLocaleString()}</td></tr>)}</tbody></table>}</div>
    </section>

    <section className="panel">
      <div className="panel-head"><h2>API reference — for a future HR system integration</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{marginBottom:12}}>A future HR system can call these endpoints directly instead of a manual upload — same validation, same leaver-revocation behavior. Full interactive schema (request/response bodies, try-it-out): <a href={`${apiBaseUrl}/docs`} target="_blank" rel="noreferrer" style={{color:'var(--teal)',fontWeight:700}}>{apiBaseUrl}/docs <ExternalLink size={12} style={{verticalAlign:'middle'}}/></a></p>
        <table style={{width:'100%'}}><tbody>
          <tr><td style={{fontWeight:700,paddingRight:16,paddingBottom:8}}><code>POST /api/v1/onboarding/csv</code></td><td style={{paddingBottom:8}}>Upload &amp; validate a CSV — JSON body <code>{'{ filename, content }'}</code></td></tr>
          <tr><td style={{fontWeight:700,paddingRight:16,paddingBottom:8}}><code>GET /api/v1/onboarding/imports/{'{id}'}</code></td><td style={{paddingBottom:8}}>Check an import's status and counts</td></tr>
          <tr><td style={{fontWeight:700,paddingRight:16,paddingBottom:8}}><code>GET /api/v1/onboarding/imports/{'{id}'}/preview</code></td><td style={{paddingBottom:8}}>Row-by-row planned action before committing</td></tr>
          <tr><td style={{fontWeight:700,paddingRight:16}}><code>POST /api/v1/onboarding/imports/{'{id}'}/commit</code></td><td>Apply the import — creates/updates/disables identities, revokes leavers' access</td></tr>
        </tbody></table>
        <p className="subtitle" style={{marginTop:12}}>Requires an Admin bearer token from the same Entra app registration as the rest of AccessPilot's API.</p>
      </div>
    </section>
  </Page>;
}
function Profile() {
  const auth = useAuth();
  const signedIn = Boolean(auth.account) || auth.breakglassActive;
  const { data: me, reload: reloadMe } = useApiResource<ApiCurrentUser>('/api/v1/me', signedIn);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState('');
  const handleRefresh = async () => {
    setRefreshing(true); setRefreshMessage('');
    try { await auth.refreshAccess(); reloadMe(); setRefreshMessage('Refreshed — your roles and permissions are now up to date.'); }
    catch { setRefreshMessage('Unable to refresh right now.'); }
    finally { setRefreshing(false); }
  };
  const displayName = auth.account?.name || me?.displayName || (auth.breakglassActive ? `Break-Glass (${auth.breakglassUsername})` : currentUser.name);
  const email = auth.account?.username || me?.email || (auth.authConfigured ? '' : currentUser.email);
  const department = me?.department || (!auth.authConfigured ? currentUser.department : null);
  const jobTitle = me?.jobTitle || (!auth.authConfigured ? currentUser.title : null);
  const roleLabel = auth.role === 'admin' ? 'AccessPilot.Admin' : 'AccessPilot.User';
  const providerLabel = auth.breakglassActive ? 'Break-Glass (emergency access)' : auth.authConfigured ? 'Microsoft Entra ID' : 'Local (mock/dev mode)';
  const sessionStarted = signedIn && auth.sessionStartedAt ? new Date(auth.sessionStartedAt).toLocaleString() : '—';
  const initials = initialsFor(displayName || 'AccessPilot User');
  return <Page eyebrow="SELF-SERVICE" title="Profile" subtitle="Your AccessPilot identity and application role.">
    <div className="detail-layout">
      <section className="panel">
        <div className="detail-section"><div className="user-cell"><span className="avatar" style={{width:52,height:52}}>{initials}</span><div><h2>{displayName}</h2><p className="subtitle">{jobTitle || email}</p></div></div></div>
        <div className="detail-section">
          <div className="detail-title"><h2>Identity details</h2><StatusBadge status={signedIn ? 'Active' : 'NOT_CONFIGURED'}/></div>
          <div className="key-grid">
            <div className="key"><span>Email</span><strong>{email || '—'}</strong></div>
            <div className="key"><span>Department</span><strong>{department || 'Not available'}</strong></div>
            <div className="key"><span>Identity provider</span><strong>{providerLabel}</strong></div>
            <div className="key"><span>Tenant</span><strong>{me?.tenantId || auth.account?.tenantId || '—'}</strong></div>
            <div className="key"><span>Session started</span><strong>{sessionStarted}</strong></div>
            {me?.employeeId && <div className="key"><span>Employee ID</span><strong>{me.employeeId}</strong></div>}
          </div>
        </div>
      </section>
      <aside className="panel">
        <div className="panel-head"><h2>Application role</h2></div>
        <div className="detail-section">
          <div className="user-cell"><span className="stat-icon"><ShieldCheck size={16}/></span><div><strong>{roleLabel}</strong><div className="user-email">{me?.roles?.join(', ') || roleLabel}</div></div></div>
          <p className="subtitle" style={{lineHeight:1.6,marginTop:18}}>Your role determines which console areas are visible. Authorization is enforced by the backend on every request.</p>
          {signedIn && <>
            <button className="btn" disabled={refreshing} onClick={handleRefresh} style={{marginTop:16}}>{refreshing ? 'Refreshing...' : 'Refresh my access'}</button>
            <p className="subtitle" style={{marginTop:8}}>If an admin just changed your roles (e.g. granted Separation-of-Duties admin access), use this instead of signing out — it forces a fresh check without waiting for your session token to expire on its own.</p>
            {refreshMessage && <div className="notice" style={{marginTop:10}}>{refreshMessage}</div>}
          </>}
        </div>
      </aside>
    </div>
  </Page>;
}
export default App;
