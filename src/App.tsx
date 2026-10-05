import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Activity, AlertTriangle, ArrowRight, BarChart3, Bell, BookOpen, Bot, Box, Check, ChevronLeft, ChevronRight, Clock3, Cloud, Copy, Database, ExternalLink, FileCheck2, FolderKanban, Gauge, GitBranch, IdCard, Image, KeyRound, LayoutDashboard, LifeBuoy, ListChecks, Lock, Menu, Network, Plus, RefreshCw, Search, Settings2, Shield, ShieldAlert, ShieldCheck, SlidersHorizontal, UploadCloud, UserRound, Users, UserX, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { currentUser, policies, type RequestStatus, type Role } from './mock';
import { mockService, useMockState } from './mockService';
import { apiBaseUrl, useAuth } from './auth';
import ProviderConfiguration from './ProviderConfiguration';
import { BreakGlassDashboard } from './BreakGlassDashboard';
import { IdleGuard, useRefreshSecuritySettings, useAppTimezone } from './IdleGuard';
import logo from './assets/logo.png';

interface ApiUser { id: string; provider_id: string; external_id: string; email: string; display_name: string; given_name: string | null; surname: string | null; department: string | null; job_title: string | null; status: string; employee_id: string | null; source: string | null; account_type: string; linked_user_id: string | null; employee_category: string | null; manager_id: string | null; start_date: string | null; leaver_date: string | null; employment_type: string | null; last_synced_at: string | null; }
interface ApiHierarchyNode { id: string; display_name: string; email: string; status: string; employee_category: string | null; manager_id: string | null; }
interface ApiLinkedAccount { id: string; display_name: string; email: string; account_type: string; status: string; }
interface ApiPrivilegedAccountPolicy { account_type: string; default_approver_id: string | null; default_approver_display_name: string | null; approval_required: boolean; }
interface ApiPrivilegedAccountRequest { id: string; requester_id: string; requester_display_name: string | null; account_type: string; status: string; approver_id: string | null; approver_display_name: string | null; justification: string | null; provisioned_user_id: string | null; provisioned_user_display_name: string | null; failure_reason: string | null; created_at: string; decided_at: string | null; }
interface ApiGroup { id: string; external_id: string; name: string; description: string | null; is_privileged: boolean; status: string; last_synced_at: string | null; }
interface ApiRole { id: string; external_id: string; name: string; description: string | null; role_type: string; is_privileged: boolean; status: string; }
interface ApiApplicationRole { id: string; name: string; description: string | null; }
interface ApiApplication { id: string; external_id: string; name: string; status: string; app_roles: ApiApplicationRole[] | null; last_synced_at: string | null; }
interface ApiPackageItem { id: string; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; }
interface ApiPackageEligiblePrincipal { principal_type: string; principal_id: string; display_name: string | null; }
interface ApiPackage { id: string; name: string; description: string | null; status: string; items: ApiPackageItem[]; default_approver_id: string | null; default_fallback_approver_id: string | null; eligible_principals: ApiPackageEligiblePrincipal[]; owners: { user_id: string; display_name: string | null; email: string | null }[]; created_at: string; }
interface ApiUserAccessItem { id: string | null; resource_type: string; resource_display_name: string | null; status: string; assignment_type: string; expiration_time: string | null; package_name: string | null; source: string; }
interface ApiUserLicense { sku_id: string; name: string; }
interface ApiUserAccessSummary { assignments: ApiUserAccessItem[]; licenses: ApiUserLicense[]; }
interface ApiPackageBatch { package_assignment_id: string; package_id: string; package_name: string; user_id: string; assignment_ids: string[]; }
interface ApiRoleBatch { role_assignment_id: string; role_id: string; role_name: string; user_id: string; assignment_ids: string[]; }
interface DashboardAdmin { users: number; groups: number; roles: number; privilegedRoles: number; activeSessions: number; pendingRequests: number; expiringAccess: number; provider: { id: string; name: string; status: string; lastSyncAt: string | null } | null; lastSync: { id: string; status: string; startedAt: string; completedAt: string | null; usersProcessed: number; groupsProcessed: number; rolesProcessed: number; errorsCount: number } | null; }
interface ApiActivationTimeline { days: number; series: { date: string; count: number }[]; }
interface ApiUserAccessSegments { permanentActive: number; eligible: number; }
interface ApiSegmentMember { id: string; display_name: string; email: string; }
interface ApiOnboardingImport { id: string; filename: string; status: string; total_records: number; created_count: number; updated_count: number; disabled_count: number; no_change_count: number; failed_count: number; access_revoked_count: number; access_revoke_failed_count: number; real_accounts_provisioned_count: number; birthright_assignments_created_count: number; birthright_assignments_revoked_count: number; moves_scheduled_count: number; error_summary: Record<string, unknown> | null; created_at: string; completed_at: string | null; }
interface ApiOnboardingImportRecord { row_number: number; employee_id: string; action: string; error_message: string | null; raw_data: Record<string, string> | null; }
interface ApiSecuritySettings { blur_enabled: boolean; blur_after_minutes: number; lock_enabled: boolean; lock_after_minutes: number; logout_enabled: boolean; logout_after_minutes: number; timezone: string; support_contact_email: string | null; }
// A short, curated list rather than every IANA zone (~400) — covers the timezones this deployment's users are
// actually likely to be in; "Other (type it below)" falls through to a free-text input validated server-side by
// the exact same zoneinfo check, so nothing is actually unreachable, just not pre-listed.
const COMMON_TIMEZONES = ['UTC', 'Europe/London', 'Europe/Berlin', 'Europe/Paris', 'Europe/Madrid', 'Europe/Rome', 'Europe/Warsaw', 'Europe/Moscow', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Sao_Paulo', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Shanghai', 'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney', 'Pacific/Auckland'];
interface ApiCurrentUser { id: string; displayName: string; email: string | null; tenantId: string; roles: string[]; department: string | null; jobTitle: string | null; employeeId: string | null; }
interface ApiSodEntity { id: string; conflict_side: string; entity_type: string; entity_id: string; entity_display_name: string | null; app_role_external_id: string | null; entity_resolved: boolean; }
interface ApiSodPolicy { id: string; name: string; description: string | null; severity: string; status: string; entities: ApiSodEntity[]; created_at: string; updated_at: string; }
interface ApiSodViolationHolding { assignment_id: string | null; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; source: string; }
interface ApiSodViolation { policy_id: string; policy_name: string; severity: string; user_id: string; user_display_name: string | null; side_a_holdings: ApiSodViolationHolding[]; side_b_holdings: ApiSodViolationHolding[]; exception_active: boolean; exception_expires_at: string | null; }
interface ApiSodException { id: string; sod_policy_id: string; policy_name: string | null; user_id: string; user_display_name: string | null; user_email: string | null; justification: string; granted_by: string | null; granted_by_display_name: string | null; expires_at: string; revoked_at: string | null; is_active: boolean; created_at: string; }
interface ApiSodNotificationSettings { notify_on_new_violation: boolean; notify_on_exception_expiring: boolean; exception_expiring_warning_days: number; notify_on_exception_requested: boolean; cooldown_enabled: boolean; cooldown_hours: number; }
interface ApiSodNotification { id: string; notification_type: string; sod_policy_id: string | null; policy_name: string | null; user_id: string | null; user_display_name: string | null; message: string; read_at: string | null; resolved_at: string | null; created_at: string; }
interface ApiNotification { id: string; notification_type: string; message: string; link: string | null; read_at: string | null; created_at: string; }
interface ApiSodExceptionRequest { id: string; sod_policy_id: string; policy_name: string | null; user_id: string; user_display_name: string | null; requested_by: string | null; requested_by_display_name: string | null; justification: string; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; approver_id: string | null; approver_display_name: string | null; assignment_type: string; expiration_time: string | null; status: string; decided_by: string | null; decided_by_display_name: string | null; decided_at: string | null; denial_reason: string | null; sod_exception_id: string | null; created_at: string; }
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

function useSupportContactEmail() {
  // Same deliberately public, unauthenticated fetch as useBranding above — the one scenario this exists for is
  // a user who can't sign in at all (the IDP itself is unreachable), so it can never go through apiRequest().
  const [email, setEmail] = useState<string | null>(null);
  useEffect(() => {
    let ignore = false;
    fetch(`${apiBaseUrl}/api/v1/security-settings/support-contact`).then(response => response.ok ? response.json() : null).then(data => { if (!ignore && data) setEmail(data.support_contact_email); }).catch(() => {});
    return () => { ignore = true; };
  }, []);
  return email;
}

const nav = [
  { label: 'Dashboard', icon: LayoutDashboard, to: '/dashboard', roles: ['user','admin'] },
  { label: 'My Access', icon: KeyRound, to: '/my-access', roles: ['user'] },
  { label: 'Request Access', icon: Plus, to: '/request-access', roles: ['user'] },
  { label: 'Request Packages', icon: Box, to: '/request-packages', roles: ['user'] },
  { label: 'My Requests', icon: ListChecks, to: '/my-requests', roles: ['user'] },
  { label: 'Approvals', icon: Check, to: '/approvals', roles: ['user','admin'] },
  { label: 'My Access Reviews', icon: FileCheck2, to: '/my-access-reviews', roles: ['user','admin'] },
  { label: 'My Packages', icon: Box, to: '/my-packages', roles: ['user','admin'] },
  { label: 'My Business Roles', icon: IdCard, to: '/my-business-roles', roles: ['user','admin'] },
  { label: 'Profile', icon: UserRound, to: '/profile', roles: ['user','admin'] },
  { label: 'Users', icon: Users, to: '/admin/users', roles: ['admin'], section: 'ADMINISTRATION' },
  { label: 'Org Chart', icon: GitBranch, to: '/admin/org-chart', roles: ['admin'] },
  { label: 'Groups', icon: Network, to: '/admin/groups', roles: ['admin'] },
  { label: 'Roles', icon: Shield, to: '/admin/roles', roles: ['admin'] },
  { label: 'Access Requests', icon: FolderKanban, to: '/admin/access-requests', roles: ['admin'], section: 'ACCESS MANAGEMENT' },
  { label: 'Assignments', icon: KeyRound, to: '/admin/assignments', roles: ['admin'] },
  { label: 'Access Packages', icon: Box, to: '/admin/access-packages', roles: ['admin'] },
  { label: 'Business Roles', icon: IdCard, to: '/admin/business-roles', roles: ['admin'] },
  { label: 'Policies', icon: SlidersHorizontal, to: '/admin/policies', roles: ['admin'], section: 'GOVERNANCE' },
  { label: 'Privileged/Test Activity', icon: ShieldCheck, to: '/admin/privileged-accounts', roles: ['admin'] },
  { label: 'Audit Logs', icon: BookOpen, to: '/admin/audit', roles: ['admin'] },
  // Deliberately NOT gated behind an extra:-flag like SoD/SoC/NHI — AccessPilot.AccessReviewAdmin's backend
  // permission set is a full Admin superset by design (see app/security/auth.py), so a holder's `role` itself
  // already becomes 'admin' (see auth.tsx) and this plain `roles: ['admin']` entry already covers them too.
  { label: 'Access Reviews', icon: FileCheck2, to: '/admin/access-reviews', roles: ['admin'], section: 'ACCESS REVIEW' },
  { label: 'Joiners', icon: UserRound, to: '/admin/joiners', roles: ['admin'] },
  { label: 'Movers', icon: Activity, to: '/admin/movers', roles: ['admin'] },
  { label: 'Leavers', icon: UserX, to: '/admin/leavers', roles: ['admin'] },
  // Its own sidebar section, not folded into GOVERNANCE — exclusive to a real AccessPilot.SoDAdmin (see Shell's
  // nav filter, which checks auth.isSodAdmin for items marked extra: 'sod'). roles: [] is deliberate: a plain
  // Admin no longer sees this section at all, the same exclusive-to-its-own-role treatment SOC already has.
  { label: 'Separation of Duties', icon: ShieldAlert, to: '/admin/sod', roles: [] as Role[], extra: 'sod', section: 'SEPARATION OF DUTIES' },
  { label: 'SoD Configuration', icon: Settings2, to: '/admin/sod/configuration', roles: [] as Role[], extra: 'sod' },
  // Same pattern as the SoD section above — visible ONLY to a plain end-user holding the real Entra
  // AccessPilot.SoCAdmin app role (auth.isSocAdmin, checked via extra: 'soc' in Shell's nav filter) —
  // deliberately roles: [], never ['admin']: exclusive to whoever actually holds AccessPilot.SoCAdmin, a plain
  // Admin included.
  { label: 'Security Operations', icon: Gauge, to: '/admin/soc', roles: [] as Role[], extra: 'soc', section: 'SECURITY OPERATIONS' },
  // Same exclusive-to-its-own-role pattern again, for AccessPilot.ServerAdmin's infra/ops health dashboard.
  { label: 'System Health', icon: Activity, to: '/admin/server-health', roles: [] as Role[], extra: 'server', section: 'SYSTEM HEALTH' },
  // Same exclusive-to-its-own-role pattern again, for AccessPilot.NHIAdmin (non-human identity ownership/risk).
  { label: 'Non-Human Identities', icon: Bot, to: '/admin/nhi', roles: [] as Role[], extra: 'nhi', section: 'NON-HUMAN IDENTITIES' },
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
  return <IdleGuard><Shell role={role} setRole={changeRole}><Routes><Route path="/" element={<Navigate to="/dashboard" replace />} /><Route path="/dashboard" element={<Dashboard role={role} />} /><Route path="/my-access" element={<MyAccess />} /><Route path="/request-access" element={<RequestAccess />} /><Route path="/request-packages" element={<RequestPackagesPage />} /><Route path="/my-requests" element={<Requests mine />} /><Route path="/approvals" element={<MyApprovalsPage />} /><Route path="/profile" element={<Profile />} /><Route path="/admin/users" element={<AdminOnly role={role}><UsersPage /></AdminOnly>} /><Route path="/admin/users/:id" element={<AdminOnly role={role}><UserDetail /></AdminOnly>} /><Route path="/admin/org-chart" element={<AdminOnly role={role}><OrgChartPage /></AdminOnly>} /><Route path="/admin/groups" element={<AdminOnly role={role}><GroupsPage /></AdminOnly>} /><Route path="/admin/groups/:id" element={<AdminOnly role={role}><GroupDetail /></AdminOnly>} /><Route path="/admin/roles" element={<AdminOnly role={role}><RolesPage /></AdminOnly>} /><Route path="/admin/access-requests" element={<AdminOnly role={role}><Requests /></AdminOnly>} /><Route path="/admin/access-requests/:id" element={<AdminOnly role={role}><RequestDetailInteractive /></AdminOnly>} /><Route path="/admin/assignments" element={<AdminOnly role={role}><AssignmentsInteractive /></AdminOnly>} /><Route path="/admin/access-packages" element={<AdminOnly role={role}><AccessPackagesInteractive /></AdminOnly>} /><Route path="/admin/business-roles" element={<AdminOnly role={role}><BusinessRolesPage /></AdminOnly>} /><Route path="/admin/policies" element={<AdminOnly role={role}><PoliciesPage /></AdminOnly>} /><Route path="/admin/privileged-accounts" element={<AdminOnly role={role}><PrivilegedAccountActivityPage /></AdminOnly>} /><Route path="/admin/joiners" element={<AdminOnly role={role}><JoinersPage /></AdminOnly>} /><Route path="/admin/movers" element={<AdminOnly role={role}><MoversPage /></AdminOnly>} /><Route path="/admin/leavers" element={<AdminOnly role={role}><LeaversPage /></AdminOnly>} /><Route path="/admin/access-reviews" element={<AdminOnly role={role}><AccessReviewsPage /></AdminOnly>} /><Route path="/admin/access-reviews/:id" element={<AdminOnly role={role}><AccessReviewDetailPage /></AdminOnly>} /><Route path="/my-packages" element={<MyPackagesPage />} /><Route path="/my-business-roles" element={<MyBusinessRolesPage />} /><Route path="/my-access-reviews" element={<MyAccessReviewsPage />} /><Route path="/admin/audit" element={<AdminOnly role={role}><AuditPage /></AdminOnly>} /><Route path="/admin/providers" element={<AdminOnly role={role}><ProvidersPage /></AdminOnly>} /><Route path="/admin/sync" element={<AdminOnly role={role}><SyncPage /></AdminOnly>} /><Route path="/admin/onboarding" element={<AdminOnly role={role}><OnboardingPage /></AdminOnly>} /><Route path="/admin/security" element={<AdminOnly role={role}><SecurityPage /></AdminOnly>} /><Route path="/admin/branding" element={<AdminOnly role={role}><BrandingPage /></AdminOnly>} /><Route path="/admin/sod" element={auth.isSodAdmin ? <SodPage /> : <Navigate to="/dashboard" replace />} /><Route path="/admin/sod/configuration" element={auth.isSodAdmin ? <SodConfigurationPage /> : <Navigate to="/dashboard" replace />} /><Route path="/admin/soc" element={auth.isSocAdmin ? <SocDashboard /> : <Navigate to="/dashboard" replace />} /><Route path="/admin/server-health" element={auth.isServerAdmin ? <ServerHealthDashboard /> : <Navigate to="/dashboard" replace />} /><Route path="/admin/server-health/troubleshooting" element={auth.isServerAdmin ? <TroubleshootingDashboard /> : <Navigate to="/dashboard" replace />} /><Route path="/admin/nhi" element={auth.isNhiAdmin ? <NhiPage /> : <Navigate to="/dashboard" replace />} /><Route path="/admin/nhi/:id" element={auth.isNhiAdmin ? <NhiDetailPage /> : <Navigate to="/dashboard" replace />} /><Route path="*" element={<Navigate to="/dashboard" replace />} /></Routes></Shell></IdleGuard>;
}
function SignInScreen() {
  const auth = useAuth();
  const branding = useBranding();
  const supportEmail = useSupportContactEmail();
  // Deliberately no mention of Break-Glass anywhere on this screen, for any user — it's reachable only via the
  // hidden /emergency-access/:token URL (src/EmergencyAccess.tsx), generated solely by a console command
  // (backend/app/cli.py). An IDP outage shows a generic notice here, never an actionable recovery hint.
  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: '#f4f7f9', padding: 24 }}>
      <img src={branding?.sign_in_logo || logo} alt="AccessPilot" style={{ width: 140, height: 140, objectFit: 'contain', marginBottom: 26 }} />
      {auth.idpUnreachable ? <div className="panel" style={{ maxWidth: 420, padding: 28, textAlign: 'center' }}>
        <div className="stat-icon" style={{ width: 40, height: 40, margin: '0 auto 16px', background: '#fdecea', color: '#8c2b21' }}><AlertTriangle size={20}/></div>
        <h1 style={{ fontSize: 19 }}>Can't reach the identity provider</h1>
        <p className="subtitle" style={{ marginTop: 10, marginBottom: supportEmail ? 6 : 18 }}>This usually means a network issue between AccessPilot and Microsoft Entra, not a problem with your account. Please contact your administrator{supportEmail ? '' : ' to get this resolved'}.</p>
        {supportEmail && <p style={{ marginBottom: 18 }}><a href={`mailto:${supportEmail}`} style={{ color: 'var(--teal)', fontWeight: 700 }}>{supportEmail}</a></p>}
        <button className="btn btn-primary" onClick={auth.signIn}>Try again</button>
      </div> : <>
        <h1>Sign in to AccessPilot</h1>
        <p className="subtitle">Use your Microsoft Entra account to continue.</p>
        <button className="btn btn-primary" onClick={auth.signIn} style={{ marginTop: 18 }}>Sign in</button>
      </>}
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
      <div className="key" style={{marginBottom:8}}><span>Display timezone</span></div>
      <p className="subtitle" style={{marginTop:0,marginBottom:14,maxWidth:640}}>Every date and time shown anywhere in AccessPilot, for every signed-in user, is displayed in this timezone — it does not change what time you're prompted for when you enter a date, only how dates already on record are shown.</p>
      <label className="key" style={{display:'block',marginBottom: COMMON_TIMEZONES.includes(form.timezone) ? 22 : 10, maxWidth:320}}>
        <span>Timezone</span>
        <select className="select" style={{width:'100%'}} value={COMMON_TIMEZONES.includes(form.timezone) ? form.timezone : 'OTHER'} onChange={event => setForm({...form, timezone: event.target.value === 'OTHER' ? '' : event.target.value})}>
          {COMMON_TIMEZONES.map(zone => <option key={zone} value={zone}>{zone}</option>)}
          <option value="OTHER">Other (type it below)</option>
        </select>
      </label>
      {!COMMON_TIMEZONES.includes(form.timezone) && <label className="key" style={{display:'block',marginBottom:22,maxWidth:320}}><span>IANA timezone name (e.g. "Asia/Kolkata")</span><input className="select" style={{width:'100%'}} value={form.timezone} onChange={event => setForm({...form, timezone: event.target.value})}/></label>}
      <div className="key" style={{marginBottom:8}}><span>Fallback support contact</span></div>
      <p className="subtitle" style={{marginTop:0,marginBottom:14,maxWidth:640}}>Shown on the sign-in screen if the identity provider itself can't be reached (e.g. no network route to it) — a real person to contact instead of a dead end. Leave blank to show a generic "contact your administrator" message with no address.</p>
      <label className="key" style={{display:'block',marginBottom:22,maxWidth:320}}><span>Support contact email</span><input className="select" style={{width:'100%'}} type="email" placeholder="helpdesk@yourcompany.com" value={form.support_contact_email || ''} onChange={event => setForm({...form, support_contact_email: event.target.value})}/></label>
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
function SodEntityPicker({ rows, groups, roles, applications, packages, businessRoles, onAdd, onRemove, onUpdate }: {
  rows: SodEntityRow[]; groups: ApiGroup[] | null; roles: ApiRole[] | null; applications: ApiApplication[] | null; packages: ApiPackage[] | null; businessRoles: ApiBusinessRole[] | null;
  onAdd: () => void; onRemove: (index: number) => void; onUpdate: (index: number, patch: Partial<SodEntityRow>) => void;
}) {
  return <div>
    {rows.map((row, index) => {
      const options: { id: string; name: string }[] = row.entity_type === 'GROUP' ? (groups || []) : row.entity_type === 'ROLE' ? (roles || []) : row.entity_type === 'APPLICATION' ? (applications || []) : row.entity_type === 'BUSINESS_ROLE' ? (businessRoles || []) : (packages || []);
      const selectedApplication = row.entity_type === 'APPLICATION' ? (applications || []).find(a => a.id === row.entity_id) : null;
      return <div key={index} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
        <select className="select" value={row.entity_type} onChange={event => onUpdate(index, { entity_type: event.target.value, entity_id: '', app_role_external_id: '' })}>
          <option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option><option value="PACKAGE">Access Package</option><option value="BUSINESS_ROLE">Business Role</option>
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
  const timezone = useAppTimezone();
  const canManageRules = auth.isSodAdmin;
  const { data: policies, loading, reload } = useApiResource<ApiSodPolicy[]>('/api/v1/sod/policies');
  const { data: violations, reload: reloadViolations } = useApiResource<ApiSodViolation[]>('/api/v1/sod/violations');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const { data: packages } = useApiResource<ApiPackage[]>('/api/v1/packages');
  const { data: businessRoles } = useApiResource<ApiBusinessRole[]>('/api/v1/business-roles');
  const { data: activity } = useApiResource<ApiSodActivityEntry[]>('/api/v1/sod/activity');
  const { data: exceptions, reload: reloadExceptions } = useApiResource<ApiSodException[]>('/api/v1/sod/exceptions');
  const { data: exceptionRequests, reload: reloadExceptionRequests } = useApiResource<ApiSodExceptionRequest[]>('/api/v1/sod/exception-requests');
  // Live: a plain DB read (no reconciliation, no Graph scan — see the Bell's own comment in Shell for why that
  // one is 60s), so a pending request from an admin shows up on this page quickly while an SoDAdmin has it open.
  useEffect(() => {
    const id = setInterval(() => reloadExceptionRequests(), 20000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [grantingRequest, setGrantingRequest] = useState<ApiSodExceptionRequest | null>(null);
  const [grantExpiresAt, setGrantExpiresAt] = useState('');
  const [requestActionSaving, setRequestActionSaving] = useState(false);
  const [requestActionMessage, setRequestActionMessage] = useState('');
  const openGrantRequest = (request: ApiSodExceptionRequest) => { setGrantingRequest(request); setGrantExpiresAt(defaultExpiry()); setRequestActionMessage(''); };
  const submitGrantRequest = async () => {
    if (!grantingRequest || !grantExpiresAt) return;
    setRequestActionSaving(true); setRequestActionMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/sod/exception-requests/${grantingRequest.id}/grant`, { method: 'POST', body: JSON.stringify({ expires_at: new Date(grantExpiresAt).toISOString() }) });
      if (response.ok) { setGrantingRequest(null); reloadExceptionRequests(); reloadExceptions(); reloadViolations(); }
      else { const body = await response.json().catch(() => null); setRequestActionMessage(body?.error?.message || 'Unable to grant this request.'); }
    } catch { setRequestActionMessage('Unable to reach the backend.'); }
    finally { setRequestActionSaving(false); }
  };
  const denyRequest = async (request: ApiSodExceptionRequest) => {
    const reason = window.prompt(`Deny the exception request from ${request.requested_by_display_name || 'this admin'} for ${request.user_display_name || request.user_id}? Reason (optional):`);
    if (reason === null) return;
    const response = await auth.apiRequest(`/api/v1/sod/exception-requests/${request.id}/deny`, { method: 'POST', body: JSON.stringify({ reason: reason.trim() || undefined }) });
    if (response.ok) reloadExceptionRequests();
  };
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptySodForm);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  // Granting an exception is deliberately only reachable through the exception-request workflow now (a blocked
  // admin attempt -> request -> SoDAdmin review in the Exception Requests panel) — there used to also be an
  // instant "Grant exception" button right on this Violations table, bypassing that trail entirely with no
  // approval-routing awareness of its own; the user asked for it removed so granting always goes through one
  // reviewable path. The backend's POST /sod/exceptions endpoint itself still exists (untouched, still callable
  // directly via the API) — only this direct-grant UI shortcut was removed; nothing in the frontend calls it
  // anymore (only GET, for the Active Exceptions list below, and DELETE, to revoke).
  // A `datetime-local` input's value is read/written as LOCAL wall-clock time, never UTC — `toISOString()`
  // always returns the UTC representation, so slicing that (the previous, buggy version of this function) silently
  // shifted the pre-filled default by the browser's own UTC offset every time, with no visible sign anything was
  // wrong until the stored expires_at came back off by exactly that many hours. Building the string from local
  // getters (getFullYear/getMonth/...) instead keeps it in the same local time the input actually expects.
  const defaultExpiry = () => {
    const d = new Date();
    d.setDate(d.getDate() + 30);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
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
    if (!window.confirm(`Delete the SoD policy "${policy.name}"? If it has never had an exception or notification, it's removed entirely; otherwise it will be disabled instead (kept for audit history).`)) return;
    const response = await auth.apiRequest(`/api/v1/sod/policies/${policy.id}`, { method: 'DELETE' });
    if (response.ok) { reload(); reloadViolations(); }
  };

  const summarize = (policy: ApiSodPolicy, side: string) => policy.entities.filter(e => e.conflict_side === side).map(e => e.entity_display_name || 'Unresolved').join(', ') || '—';

  return <Page eyebrow="GOVERNANCE" title="Separation of Duties" subtitle="Admin-configurable rules preventing any user from holding two conflicting entitlements at once — enforced live, at the moment access actually becomes real." action={canManageRules && <button className="btn btn-primary" onClick={startCreate}>+ Add SoD policy</button>}>
    {!canManageRules && <div className="notice" style={{ marginBottom: 18 }}>You can view rules and violations. Only an AccessPilot.SoDAdmin can create, edit, or disable rules — that role is assigned directly in Entra, not from inside AccessPilot.</div>}
    {showForm && canManageRules && <div className="panel" style={{ marginBottom: 24 }}><div className="detail-section">
      <div className="detail-title"><h2>{editingId ? 'Edit SoD policy' : 'New SoD policy'}</h2></div>
      <label className="key" style={{ display: 'block', marginBottom: 14, maxWidth: 420 }}><span>Name</span><input className="select" style={{ width: '100%' }} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 14, maxWidth: 420 }}><span>Description</span><input className="select" style={{ width: '100%' }} value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} /></label>
      <label className="key" style={{ display: 'block', marginBottom: 20, maxWidth: 220 }}><span>Severity</span><select className="select" style={{ width: '100%' }} value={form.severity} onChange={event => setForm({ ...form, severity: event.target.value })}><option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option><option value="CRITICAL">Critical</option></select></label>
      <div className="key" style={{ marginBottom: 8 }}><span>Side A — holding anything here...</span></div>
      <SodEntityPicker rows={form.sideA} groups={groups} roles={roles} applications={applications} packages={packages} businessRoles={businessRoles} onAdd={() => addEntity('sideA')} onRemove={i => removeEntity('sideA', i)} onUpdate={(i, patch) => updateEntity('sideA', i, patch)} />
      <div className="key" style={{ margin: '18px 0 8px' }}><span>...conflicts with holding anything here (Side B)</span></div>
      <SodEntityPicker rows={form.sideB} groups={groups} roles={roles} applications={applications} packages={packages} businessRoles={businessRoles} onAdd={() => addEntity('sideB')} onRemove={i => removeEntity('sideB', i)} onUpdate={(i, patch) => updateEntity('sideB', i, patch)} />
      {message && <div className="notice" style={{ margin: '14px 0' }}>{message}</div>}
      <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
        <button className="btn btn-primary" disabled={saving} onClick={submit}>{saving ? 'Saving...' : 'Save policy'}</button>
        <button className="btn" onClick={() => setShowForm(false)}>Cancel</button>
      </div>
    </div></div>}

    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Policies</h2></div>
      {loading ? <div className="empty">Loading...</div> : !policies || policies.length === 0 ? <div className="empty">No SoD policies defined yet.</div> : <div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Severity</th><th>Status</th><th>Side A</th><th>Side B</th>{canManageRules && <th>Actions</th>}</tr></thead><tbody>
        {policies.map(policy => <tr key={policy.id}>
          <td>{policy.name}</td><td>{policy.severity}</td><td><StatusBadge status={policy.status} /></td>
          <td>{summarize(policy, 'A')}</td><td>{summarize(policy, 'B')}</td>
          {canManageRules && <td style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={() => startEdit(policy)}>Edit</button>
            <button className="btn" onClick={() => toggleStatus(policy)}>{policy.status === 'ACTIVE' ? 'Disable' : 'Enable'}</button>
            <button className="btn" onClick={() => remove(policy)}>Delete</button>
          </td>}
        </tr>)}
      </tbody></table></div>}
    </div>

    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Violations</h2></div>
      {!canManageRules ? null : <div className="notice" style={{ margin: '0 18px 18px' }}>Exceptions can no longer be granted directly from this table — an admin must request one via a blocked assignment attempt, which the SoDAdmin then reviews in the Exception Requests panel below.</div>}
      {!violations ? <div className="empty">Loading...</div> : violations.length === 0 ? <div className="empty">No current violations — nobody holds both sides of an active rule.</div> : <div className="table-wrap"><table className="table"><thead><tr><th>User</th><th>Policy</th><th>Severity</th><th>Side A holdings</th><th>Side B holdings</th><th>Risk status</th></tr></thead><tbody>
        {violations.map((v, i) => <tr key={i}>
          <td>{v.user_display_name || v.user_id}</td><td>{v.policy_name}</td><td>{v.severity}</td>
          <td>{v.side_a_holdings.map(h => `${h.resource_display_name || h.resource_type}${h.source === 'DIRECT_IN_ENTRA' ? ' (direct in Entra)' : ''}`).join(', ')}</td>
          <td>{v.side_b_holdings.map(h => `${h.resource_display_name || h.resource_type}${h.source === 'DIRECT_IN_ENTRA' ? ' (direct in Entra)' : ''}`).join(', ')}</td>
          <td>{v.exception_active
            ? <span className="badge success">Accepted until {v.exception_expires_at ? formatWithZone(v.exception_expires_at, timezone) : '—'}</span>
            : <span className="badge danger">Open</span>}
          </td>
        </tr>)}
      </tbody></table></div>}
    </div>

    {grantingRequest && <form role="dialog" aria-modal="true" className="panel" style={{ maxWidth: 480, marginBottom: 24 }} onSubmit={event => { event.preventDefault(); void submitGrantRequest(); }}>
      <div className="panel-head"><h2>Grant exception request</h2><button type="button" className="btn" aria-label="Close" onClick={() => setGrantingRequest(null)}><X size={14} /></button></div>
      <div className="detail-section">
        <p className="subtitle" style={{ marginTop: 0 }}>Requested by <strong>{grantingRequest.requested_by_display_name || 'an administrator'}</strong> for <strong>{grantingRequest.user_display_name || grantingRequest.user_id}</strong> on policy <strong>{grantingRequest.policy_name || '—'}</strong>, for <strong>{grantingRequest.resource_display_name || grantingRequest.resource_type}</strong>.</p>
        <p className="subtitle" style={{ marginBottom: 16 }}>Justification: {grantingRequest.justification}</p>
        <label className="key" style={{ display: 'block' }}><span>Expires (your device's local time)</span><input className="select" style={{ width: '100%' }} type="datetime-local" required value={grantExpiresAt} onChange={event => setGrantExpiresAt(event.target.value)} /></label>
        {requestActionMessage && <div className="notice" style={{ marginTop: 14 }}>{requestActionMessage}</div>}
      </div>
      <div className="detail-section" style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}><button type="button" className="btn" onClick={() => setGrantingRequest(null)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={requestActionSaving}>{requestActionSaving ? 'Granting...' : 'Grant exception'}</button></div>
    </form>}

    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Exception Requests</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{ marginTop: 0, marginBottom: 16 }}>When an admin's own assignment is blocked by a policy, they can ask here instead of being stuck — granting recreates the original attempt exactly, including routing through the same approver if one was configured, unless another, still-unresolved policy also blocks it.</p>
        {!exceptionRequests || exceptionRequests.length === 0 ? <div className="empty">No exception requests have been made yet.</div> : <div className="table-wrap"><table className="table"><thead><tr><th>Requested by</th><th>User</th><th>Policy</th><th>Target</th><th>Routing</th><th>Justification</th><th>Status</th>{canManageRules && <th>Actions</th>}</tr></thead><tbody>
          {exceptionRequests.map(request => <tr key={request.id}>
            <td>{request.requested_by_display_name || '—'}</td>
            <td>{request.user_display_name || request.user_id}</td>
            <td>{request.policy_name || '—'}</td>
            <td>{request.resource_display_name || request.resource_type}</td>
            <td>{request.approver_id ? `Approval: ${request.approver_display_name || '—'}` : 'Direct (eligible)'}</td>
            <td>{request.justification}</td>
            <td>{request.status === 'PENDING' ? <span className="badge danger">Pending</span> : request.status === 'GRANTED' ? <span className="badge success">Granted</span> : <span className="badge neutral">Denied{request.denial_reason ? `: ${request.denial_reason}` : ''}</span>}</td>
            {canManageRules && <td>{request.status === 'PENDING' && <div style={{ display: 'flex', gap: 8 }}><button className="btn" onClick={() => openGrantRequest(request)}>Grant</button><button className="btn" onClick={() => denyRequest(request)}>Deny</button></div>}</td>}
          </tr>)}
        </tbody></table></div>}
      </div>
    </div>

    <div className="panel" style={{ marginBottom: 24 }}>
      <div className="panel-head"><h2>Active Exceptions</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{ marginTop: 0, marginBottom: 16 }}>Time-boxed risk acceptances — while one is active, new grants for that user on that rule aren't blocked. Every grant, revoke, and expiry is on the record in SoD Activity below.</p>
        {!exceptions || exceptions.length === 0 ? <div className="empty">No exceptions have ever been granted.</div> : <div className="table-wrap"><table className="table"><thead><tr><th>User</th><th>Policy</th><th>Justification</th><th>Granted by</th><th>Status</th><th>Actions</th></tr></thead><tbody>
          {exceptions.map(exception => <tr key={exception.id}>
            <td>{exception.user_display_name || exception.user_id}</td>
            <td>{exception.policy_name || '—'}</td>
            <td>{exception.justification}</td>
            <td>{exception.granted_by_display_name || '—'}</td>
            <td>{exception.revoked_at ? <span className="badge neutral">Revoked</span> : exception.is_active ? <span className="badge success">Active until {formatWithZone(exception.expires_at, timezone)}</span> : <span className="badge neutral">Expired</span>}</td>
            <td>{canManageRules && exception.is_active && <button className="btn" onClick={() => revokeException(exception.id)}>Revoke</button>}</td>
          </tr>)}
        </tbody></table></div>}
      </div>
    </div>

    <div className="panel">
      <div className="panel-head"><h2>SoD Activity</h2></div>
      {!activity ? <div className="empty">Loading...</div> : activity.length === 0 ? <div className="empty">No SoD activity yet — rule changes, roster changes, and any blocked or overridden grant will show up here.</div> : <div className="table-wrap"><table className="table"><thead><tr><th>When</th><th>Action</th><th>Actor</th><th>Target user</th><th>Result</th><th>Details</th></tr></thead><tbody>
        {activity.map(entry => <tr key={entry.id}>
          <td>{formatDateTime(entry.timestamp, timezone)}</td>
          <td>{entry.action.replace(/_/g, ' ')}</td>
          <td>{entry.actor_display_name || 'System'}</td>
          <td>{entry.target_user_display_name || '—'}</td>
          <td><StatusBadge status={entry.result} /></td>
          <td>{summarizeSodActivity(entry)}</td>
        </tr>)}
      </tbody></table></div>}
    </div>
  </Page>;
}
function SodConfigurationPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const canManage = auth.isSodAdmin;
  const { data: settings, reload: reloadSettings } = useApiResource<ApiSodNotificationSettings>('/api/v1/sod/notification-settings');
  const { data: notifications, reload: reloadNotifications } = useApiResource<ApiSodNotification[]>('/api/v1/sod/notifications');
  const [form, setForm] = useState<ApiSodNotificationSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { if (settings) setForm(settings); }, [settings]);
  // Live: this page's own notification log auto-refreshes so a new violation or exception request shows up
  // without the SoDAdmin needing to manually reload. 60s, matching the Bell's own interval in Shell (both call
  // the same reconciling endpoint — see that comment for the real Graph-scan cost this trades off against).
  useEffect(() => {
    const id = setInterval(() => reloadNotifications(), 60000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}><input type="checkbox" disabled={!canManage} checked={form.notify_on_exception_requested} onChange={event => setForm({ ...form, notify_on_exception_requested: event.target.checked })} /><span>Notify when an admin requests an SoD exception for a blocked assignment</span></label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}><input type="checkbox" disabled={!canManage} checked={form.cooldown_enabled} onChange={event => setForm({ ...form, cooldown_enabled: event.target.checked })} /><span>Enforce a cooldown after deactivating or revoking access, before the conflicting side can be activated</span></label>
        <p className="subtitle" style={{ marginTop: 0, marginBottom: 14, maxWidth: 640 }}>Without this, a user could deactivate one side of a conflict and immediately activate the other, then flip back later — never holding both at the exact same instant, but never really giving either up either. Enabling this blocks that cycle for a bounded window after each deactivation/revocation.</p>
        <label className="key" style={{ display: 'block', marginBottom: 22, maxWidth: 260 }}><span>Cooldown window (hours)</span><input className="select" style={{ width: '100%' }} type="number" min={1} max={720} disabled={!canManage || !form.cooldown_enabled} value={form.cooldown_hours} onChange={event => setForm({ ...form, cooldown_hours: Number(event.target.value) })} /></label>
        {message && <div className="notice" style={{ marginBottom: 14 }}>{message}</div>}
        {canManage && <button className="btn btn-primary" disabled={saving} onClick={save}>{saving ? 'Saving...' : 'Save'}</button>}
      </div>}
    </div>

    <div className="panel">
      <div className="panel-head"><h2>Notification Log</h2><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>{unreadCount > 0 && <span className="badge danger">{unreadCount} unread</span>}<button className="btn" onClick={markAllRead} disabled={!notifications || unreadCount === 0}>Mark all as read</button></div></div>
      {!notifications ? <div className="empty">Loading...</div> : notifications.length === 0 ? <div className="empty">Nothing has been reported yet — a new violation or a soon-expiring exception will show up here.</div> : <div className="table-wrap"><table className="table"><thead><tr><th>When</th><th>Type</th><th>Message</th><th>Status</th><th>Actions</th></tr></thead><tbody>
        {notifications.map(n => <tr key={n.id} style={{ opacity: n.read_at ? 0.7 : 1 }}>
          <td>{formatDateTime(n.created_at, timezone)}</td>
          <td>{n.notification_type.replace(/_/g, ' ')}</td>
          <td>{n.message}</td>
          <td>{n.resolved_at ? <span className="badge neutral">Resolved</span> : n.read_at ? <span className="badge neutral">Read</span> : <span className="badge danger">Unread</span>}</td>
          <td>{!n.read_at && <button className="btn" onClick={() => markRead(n.id)}>Mark read</button>}</td>
        </tr>)}
      </tbody></table></div>}
    </div>
  </Page>;
}
interface ApiSocFieldInfo { field: string; label: string; }
interface ApiSocSourceInfo { source: string; label: string; fields: ApiSocFieldInfo[]; }
interface ApiSocFields { sources: ApiSocSourceInfo[]; builtin_widgets: ApiSocFieldInfo[]; }
interface ApiSocWidgetFilter { field: string; value: string; }
interface ApiSocWidget { id: string; title: string; kind: 'card' | 'timeseries' | 'bar' | 'list'; source: string; builtin_id?: string | null; group_by?: string | null; filters: ApiSocWidgetFilter[]; visible: boolean; order: number; }
interface ApiSocLayout { widgets: ApiSocWidget[]; }
interface ApiSocWidgetDataResult { value?: number | null; series?: { date?: string; label?: string; count?: number; value?: number }[] | null; rows?: Record<string, string>[] | null; }
interface ApiSocWidgetDataResponse { results: Record<string, ApiSocWidgetDataResult>; }
const emptySocWidgetForm = { title: '', source: 'builtin', builtin_id: '', kind: 'card' as ApiSocWidget['kind'], group_by: '', filter_field: '', filter_value: '' };

// A small, hand-rolled horizontal bar list — used for both 'bar' and 'list' custom-graph kinds, since the only
// real difference the user asked for is which fields feed it, not a genuinely different visual. No new charting
// dependency, same convention as ActivationTimelineChart/PieChart elsewhere in this app.
function SocBarList({ rows, onRowClick }: { rows: { label: string; value: number }[]; onRowClick?: (label: string) => void }) {
  if (rows.length === 0) return <div className="empty">No matching data yet.</div>;
  const max = Math.max(...rows.map(r => r.value), 1);
  return <div className="soc-bar-list">{rows.slice(0, 12).map(r => <div key={r.label} className={`soc-bar-row${onRowClick ? ' soc-bar-row-clickable' : ''}`} onClick={onRowClick ? () => onRowClick(r.label) : undefined}>
    <span className="soc-bar-label">{r.label}</span>
    <span className="soc-bar-track"><span className="soc-bar-fill" style={{ width: `${(r.value / max) * 100}%` }}/></span>
    <span className="soc-bar-value">{r.value}</span>
  </div>)}</div>;
}

function SocDashboard() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: fields } = useApiResource<ApiSocFields>('/api/v1/soc/fields');
  const { data: layoutData, loading: layoutLoading } = useApiResource<ApiSocLayout>('/api/v1/soc/layout');
  const [widgets, setWidgets] = useState<ApiSocWidget[] | null>(null);
  const [data, setData] = useState<Record<string, ApiSocWidgetDataResult>>({});
  const [dragId, setDragId] = useState<string | null>(null);
  const [showBuilder, setShowBuilder] = useState(false);
  const [builderForm, setBuilderForm] = useState(emptySocWidgetForm);
  const [builderMessage, setBuilderMessage] = useState('');
  const [drilldown, setDrilldown] = useState<{ title: string; date: string; rows: Record<string, any>[]; loading: boolean } | null>(null);
  useEffect(() => { if (layoutData) setWidgets(layoutData.widgets); }, [layoutData]);

  // Every card and graph drills into the real rows behind whatever was clicked: a card shows the rows behind its
  // total, a timeseries point shows that day's rows, and a bar/list entry shows the rows in that group. The "SoD
  // violations" card is the one exception with real per-row detail beyond a plain row list (exception coverage),
  // handled entirely server-side — the frontend just renders whatever shape comes back.
  const openDrilldown = async (widget: ApiSocWidget, point?: { date: string; count: number }, groupValue?: string) => {
    const date = point?.date || '';
    setDrilldown({ title: widget.title, date, rows: [], loading: true });
    const response = await auth.apiRequest('/api/v1/soc/widget-drilldown', { method: 'POST', body: JSON.stringify({ widget, date: point?.date, group_value: groupValue }) });
    const rows = response.ok ? ((await response.json()) as { rows: Record<string, any>[] }).rows : [];
    setDrilldown({ title: widget.title, date, rows, loading: false });
  };

  const loadData = async (list: ApiSocWidget[]) => {
    if (list.length === 0) { setData({}); return; }
    const response = await auth.apiRequest('/api/v1/soc/widget-data', { method: 'POST', body: JSON.stringify({ widgets: list }) });
    if (response.ok) { const body = await response.json() as ApiSocWidgetDataResponse; setData(body.results); }
  };
  useEffect(() => { if (widgets) void loadData(widgets); }, [widgets]); // eslint-disable-line react-hooks/exhaustive-deps
  // Live: matches the admin Dashboard's own 30s polling cadence — no push infrastructure exists anywhere in this
  // app, so "real-time" always means a short interval timer re-fetching the same widgets' data.
  useEffect(() => {
    const id = setInterval(() => { if (widgets) void loadData(widgets); }, 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgets]);

  const saveLayout = (next: ApiSocWidget[]) => {
    setWidgets(next);
    void auth.apiRequest('/api/v1/soc/layout', { method: 'PUT', body: JSON.stringify({ widgets: next }) });
  };
  const removeWidget = (id: string) => { if (widgets) saveLayout(widgets.filter(w => w.id !== id)); };

  // Real mouse drag-and-drop reordering via the browser's native HTML5 Drag and Drop API — no grid-layout
  // dependency added. Dropping a widget onto another one moves it to that position in the order (like reordering
  // cards on a Trello board), then persists immediately.
  const sorted = (widgets || []).slice().sort((a, b) => a.order - b.order);
  const handleDrop = (targetId: string) => {
    if (!widgets || !dragId || dragId === targetId) { setDragId(null); return; }
    const list = sorted.slice();
    const fromIndex = list.findIndex(w => w.id === dragId);
    const toIndex = list.findIndex(w => w.id === targetId);
    if (fromIndex === -1 || toIndex === -1) { setDragId(null); return; }
    const [moved] = list.splice(fromIndex, 1);
    list.splice(toIndex, 0, moved);
    saveLayout(list.map((w, i) => ({ ...w, order: i })));
    setDragId(null);
  };

  const openBuilder = () => { setBuilderForm(emptySocWidgetForm); setBuilderMessage(''); setShowBuilder(true); };
  const submitBuilder = () => {
    if (!widgets) return;
    if (builderForm.source === 'builtin') {
      if (!builderForm.builtin_id) { setBuilderMessage('Choose a built-in panel.'); return; }
      const meta = fields?.builtin_widgets.find(b => b.field === builderForm.builtin_id);
      const kind = builderForm.builtin_id === 'high_signal_events' ? 'list' : builderForm.builtin_id === 'activity_timeline' ? 'timeseries' : 'card';
      saveLayout([...widgets, { id: `${builderForm.builtin_id}-${Date.now()}`, title: meta?.label || builderForm.builtin_id, kind, source: 'builtin', builtin_id: builderForm.builtin_id, filters: [], visible: true, order: widgets.length }]);
    } else {
      if (!builderForm.title.trim()) { setBuilderMessage('Give the graph a title.'); return; }
      if (builderForm.kind !== 'card' && builderForm.kind !== 'timeseries' && !builderForm.group_by) { setBuilderMessage('Choose a field to group by.'); return; }
      const filters: ApiSocWidgetFilter[] = builderForm.filter_field && builderForm.filter_value ? [{ field: builderForm.filter_field, value: builderForm.filter_value }] : [];
      saveLayout([...widgets, { id: `custom-${Date.now()}`, title: builderForm.title.trim(), kind: builderForm.kind, source: builderForm.source, group_by: builderForm.kind === 'timeseries' ? null : builderForm.group_by, filters, visible: true, order: widgets.length }]);
    }
    setShowBuilder(false);
  };

  const activeSourceFields = fields?.sources.find(s => s.source === builderForm.source)?.fields || [];

  const renderWidget = (widget: ApiSocWidget) => {
    const result = data[widget.id];
    const kindClass = widget.kind === 'card' ? 'soc-widget-card' : widget.kind === 'timeseries' ? 'soc-widget-chart' : 'soc-widget-list';
    return <div key={widget.id} className={`soc-widget ${kindClass} ${dragId === widget.id ? 'soc-widget-dragging' : ''}`} draggable
      onDragStart={event => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', widget.id); setDragId(widget.id); }}
      onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }}
      onDrop={event => { event.preventDefault(); handleDrop(widget.id); }}
      onDragEnd={() => setDragId(null)}>
      <div className="soc-widget-head">
        <span className="soc-widget-drag" title="Drag to move"><Menu size={13}/></span>
        <h3>{widget.title}</h3>
        <div className="soc-widget-controls">
          <button type="button" aria-label="Remove widget" onClick={() => removeWidget(widget.id)}><X size={13}/></button>
        </div>
      </div>
      <div className="soc-widget-body">
        {!result ? <div className="empty">Loading...</div> : <>
          {widget.kind === 'card' && <div className="soc-card-clickable" onClick={() => void openDrilldown(widget)}>
              <div className="soc-card-value">{result.value ?? '—'}</div>
              <div className="soc-card-hint">Click to view details</div>
            </div>}
          {widget.kind === 'timeseries' && <ActivationTimelineChart series={(result.series || []).map(p => ({ date: p.date || '', count: p.count ?? p.value ?? 0 }))} yAxisLabel="Events" unitLabel="event" tooltipSuffix="recorded" onPointClick={point => void openDrilldown(widget, point)}/>}
          {(widget.kind === 'bar' || widget.kind === 'list') && (widget.builtin_id === 'high_signal_events'
            ? ((result.rows || []).length === 0 ? <div className="empty">No high-signal events recorded yet.</div> : (result.rows || []).map(row => <div key={row.id} className="soc-event-row"><div className="soc-event-main"><strong>{(row.action || '').replace(/_/g, ' ')}</strong> — {row.actor_display_name || 'System'}{row.target_user_display_name ? ` → ${row.target_user_display_name}` : ''}</div><div className="soc-event-time">{formatDateTime(row.timestamp, timezone)}</div></div>))
            : <SocBarList rows={(result.series || []).map(p => ({ label: p.label || '—', value: p.value ?? 0 }))} onRowClick={label => void openDrilldown(widget, undefined, label)}/>)}
        </>}
      </div>
    </div>;
  };

  return <div className="soc-page"><Page eyebrow="SECURITY OPERATIONS" title="Security Operations Dashboard" subtitle="Real-time platform activity, blocked or overridden access, emergency-access usage, and open Separation-of-Duties conflicts. Drag a card to reorder it, or build your own graph from any available field." action={<button className="btn btn-primary" onClick={openBuilder}><Plus size={14}/> Add widget</button>}>
    {showBuilder && <form role="dialog" aria-modal="true" className="panel" style={{ maxWidth: 480, marginBottom: 18 }} onSubmit={event => { event.preventDefault(); submitBuilder(); }}>
      <div className="panel-head"><h2>Add widget</h2><button type="button" className="btn" aria-label="Close" onClick={() => setShowBuilder(false)}><X size={14}/></button></div>
      <div className="detail-section">
        <label className="key" style={{ display: 'block', marginBottom: 14 }}><span>Widget type</span>
          <select className="select" style={{ width: '100%' }} value={builderForm.source} onChange={event => setBuilderForm({ ...emptySocWidgetForm, source: event.target.value })}>
            <option value="builtin">Built-in panel</option>
            {(fields?.sources || []).map(s => <option key={s.source} value={s.source}>Custom graph — {s.label}</option>)}
          </select>
        </label>
        {builderForm.source === 'builtin' ? <label className="key" style={{ display: 'block' }}><span>Panel</span>
          <select className="select" style={{ width: '100%' }} value={builderForm.builtin_id} onChange={event => setBuilderForm({ ...builderForm, builtin_id: event.target.value })}>
            <option value="">Select...</option>
            {(fields?.builtin_widgets || []).map(b => <option key={b.field} value={b.field}>{b.label}</option>)}
          </select>
        </label> : <>
          <label className="key" style={{ display: 'block', marginBottom: 14 }}><span>Title</span><input className="select" style={{ width: '100%' }} value={builderForm.title} onChange={event => setBuilderForm({ ...builderForm, title: event.target.value })}/></label>
          <label className="key" style={{ display: 'block', marginBottom: 14 }}><span>Chart type</span>
            <select className="select" style={{ width: '100%' }} value={builderForm.kind} onChange={event => setBuilderForm({ ...builderForm, kind: event.target.value as ApiSocWidget['kind'] })}>
              <option value="card">Number (count)</option>
              <option value="timeseries">Line chart over time</option>
              <option value="bar">Bar chart (grouped)</option>
              <option value="list">List (grouped)</option>
            </select>
          </label>
          {(builderForm.kind === 'bar' || builderForm.kind === 'list') && <label className="key" style={{ display: 'block', marginBottom: 14 }}><span>Group by field</span>
            <select className="select" style={{ width: '100%' }} value={builderForm.group_by} onChange={event => setBuilderForm({ ...builderForm, group_by: event.target.value })}>
              <option value="">Select a field...</option>
              {activeSourceFields.map(f => <option key={f.field} value={f.field}>{f.label}</option>)}
            </select>
          </label>}
          <div className="key" style={{ marginBottom: 8 }}><span>Filter (optional)</span></div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
            <select className="select" style={{ flex: 1 }} value={builderForm.filter_field} onChange={event => setBuilderForm({ ...builderForm, filter_field: event.target.value })}>
              <option value="">No filter</option>
              {activeSourceFields.map(f => <option key={f.field} value={f.field}>{f.label}</option>)}
            </select>
            <input className="select" style={{ flex: 1 }} placeholder="equals..." disabled={!builderForm.filter_field} value={builderForm.filter_value} onChange={event => setBuilderForm({ ...builderForm, filter_value: event.target.value })}/>
          </div>
        </>}
        {builderMessage && <div className="notice" style={{ marginTop: 10 }}>{builderMessage}</div>}
      </div>
      <div className="detail-section" style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}><button type="button" className="btn" onClick={() => setShowBuilder(false)}>Cancel</button><button type="submit" className="btn btn-primary">Add widget</button></div>
    </form>}
    {layoutLoading || !widgets ? <div className="empty">Loading Security Operations dashboard...</div> :
      widgets.length === 0 ? <div className="empty">No widgets on this dashboard yet — click "Add widget" to build one.</div> :
      <div className="soc-widget-grid">{sorted.map(w => renderWidget(w))}</div>}
    {drilldown && <div className="overlay-backdrop" onClick={() => setDrilldown(null)}>
      <div className="overlay-card" onClick={event => event.stopPropagation()}>
        <div className="panel-head"><h2>{drilldown.title}{drilldown.date ? ` — ${formatDate(drilldown.date, timezone)}` : ''}</h2><button type="button" className="btn" aria-label="Close" onClick={() => setDrilldown(null)}><X size={14}/></button></div>
        <div className="table-wrap" style={{ padding: '4px 20px 20px' }}>
          {drilldown.loading ? <div className="empty">Loading...</div> : drilldown.rows.length === 0 ? <div className="empty">{drilldown.date ? 'No matching records for this day.' : 'No conflicts found — every policy is currently clear.'}</div> : drilldown.rows.map((row, index) => <div key={row.id || index} className="soc-event-row">
            {row.policy_name ? <div className="soc-event-main">
              <strong>{row.user_display_name}</strong> — {row.policy_name}
              <span className={`badge ${row.exception_active ? 'success' : 'danger'}`} style={{ marginLeft: 8 }}>{row.exception_active ? 'Exception active' : 'Open'}</span>
              <div className="soc-event-time" style={{ marginTop: 4 }}>Holds: {(row.side_a as string[]).join(', ')} + {(row.side_b as string[]).join(', ')}{row.exception_active && row.exception_expires_at ? ` — exception expires ${formatDate(row.exception_expires_at, timezone)}` : ''}</div>
            </div> : row.action
              ? <div className="soc-event-main"><strong>{row.action.replace(/_/g, ' ')}</strong> — {row.actor_display_name || 'System'}{row.target_user_display_name ? ` → ${row.target_user_display_name}` : ''}</div>
              : <div className="soc-event-main"><strong>{row.status}</strong> — {row.resource_type}{row.user_display_name ? ` · ${row.user_display_name}` : ''}{row.activated_at ? ` · activated ${formatDate(row.activated_at, timezone)}` : ''}</div>}
            {!row.policy_name && <div className="soc-event-time">{formatDateTime(row.timestamp || row.created_at, timezone)}</div>}
          </div>)}
        </div>
      </div>
    </div>}
  </Page></div>;
}

// ---- System Health (AccessPilot.ServerAdmin) ----
// V1 is deliberately mock data from a real, role-gated endpoint (GET /api/v1/server-health) — only a real
// AccessPilot.ServerAdmin can reach it at all (see security/auth.py), but every number on the page today is
// placeholder data pending v2's real instrumentation (see services/server_health.py for exactly which pieces
// are already real-and-cheap to wire up vs. which need new work). The frontend renders whatever shape comes
// back identically either way, so swapping the backend's internals later needs zero changes here.
interface ApiServiceStatusCard { name: string; tag: string; status: string; variant: string; metric: string; metric_unit: string; metric_label: string; foot_label: string; foot_value: string; }
interface ApiRequestVolumeChart { avg_req_per_min: number; bars: number[]; latency_line: number[]; }
interface ApiWorkerStatus { name: string; cadence: string; last_run: string; ticks: string[]; }
interface ApiWorkflowStatus { name: string; description: string; kind: string; cadence: string; status: string; variant: string; last_run: string; ticks: string[]; recent_activity: string; }
interface ApiEndpointHealth { method: string; path: string; status: string; status_variant: string; avg: string; p95: string; req_per_min: number; error_rate: string; }
interface ApiDatabaseHealth { pool_used: number; pool_max: number; avg_query_ms: number; slowest_query_ms: number; audit_log_rows: number; replication_lag: string; last_backup: string; }
interface ApiLiveEvent { level: string; time: string; message: string; }
interface ApiServerHealth { is_mock: boolean; overall_status: string; services: ApiServiceStatusCard[]; request_chart: ApiRequestVolumeChart; workers: ApiWorkerStatus[]; workflows: ApiWorkflowStatus[]; endpoints: ApiEndpointHealth[]; database: ApiDatabaseHealth; events: ApiLiveEvent[]; }

function ServerHealthDashboard() {
  const { data, loading, error, reload } = useApiResource<ApiServerHealth>('/api/v1/server-health');
  const [clock, setClock] = useState(() => new Date().toLocaleTimeString('en-US', { hour12: false }));
  useEffect(() => { const id = setInterval(() => setClock(new Date().toLocaleTimeString('en-US', { hour12: false })), 1000); return () => clearInterval(id); }, []);
  // Real-time: every 20s, matching the user's explicit ask — same interval-timer "real-time" pattern already
  // used by the admin Dashboard (30s) and SOC (30s), just a tighter cadence since this page is meant for
  // actively watching something during an incident, not a passive overview. Empty deps deliberately: `reload`
  // is a fresh closure every render (useApiResource doesn't memoize it) but always calls the same underlying
  // stable setState setter, so capturing it once at mount is safe — depending on it would tear down and
  // recreate this interval on every render (including every 1s clock tick above), so the 20s timer would never
  // actually get to fire.
  useEffect(() => { const id = setInterval(reload, 20000); return () => clearInterval(id); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="serverhealth-page"><Page eyebrow="SYSTEM HEALTH" title="System Health" subtitle="Live status of every server-side component — API, database, background workers, and connected identity providers." action={<div className="sh-status-strip"><span className={`sh-pulse-dot sh-pulse-${data?.overall_status === 'operational' ? 'ok' : 'warn'}`}/><span>{data ? (data.overall_status === 'operational' ? 'All systems operational' : data.overall_status) : 'Checking...'}</span><span className="sh-clock">· {clock}</span><Link to="/admin/server-health/troubleshooting" className="btn" style={{ marginLeft: 10 }}>Troubleshoot</Link></div>}>
    {loading && !data && <div className="empty">Loading system health...</div>}
    {error && <div className="empty">{error}</div>}
    {data && <>
      <div className="sh-status-grid">
        {data.services.map(card => <div key={card.name} className={`sh-status-card sh-${card.variant}`}>
          <div className="sh-status-head">
            <div><div className="sh-status-name">{card.name}</div><div className="sh-status-tag">{card.tag}</div></div>
            <span className={`sh-status-badge sh-${card.variant}`}>{card.status}</span>
          </div>
          <div className="sh-status-metric">{card.metric}<span className="sh-status-metric-unit">{card.metric_unit}</span></div>
          <div className="sh-status-metric-label">{card.metric_label}</div>
          <div className="sh-status-foot"><span>{card.foot_label}</span><span>{card.foot_value}</span></div>
        </div>)}
      </div>

      <div className="panel sh-panel" style={{ marginBottom: 18 }}>
        <div className="panel-head"><h2>API request volume &amp; latency — last 60 min</h2><span className="panel-link">avg {data.request_chart.avg_req_per_min} req/min</span></div>
        <div className="detail-section"><ServerHealthChart chart={data.request_chart}/>
          <div className="sh-chart-legend"><span><i className="sh-legend-swatch" style={{ background: '#D7DCE3' }}/>Requests / min</span><span><i className="sh-legend-swatch" style={{ background: 'var(--sh-accent)' }}/>p95 latency</span></div>
        </div>
      </div>

      <div className="section-label">WORKFLOWS</div>
      <div className="sh-workflow-grid">
        {data.workflows.map(workflow => <div key={workflow.name} className={`sh-workflow-card sh-${workflow.variant}`}>
          <div className="sh-workflow-head">
            <div><div className="sh-workflow-name">{workflow.name}</div><div className="sh-workflow-kind">{workflow.kind === 'background' ? `Background · ${workflow.cadence}` : 'On demand'}</div></div>
            <span className={`sh-status-badge sh-${workflow.variant}`}>{workflow.status}</span>
          </div>
          <div className="sh-workflow-desc">{workflow.description}</div>
          {workflow.ticks.length > 0 && <div className="sh-tick-row sh-tick-row-left">{workflow.ticks.map((tick, index) => <span key={index} className={`sh-tick sh-tick-${tick}`}/>)}</div>}
          <div className="sh-workflow-foot"><span>{workflow.last_run}</span><span>{workflow.recent_activity}</span></div>
        </div>)}
      </div>

      <div className="panel sh-panel" style={{ marginBottom: 14 }}>
        <div className="panel-head"><h2>Endpoint health — last 15 min</h2><span className="panel-link">{data.endpoints.length} routes monitored</span></div>
        <div className="table-wrap"><table><thead><tr><th>Method</th><th>Path</th><th>Status</th><th>Avg</th><th>P95</th><th>Req/min</th><th>Error rate</th></tr></thead><tbody>
          {data.endpoints.map(endpoint => <tr key={`${endpoint.method}-${endpoint.path}`}>
            <td><span className={`sh-method-badge sh-method-${endpoint.method.toLowerCase()}`}>{endpoint.method}</span></td>
            <td>{endpoint.path}</td>
            <td><span className={`badge ${endpoint.status_variant === 'info' ? 'success' : endpoint.status_variant === 'warn' ? 'warning' : endpoint.status_variant === 'error' ? 'danger' : 'neutral'}`}>{endpoint.status}</span></td>
            <td>{endpoint.avg}</td><td>{endpoint.p95}</td><td>{endpoint.req_per_min}</td><td>{endpoint.error_rate}</td>
          </tr>)}
        </tbody></table></div>
      </div>

      <div className="sh-bottom-grid">
        <div className="panel sh-panel">
          <div className="panel-head"><h2>Database</h2><span className="panel-link">Postgres</span></div>
          <div className="detail-section">
            <div className="sh-db-row"><span>Connection pool</span><span>{data.database.pool_used} / {data.database.pool_max}</span></div>
            <div className="sh-pool-track"><div className="sh-pool-fill" style={{ width: `${(data.database.pool_used / data.database.pool_max) * 100}%` }}/></div>
            <div className="sh-db-row" style={{ marginTop: 14 }}><span>Avg query time</span><span>{data.database.avg_query_ms} ms</span></div>
            <div className="sh-db-row"><span>Slowest query (5m)</span><span>{data.database.slowest_query_ms} ms</span></div>
            <div className="sh-db-row"><span>Rows in audit_logs</span><span>{data.database.audit_log_rows.toLocaleString()}</span></div>
            <div className="sh-db-row"><span>Replication lag</span><span>{data.database.replication_lag}</span></div>
            <div className="sh-db-row"><span>Last backup</span><span>{data.database.last_backup}</span></div>
          </div>
        </div>
        <div className="panel sh-panel">
          <div className="panel-head"><h2>Live event log</h2><span className="panel-link">auto-scrolling</span></div>
          <div className="table-wrap"><table><thead><tr><th style={{ width: 70 }}>Level</th><th style={{ width: 90 }}>Time</th><th>Event</th></tr></thead><tbody>
            {data.events.map((event, index) => <tr key={index}>
              <td><span className={`badge ${event.level === 'info' ? 'neutral' : event.level === 'warn' ? 'warning' : event.level === 'error' ? 'danger' : 'neutral'}`}>{event.level.toUpperCase()}</span></td>
              <td className="sh-log-time">{event.time}</td>
              <td>{event.message}</td>
            </tr>)}
          </tbody></table></div>
        </div>
      </div>
    </>}
  </Page></div>;
}

function ServerHealthChart({ chart }: { chart: ApiRequestVolumeChart }) {
  const width = 760, height = 190;
  const barCount = chart.bars.length;
  const barWidth = 10, gap = (width - barCount * barWidth) / (barCount + 1);
  const maxBar = Math.max(...chart.bars, 1);
  const maxLatency = Math.max(...chart.latency_line, 1);
  const barPoints = chart.bars.map((value, index) => ({ x: gap + index * (barWidth + gap), height: (value / maxBar) * 130 }));
  const linePoints = chart.latency_line.map((value, index) => `${gap + index * (barWidth + gap) + barWidth / 2},${20 + (1 - value / maxLatency) * 130}`).join(' ');
  return <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
    <g stroke="var(--line)" strokeWidth={1}>
      <line x1={0} y1={10} x2={width} y2={10}/><line x1={0} y1={55} x2={width} y2={55}/><line x1={0} y1={100} x2={width} y2={100}/><line x1={0} y1={145} x2={width} y2={145}/>
    </g>
    <g fill="#E9EBEF">{barPoints.map((bar, index) => <rect key={index} x={bar.x} y={165 - bar.height} width={barWidth} height={bar.height}/>)}</g>
    <polyline fill="none" stroke="var(--sh-accent)" strokeWidth={2} points={linePoints}/>
  </svg>;
}

// ---- Non-Human Identities (AccessPilot.NHIAdmin) ----
// Governs Entra service principals / Okta service apps the same way this app already governs human users: who's
// accountable for it (an owner), and whether a real, live-computed risk (no owner, expiring/expired credential)
// is currently open or has been formally, time-boxed accepted. Exclusive to AccessPilot.NHIAdmin — a plain Admin
// cannot reach this page at all, same self-escalation reasoning that already keeps SoDAdmin/SoCAdmin/ServerAdmin
// Entra-only (see backend/app/security/auth.py). `Application` rows already exist for per-user app-role
// assignment; this page treats those same rows as an inventory of the identities themselves.
interface ApiNhiOwner { user_id: string; display_name: string; email: string; }
interface ApiNhiCredential { credential_type: string; display_name: string | null; expires_at: string | null; }
interface ApiNonHumanIdentity { id: string; provider_id: string; provider_name: string; provider_type: string; external_id: string; name: string; status: string; nhi_type: string; nhi_type_overridden: boolean; credential_expires_at: string | null; credentials: ApiNhiCredential[]; owners: ApiNhiOwner[]; risk_flags: string[]; last_synced_at: string | null; }
interface ApiNhiSummary { total: number; no_owner: number; credential_expiring_soon: number; credential_expired: number; by_type: Record<string, number>; }
interface ApiNhiPermission { resource_display_name: string; role_name: string; }
const NHI_RISK_LABELS: Record<string, string> = { NO_OWNER: 'No owner', CREDENTIAL_EXPIRED: 'Credential expired', CREDENTIAL_EXPIRING_SOON: 'Credential expiring soon' };
function nhiRiskBadge(flag: string) { return <span key={flag} className={`badge ${flag === 'CREDENTIAL_EXPIRED' ? 'danger' : 'warning'}`} style={{ marginRight: 6 }}>{NHI_RISK_LABELS[flag] || flag}</span>; }
// SERVICE_PRINCIPAL/MANAGED_IDENTITY/OKTA_SERVICE_APP are auto-detected from what the connector actually reports
// (Entra's real servicePrincipalType field, or Okta's app type). AI_AGENT/API/BOT/OTHER have no reliable
// auto-detection signal from either provider today — they only ever get set by a deliberate NHIAdmin
// reclassification (the "Type" control on the detail page below), never invented by a sync.
const NHI_TYPE_LABELS: Record<string, string> = { SERVICE_PRINCIPAL: 'Entra service principal', MANAGED_IDENTITY: 'Managed identity', OKTA_SERVICE_APP: 'Okta service app', AI_AGENT: 'AI agent', API: 'API', BOT: 'Bot', OTHER: 'Other' };
const NHI_TYPE_OPTIONS: FilterOption[] = Object.entries(NHI_TYPE_LABELS).map(([value, label]) => ({ value, label }));
function nhiTypeLabel(type: string) { return NHI_TYPE_LABELS[type] || type.replace(/_/g, ' '); }
function nhiProviderLabel(identity: { provider_type: string; provider_name: string }) { return identity.provider_type === 'ENTRA' ? 'Microsoft Entra ID' : identity.provider_type === 'OKTA' ? 'Okta' : identity.provider_name; }

function NhiPage() {
  const timezone = useAppTimezone();
  const { data: identities, error, loading } = useApiResource<ApiNonHumanIdentity[]>('/api/v1/nhi');
  const { data: summary } = useApiResource<ApiNhiSummary>('/api/v1/nhi/summary');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const riskFilter = searchParams.get('risk') || '';
  const typeFilter = searchParams.get('type') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setRiskFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('risk', value); else next.delete('risk'); return next; });
  const setTypeFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('type', value); else next.delete('type'); return next; });

  const filtered = (identities || []).filter(identity =>
    (!riskFilter || identity.risk_flags.includes(riskFilter)) &&
    (!typeFilter || identity.nhi_type === typeFilter) &&
    (!search || identity.name.toLowerCase().includes(search.toLowerCase()) || identity.external_id.toLowerCase().includes(search.toLowerCase()))
  );
  const riskOptions: FilterOption[] = [{ value: 'NO_OWNER', label: 'No owner' }, { value: 'CREDENTIAL_EXPIRING_SOON', label: 'Credential expiring soon' }, { value: 'CREDENTIAL_EXPIRED', label: 'Credential expired' }];
  const typesWithCounts = NHI_TYPE_OPTIONS.map(option => ({ ...option, count: summary?.by_type[option.value] ?? 0 }));

  return <Page eyebrow="NON-HUMAN IDENTITIES" title="Non-Human Identities" subtitle="Entra service principals, Okta service apps, AI agents, bots, and APIs — who owns each one, and whether a credential-expiry or ownership risk is currently open.">
    <div className="stats" style={{ marginBottom: 18 }}>
      <div className="stat stat-link" onClick={() => { setRiskFilter(''); setTypeFilter(''); }}><div className="stat-top"><span>Total</span><span className="stat-icon"><Bot size={15} /></span></div><div className="stat-value">{summary ? summary.total : '—'}</div><div className="stat-foot">Synced across every connected provider</div></div>
      <div className="stat stat-link" onClick={() => setRiskFilter('NO_OWNER')}><div className="stat-top"><span>No owner</span><span className="stat-icon"><Users size={15} /></span></div><div className="stat-value">{summary ? summary.no_owner : '—'}</div><div className="stat-foot">Nobody accountable for these yet</div></div>
      <div className="stat stat-link" onClick={() => setRiskFilter('CREDENTIAL_EXPIRING_SOON')}><div className="stat-top"><span>Expiring soon</span><span className="stat-icon"><Clock3 size={15} /></span></div><div className="stat-value">{summary ? summary.credential_expiring_soon : '—'}</div><div className="stat-foot">Credential expires within 30 days</div></div>
      <div className="stat stat-link" onClick={() => setRiskFilter('CREDENTIAL_EXPIRED')}><div className="stat-top"><span>Expired</span><span className="stat-icon"><AlertTriangle size={15} /></span></div><div className="stat-value">{summary ? summary.credential_expired : '—'}</div><div className="stat-foot">Credential has already expired</div></div>
    </div>

    <div className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-head"><h2>By type</h2><span className="panel-link">Click a type to filter the table below</span></div>
      <div className="detail-section" style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
        {typesWithCounts.map(({ value, label, count }) => <button key={value} className="btn" style={typeFilter === value ? { borderColor: 'var(--teal)', color: 'var(--teal-dark)' } : undefined} onClick={() => setTypeFilter(typeFilter === value ? '' : value)}>{label} <span className="badge neutral" style={{ marginLeft: 6 }}>{count}</span></button>)}
      </div>
    </div>

    <TablePanel toolbar={<><Toolbar placeholder="Search by name or external ID" searchValue={search} onSearchChange={setSearch} filterLabel="All risk flags" filterValue={riskFilter} onFilterChange={setRiskFilter} filterOptions={riskOptions} /><select className="select" value={typeFilter} onChange={event => setTypeFilter(event.target.value)}><option value="">All types</option>{NHI_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></>}>
      {loading ? <div className="empty">Loading non-human identities...</div> : error ? <div className="empty">{error}</div> : !identities || identities.length === 0 ? <div className="empty">No applications synced yet — run a directory sync to populate this list.</div> : filtered.length === 0 ? <div className="empty">No non-human identities match this filter.</div> : <table><thead><tr><th>Name</th><th>Provider</th><th>Type</th><th>Status</th><th>Credential expiry</th><th>Owners</th><th>Risk</th><th></th></tr></thead><tbody>
        {filtered.map(identity => <tr key={identity.id}>
          <td><Link to={`/admin/nhi/${identity.id}`} className="user-name">{identity.name}</Link></td>
          <td><span className="badge neutral">{nhiProviderLabel(identity)}</span></td>
          <td>{nhiTypeLabel(identity.nhi_type)}{identity.nhi_type_overridden && <span title="Manually classified" style={{ marginLeft: 5, color: 'var(--muted)' }}>*</span>}</td>
          <td><StatusBadge status={identity.status} /></td>
          <td>{identity.credential_expires_at ? formatDateTime(identity.credential_expires_at, timezone) : 'Not tracked'}</td>
          <td>{identity.owners.length === 0 ? '—' : identity.owners.map(o => o.display_name).join(', ')}</td>
          <td>{identity.risk_flags.length === 0 ? <span className="badge success">None</span> : identity.risk_flags.map(nhiRiskBadge)}</td>
          <td><Link to={`/admin/nhi/${identity.id}`}><ChevronRight size={15} color="#829198" /></Link></td>
        </tr>)}
      </tbody></table>}
    </TablePanel>
    {identities && identities.length > 0 && <p className="footer-note">Showing {filtered.length} of {identities.length} non-human identities{identities.some(i => i.nhi_type_overridden) ? ' · * manually classified' : ''}</p>}
  </Page>;
}

function NhiDetailPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { id } = useParams();
  const { data: identity, error, loading, reload } = useApiResource<ApiNonHumanIdentity>(`/api/v1/nhi/${id}`);
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: activity } = useApiResource<ApiAuditLog[]>(`/api/v1/nhi/${id}/activity`);
  const { data: permissions, loading: permissionsLoading, error: permissionsError } = useApiResource<ApiNhiPermission[]>(`/api/v1/nhi/${id}/permissions`);

  const [addOwnerId, setAddOwnerId] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [acceptingRisk, setAcceptingRisk] = useState<string | null>(null);
  const [justification, setJustification] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [nhiType, setNhiType] = useState('');
  const [savingType, setSavingType] = useState(false);
  const [togglingStatus, setTogglingStatus] = useState(false);

  useEffect(() => { if (identity) setNhiType(identity.nhi_type); }, [identity]);

  if (loading) return <Page eyebrow="NON-HUMAN IDENTITIES" title="Loading..." subtitle=""><div className="empty">Loading identity...</div></Page>;
  if (error || !identity) return <Page eyebrow="NON-HUMAN IDENTITIES" title="Non-Human Identity" subtitle=""><div className="empty">{error || 'Identity not found.'}</div></Page>;

  const reclassify = async () => {
    if (nhiType === identity.nhi_type) return;
    setSavingType(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/nhi/${identity.id}/type`, { method: 'PATCH', body: JSON.stringify({ nhi_type: nhiType }) });
      if (response.ok) reload();
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to reclassify this identity.'); setNhiType(identity.nhi_type); }
    } catch { setMessage('Unable to reach the backend.'); setNhiType(identity.nhi_type); } finally { setSavingType(false); }
  };

  const toggleStatus = async () => {
    const enabling = identity.status !== 'ACTIVE';
    if (!window.confirm(`${enabling ? 'Enable' : 'Disable'} "${identity.name}" at ${nhiProviderLabel(identity)}? This is a real, immediate change made directly against the provider, not just a local flag.`)) return;
    setTogglingStatus(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/nhi/${identity.id}/${enabling ? 'enable' : 'disable'}`, { method: 'POST' });
      if (response.ok) reload();
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || `Unable to ${enabling ? 'enable' : 'disable'} this identity — the provider may not have granted the write permission this needs.`); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setTogglingStatus(false); }
  };

  const addOwner = async () => {
    if (!addOwnerId) return;
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/nhi/${identity.id}/owners`, { method: 'POST', body: JSON.stringify({ user_id: addOwnerId }) });
      if (response.ok) { reload(); setAddOwnerId(''); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to add this owner.'); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };

  const removeOwner = async (userId: string) => {
    if (!window.confirm('Remove this owner from this non-human identity?')) return;
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/nhi/${identity.id}/owners/${userId}`, { method: 'DELETE' });
      if (response.ok) reload();
      else setMessage('Unable to remove this owner.');
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };

  const acceptRisk = async (riskType: string) => {
    if (!justification.trim() || !expiresAt) { setMessage('A justification and an expiry date are required.'); return; }
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/nhi/${identity.id}/risk-exceptions`, { method: 'POST', body: JSON.stringify({ risk_type: riskType, justification: justification.trim(), expires_at: new Date(expiresAt).toISOString() }) });
      if (response.ok) { setAcceptingRisk(null); setJustification(''); setExpiresAt(''); reload(); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to accept this risk.'); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };

  const availableOwners = (users || []).filter(user => !identity.owners.some(owner => owner.user_id === user.id));

  return <Page eyebrow="NON-HUMAN IDENTITIES" title={identity.name} subtitle={`${nhiProviderLabel(identity)} · ${nhiTypeLabel(identity.nhi_type)} · external ID ${identity.external_id}`} action={<Link to="/admin/nhi" className="btn">Back to list</Link>}>
    <div className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-head"><h2>Overview</h2><div style={{ display: 'flex', alignItems: 'center', gap: 10 }}><StatusBadge status={identity.status} /><button className="btn" disabled={togglingStatus} onClick={toggleStatus}>{togglingStatus ? 'Working...' : identity.status === 'ACTIVE' ? 'Disable' : 'Enable'}</button></div></div>
      <div className="detail-section">
        <div className="key-grid">
          <div className="key"><span>Provider</span><strong>{nhiProviderLabel(identity)}</strong></div>
          <div className="key"><span>External ID</span><strong>{identity.external_id}</strong></div>
          <div className="key"><span>Credential expiry (soonest)</span><strong>{identity.credential_expires_at ? formatDateTime(identity.credential_expires_at, timezone) : 'Not tracked for this provider yet'}</strong></div>
          <div className="key"><span>Last synced</span><strong>{identity.last_synced_at ? formatDateTime(identity.last_synced_at, timezone) : 'Never'}</strong></div>
        </div>
        <div className="key" style={{ margin: '20px 0 8px' }}><span>Type</span></div>
        <p className="subtitle" style={{ marginTop: 0, marginBottom: 8 }}>{identity.nhi_type_overridden ? 'Manually classified — a directory sync will not change this.' : "Auto-detected from the provider — reclassify if it's actually an AI agent, bot, or API."}</p>
        <div style={{ display: 'flex', gap: 8 }}>
          <select className="select" style={{ flex: 1, maxWidth: 320 }} value={nhiType} onChange={event => setNhiType(event.target.value)}>{NHI_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
          <button className="btn btn-primary" disabled={savingType || nhiType === identity.nhi_type} onClick={reclassify}>{savingType ? 'Saving...' : 'Save type'}</button>
        </div>
        {message && <div className="notice" style={{ marginTop: 14 }}>{message}</div>}
      </div>
    </div>

    <div className="grid-2" style={{ marginBottom: 18 }}>
      <section className="panel">
        <div className="panel-head"><h2>Owners</h2></div>
        <div className="detail-section">
          {identity.owners.length === 0 ? <div className="empty" style={{ padding: '10px 0' }}>No owner assigned yet.</div> : identity.owners.map(owner => <div key={owner.user_id} className="user-cell" style={{ marginBottom: 8, justifyContent: 'space-between', display: 'flex' }}>
            <span><span className="user-name">{owner.display_name}</span><span className="user-email">{owner.email}</span></span>
            <button className="btn" disabled={saving} onClick={() => removeOwner(owner.user_id)}>Remove</button>
          </div>)}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <select className="select" style={{ flex: 1 }} value={addOwnerId} onChange={event => setAddOwnerId(event.target.value)}><option value="">Select a user to add as owner</option>{availableOwners.map(user => <option key={user.id} value={user.id}>{user.display_name} ({user.email})</option>)}</select>
            <button className="btn btn-primary" disabled={saving || !addOwnerId} onClick={addOwner}>Add owner</button>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Risk</h2></div>
        <div className="detail-section">
          {identity.risk_flags.length === 0 ? <div className="empty" style={{ padding: '10px 0' }}>No open risk on this identity.</div> : identity.risk_flags.map(flag => <div key={flag} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: acceptingRisk === flag ? 8 : 0 }}>
              {nhiRiskBadge(flag)}
              {acceptingRisk !== flag && <button className="btn" onClick={() => { setAcceptingRisk(flag); setMessage(''); }}>Accept risk</button>}
            </div>
            {acceptingRisk === flag && <div className="panel" style={{ padding: 14 }}>
              <label className="key" style={{ display: 'block', marginBottom: 10 }}><span>Justification</span><input className="select" style={{ width: '100%' }} value={justification} onChange={event => setJustification(event.target.value)} /></label>
              <label className="key" style={{ display: 'block', marginBottom: 10 }}><span>Accepted until</span><input className="select" type="date" style={{ width: '100%' }} value={expiresAt} onChange={event => setExpiresAt(event.target.value)} /></label>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-primary" disabled={saving} onClick={() => acceptRisk(flag)}>{saving ? 'Saving...' : 'Accept'}</button>
                <button className="btn" onClick={() => setAcceptingRisk(null)}>Cancel</button>
              </div>
            </div>}
          </div>)}
        </div>
      </section>
    </div>

    <div className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-head"><h2>Certificates &amp; secrets</h2><span className="panel-link">{identity.credentials.length} on file</span></div>
      {identity.credentials.length === 0 ? <div className="empty">No certificate or secret data tracked for this provider yet.</div> : <div className="table-wrap"><table><thead><tr><th>Type</th><th>Name</th><th>Expires</th></tr></thead><tbody>
        {identity.credentials.map((credential, index) => <tr key={index}>
          <td>{credential.credential_type === 'PASSWORD' ? 'Secret' : 'Certificate'}</td>
          <td>{credential.display_name || '—'}</td>
          <td>{credential.expires_at ? formatDateTime(credential.expires_at, timezone) : 'No expiry on file'}</td>
        </tr>)}
      </tbody></table></div>}
      <div className="detail-section"><p className="subtitle" style={{ margin: 0 }}>Adding a new certificate or secret isn't available yet — it needs this app to also sync the underlying App Registration object (not just the Service Principal), which is a separate piece of work. Rotate credentials directly in {nhiProviderLabel(identity)} for now; this list reflects what's there as of the last sync.</p></div>
    </div>

    <div className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-head"><h2>Exposed API access</h2><span className="panel-link">Live from {nhiProviderLabel(identity)}, not synced</span></div>
      {permissionsLoading ? <div className="empty">Loading...</div> : permissionsError ? <div className="empty">{permissionsError}</div> : !permissions || permissions.length === 0 ? <div className="empty">No API access grants found for this identity.</div> : <div className="table-wrap"><table><thead><tr><th>Resource</th><th>Role / scope</th></tr></thead><tbody>
        {permissions.map((permission, index) => <tr key={index}><td>{permission.resource_display_name}</td><td>{permission.role_name}</td></tr>)}
      </tbody></table></div>}
    </div>

    <div className="panel">
      <div className="panel-head"><h2>Recent activity</h2><span className="panel-link">AccessPilot actions on this identity</span></div>
      {!activity || activity.length === 0 ? <div className="empty">No activity recorded for this identity yet.</div> : activity.map(entry => <div className="activity" key={entry.id}><div className="activity-row"><span className="activity-dot" /><div className="activity-copy"><strong>{entry.action.replace(/_/g, ' ')}</strong><small>{entry.actor_display_name || 'System'} · {formatDateTime(entry.timestamp, timezone)}</small></div><StatusBadge status={entry.result} /></div></div>)}
    </div>
  </Page>;
}

// ---- Troubleshooting (drill-down from System Health) ----
// Same real, role-gated data discipline as System Health — every field here comes from a real signal (recent
// SyncError rows, the same live service cards, real audit history), never a fabricated scenario. When nothing
// is actually wrong, `incident` is null and the page shows a calm "no active incidents" state instead of
// pretending there's always something to diagnose.
interface ApiIncident { title: string; severity: string; started_label: string; affects: string[]; }
interface ApiRootCause { rank: number; title: string; detail: string; confidence: string; }
interface ApiChecklistItem { label: string; detail: string; done: boolean; }
interface ApiErrorTrendPoint { label: string; count: number; }
interface ApiDependencyNode { name: string; status: string; variant: string; }
interface ApiProviderDetail { name: string; type: string; tag: string; status: string; variant: string; detail: string; }
interface ApiTroubleshootLog { level: string; time: string; message: string; service: string; }
interface ApiTroubleshooting { incident: ApiIncident | null; root_causes: ApiRootCause[]; checklist: ApiChecklistItem[]; error_trend: ApiErrorTrendPoint[]; dependency_chain: ApiDependencyNode[]; providers: ApiProviderDetail[]; logs: ApiTroubleshootLog[]; primary_provider_id: string | null; }

function TroubleshootingDashboard() {
  const { data, loading, error, reload } = useApiResource<ApiTroubleshooting>('/api/v1/server-health/troubleshooting');
  const [logFilter, setLogFilter] = useState('All services');
  useEffect(() => { const id = setInterval(reload, 20000); return () => clearInterval(id); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const copyDiagnostics = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(data, null, 2)); } catch { /* clipboard unavailable — silently ignore, nothing else to do */ }
  };

  const serviceOptions = ['All services', ...Array.from(new Set((data?.logs || []).map(log => log.service)))];
  const visibleLogs = (data?.logs || []).filter(log => logFilter === 'All services' || log.service === logFilter);

  return <div className="troubleshoot-page"><Page eyebrow="SYSTEM HEALTH" title="Troubleshooting" subtitle="Diagnose and resolve active issues, drilled down from System Health." action={<Link to="/admin/server-health" className="btn">← Back to System Health</Link>}>
    {loading && !data && <div className="empty">Loading diagnostics...</div>}
    {error && <div className="empty">{error}</div>}
    {data && <>
      {data.incident ? <div className={`ts-incident-banner ts-${data.incident.severity}`}>
        <div className="ts-incident-left">
          <div className="ts-incident-icon">!</div>
          <div><div className="ts-incident-title">{data.incident.title}</div><div className="ts-incident-meta">Started {data.incident.started_label} · affects: {data.incident.affects.join(', ')}</div></div>
        </div>
      </div> : <div className="ts-incident-banner ts-ok"><div className="ts-incident-left"><div className="ts-incident-icon">✓</div><div><div className="ts-incident-title">No active incidents</div><div className="ts-incident-meta">Every service card on System Health is currently healthy.</div></div></div></div>}

      <div className="ts-grid-2">
        <div className="panel ts-panel">
          <div className="panel-head"><h2>Likely root cause</h2><span className="panel-meta">ranked</span></div>
          {data.root_causes.length === 0 ? <div className="empty">Nothing to diagnose right now.</div> : data.root_causes.map(cause => <div key={cause.rank} className="ts-cause-row">
            <div className={`ts-cause-rank ${cause.rank === 1 ? 'ts-top' : ''}`}>{cause.rank}</div>
            <div className="ts-cause-text"><b>{cause.title}</b> — {cause.detail}<div className="ts-cause-conf">Confidence: {cause.confidence}</div></div>
          </div>)}
        </div>
        <div className="panel ts-panel">
          <div className="panel-head"><h2>Diagnostic checklist</h2><span className="panel-meta">{data.checklist.filter(item => item.done).length} of {data.checklist.length} done</span></div>
          {data.checklist.length === 0 ? <div className="empty">Nothing to check right now.</div> : data.checklist.map(item => <div key={item.label} className="ts-check-item">
            <div className={`ts-check-box ${item.done ? 'ts-done' : ''}`}/>
            <div><div className={`ts-check-label ${item.done ? 'ts-done' : ''}`}>{item.label}</div><div className="ts-check-sub">{item.detail}</div></div>
          </div>)}
          <div className="section-label" style={{ marginTop: 18 }}>QUICK ACTIONS</div>
          <div className="ts-qa-grid">
            <div className="ts-qa-btn"><div className="ts-qa-title">↻ Retry Graph connection</div><div className="ts-qa-sub">Requires an Admin — Admin → Providers → Test connection</div></div>
            <div className="ts-qa-btn"><div className="ts-qa-title">▶ Run manual sync</div><div className="ts-qa-sub">Requires an Admin — Admin → Sync → Sync now</div></div>
            <div className="ts-qa-btn" style={{ cursor: 'pointer' }} onClick={() => void copyDiagnostics()}><div className="ts-qa-title">📋 Copy diagnostics</div><div className="ts-qa-sub">Copies this page's real data as JSON</div></div>
          </div>
        </div>
      </div>

      <div className="panel ts-panel" style={{ marginBottom: 14 }}>
        <div className="panel-head"><h2>Error rate — last 30 min</h2><span className="panel-meta">real SyncError timestamps</span></div>
        <TroubleshootTrendChart points={data.error_trend}/>
        <div className="section-label" style={{ marginTop: 14 }}>DEPENDENCY CHAIN</div>
        <div className="ts-chain-row">{data.dependency_chain.map((node, index) => <Fragment key={node.name}>
          {index > 0 && <div className="ts-chain-arrow">→</div>}
          <div className="ts-chain-node"><div className={`ts-chain-pill ts-${node.variant}`}><div className="ts-chain-name">{node.name}</div></div><div className={`ts-chain-status ts-${node.variant}`}>{node.status}</div></div>
        </Fragment>)}</div>
      </div>

      <div className="ts-grid-2">
        <div className="panel ts-panel">
          <div className="panel-head"><h2>Identity providers</h2><span className="panel-meta">portal authentication</span></div>
          {data.providers.map(provider => <div key={provider.name} className="ts-idp-row">
            <div className="ts-idp-left"><div className={`ts-idp-dot ts-${provider.variant}`}/><div><div className="ts-idp-name">{provider.name} <span className={`ts-idp-tag ts-${provider.variant}`}>{provider.tag}</span></div><div className="ts-idp-sub">{provider.type}</div></div></div>
            <div className="ts-idp-right"><div className={`ts-idp-status ts-${provider.variant}`}>{provider.status}</div><div className="ts-idp-detail">{provider.detail}</div></div>
          </div>)}
        </div>
        <div className="panel ts-panel">
          <div className="panel-head"><h2>Correlated logs</h2><span className="panel-meta">server-generated only</span></div>
          <div className="ts-filter-row">{serviceOptions.map(option => <span key={option} className={`ts-filter-pill ${logFilter === option ? 'ts-active' : ''}`} onClick={() => setLogFilter(option)}>{option}</span>)}</div>
          <div className="table-wrap"><table className="ts-log-table"><thead><tr><th style={{ width: 60 }}>Level</th><th style={{ width: 80 }}>Time</th><th>Event</th></tr></thead><tbody>
            {visibleLogs.length === 0 ? <tr><td colSpan={3} className="empty">No matching log entries.</td></tr> : visibleLogs.map((log, index) => <tr key={index} className={log.level === 'error' ? 'ts-hot' : ''}>
              <td><span className={`ts-log-level ts-${log.level}`}>{log.level.toUpperCase()}</span></td>
              <td className="ts-log-time">{log.time}</td>
              <td>{log.message}</td>
            </tr>)}
          </tbody></table></div>
        </div>
      </div>
    </>}
  </Page></div>;
}

function TroubleshootTrendChart({ points }: { points: ApiErrorTrendPoint[] }) {
  const width = 760, height = 120;
  const max = Math.max(...points.map(point => point.count), 1);
  const step = width / Math.max(points.length - 1, 1);
  const linePoints = points.map((point, index) => `${index * step},${10 + (1 - point.count / max) * 90}`).join(' ');
  return <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
    <g stroke="var(--ts-border)" strokeWidth={1}><line x1={0} y1={10} x2={width} y2={10}/><line x1={0} y1={60} x2={width} y2={60}/><line x1={0} y1={100} x2={width} y2={100}/></g>
    <polyline fill="none" stroke={points.some(point => point.count > 0) ? 'var(--crit)' : 'var(--ts-faint)'} strokeWidth={2} points={linePoints}/>
  </svg>;
}

function AdminOnly({ role, children }: { role: Role; children: React.ReactNode }) { return role === 'admin' ? children : <Navigate to="/dashboard" replace />; }
function Shell({ role, setRole, children }: { role: Role; setRole: (r: Role) => void; children: React.ReactNode }) {
  const location = useLocation(); const navigate = useNavigate();
  const auth = useAuth();
  const timezone = useAppTimezone();
  const branding = useBranding();
  const visible = nav.filter(item => item.roles.includes(role) || (item.extra === 'sod' && auth.isSodAdmin) || (item.extra === 'soc' && auth.isSocAdmin) || (item.extra === 'server' && auth.isServerAdmin) || (item.extra === 'nhi' && auth.isNhiAdmin));
  const path = location.pathname;
  const signedIn = Boolean(auth.account) || auth.breakglassActive;
  const seesSodBell = auth.isSodAdmin;
  const { data: sodNotifications, reload: reloadSodNotifications } = useApiResource<ApiSodNotification[]>('/api/v1/sod/notifications', seesSodBell);
  // Every signed-in user's own assignment/approval notifications (see backend/app/services/notifications.py) —
  // same dropdown, same style, as the org-wide notification experience, distinct from the SoD-only feed above.
  const { data: myNotifications, reload: reloadMyNotifications } = useApiResource<ApiNotification[]>('/api/v1/notifications', signedIn);
  const unreadSodCount = (sodNotifications || []).filter(n => !n.read_at && !n.resolved_at).length;
  const unreadMyCount = (myNotifications || []).filter(n => !n.read_at).length;
  const unreadTotal = unreadSodCount + unreadMyCount;
  // Live: the Bell badge auto-refreshes so new notifications are visible from anywhere in the app, not just
  // after opening a specific page. The personal feed is a plain per-user DB read (cheap), so it polls every 10s
  // — short enough to feel close to live without needing real push infrastructure (no WebSocket/SSE exists in
  // this app). The SoD feed stays at 60s deliberately — it re-runs a full reconciliation pass on every call, a
  // real ~15s Graph-read cost when ROLE/APPLICATION rules exist (see docs/19_SOD_ENGINE.md §11/§16), so polling
  // it anywhere near this fast would leave that scan running almost continuously.
  useEffect(() => {
    if (!signedIn) return;
    const id = setInterval(() => reloadMyNotifications(), 10000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn]);
  useEffect(() => {
    if (!seesSodBell) return;
    const id = setInterval(() => reloadSodNotifications(), 60000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seesSodBell]);
  const [notifOpen, setNotifOpen] = useState(false);
  const notifRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!notifOpen) return;
    const onClickOutside = (event: MouseEvent) => { if (notifRef.current && !notifRef.current.contains(event.target as Node)) setNotifOpen(false); };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [notifOpen]);
  const toggleNotif = () => { setNotifOpen(open => !open); if (!notifOpen) { reloadMyNotifications(); if (seesSodBell) reloadSodNotifications(); } };
  const markMyNotifRead = async (id: string) => { await auth.apiRequest(`/api/v1/notifications/${id}/read`, { method: 'POST' }); reloadMyNotifications(); };
  const markSodNotifRead = async (id: string) => { await auth.apiRequest(`/api/v1/sod/notifications/${id}/read`, { method: 'POST' }); reloadSodNotifications(); };
  const markAllNotifRead = async () => { await Promise.all([auth.apiRequest('/api/v1/notifications/read-all', { method: 'POST' }), ...(seesSodBell ? [auth.apiRequest('/api/v1/sod/notifications/read-all', { method: 'POST' })] : [])]); reloadMyNotifications(); if (seesSodBell) reloadSodNotifications(); };
  type MergedNotif = { key: string; message: string; created_at: string; unread: boolean; link: string | null; onMarkRead: (() => void) | null };
  const mergedNotifs: MergedNotif[] = [
    ...(myNotifications || []).map(n => ({ key: `my-${n.id}`, message: n.message, created_at: n.created_at, unread: !n.read_at, link: n.link, onMarkRead: n.read_at ? null : () => markMyNotifRead(n.id) })),
    ...(seesSodBell ? (sodNotifications || []).map(n => ({ key: `sod-${n.id}`, message: n.message, created_at: n.created_at, unread: !n.read_at && !n.resolved_at, link: '/admin/sod/configuration', onMarkRead: (!n.read_at && !n.resolved_at) ? () => markSodNotifRead(n.id) : null })) : []),
  ].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).slice(0, 10);
  const notifBell = signedIn ? <div className="notif-bell-wrap" ref={notifRef}>
    <button type="button" className="notif-bell-btn" aria-label="Notifications" onClick={toggleNotif}>
      <Bell size={17} color="#718088"/>
      {unreadTotal > 0 && <span style={{position:'absolute',top:-2,right:-2,background:'#c0392b',color:'#fff',borderRadius:9,fontSize:10,fontWeight:700,padding:'0 5px',lineHeight:'16px',minWidth:16,textAlign:'center'}}>{unreadTotal}</span>}
    </button>
    {notifOpen && <div className="notif-dropdown">
      <div className="notif-dropdown-head"><h3>Notifications</h3>{unreadTotal > 0 && <span className="badge danger">{unreadTotal} unread</span>}</div>
      <div className="notif-dropdown-list">
        {mergedNotifs.length === 0 ? <div className="notif-dropdown-empty">Nothing to report yet.</div> : mergedNotifs.map(n => <div key={n.key} className={`notif-item ${n.unread ? 'unread' : ''}`}>
          <div className="notif-item-top"><span className="notif-item-message">{n.message}</span><span className="notif-item-time">{new Date(n.created_at).toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:timezone})}</span></div>
          <div className="notif-item-actions">
            {n.onMarkRead ? <button type="button" onClick={n.onMarkRead}>Mark read</button> : <span className="footer-note" style={{margin:0}}>Read</span>}
            {n.link && <Link to={n.link} onClick={() => setNotifOpen(false)}>View</Link>}
          </div>
        </div>)}
      </div>
      {(seesSodBell || unreadTotal > 0) && <div className="notif-dropdown-foot">
        {seesSodBell ? <Link to="/admin/sod/configuration" onClick={() => setNotifOpen(false)}>Open SoD Configuration</Link> : <span/>}
        {unreadTotal > 0 && <button type="button" onClick={markAllNotifRead}>Mark all as read</button>}
      </div>}
    </div>}
  </div> : <Bell size={17} color="#718088"/>;
  return <div className="app"><aside className="sidebar"><Link to="/dashboard" className="brand"><span className="brand-mark"><img src={branding?.internal_logo || logo} alt="AccessPilot" /></span> AccessPilot</Link>{visible.map((item, index) => { const I = item.icon; const previous = visible[index - 1]; return <div key={item.to}>{item.section && item.section !== previous?.section && <div className="nav-label">{item.section}</div>}<Link className={`nav-item ${path === item.to || (item.to !== '/dashboard' && path.startsWith(item.to)) ? 'active' : ''}`} to={item.to}><I />{item.label}</Link></div> })}<div className="sidebar-foot"><div>ACCESSPILOT CONSOLE</div><div style={{marginTop:5}}>v0.1.0 · Mock environment</div><div className="sidebar-credit">by <span>{branding?.powered_by_text || 'Clover‑X'}</span></div></div></aside><main className="main"><header className="topbar"><button className="mobile-menu" aria-label="Open navigation"><Menu size={20}/></button><div className="crumb">Workspace / <strong>{role === 'admin' ? 'Administration' : 'Self-service'}</strong></div><div className="top-actions">{auth.authConfigured ? <button className="btn" onClick={() => (auth.account || auth.breakglassActive) ? auth.signOut() : auth.signIn()}>{(auth.account || auth.breakglassActive) ? 'Sign out' : 'Sign in'}</button> : <div className="role-switch" aria-label="Development role switcher"><button className={role === 'user' ? 'active' : ''} onClick={() => { setRole('user'); navigate('/dashboard'); }}>User</button><button className={role === 'admin' ? 'active' : ''} onClick={() => { setRole('admin'); navigate('/dashboard'); }}>Admin</button></div>}{notifBell}<div className="profile"><span>{auth.account?.name || (auth.breakglassActive ? `Break-Glass (${auth.breakglassUsername})` : currentUser.name)}</span><span className="avatar">{currentUser.initials}</span></div></div></header>{children}</main></div>;
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
function ActivationTimelineChart({ series, yAxisLabel = 'Users activated', unitLabel = 'user', tooltipSuffix = 'activated', onPointClick }: { series: { date: string; count: number }[]; yAxisLabel?: string; unitLabel?: string; tooltipSuffix?: string; onPointClick?: (point: { date: string; count: number }) => void }) {
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
      <text x={14} y={marginTop + plotHeight / 2} textAnchor="middle" fontSize="11" fontWeight={700} fill="#687782" transform={`rotate(-90 14 ${marginTop + plotHeight / 2})`}>{yAxisLabel}</text>
      <path d={linePath} fill="none" stroke="#087f82" strokeWidth={2}/>
      {hoverIndex !== null && <line x1={points[hoverIndex].x} y1={marginTop} x2={points[hoverIndex].x} y2={height - marginBottom} stroke="#087f82" strokeDasharray="3 3" opacity={0.4}/>}
      {points.map((p, i) => <circle key={p.date} cx={p.x} cy={p.y} r={i === hoverIndex ? 6 : 4} fill="#fff" stroke="#087f82" strokeWidth={2} style={{cursor: onPointClick ? 'pointer' : 'default'}} onMouseEnter={() => setHoverIndex(i)} onMouseLeave={() => setHoverIndex(null)} onClick={() => onPointClick?.(p)}/>)}
    </svg>
    {hovered && <div className="notice" style={{position:'absolute', top:0, left:`${(hovered.x / width) * 100}%`, transform:'translate(-50%, -100%)', whiteSpace:'nowrap', pointerEvents:'none', padding:'6px 10px'}}>
      <strong>{new Date(hovered.date).toLocaleDateString(undefined, { month:'short', day:'numeric' })}</strong>: {hovered.count} {unitLabel}{hovered.count === 1 ? '' : 's'} {tooltipSuffix}{onPointClick ? ' — click to see details' : ''}
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
  const timezone = useAppTimezone();
  const navigate = useNavigate();
  const { data: dashboard, error, loading, reload: reloadDashboard } = useApiResource<DashboardAdmin>('/api/v1/dashboard/admin', admin);
  const { data: recentAudit, reload: reloadAudit } = useApiResource<ApiAuditLog[]>('/api/v1/audit-logs', admin);
  const { data: timeline, reload: reloadTimeline } = useApiResource<ApiActivationTimeline>('/api/v1/dashboard/privileged-role-activations?days=30', admin);
  const { data: segments, reload: reloadSegments } = useApiResource<ApiUserAccessSegments>('/api/v1/dashboard/user-access-segments', admin);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const { data: segmentMembers, loading: membersLoading } = useApiResource<ApiSegmentMember[]>(`/api/v1/dashboard/user-access-segments/${selectedSegment}`, Boolean(selectedSegment));
  const segmentTitle = selectedSegment === 'permanent-active' ? 'Permanent & Active' : selectedSegment === 'eligible' ? 'Eligible (not yet activated)' : '';
  const { data: myAssignments, reload: reloadMine } = useApiResource<ApiAssignment[]>('/api/v1/assignments/mine', !admin);
  const seesSod = auth.isSodAdmin;
  const { data: sodViolations } = useApiResource<ApiSodViolation[]>('/api/v1/sod/violations', seesSod);
  const { data: sodActivity } = useApiResource<ApiSodActivityEntry[]>('/api/v1/sod/activity', seesSod);
  const greetingName = auth.account?.name || (auth.authConfigured ? '' : currentUser.name);
  const lastSyncLabel = dashboard?.lastSync?.completedAt ? formatDateTime(dashboard.lastSync.completedAt, timezone) : dashboard?.lastSync ? 'In progress' : 'Never synced';

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
          {sodActivity.slice(0,3).map(entry => <div key={entry.id} className="activity-row" style={{padding:'6px 0'}}><span className="activity-dot"/><div className="activity-copy"><strong>{entry.action.replace(/_/g,' ')}</strong><small>{entry.actor_display_name || 'System'}{entry.target_user_display_name ? ` · ${entry.target_user_display_name}` : ''} · {summarizeSodActivity(entry)} · {formatDateTime(entry.timestamp, timezone)}</small></div></div>)}
        </div>}
      </div>
    </div>}
    {selectedSegment && <div className="overlay-backdrop" onClick={() => setSelectedSegment(null)}>
      <div className="overlay-card" onClick={event => event.stopPropagation()}>
        <div className="panel-head"><h2>{segmentTitle}</h2><button type="button" className="btn" aria-label="Close" onClick={() => setSelectedSegment(null)}><X size={14}/></button></div>
        <div className="table-wrap">{membersLoading ? <div className="empty">Loading users...</div> : !segmentMembers || segmentMembers.length === 0 ? <div className="empty">No users in this segment.</div> : <table><thead><tr><th>User</th><th>Email</th></tr></thead><tbody>{segmentMembers.map(m => <tr key={m.id}><td className="user-name">{m.display_name}</td><td>{m.email}</td></tr>)}</tbody></table>}</div>
      </div>
    </div>}
    <div className="grid-2"><section className="panel"><div className="panel-head"><h2>{admin ? 'Recent access requests' : 'Recent activity'}</h2><Link to={admin ? '/admin/audit' : '/my-requests'} className="panel-link">View all <ChevronRight size={12}/></Link></div>{admin ? (!recentAudit || recentAudit.length === 0 ? <div className="empty">No recent activity.</div> : recentAudit.slice(0,6).map(entry => <div className="activity" key={entry.id}><div className="activity-row"><span className="activity-dot"/><div className="activity-copy"><strong>{entry.action}</strong><small>{entry.actor_display_name || 'System'}{entry.target_user_display_name ? ` · ${entry.target_user_display_name}` : ''} · {formatDateTime(entry.timestamp, timezone)}</small></div><StatusBadge status={entry.result}/></div></div>)) : (myRecentActivity.length === 0 ? <div className="empty">No activity yet.</div> : myRecentActivity.map(item => <div className="activity" key={item.id}><div className="activity-row"><span className="activity-dot"/><div className="activity-copy"><strong>{item.resource_display_name || item.resource_type}{item.package_name ? ` (${item.package_name})` : ''}</strong><small>{item.resource_type} · {formatDateTime(item.activated_at || item.created_at, timezone)}</small></div><StatusBadge status={item.status}/></div></div>))}</section><section className="panel"><div className="panel-head"><h2>{admin ? 'Provider status' : 'Current active access'}</h2>{admin && <StatusBadge status={dashboard?.provider?.status || 'NOT_CONFIGURED'}/>}</div>{admin ? <div className="detail-section"><div className="user-cell"><span className="avatar" style={{background:'#e4f1f5',color:'#33758a'}}><Cloud size={15}/></span><div><div className="user-name">{dashboard?.provider?.name || 'No provider configured'}</div><div className="user-email">{dashboard?.provider ? `${dashboard.provider.status} · Last sync ${lastSyncLabel}` : 'Configure a provider to begin syncing.'}</div></div></div><div className="key-grid" style={{marginTop:24}}><div className="key"><span>Users synced</span><strong>{dashboard ? dashboard.users : '—'}</strong></div><div className="key"><span>Groups synced</span><strong>{dashboard ? dashboard.groups : '—'}</strong></div><div className="key"><span>Directory roles</span><strong>{dashboard ? dashboard.roles : '—'}</strong></div><div className="key"><span>Last sync</span><strong>{lastSyncLabel}</strong></div></div></div> : (myActiveAccess.length === 0 ? <div className="detail-section"><div className="empty">No active access right now. Check My Access for anything eligible to activate.</div></div> : <div className="table-wrap"><table><thead><tr><th>Resource</th><th>Type</th><th>Expires</th></tr></thead><tbody>{myActiveAccess.map(item => <tr key={item.id}><td className="user-name">{item.resource_display_name || item.resource_type}{item.package_name ? <div className="user-email">{item.package_name}</div> : null}</td><td>{item.resource_type}</td><td>{item.expiration_time ? formatDateTime(item.expiration_time, timezone) : 'Permanent'}</td></tr>)}</tbody></table></div>)}</section></div></Page>; }
function StatusBadge({ status }: { status: string }) { const cls = ['APPROVED','ACTIVE','COMPLETED','CONNECTED','ELIGIBLE','SUCCESS','Healthy','Active'].includes(status) ? 'success' : ['PENDING','PENDING_APPROVAL','SCHEDULED','RUNNING','PARTIAL','Medium','AUTO_REVOKED'].includes(status) ? 'warning' : ['REJECTED','EXPIRED','REVOKED','FAILED','Disabled','High'].includes(status) ? 'danger' : 'neutral'; return <span className={`badge ${cls}`}>{status}</span>; }
function TablePanel({ children, toolbar }: { children: React.ReactNode; toolbar?: React.ReactNode }) { return <><div className="toolbar">{toolbar}</div><section className="panel"><div className="table-wrap">{children}</div></section></>; }
interface FilterOption { value: string; label: string; }
function Toolbar({ placeholder = 'Search', searchValue, onSearchChange, filterLabel = 'All statuses', filterValue = '', onFilterChange, filterOptions }: { placeholder?: string; searchValue?: string; onSearchChange?: (value: string) => void; filterLabel?: string; filterValue?: string; onFilterChange?: (value: string) => void; filterOptions?: FilterOption[]; }) {
  return <><div className="toolbar-left">{onSearchChange && <div className="search-box"><Search size={15}/><input className="search" placeholder={placeholder} value={searchValue ?? ''} onChange={event => onSearchChange(event.target.value)}/></div>}{filterOptions && <select className="select" value={filterValue} onChange={event => onFilterChange?.(event.target.value)}><option value="">{filterLabel}</option>{filterOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</div></>;
}
function initialsFor(name: string) { const parts = name.trim().split(/\s+/); return ((parts[0]?.[0] || '') + (parts[parts.length - 1]?.[0] || '')).toUpperCase() || '?'; }
// Plain toLocaleString()/toLocaleDateString() renders a time with no indication of which timezone it's in —
// confusing for SoD exception expiry specifically, since the backend's own notification text always states UTC
// explicitly (see services/sod.py) while an unlabeled local-time display could easily be mistaken for the same
// moment shown differently. Always showing the zone (via Intl's timeZoneName) makes either representation
// self-explanatory instead of ambiguous, without forcing every display onto the same one.
// dateStyle/timeStyle cannot be combined with timeZoneName or timeZone per the Intl spec — some engines throw
// "Invalid option : option" for it (confirmed live, crashed the whole SoD page). Explicit component fields
// (year/month/day/hour/minute) are the valid way to get a zone label alongside a formatted date and time.
// Every display-only date/time render in the app goes through these two so every viewer sees the same admin-
// configured timezone (useAppTimezone()) regardless of their own browser/OS locale — inputs are unaffected,
// they still use each user's own browser-local time.
function formatWithZone(iso: string | number, timezone: string): string { return new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short', timeZone: timezone }); }
function formatDateTime(iso: string | number, timezone: string): string { return new Date(iso).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: timezone }); }
function formatDate(iso: string | number, timezone: string): string { return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', timeZone: timezone }); }
function formatTime(iso: string | number, timezone: string): string { return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', timeZone: timezone }); }
function sourceLabel(user: ApiUser, providers: ApiProvider[] | null): { label: string; detail: string } {
  const provider = providers?.find(p => p.id === user.provider_id);
  const connector = provider ? `${provider.provider_type === 'ENTRA' ? 'Microsoft Entra ID' : provider.provider_type === 'OKTA' ? 'Okta' : provider.name} · ${user.external_id}` : user.external_id;
  if (user.source === 'CSV_ONBOARDING') return { label: 'CSV Onboarding', detail: `Employee ID ${user.employee_id} — ${connector}` };
  return { label: provider?.provider_type === 'ENTRA' ? 'Microsoft Entra ID' : provider?.provider_type === 'OKTA' ? 'Okta' : provider?.name || 'Connector', detail: connector };
}
function UsersPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
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
    <TablePanel toolbar={<Toolbar placeholder="Search users by name or email" searchValue={search} onSearchChange={setSearch} filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={statusOptions}/>}>{loading ? <div className="empty">Loading users...</div> : error ? <div className="empty">{error}</div> : !users || users.length === 0 ? <div className="empty">No users found.</div> : filteredUsers.length === 0 ? <div className="empty">No users match this filter.</div> : <table><thead><tr><th>User</th><th>Department</th><th>Job title</th><th>Source</th><th>Status</th><th>Last synced</th><th></th></tr></thead><tbody>{filteredUsers.map(u => { const source = sourceLabel(u, providers); return <tr key={u.id}><td><Link to={`/admin/users/${u.id}`} className="user-cell"><span className="avatar">{initialsFor(u.display_name)}</span><span><span className="user-name">{u.display_name}</span><span className="user-email">{u.email}</span></span></Link></td><td>{u.department || '—'}</td><td>{u.job_title || '—'}</td><td><span className="badge neutral" title={source.detail}>{source.label}</span></td><td><StatusBadge status={u.status}/></td><td>{u.last_synced_at ? formatDateTime(u.last_synced_at, timezone) : 'Never'}</td><td><ChevronRight size={15} color="#829198"/></td></tr>; })}</tbody></table>}</TablePanel>
    {users && users.length > 0 && <p className="footer-note">Showing {filteredUsers.length} of {users.length} users</p>}
  </Page>;
}
function OrgChartNode({ node, byManager, collapsed, toggle, depth }: { node: ApiHierarchyNode; byManager: Map<string, ApiHierarchyNode[]>; collapsed: Set<string>; toggle: (id: string) => void; depth: number }) {
  const children = byManager.get(node.id) || [];
  const isCollapsed = collapsed.has(node.id);
  return <li>
    <div className="user-cell" style={{padding:'6px 0'}}>
      {children.length > 0 ? <button type="button" className="btn" aria-label={isCollapsed ? 'Expand' : 'Collapse'} onClick={() => toggle(node.id)} style={{padding:'2px 7px',marginRight:8}}>{isCollapsed ? <ChevronRight size={12}/> : <ChevronLeft size={12} style={{transform:'rotate(-90deg)'}}/>}</button> : <span style={{display:'inline-block',width:28}}/>}
      <span className="avatar" style={{width:30,height:30,fontSize:12}}>{initialsFor(node.display_name)}</span>
      <span><Link to={`/admin/users/${node.id}`} className="user-name">{node.display_name}</Link><span className="user-email">{node.email}{node.employee_category ? ` · ${node.employee_category === 'MANAGER' ? 'Manager' : 'Employee'}` : ''}{children.length > 0 ? ` · ${children.length} direct report${children.length === 1 ? '' : 's'}` : ''}</span></span>
    </div>
    {!isCollapsed && children.length > 0 && <ul style={{listStyle:'none',margin:0,paddingLeft:36,borderLeft:'1px dashed #d5dde0'}}>{children.map(child => <OrgChartNode key={child.id} node={child} byManager={byManager} collapsed={collapsed} toggle={toggle} depth={depth + 1}/>)}</ul>}
  </li>;
}
function OrgChartPage() {
  const { data: nodes, loading, error, reload } = useApiResource<ApiHierarchyNode[]>('/api/v1/users/hierarchy-tree');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (id: string) => setCollapsed(prev => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const byManager = useMemo(() => {
    const map = new Map<string, ApiHierarchyNode[]>();
    (nodes || []).forEach(node => { if (node.manager_id) { if (!map.has(node.manager_id)) map.set(node.manager_id, []); map.get(node.manager_id)!.push(node); } });
    return map;
  }, [nodes]);
  // A "root" is anyone with no manager. Among those, a tagged Manager who nonetheless has direct reports is a
  // real top-of-tree node; everyone else with no manager AND no reports is truly unassigned, shown separately
  // rather than silently mixed into the tree as a false root.
  const managedIds = useMemo(() => new Set((nodes || []).filter(n => n.manager_id).map(n => n.id)), [nodes]);
  const roots = (nodes || []).filter(n => !n.manager_id && (byManager.has(n.id) || n.employee_category === 'MANAGER'));
  const unassigned = (nodes || []).filter(n => !n.manager_id && !byManager.has(n.id) && n.employee_category !== 'MANAGER');
  return <Page eyebrow="ADMINISTRATION" title="Org Chart" subtitle="The reporting hierarchy built from each user's Role &amp; Manager tag — AccessPilot-internal only, never synced to/from Entra." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>Reporting hierarchy</h2></div>
      <div className="detail-section">
        {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : roots.length === 0 ? <div className="empty">No one is tagged as a Manager with reports yet — tag users from their own detail page.</div> : <ul style={{listStyle:'none',margin:0,padding:0}}>{roots.map(root => <OrgChartNode key={root.id} node={root} byManager={byManager} collapsed={collapsed} toggle={toggle} depth={0}/>)}</ul>}
      </div>
    </section>
    <section className="panel">
      <div className="panel-head"><h2>Unassigned</h2><span className="panel-link">{unassigned.length}</span></div>
      <div className="detail-section">
        {unassigned.length === 0 ? <div className="empty">Everyone is placed in the hierarchy above.</div> : <div className="table-wrap"><table><thead><tr><th>User</th><th>Tag</th></tr></thead><tbody>{unassigned.map(u => <tr key={u.id}><td><Link to={`/admin/users/${u.id}`} className="user-name">{u.display_name}</Link></td><td>{u.employee_category === 'EMPLOYEE' ? 'Employee' : 'Unclassified'}</td></tr>)}</tbody></table></div>}
      </div>
    </section>
  </Page>;
}
const EMPLOYMENT_TYPES = [{ value: 'EMPLOYEE', label: 'Employee' }, { value: 'CONTRACTOR', label: 'Contractor' }, { value: 'INTERN', label: 'Intern' }, { value: 'OTHER', label: 'Other' }];
// Leaver date + employment type for one person (the type can scope a leaver policy), and the manual
// "Start leaver process now" button. The leaver date is stored in AccessPilot only; on that date (at the policy's
// time) the leaver policy runs automatically: access revoked, accounts disabled in every IdP, and so on.
interface ApiLeaverOverview { status: string; leaver_processed_at: string | null; leaver_date: string | null; policy_name: string | null; pending_leaver_request: ApiLeaverRequest | null; pending_reenable_request: ApiReenableRequest | null; recent_events: ApiLifecycleEvent[]; }
// A dedicated Leaver component for the User Detail page: the date/policy fields, the current state of any pending
// manual-start or re-enable request (decidable right here, so it is never invisible), and a short recent-activity
// log — so a second click on "Start leaver process" never again looks like a silent no-op.
function UserLeaverPanel({ user, onChanged }: { user: ApiUser; onChanged: () => void }) {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: overview, reload: reloadOverview } = useApiResource<ApiLeaverOverview>(`/api/v1/lifecycle/people/${user.id}/leaver-overview`);
  const [leaverDate, setLeaverDate] = useState(user.leaver_date || '');
  const [employmentType, setEmploymentType] = useState(user.employment_type || '');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { setLeaverDate(user.leaver_date || ''); setEmploymentType(user.employment_type || ''); }, [user.leaver_date, user.employment_type]);
  if (user.account_type !== 'NORMAL') return null;
  const refresh = () => { reloadOverview(); onChanged(); };
  const save = async () => {
    setBusy(true); setMessage('');
    try {
      const payload: Record<string, unknown> = {};
      if (leaverDate) payload.leaver_date = leaverDate; else payload.clear_leaver_date = true;
      if (employmentType) payload.employment_type = employmentType; else payload.clear_employment_type = true;
      const response = await auth.apiRequest(`/api/v1/lifecycle/people/${user.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? 'Saved.' : (body?.error?.message || 'Unable to save this.'));
      if (response.ok) refresh();
    } catch { setMessage('Unable to reach the backend.'); } finally { setBusy(false); }
  };
  // A proper inline form, not a browser prompt() dialog — window.prompt/confirm silently return null with no
  // dialog at all in some embedded/webview browser contexts, which made this button appear to do nothing.
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [justification, setJustification] = useState('');
  const leaveNow = async () => {
    if (justification.trim().length < 10) { setMessage('Enter a justification of at least 10 characters.'); return; }
    setBusy(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/lifecycle/people/${user.id}/leave-now`, { method: 'POST', body: JSON.stringify({ justification: justification.trim() }) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? `Accounts disabled (${body.accounts_note}). Waiting for approval from ${(body.approvers || []).join(', ') || 'an admin'}. The leaver process starts once it is approved.` : (body?.error?.message || 'Unable to start the leaver process.'));
      if (response.ok) { setConfirmingLeave(false); setJustification(''); }
      refresh();
    } catch { setMessage('Unable to reach the backend.'); } finally { setBusy(false); }
  };
  const [decideNote, setDecideNote] = useState('');
  const [decideBusy, setDecideBusy] = useState(false);
  const decideLeaverRequest = async (approve: boolean) => {
    if (!overview?.pending_leaver_request) return;
    setDecideBusy(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/lifecycle/leaver-requests/${overview.pending_leaver_request.id}/decision`, { method: 'POST', body: JSON.stringify({ approve, note: decideNote.trim() || undefined }) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? (body?.outcome || 'Done.') : (body?.error?.message || 'Unable to record the decision.'));
      if (response.ok) setDecideNote('');
      refresh();
    } catch { setMessage('Unable to reach the backend.'); } finally { setDecideBusy(false); }
  };
  const decideReenableRequest = async (approve: boolean) => {
    if (!overview?.pending_reenable_request) return;
    setDecideBusy(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/lifecycle/reenable-requests/${overview.pending_reenable_request.id}/decision`, { method: 'POST', body: JSON.stringify({ approve, note: decideNote.trim() || undefined }) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? (approve ? `Approved. ${body?.accounts_note || ''}` : 'Rejected.') : (body?.error?.message || 'Unable to record the decision.'));
      if (response.ok) setDecideNote('');
      refresh();
    } catch { setMessage('Unable to reach the backend.'); } finally { setDecideBusy(false); }
  };
  const pendingLeaver = overview?.pending_leaver_request;
  const pendingReenable = overview?.pending_reenable_request;
  // The leaver process already ran and finished (no pending request either way) — "Start leaver process now" has
  // nothing left to start until the account is brought back, so it stays hidden instead of clickable-but-doomed.
  const alreadyProcessed = !!(overview?.leaver_processed_at && overview?.status === 'DISABLED');
  return <>
    <div className="panel-head"><h2>Leaver</h2></div>
    <div className="detail-section">
      <label className="key" style={{display:'block',marginBottom:10}}><span>Leaver date (last day)</span><input className="select" style={{width:'100%'}} type="date" value={leaverDate} onChange={event => setLeaverDate(event.target.value)}/></label>
      <label className="key" style={{display:'block',marginBottom:10}}><span>Employment type</span><select className="select" style={{width:'100%'}} value={employmentType} onChange={event => setEmploymentType(event.target.value)}><option value="">Not set</option>{EMPLOYMENT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}</select></label>
      {leaverDate && !pendingLeaver && <p className="subtitle" style={{marginTop:0,marginBottom:10}}>{overview?.policy_name ? `On this date the leaver process runs automatically, per the "${overview.policy_name}" policy (Movers page).` : 'On this date the leaver process runs automatically, per the matching leaver policy (Movers page).'}</p>}
      {message && <div className="notice" style={{marginBottom:10}}>{message}</div>}

      {pendingLeaver ? <div style={{marginBottom:12,padding:12,border:'1px solid #e0a3a3',borderRadius:8,background:'#fff8f7'}}>
        <p style={{margin:'0 0 6px',fontWeight:600}}>A leaver request is waiting for approval</p>
        <p className="subtitle" style={{margin:'0 0 6px'}}>{pendingLeaver.justification}</p>
        <p className="subtitle" style={{margin:'0 0 10px'}}>Requested by {pendingLeaver.requested_by_name || 'an admin'} on {formatDateTime(pendingLeaver.created_at, timezone)} &middot; approver: {pendingLeaver.approvers.join(', ') || 'admins'}</p>
        {pendingLeaver.can_decide ? <>
          <input className="select" style={{width:'100%',marginBottom:8}} placeholder="Optional note" value={decideNote} onChange={event => setDecideNote(event.target.value)}/>
          <div style={{display:'flex',gap:8}}>
            <button className="btn btn-primary" disabled={decideBusy} onClick={() => void decideLeaverRequest(true)}>Approve (run leaver process)</button>
            <button className="btn" disabled={decideBusy} onClick={() => void decideLeaverRequest(false)}>Deny (enable accounts again)</button>
          </div>
        </> : <p className="subtitle" style={{margin:0}}>Only {pendingLeaver.approvers.join(', ') || 'an admin'} can decide this.</p>}
      </div> : pendingReenable ? <div style={{marginBottom:12,padding:12,border:'1px solid #e0a3a3',borderRadius:8,background:'#fff8f7'}}>
        <p style={{margin:'0 0 6px',fontWeight:600}}>A request to enable {pendingReenable.scope_label === 'All accounts' ? 'this account' : pendingReenable.scope_label} again is waiting for approval</p>
        <p className="subtitle" style={{margin:'0 0 6px'}}>{pendingReenable.reason}</p>
        <p className="subtitle" style={{margin:'0 0 10px'}}>Requested by {pendingReenable.requested_by_name || 'an admin'} on {formatDateTime(pendingReenable.created_at, timezone)} &middot; approver: {pendingReenable.approvers.join(', ') || 'admins'}</p>
        {pendingReenable.can_decide ? <>
          <input className="select" style={{width:'100%',marginBottom:8}} placeholder="Optional note" value={decideNote} onChange={event => setDecideNote(event.target.value)}/>
          <div style={{display:'flex',gap:8}}>
            <button className="btn btn-primary" disabled={decideBusy} onClick={() => void decideReenableRequest(true)}>Approve</button>
            <button className="btn" disabled={decideBusy} onClick={() => void decideReenableRequest(false)}>Reject</button>
          </div>
        </> : <p className="subtitle" style={{margin:0}}>Only {pendingReenable.approvers.join(', ') || 'an admin'} can decide this.</p>}
      </div> : alreadyProcessed ? <div style={{marginBottom:12,padding:12,border:'1px solid #e1e8ea',borderRadius:8,background:'#fafbfb'}}>
        <p style={{margin:'0 0 4px',fontWeight:600}}>This person already left</p>
        <p className="subtitle" style={{margin:0}}>The leaver process ran on {formatDateTime(overview!.leaver_processed_at!, timezone)}. To bring them back, enable their account below (Accounts panel) — that needs their manager's approval.</p>
      </div> : null}

      <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
        <button className="btn btn-primary" disabled={busy} onClick={() => void save()}>{busy ? 'Working...' : 'Save'}</button>
        {!confirmingLeave && !pendingLeaver && !pendingReenable && !alreadyProcessed && <button className="btn" style={{borderColor:'#e0a3a3',color:'#ae4949'}} disabled={busy} title={user.status === 'DISABLED' ? 'This person is already disabled; running the process also revokes any remaining access and disables their accounts in every IdP.' : undefined} onClick={() => { setConfirmingLeave(true); setJustification(''); setMessage(''); }}>Start leaver process now</button>}
      </div>
      {confirmingLeave && <div style={{marginTop:12,padding:12,border:'1px solid #e0a3a3',borderRadius:8,background:'#fff8f7'}}>
        <p className="subtitle" style={{marginTop:0}}>Starting the leaver process for <strong>{user.display_name}</strong> disables their accounts in every connected IdP straight away, then asks their manager (or a lifecycle owner) to approve. Approved: the leaver process runs and access is revoked. Denied: the accounts are enabled again and you are notified.</p>
        <label className="key" style={{display:'block',marginBottom:10}}><span>Justification (at least 10 characters)</span><textarea className="select" style={{width:'100%',minHeight:70,resize:'vertical'}} value={justification} onChange={event => setJustification(event.target.value)} placeholder="Why is this person leaving?"/></label>
        <div style={{display:'flex',gap:8}}>
          <button className="btn btn-primary" style={{background:'#ae4949',borderColor:'#ae4949'}} disabled={busy || justification.trim().length < 10} onClick={() => void leaveNow()}>{busy ? 'Working...' : 'Disable accounts and request approval'}</button>
          <button className="btn" disabled={busy} onClick={() => { setConfirmingLeave(false); setJustification(''); }}>Cancel</button>
        </div>
      </div>}

      {!!overview?.recent_events.length && <div style={{marginTop:16}}>
        <p className="subtitle" style={{margin:'0 0 6px',fontWeight:600,color:'inherit'}}>Recent leaver activity</p>
        {overview.recent_events.map(event => <div key={event.id} className="subtitle" style={{margin:'0 0 4px'}}>{formatDateTime(event.created_at, timezone)} &middot; {SOURCE_LABELS[event.source] || event.source} &middot; {event.revoked_count} access removed{event.notified.length ? ` · notified: ${event.notified.join(', ')}` : ''}</div>)}
      </div>}
    </div>
  </>;
}
interface ApiIdentityAccount { id: string; provider_id: string; provider_name: string; provider_type: string; external_id: string; username: string | null; status: string; provisioned_by: string; is_primary: boolean; created_at: string; }
interface ApiAccountsAction { user_status: string; results: { account_id: string; provider_name: string; ok: boolean; already: boolean; error: string | null }[]; accounts: ApiIdentityAccount[]; }
// One row per connected IdP the person holds an account in, with a real "disable / enable in ALL IdPs" control
// (each directory is attempted independently and reported separately). Accounts only — access is not revoked here.
function UserAccountsPanel({ userId, onChanged }: { userId: string; onChanged: () => void }) {
  const auth = useAuth();
  const { data: accounts, error, reload } = useApiResource<ApiIdentityAccount[]>(`/api/v1/users/${userId}/accounts`);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const activeCount = (accounts || []).filter(a => a.status !== 'DISABLED').length;
  const disabledCount = (accounts || []).filter(a => a.status === 'DISABLED').length;
  const summarize = (body: ApiAccountsAction) => {
    const failed = body.results.filter(r => !r.ok);
    const done = body.results.filter(r => r.ok && !r.already).map(r => r.provider_name);
    return [done.length ? `Changed: ${done.join(', ')}.` : 'Nothing needed changing.', ...failed.map(r => `${r.provider_name} failed: ${r.error}`)].join(' ');
  };
  // After the leaver process the accounts can only come back through an approved request (reason + manager
  // approval) — a proper inline form, not window.prompt(), which silently returns null with no dialog at all in
  // some embedded/webview browser contexts, making this look like it does nothing.
  // `null` scope = every disabled account ("Enable in all IdPs"); a one-item list = just that one account (a
  // single account's own "Enable" click) — approval then enables exactly that scope, nothing more.
  const [needsReenable, setNeedsReenable] = useState(false);
  const [reenableScope, setReenableScope] = useState<{ accountIds: string[] | null; label: string } | null>(null);
  const [reenableReason, setReenableReason] = useState('');
  const [reenableBusy, setReenableBusy] = useState(false);
  const submitReenable = async () => {
    if (reenableReason.trim().length < 10) { setMessage('Enter a reason of at least 10 characters.'); return; }
    setReenableBusy(true); setMessage('');
    try {
      const payload: Record<string, unknown> = { reason: reenableReason.trim() };
      if (reenableScope?.accountIds) payload.account_ids = reenableScope.accountIds;
      const response = await auth.apiRequest(`/api/v1/lifecycle/people/${userId}/reenable-request`, { method: 'POST', body: JSON.stringify(payload) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? `Request sent to ${(body?.approvers || []).join(', ') || 'the admins'} for approval. ${reenableScope?.label || 'The account'} is enabled once it is approved.` : (body?.error?.message || 'Unable to send the request.'));
      if (response.ok) { setNeedsReenable(false); setReenableReason(''); setReenableScope(null); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setReenableBusy(false); }
  };
  const runAll = async (enable: boolean) => {
    const names = (accounts || []).map(a => a.provider_name).join(', ');
    if (!window.confirm(enable ? `Enable this person's account in every connected IdP (${names})?` : `Disable this person's account in EVERY connected IdP (${names})? They will be unable to sign in there. Their AccessPilot access is not revoked by this — use the leaver process for that.`)) return;
    setBusy('all'); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/users/${userId}/accounts/${enable ? 'enable-all' : 'disable-all'}`, { method: 'POST' });
      const body = await response.json().catch(() => null);
      if (!response.ok && body?.error?.code === 'LEAVER_REENABLE_APPROVAL_REQUIRED') { setNeedsReenable(true); setReenableScope({ accountIds: null, label: 'The account in every IdP' }); setMessage(body?.error?.message || 'This person has left; enabling their account again needs a reason and approval.'); }
      else setMessage(response.ok ? summarize(body as ApiAccountsAction) : (body?.error?.message || 'Unable to change these accounts.'));
      reload(); onChanged();
    } catch { setMessage('Unable to reach the backend.'); } finally { setBusy(null); }
  };
  const toggleOne = async (account: ApiIdentityAccount) => {
    const enable = account.status === 'DISABLED';
    if (!window.confirm(`${enable ? 'Enable' : 'Disable'} the ${account.provider_name} account (${account.username || account.external_id})?`)) return;
    setBusy(account.id); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/users/${userId}/accounts/${account.id}/enabled`, { method: 'POST', body: JSON.stringify({ enabled: enable }) });
      if (!response.ok) {
        const failure = await response.json().catch(() => null);
        if (failure?.error?.code === 'LEAVER_REENABLE_APPROVAL_REQUIRED') { setNeedsReenable(true); setReenableScope({ accountIds: [account.id], label: `Only the ${account.provider_name} account` }); setMessage(failure?.error?.message || 'This person has left; enabling their account again needs a reason and approval.'); } else setMessage(failure?.error?.message || 'Unable to change this account.');
      }
      reload(); onChanged();
    } catch { setMessage('Unable to reach the backend.'); } finally { setBusy(null); }
  };
  return <>
    <div className="panel-head"><h2>Accounts in connected IdPs</h2></div>
    <div className="detail-section">
      {error ? <div className="notice">{error}</div> : !accounts ? <div className="empty">Loading accounts...</div> : accounts.length === 0 ? <div className="notice">This identity has no real directory account (CSV bookkeeping only).</div> : <>
        {accounts.map(account => <div key={account.id} style={{display:'flex',alignItems:'center',gap:10,justifyContent:'space-between',padding:'8px 0',borderBottom:'1px solid #edf1f2'}}>
          <div style={{minWidth:0}}><strong style={{fontSize:13}}>{account.provider_name}</strong>{account.is_primary && <span className="badge neutral" style={{marginLeft:6}}>Primary</span>}<div className="user-email" style={{wordBreak:'break-all'}}>{account.username || account.external_id}</div></div>
          <div style={{display:'flex',alignItems:'center',gap:8,flex:'none'}}><StatusBadge status={account.status === 'DISABLED' ? 'Disabled' : 'Active'}/><button className="btn" disabled={busy !== null} onClick={() => void toggleOne(account)}>{account.status === 'DISABLED' ? 'Enable' : 'Disable'}</button></div>
        </div>)}
        <div style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:12}}>
          <button className="btn" style={{borderColor:'#e0a3a3',color:'#ae4949'}} disabled={busy !== null || activeCount === 0} onClick={() => void runAll(false)}>{busy === 'all' ? 'Working...' : 'Disable in all IdPs'}</button>
          <button className="btn" disabled={busy !== null || disabledCount === 0} onClick={() => void runAll(true)}>Enable in all IdPs</button>
        </div>
        {message && <div className="notice" style={{marginTop:10}}>{message}</div>}
        {needsReenable && <div style={{marginTop:12,padding:12,border:'1px solid #e0a3a3',borderRadius:8,background:'#fff8f7'}}>
          <p className="subtitle" style={{marginTop:0,marginBottom:8}}>{reenableScope?.label || 'The account'} will be enabled once your manager (or a lifecycle owner) approves this — nothing else changes.</p>
          <label className="key" style={{display:'block',marginBottom:10}}><span>Reason to enable this again (at least 10 characters)</span><textarea className="select" style={{width:'100%',minHeight:60,resize:'vertical'}} value={reenableReason} onChange={event => setReenableReason(event.target.value)} placeholder="Why should this be enabled again?"/></label>
          <div style={{display:'flex',gap:8}}>
            <button className="btn btn-primary" disabled={reenableBusy || reenableReason.trim().length < 10} onClick={() => void submitReenable()}>{reenableBusy ? 'Sending...' : 'Send for approval'}</button>
            <button className="btn" disabled={reenableBusy} onClick={() => { setNeedsReenable(false); setReenableReason(''); setReenableScope(null); }}>Cancel</button>
          </div>
        </div>}
      </>}
    </div>
  </>;
}
function UserDetail() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { id } = useParams();
  const { data: user, error, loading, reload: reloadUser } = useApiResource<ApiUser>(`/api/v1/users/${id}`);
  const [attributesForm, setAttributesForm] = useState({ department: '', job_title: '' });
  const [savingAttributes, setSavingAttributes] = useState(false);
  const [attributesMessage, setAttributesMessage] = useState('');
  useEffect(() => { if (user) setAttributesForm({ department: user.department || '', job_title: user.job_title || '' }); }, [user]);
  const { data: hierarchyNodes, reload: reloadHierarchy } = useApiResource<ApiHierarchyNode[]>('/api/v1/users/hierarchy-tree');
  const [hierarchyForm, setHierarchyForm] = useState({ employee_category: '', manager_id: '' });
  const [savingHierarchy, setSavingHierarchy] = useState(false);
  const [hierarchyMessage, setHierarchyMessage] = useState('');
  useEffect(() => { if (user) setHierarchyForm({ employee_category: user.employee_category || '', manager_id: user.manager_id || '' }); }, [user]);
  const saveHierarchy = async () => {
    setSavingHierarchy(true); setHierarchyMessage('');
    try {
      const payload: Record<string, unknown> = {};
      if (hierarchyForm.employee_category) payload.employee_category = hierarchyForm.employee_category; else payload.clear_employee_category = true;
      if (hierarchyForm.manager_id) payload.manager_id = hierarchyForm.manager_id; else payload.clear_manager = true;
      const response = await auth.apiRequest(`/api/v1/users/${id}/hierarchy`, { method: 'PATCH', body: JSON.stringify(payload) });
      if (response.ok) { setHierarchyMessage('Saved.'); reloadUser(); reloadHierarchy(); }
      else { const body = await response.json().catch(() => null); setHierarchyMessage(body?.error?.message || 'Unable to save this.'); }
    } catch { setHierarchyMessage('Unable to reach the backend.'); } finally { setSavingHierarchy(false); }
  };
  const managerOptions = (hierarchyNodes || []).filter(n => n.employee_category === 'MANAGER' && n.id !== id);
  const saveAttributes = async () => {
    setSavingAttributes(true); setAttributesMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/users/${id}/attributes`, { method: 'PATCH', body: JSON.stringify({ department: attributesForm.department.trim() || null, job_title: attributesForm.job_title.trim() || null }) });
      if (response.ok) { setAttributesMessage('Saved — pushed to the real directory and any birthright policy grants re-evaluated.'); reloadUser(); }
      else { const body = await response.json().catch(() => null); setAttributesMessage(body?.error?.message || 'Unable to save these attributes.'); }
    } catch { setAttributesMessage('Unable to reach the backend.'); } finally { setSavingAttributes(false); }
  };
  const { data: providers } = useApiResource<ApiProvider[]>('/api/v1/providers');
  const { data: departments } = useApiResource<ApiDepartment[]>('/api/v1/policies/departments');
  const { data: access, error: accessError, loading: accessLoading, reload: reloadAccess } = useApiResource<ApiUserAccessSummary>(`/api/v1/users/${id}/access-summary`);
  const { data: linkedAccounts, reload: reloadLinked } = useApiResource<ApiLinkedAccount[]>(`/api/v1/users/${id}/linked-accounts`);
  const { data: linkedOwner } = useApiResource<ApiUser>(`/api/v1/users/${user?.linked_user_id}`, Boolean(user?.linked_user_id));
  const [enabledBusyId, setEnabledBusyId] = useState<string | null>(null);
  const toggleAccountEnabled = async (accountId: string, enable: boolean) => {
    setEnabledBusyId(accountId);
    try { await auth.apiRequest(`/api/v1/users/${accountId}/enabled`, { method: 'POST', body: JSON.stringify({ enabled: enable }) }); reloadLinked(); }
    finally { setEnabledBusyId(null); }
  };
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
  const renderAccessItem = (item: ApiUserAccessItem, index: number) => <div key={item.id || `${item.resource_type}-${index}`} className="timeline-item"><strong>{item.resource_display_name || item.resource_type}</strong><small>{item.source === 'DIRECT_IN_ENTRA' ? 'Added directly in Entra' : item.assignment_type}{item.expiration_time ? ` · expires ${formatDateTime(item.expiration_time, timezone)}` : ''}</small><div style={{marginTop:5}}><StatusBadge status={item.status}/></div></div>;
  const [copied, setCopied] = useState(false);
  const copyEmail = async () => {
    if (!user) return;
    try { await navigator.clipboard.writeText(user.email); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard unavailable */ }
  };
  // Only the FIRST load (no user data yet) shows the loading placeholder. `onChanged` (passed to every panel below)
  // calls this same resource's reload after an action completes — that briefly sets `loading` true again too, and
  // returning the placeholder for THAT would unmount this whole subtree and wipe every child panel's own state
  // (e.g. an inline approval-request form that was just opened) before the person ever sees it.
  if (loading && !user) return <Page eyebrow="USER DIRECTORY" title="Loading..." subtitle=""><div className="empty">Loading user...</div></Page>;
  if (!user) return <Page eyebrow="USER DIRECTORY" title="User" subtitle=""><div className="empty">{error || 'User not found.'}</div></Page>;
  const provider = providers?.find(p => p.id === user.provider_id);
  const connectorName = provider ? (provider.provider_type === 'ENTRA' ? 'Microsoft Entra ID' : provider.provider_type === 'OKTA' ? 'Okta' : provider.name) : 'Unknown connector';
  const isCsvOnly = provider?.provider_type === 'CSV';
  return <Page eyebrow="USER DIRECTORY" title={user.display_name} subtitle={user.email} action={<button className="btn" aria-label="Refresh" onClick={() => reloadAccess()}><RefreshCw size={14}/></button>}><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="user-cell"><span className="avatar" style={{width:45,height:45}}>{initialsFor(user.display_name)}</span><div><h2>{user.job_title || 'No job title on file'}</h2><p className="subtitle">{user.department || 'No department on file'} · {user.status}</p></div></div></div><div className="detail-section"><div className="detail-title"><h2>Overview</h2><StatusBadge status={user.status}/></div><div className="key-grid"><div className="key"><span>Email</span><strong style={{display:'flex',alignItems:'center',gap:8}}>{user.email}<button type="button" className="btn" aria-label="Copy email" onClick={() => void copyEmail()} style={{padding:'2px 7px'}}><Copy size={12}/></button>{copied && <span className="footer-note">Copied</span>}</strong></div><div className="key"><span>Given name</span><strong>{user.given_name || '—'}</strong></div><div className="key"><span>Surname</span><strong>{user.surname || '—'}</strong></div><div className="key"><span>Last synced</span><strong>{user.last_synced_at ? formatDateTime(user.last_synced_at, timezone) : 'Never'}</strong></div><div className="key"><span>Groups</span><strong>{accessLoading ? '…' : groupCount}</strong></div><div className="key"><span>Applications</span><strong>{accessLoading ? '…' : applicationCount}</strong></div></div></div><div className="detail-section"><div className="detail-title"><h2>Identity source</h2>{isCsvOnly && <span className="badge warning">No real account yet</span>}</div><div className="key-grid">
    <div className="key"><span>Onboarded via</span><strong>{user.source === 'CSV_ONBOARDING' ? 'CSV Onboarding' : 'Directory sync'}</strong></div>
    {user.employee_id && <div className="key"><span>Employee ID (from CSV)</span><strong>{user.employee_id}</strong></div>}
    <div className="key"><span>Connector</span><strong>{connectorName}</strong></div>
    <div className="key"><span>Connector external ID</span><strong>{user.external_id}</strong></div>
  </div>{isCsvOnly && <p className="subtitle" style={{marginTop:12}}>This identity has no real {providers?.some(p => p.provider_type === 'ENTRA') ? 'Entra' : providers?.some(p => p.provider_type === 'OKTA') ? 'Okta' : 'connector'} account yet — group/role membership shown below is AccessPilot-local (eligible) only. Re-uploading its CSV row after a real connector is available will provision one automatically.</p>}</div>{user.account_type !== 'NORMAL' && <div className="detail-section"><div className="detail-title"><h2>{user.account_type === 'PU' ? 'Privileged' : 'Test'} account</h2><span className="badge neutral">{user.account_type}</span></div><p className="subtitle" style={{marginTop:0}}>This is a {user.account_type === 'PU' ? 'Privileged (PU)' : 'Test (TU)'} account — deliberately excluded from Birthright and Group Role Mapping automation. Access to it is always granted manually.</p><div className="key-grid"><div className="key"><span>Linked to</span><strong>{linkedOwner ? <Link to={`/admin/users/${linkedOwner.id}`} className="user-name">{linkedOwner.display_name}</Link> : 'Not linked to a real user'}</strong></div></div></div>}{(linkedAccounts && linkedAccounts.length > 0) && <div className="detail-section"><div className="detail-title"><h2>Privileged / Test accounts</h2></div><p className="subtitle" style={{marginTop:0,marginBottom:12}}>Shadow accounts linked to this person for elevated admin work or QA/UAT — no mailbox, access granted manually only.</p><div className="table-wrap"><table><thead><tr><th>Account</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{linkedAccounts.map(account => <tr key={account.id}><td className="user-name"><Link to={`/admin/users/${account.id}`} className="user-name">{account.display_name}</Link></td><td>{account.account_type}</td><td><StatusBadge status={account.status}/></td><td><button className="btn" disabled={enabledBusyId === account.id} onClick={() => void toggleAccountEnabled(account.id, account.status !== 'ACTIVE')}>{enabledBusyId === account.id ? 'Working...' : account.status === 'ACTIVE' ? 'Disable' : 'Enable'}</button></td></tr>)}</tbody></table></div></div>}<div className="detail-section"><div className="detail-title"><h2>Department &amp; job title</h2></div><p className="subtitle" style={{marginTop:0,marginBottom:14}}>Editing either pushes a real write to {connectorName} (not just a local edit) and re-evaluates birthright policies — a mover loses any group a policy no longer grants and gains any newly-matching one, ELIGIBLE only, same as a new joiner.</p><div className="key-grid"><label className="key" style={{display:'block'}}><span>Department</span><select className="select" style={{width:'100%'}} value={attributesForm.department} onChange={event => setAttributesForm({...attributesForm, department: event.target.value})}><option value="">No department</option>{attributesForm.department && !(departments || []).some(d => d.name === attributesForm.department) && <option value={attributesForm.department}>{attributesForm.department} (not in the managed list)</option>}{(departments || []).map(d => <option key={d.id} value={d.name}>{d.name}</option>)}</select></label><label className="key" style={{display:'block'}}><span>Job title</span><input className="select" style={{width:'100%'}} value={attributesForm.job_title} onChange={event => setAttributesForm({...attributesForm, job_title: event.target.value})}/></label></div><div style={{display:'flex',alignItems:'center',gap:10,marginTop:14}}><button className="btn btn-primary" disabled={savingAttributes} onClick={saveAttributes}>{savingAttributes ? 'Saving...' : 'Save'}</button>{attributesMessage && <span className="footer-note" style={{margin:0}}>{attributesMessage}</span>}</div></div><div className="detail-section"><div className="detail-title"><h2>Role &amp; manager</h2></div><p className="subtitle" style={{marginTop:0,marginBottom:14}}>AccessPilot-internal only — never pushed to Entra/Okta. Tag this person as an Employee or a Manager, and (for an Employee, or a Manager reporting further up) who they report to. Powers the Org Chart tab.</p><div className="key-grid"><label className="key" style={{display:'block'}}><span>Tag</span><select className="select" style={{width:'100%'}} value={hierarchyForm.employee_category} onChange={event => setHierarchyForm({...hierarchyForm, employee_category: event.target.value})}><option value="">Unclassified</option><option value="EMPLOYEE">Employee</option><option value="MANAGER">Manager</option></select></label><label className="key" style={{display:'block'}}><span>Reports to</span><select className="select" style={{width:'100%'}} value={hierarchyForm.manager_id} onChange={event => setHierarchyForm({...hierarchyForm, manager_id: event.target.value})}><option value="">No manager assigned</option>{managerOptions.map(m => <option key={m.id} value={m.id}>{m.display_name}</option>)}</select></label></div><div style={{display:'flex',alignItems:'center',gap:10,marginTop:14}}><button className="btn btn-primary" disabled={savingHierarchy} onClick={() => void saveHierarchy()}>{savingHierarchy ? 'Saving...' : 'Save'}</button>{hierarchyMessage && <span className="footer-note" style={{margin:0}}>{hierarchyMessage}</span>}</div></div></section><aside className="panel">
    <UserAccountsPanel userId={user.id} onChanged={reloadUser}/>
    <UserLeaverPanel user={user} onChanged={reloadUser}/>
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
  const timezone = useAppTimezone();
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
      <TablePanel toolbar={undefined}>{historyLoading ? <div className="empty">Loading your requests...</div> : historyError ? <div className="empty">{historyError}</div> : requestHistory.length === 0 ? <div className="empty">You haven't requested any packages yet.</div> : <table><thead><tr><th>Package</th><th>Requested</th><th>Status</th></tr></thead><tbody>{requestHistory.map(row => <tr key={row.batchId}><td className="user-name">{row.packageName}</td><td>{formatDateTime(row.requestedAt, timezone)}</td><td><StatusBadge status={row.status}/></td></tr>)}</tbody></table>}</TablePanel>
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
  const timezone = useAppTimezone();
  const { data: assignments, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments/mine');
  const { data: policy } = useApiResource<ApiActivationPolicy>('/api/v1/assignments/activation-policy');
  const { data: batches } = useApiResource<ApiPackageBatch[]>('/api/v1/packages/my-package-batches');
  const maxHours = policy?.max_self_activation_hours ?? 8;
  const [activateTarget, setActivateTarget] = useState<{ ids: string[]; label: string } | null>(null);
  const [expandedPackages, setExpandedPackages] = useState<string[]>([]);
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

  // Live: without this, a row an SoD exception auto-revoked in the background (see workers/sod_expiry.py) or a
  // conflict that just resolved itself would keep showing here — stale eligibility, a stale "⚠ SoD conflict"
  // badge, or both — until the user happened to click the manual refresh button. 30s matches the self-service
  // Dashboard's own polling cadence for the same /assignments/mine data.
  useEffect(() => {
    const id = setInterval(() => reload(), 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          ? <div key={row.assignment.id} className="activity-row"><span className="avatar"><Shield size={14}/></span><div className="activity-copy"><strong>{row.assignment.resource_display_name || row.assignment.resource_id}</strong><small>{row.assignment.resource_type}{row.assignment.package_name ? ` · ${row.assignment.package_name}` : ''} · {row.assignment.assignment_type === 'TEMPORARY' && row.assignment.expiration_time ? `Activate by ${formatDateTime(row.assignment.expiration_time, timezone)}` : row.assignment.sod_exception_expires_at ? `Eligible until the SoD exception expires (${formatDateTime(row.assignment.sod_exception_expires_at, timezone)})` : 'No activation deadline'}</small></div><span style={{display:'flex',alignItems:'center',gap:8}}>{warnings.length > 0 && <span className="badge danger" title={warningTitle}>⚠ SoD conflict</span>}<button className="btn btn-primary" onClick={() => openActivate([row.assignment.id], row.assignment.resource_display_name || 'this access')}>Activate <ArrowRight size={13}/></button></span></div>
          : <Fragment key={row.batch.package_id}><div className="activity-row"><span className="avatar">📦</span><div className="activity-copy"><button type="button" onClick={() => setExpandedPackages(prev => prev.includes(row.batch.package_id) ? prev.filter(id => id !== row.batch.package_id) : [...prev, row.batch.package_id])} style={{border:'none',background:'none',padding:0,display:'inline-flex',alignItems:'center',gap:6,fontWeight:700,color:'inherit',cursor:'pointer',font:'inherit'}}><ChevronRight size={14} style={{transform: expandedPackages.includes(row.batch.package_id) ? 'rotate(90deg)' : 'none', transition:'transform 0.1s'}}/>{row.batch.package_name}</button><small>PACKAGE · {row.assignments.length} items · click to activate just one</small></div><span style={{display:'flex',alignItems:'center',gap:8}}>{warnings.length > 0 && <span className="badge danger" title={warningTitle}>⚠ SoD conflict</span>}<button className="btn btn-primary" onClick={() => openActivate(row.assignments.map(a => a.id), `"${row.batch.package_name}" (${row.assignments.length} items)`)}>Activate all <ArrowRight size={13}/></button></span></div>
            {expandedPackages.includes(row.batch.package_id) && row.assignments.map(a => <div key={a.id} className="activity-row" style={{paddingLeft:16,background:'#fafbfb'}}><span/><div className="activity-copy"><strong>{a.resource_display_name || a.resource_id}</strong><small>{a.resource_type} · from {row.batch.package_name}</small></div><span style={{display:'flex',alignItems:'center',gap:8}}>{(sodWarnings[a.id] || []).length > 0 && <span className="badge danger" title={`Activating this may conflict with Separation-of-Duties: ${(sodWarnings[a.id] || []).join(', ')}`}>⚠ SoD conflict</span>}<button className="btn" onClick={() => openActivate([a.id], a.resource_display_name || 'this access')}>Activate <ArrowRight size={13}/></button></span></div>)}
          </Fragment>;
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
      ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.resource_display_name || row.assignment.resource_id}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.package_name || '—'}</td><td>{row.assignment.activated_at ? formatDateTime(row.assignment.activated_at, timezone) : '—'}</td><td>{row.assignment.expiration_time ? formatDateTime(row.assignment.expiration_time, timezone) : 'Never'}</td><td>{row.assignment.expiration_time ? formatRemaining(row.assignment.expiration_time) : '—'}</td><td>{row.assignment.bypass_activation ? <span className="footer-note">Assigned by admin</span> : <button className="btn" disabled={busyIds === row.assignment.id} onClick={() => void deactivate([row.assignment.id], row.assignment.resource_display_name || 'this access')}>Deactivate</button>}</td></tr>
      : <tr key={row.batch.package_id}><td className="user-name">📦 {row.batch.package_name}</td><td>PACKAGE ({row.assignments.length})</td><td>{row.batch.package_name}</td><td>{row.assignments[0]?.activated_at ? formatDateTime(row.assignments[0].activated_at!, timezone) : '—'}</td><td>{row.assignments[0]?.expiration_time ? formatDateTime(row.assignments[0].expiration_time!, timezone) : 'Never'}</td><td>{row.assignments[0]?.expiration_time ? formatRemaining(row.assignments[0].expiration_time) : '—'}</td><td><button className="btn" disabled={busyIds === row.assignments.map(a => a.id).join(',')} onClick={() => void deactivate(row.assignments.map(a => a.id), `"${row.batch.package_name}" (${row.assignments.length} items)`)}>Deactivate all</button></td></tr>
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
interface ApiAssignment { id: string; user_id: string; user_display_name: string | null; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; assignment_type: string; status: string; start_time: string | null; expiration_time: string | null; justification: string | null; requested_by: string | null; approved_by: string | null; bypass_activation: boolean; activated_at: string | null; created_at: string; package_name: string | null; business_role_name: string | null; sod_exception_expires_at: string | null; }
interface ApiActivationPolicy { max_self_activation_hours: number; }
function todayDateValue(date: Date) { const pad = (n: number) => String(n).padStart(2, '0'); return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`; }
const emptyAssignmentForm = { user_id: '', resource_type: 'GROUP', resource_id: '', app_role_external_id: '', assignment_type: 'PERMANENT', start_date: '', start_clock: '', end_date: '', end_clock: '', approver_id: '', bypass_activation: false, justification: '' };
function AssignmentsInteractive() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: assignmentList, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments');
  const { data: users, reload: reloadUsers } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: groups, reload: reloadGroups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles, reload: reloadRoles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications, reload: reloadApplications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const { data: packages, reload: reloadPackages } = useApiResource<ApiPackage[]>('/api/v1/packages');
  const { data: batches } = useApiResource<ApiPackageBatch[]>('/api/v1/packages/assignment-batches');
  const { data: roleBatches } = useApiResource<ApiRoleBatch[]>('/api/v1/business-roles/assignment-batches');
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
  const [sodBlock, setSodBlock] = useState<{ conflicts: { policy_id: string; policy_name: string; severity: string }[]; user_id: string; resource_type: string; resource_id: string; app_role_external_id?: string } | null>(null);
  const [requestingException, setRequestingException] = useState(false);
  const [exceptionRequestMessage, setExceptionRequestMessage] = useState('');
  const targets = form.resource_type === 'GROUP' ? (groups || []) : form.resource_type === 'ROLE' ? (roles || []) : form.resource_type === 'APPLICATION' ? (applications || []) : (packages || []).filter(p => p.status === 'ACTIVE');
  const selectedApplication = form.resource_type === 'APPLICATION' ? (applications || []).find(a => a.id === form.resource_id) : undefined;
  const today = todayDateValue(new Date());
  const batchByAssignmentId = useMemo(() => { const map = new Map<string, ApiPackageBatch>(); (batches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [batches]);
  const roleBatchByAssignmentId = useMemo(() => { const map = new Map<string, ApiRoleBatch>(); (roleBatches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [roleBatches]);
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
    const rows: Array<{ kind: 'single'; assignment: ApiAssignment } | { kind: 'batch'; batch: ApiPackageBatch; assignments: ApiAssignment[] } | { kind: 'roleBatch'; batch: ApiRoleBatch; assignments: ApiAssignment[] }> = [];
    const seenBatches = new Set<string>();
    const seenRoleBatches = new Set<string>();
    filteredAssignments.forEach(a => {
      const batch = batchByAssignmentId.get(a.id);
      if (batch) {
        if (seenBatches.has(batch.package_assignment_id)) return;
        seenBatches.add(batch.package_assignment_id);
        const assignments = filteredAssignments.filter(x => batchByAssignmentId.get(x.id)?.package_assignment_id === batch.package_assignment_id);
        rows.push({ kind: 'batch', batch, assignments });
        return;
      }
      const roleBatch = roleBatchByAssignmentId.get(a.id);
      if (roleBatch) {
        if (seenRoleBatches.has(roleBatch.role_assignment_id)) return;
        seenRoleBatches.add(roleBatch.role_assignment_id);
        const assignments = filteredAssignments.filter(x => roleBatchByAssignmentId.get(x.id)?.role_assignment_id === roleBatch.role_assignment_id);
        rows.push({ kind: 'roleBatch', batch: roleBatch, assignments });
        return;
      }
      rows.push({ kind: 'single', assignment: a });
    });
    return rows;
  }, [filteredAssignments, batchByAssignmentId, roleBatchByAssignmentId]);
  const toggleBatch = (id: string) => setExpandedBatches(prev => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const isRevocable = (status: string) => !['REJECTED', 'REVOKED', 'EXPIRED'].includes(status);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.user_id || !form.resource_id) { setFormMessage('Select a user and a target.'); return; }
    if (form.resource_type === 'APPLICATION' && !form.app_role_external_id) { setFormMessage('Select an application role.'); return; }
    if (form.assignment_type === 'TEMPORARY' && !form.end_date && !form.end_clock) { setFormMessage('Set an end date/time for a time-bound assignment.'); return; }
    if (form.justification.trim().length < 3) { setFormMessage('A justification (at least 3 characters) for this assignment is required.'); return; }
    setSaving(true); setFormMessage(''); setSodBlock(null); setExceptionRequestMessage('');
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
      else {
        const errorBody = await response.json().catch(() => null);
        setFormMessage(errorBody?.error?.message || 'Unable to create assignment.');
        if (!isPackage && response.status === 409 && errorBody?.error?.code === 'SOD_CONFLICT' && Array.isArray(errorBody?.error?.details?.conflicts)) {
          setSodBlock({ conflicts: errorBody.error.details.conflicts, user_id: form.user_id, resource_type: form.resource_type, resource_id: form.resource_id, app_role_external_id: form.resource_type === 'APPLICATION' ? form.app_role_external_id : undefined });
        }
      }
    } catch (err) {
      setFormMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to create assignment.');
    } finally { setSaving(false); }
  };

  const requestSodException = async () => {
    if (!sodBlock) return;
    setRequestingException(true); setExceptionRequestMessage('');
    try {
      // Carries the rest of the originally-blocked attempt's shape (approver, duration) so that granting can
      // recreate it faithfully — including routing through the same approver — instead of only ever landing on
      // a bare no-approver ELIGIBLE row. Read live off `form`, which still holds the blocked attempt's values.
      const expiration_time = form.assignment_type === 'TEMPORARY' && (form.end_date || form.end_clock) ? new Date(`${form.end_date || today}T${form.end_clock || '23:59'}`).toISOString() : undefined;
      const responses = await Promise.all(sodBlock.conflicts.map(c => auth.apiRequest('/api/v1/sod/exception-requests', { method: 'POST', body: JSON.stringify({ sod_policy_id: c.policy_id, user_id: sodBlock.user_id, justification: form.justification.trim(), resource_type: sodBlock.resource_type, resource_id: sodBlock.resource_id, app_role_external_id: sodBlock.app_role_external_id || undefined, approver_id: form.approver_id || undefined, assignment_type: form.assignment_type, expiration_time }) })));
      if (responses.every(r => r.ok)) { setExceptionRequestMessage('Exception request sent to the SoD Admin — once granted, the user becomes eligible automatically (or routes to your chosen approver, if one was set); no need to redo this yourself.'); setSodBlock(null); }
      else setExceptionRequestMessage('Unable to submit the exception request for one or more conflicting policies.');
    } catch { setExceptionRequestMessage('Unable to reach the backend.'); }
    finally { setRequestingException(false); }
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

  const decideRoleBatch = async (batch: ApiRoleBatch, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm(`Reject all ${batch.assignment_ids.length} items in "${batch.role_name}"?`)) return;
    let justification: string | null = null;
    if (decision === 'approve') {
      justification = window.prompt(`Justification for approving all ${batch.assignment_ids.length} items in "${batch.role_name}" (required):`);
      if (justification === null) return;
      if (justification.trim().length < 3) { window.alert('A justification (at least 3 characters) is required to approve.'); return; }
    }
    setActioningId(batch.role_assignment_id);
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

  const revokeRoleBatch = async (batch: ApiRoleBatch) => {
    const justification = window.prompt(`Revoke all ${batch.assignment_ids.length} items in "${batch.role_name}"? Justification (required):`);
    if (justification === null) return;
    if (justification.trim().length < 3) { window.alert('A justification (at least 3 characters) is required to revoke.'); return; }
    setActioningId(batch.role_assignment_id);
    try {
      await Promise.all(batch.assignment_ids.map(id => auth.apiRequest(`/api/v1/assignments/${id}/revoke`, { method: 'POST', body: JSON.stringify({ justification: justification.trim() }) })));
      reload();
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

  return <Page eyebrow="ACCESS MANAGEMENT" title="Assignments" subtitle="Assign group, role, application, or package access to users, with optional approval and time-bound expiration." action={<><button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button><button className="btn btn-primary" onClick={() => { setOpen(true); setFormMessage(''); setSodBlock(null); setExceptionRequestMessage(''); reloadUsers(); reloadGroups(); reloadRoles(); reloadApplications(); reloadPackages(); }}><Plus size={14}/> Add assignment</button></>}>
    {open && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:720,marginBottom:18}} onSubmit={submit}>
      <div className="panel-head"><h2>Add assignment</h2><button type="button" className="btn" aria-label="Close" onClick={() => { setOpen(false); setSodBlock(null); }}><X size={14}/></button></div>
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
      {formMessage && <div className="notice" style={{marginTop:14}}>{formMessage}</div>}
      {sodBlock && <div style={{marginTop:10,display:'flex',alignItems:'center',gap:10}}><button type="button" className="btn" disabled={requestingException} onClick={() => void requestSodException()}>{requestingException ? 'Sending...' : 'Request SoD Exception'}</button><span className="footer-note">Notifies the SoD Admin — granting it makes the user eligible automatically, no retry needed.</span></div>}
      {exceptionRequestMessage && <div className="notice" style={{marginTop:10}}>{exceptionRequestMessage}</div>}</div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Creating...' : 'Create assignment'}</button></div>
    </form>}
    <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:12}}><label style={{display:'flex',alignItems:'center',gap:6,fontSize:12,color:'#52656d',cursor:'pointer'}}><input type="checkbox" checked={expiringFilter === '24h'} onChange={event => setExpiringFilter(event.target.checked)}/> Expiring within 24 hours</label></div>
    <TablePanel toolbar={<Toolbar placeholder="Search assignments" searchValue={search} onSearchChange={setSearch} filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={statusOptions}/>}>{loading ? <div className="empty">Loading assignments...</div> : error ? <div className="empty">{error}</div> : !assignmentList || assignmentList.length === 0 ? <div className="empty">No assignments found.</div> : groupedRows.length === 0 ? <div className="empty">No assignments match this filter.</div> : <table><thead><tr><th>User</th><th>Resource</th><th>Type</th><th>Duration</th><th>Status</th><th>Start</th><th>Expiration</th><th></th></tr></thead><tbody>{groupedRows.map(row => row.kind === 'single' ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.user_display_name || row.assignment.user_id}</td><td>{row.assignment.resource_display_name || row.assignment.resource_id}{row.assignment.business_role_name && <div className="user-email">🏷 {row.assignment.business_role_name}</div>}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.assignment_type}</td><td><StatusBadge status={row.assignment.status}/></td><td>{row.assignment.start_time ? formatDateTime(row.assignment.start_time, timezone) : '—'}</td><td>{row.assignment.expiration_time ? formatDateTime(row.assignment.expiration_time, timezone) : '—'}</td><td>{isRevocable(row.assignment.status) ? <span style={{display:'flex',gap:5}}>{row.assignment.status === 'PENDING_APPROVAL' && <><button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'approve')} aria-label="Approve"><Check size={14}/></button><button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'reject')} aria-label="Reject"><X size={14}/></button></>}<button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void revoke(row.assignment.id, row.assignment.resource_display_name || 'this assignment')} aria-label="Revoke">Revoke</button></span> : <span className="footer-note">No actions</span>}</td></tr> : row.kind === 'batch' ? <>
      <tr key={row.batch.package_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.package_assignment_id)}>
        <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
        <td>📦 {row.batch.package_name} <span className="footer-note">({row.assignments.length} items)</span></td>
        <td>PACKAGE</td>
        <td>{row.assignments[0]?.assignment_type}</td>
        <td><StatusBadge status={new Set(row.assignments.map(a => a.status)).size === 1 ? row.assignments[0].status : 'MIXED'}/></td>
        <td>{row.assignments[0]?.start_time ? formatDateTime(row.assignments[0].start_time!, timezone) : '—'}</td>
        <td>{row.assignments[0]?.expiration_time ? formatDateTime(row.assignments[0].expiration_time!, timezone) : '—'}</td>
        <td>{row.assignments.some(a => isRevocable(a.status)) ? <span style={{display:'flex',gap:5}} onClick={event => event.stopPropagation()}>{row.assignments.some(a => a.status === 'PENDING_APPROVAL') && <><button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'approve')} aria-label="Approve all"><Check size={14}/></button><button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'reject')} aria-label="Reject all"><X size={14}/></button></>}<button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void revokeBatch(row.batch)} aria-label="Revoke all">Revoke all</button></span> : <span className="footer-note">No actions</span>}</td>
      </tr>
      {expandedBatches.has(row.batch.package_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{a.start_time ? formatDateTime(a.start_time, timezone) : '—'}</td><td>{a.expiration_time ? formatDateTime(a.expiration_time, timezone) : '—'}</td><td>{isRevocable(a.status) ? <span style={{display:'flex',gap:5}}>{a.status === 'PENDING_APPROVAL' && <><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'approve')} aria-label="Approve"><Check size={14}/></button><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'reject')} aria-label="Reject"><X size={14}/></button></>}<button className="btn" disabled={actioningId === a.id} onClick={() => void revoke(a.id, a.resource_display_name || 'this assignment')} aria-label="Revoke">Revoke</button></span> : <span className="footer-note">No actions</span>}</td></tr>)}
    </> : <>
      <tr key={row.batch.role_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.role_assignment_id)}>
        <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
        <td>🏷 {row.batch.role_name} <span className="footer-note">({row.assignments.length} items)</span></td>
        <td>BUSINESS ROLE</td>
        <td>{row.assignments[0]?.assignment_type}</td>
        <td><StatusBadge status={new Set(row.assignments.map(a => a.status)).size === 1 ? row.assignments[0].status : 'MIXED'}/></td>
        <td>{row.assignments[0]?.start_time ? formatDateTime(row.assignments[0].start_time!, timezone) : '—'}</td>
        <td>{row.assignments[0]?.expiration_time ? formatDateTime(row.assignments[0].expiration_time!, timezone) : '—'}</td>
        <td>{row.assignments.some(a => isRevocable(a.status)) ? <span style={{display:'flex',gap:5}} onClick={event => event.stopPropagation()}>{row.assignments.some(a => a.status === 'PENDING_APPROVAL') && <><button className="btn" disabled={actioningId === row.batch.role_assignment_id} onClick={() => void decideRoleBatch(row.batch, 'approve')} aria-label="Approve all"><Check size={14}/></button><button className="btn" disabled={actioningId === row.batch.role_assignment_id} onClick={() => void decideRoleBatch(row.batch, 'reject')} aria-label="Reject all"><X size={14}/></button></>}<button className="btn" disabled={actioningId === row.batch.role_assignment_id} onClick={() => void revokeRoleBatch(row.batch)} aria-label="Revoke all">Revoke all</button></span> : <span className="footer-note">No actions</span>}</td>
      </tr>
      {expandedBatches.has(row.batch.role_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{a.start_time ? formatDateTime(a.start_time, timezone) : '—'}</td><td>{a.expiration_time ? formatDateTime(a.expiration_time, timezone) : '—'}</td><td>{isRevocable(a.status) ? <span style={{display:'flex',gap:5}}>{a.status === 'PENDING_APPROVAL' && <><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'approve')} aria-label="Approve"><Check size={14}/></button><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'reject')} aria-label="Reject"><X size={14}/></button></>}<button className="btn" disabled={actioningId === a.id} onClick={() => void revoke(a.id, a.resource_display_name || 'this assignment')} aria-label="Revoke">Revoke</button></span> : <span className="footer-note">No actions</span>}</td></tr>)}
    </>)}</tbody></table>}</TablePanel>
  </Page>;
}
function AssignmentsPage() { return <AssignmentsInteractive />; }
const emptyPackageForm = { name: '', description: '', owner_ids: [] as string[], items: [] as { resource_type: string; resource_id: string; app_role_external_id: string }[], principals: [] as { principal_type: string; principal_id: string }[], default_approver_id: '', default_fallback_approver_id: '', fallback_unlock_hours: '' };
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
    setForm({ name: pkg.name, description: pkg.description || '', owner_ids: (pkg.owners || []).map(o => o.user_id), items: pkg.items.map(item => ({ resource_type: item.resource_type, resource_id: item.resource_id, app_role_external_id: item.app_role_external_id || '' })), principals: [], default_approver_id: '', default_fallback_approver_id: '', fallback_unlock_hours: '' });
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
      payload.owner_ids = form.owner_ids;
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
        <div style={{marginTop:14}}>
          <div className="key"><span>Owners — can rename this package and remove items from it in their own portal (My Packages), nothing else</span></div>
          <select className="select" value="" onChange={event => { const id = event.target.value; if (id && !form.owner_ids.includes(id)) setForm({...form, owner_ids: [...form.owner_ids, id]}); }}><option value="">Add an owner…</option>{(users || []).filter(u => !form.owner_ids.includes(u.id)).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select>
          {form.owner_ids.length > 0 && <div style={{display:'flex',flexWrap:'wrap',gap:8,marginTop:8}}>{form.owner_ids.map(id => <span key={id} className="badge neutral" style={{display:'inline-flex',alignItems:'center',gap:6}}>{(users || []).find(u => u.id === id)?.display_name || id}<button type="button" className="btn" aria-label="Remove owner" onClick={() => setForm({...form, owner_ids: form.owner_ids.filter(x => x !== id)})} style={{padding:'0 6px',minWidth:0}}><X size={11}/></button></span>)}</div>}
          <div className="key" style={{marginTop:8}}><span>Owners — can rename this Business Role and remove mapped items from it in their own portal (My Business Roles), nothing else</span></div>
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
  const timezone = useAppTimezone();
  const { data: items, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments/pending-approval');
  // Self-scoped: returns only batches where the caller is the designated approver, so it works for any
  // authenticated user (not just Admins) — same access model as /assignments/pending-approval above.
  const { data: batches } = useApiResource<ApiPackageBatch[]>('/api/v1/packages/my-assignment-batches');
  const { data: roleBatches } = useApiResource<ApiRoleBatch[]>('/api/v1/business-roles/my-assignment-batches');
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
  const decideRoleBatch = async (batch: ApiRoleBatch, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm(`Reject all ${batch.assignment_ids.length} items in "${batch.role_name}"?`)) return;
    let justification: string | null = null;
    if (decision === 'approve') {
      justification = window.prompt(`Justification for approving all ${batch.assignment_ids.length} items in "${batch.role_name}" (required):`);
      if (justification === null) return;
      if (justification.trim().length < 3) { setMessage('A justification (at least 3 characters) is required to approve.'); return; }
    }
    setActioningId(batch.role_assignment_id); setMessage('');
    try {
      const responses = await Promise.all(batch.assignment_ids.map(id => auth.apiRequest(`/api/v1/assignments/${id}/${decision}`, { method: 'POST', body: decision === 'approve' ? JSON.stringify({ justification: justification!.trim() }) : undefined })));
      if (responses.every(r => r.ok)) reload();
      else setMessage('Some items in this Business Role could not be processed.');
    } catch { setMessage('Unable to complete this action.'); } finally { setActioningId(null); }
  };
  const toggleBatch = (id: string) => setExpandedBatches(prev => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const batchByAssignmentId = useMemo(() => { const map = new Map<string, ApiPackageBatch>(); (batches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [batches]);
  const roleBatchByAssignmentId = useMemo(() => { const map = new Map<string, ApiRoleBatch>(); (roleBatches || []).forEach(b => b.assignment_ids.forEach(id => map.set(id, b))); return map; }, [roleBatches]);
  const groupRows = (list: ApiAssignment[]) => {
    const rows: Array<{ kind: 'single'; assignment: ApiAssignment } | { kind: 'batch'; batch: ApiPackageBatch; assignments: ApiAssignment[] } | { kind: 'roleBatch'; batch: ApiRoleBatch; assignments: ApiAssignment[] }> = [];
    const seen = new Set<string>();
    const seenRoles = new Set<string>();
    list.forEach(a => {
      const batch = batchByAssignmentId.get(a.id);
      if (batch) {
        if (seen.has(batch.package_assignment_id)) return;
        seen.add(batch.package_assignment_id);
        rows.push({ kind: 'batch', batch, assignments: list.filter(x => batchByAssignmentId.get(x.id)?.package_assignment_id === batch.package_assignment_id) });
        return;
      }
      const roleBatch = roleBatchByAssignmentId.get(a.id);
      if (roleBatch) {
        if (seenRoles.has(roleBatch.role_assignment_id)) return;
        seenRoles.add(roleBatch.role_assignment_id);
        rows.push({ kind: 'roleBatch', batch: roleBatch, assignments: list.filter(x => roleBatchByAssignmentId.get(x.id)?.role_assignment_id === roleBatch.role_assignment_id) });
        return;
      }
      rows.push({ kind: 'single', assignment: a });
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
      {pendingRows.map(row => row.kind === 'single' ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.user_display_name || row.assignment.user_id}</td><td>{row.assignment.resource_display_name || row.assignment.resource_id}{row.assignment.business_role_name && <div className="user-email">🏷 {row.assignment.business_role_name}</div>}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.assignment_type}</td><td><StatusBadge status={row.assignment.status}/></td><td>{formatDateTime(row.assignment.created_at, timezone)}</td><td><span style={{display:'flex',gap:5}}><button className="btn btn-primary" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'approve')} aria-label="Approve"><Check size={14}/> Approve</button><button className="btn" disabled={actioningId === row.assignment.id} onClick={() => void decide(row.assignment.id, 'reject')} aria-label="Reject"><X size={14}/> Reject</button></span></td></tr> : row.kind === 'batch' ? <>
        <tr key={row.batch.package_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.package_assignment_id)}>
          <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
          <td>📦 {row.batch.package_name} <span className="footer-note">({row.assignments.length} items)</span></td>
          <td>PACKAGE</td>
          <td>{row.assignments[0]?.assignment_type}</td>
          <td><StatusBadge status="PENDING_APPROVAL"/></td>
          <td>{formatDateTime(row.assignments[0].created_at, timezone)}</td>
          <td><span style={{display:'flex',gap:5}} onClick={event => event.stopPropagation()}><button className="btn btn-primary" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'approve')} aria-label="Approve all"><Check size={14}/> Approve all</button><button className="btn" disabled={actioningId === row.batch.package_assignment_id} onClick={() => void decideBatch(row.batch, 'reject')} aria-label="Reject all"><X size={14}/> Reject all</button></span></td>
        </tr>
        {expandedBatches.has(row.batch.package_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{formatDateTime(a.created_at, timezone)}</td><td></td></tr>)}
      </> : <>
        <tr key={row.batch.role_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.role_assignment_id)}>
          <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
          <td>🏷 {row.batch.role_name} <span className="footer-note">({row.assignments.length} items)</span></td>
          <td>BUSINESS ROLE</td>
          <td>{row.assignments[0]?.assignment_type}</td>
          <td><StatusBadge status="PENDING_APPROVAL"/></td>
          <td>{formatDateTime(row.assignments[0].created_at, timezone)}</td>
          <td><span style={{display:'flex',gap:5}} onClick={event => event.stopPropagation()}><button className="btn btn-primary" disabled={actioningId === row.batch.role_assignment_id} onClick={() => void decideRoleBatch(row.batch, 'approve')} aria-label="Approve all"><Check size={14}/> Approve all</button><button className="btn" disabled={actioningId === row.batch.role_assignment_id} onClick={() => void decideRoleBatch(row.batch, 'reject')} aria-label="Reject all"><X size={14}/> Reject all</button></span></td>
        </tr>
        {expandedBatches.has(row.batch.role_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{formatDateTime(a.created_at, timezone)}</td><td></td></tr>)}
      </>)}
      {decidedRows.map(row => row.kind === 'single' ? <tr key={row.assignment.id}><td className="user-name">{row.assignment.user_display_name || row.assignment.user_id}</td><td>{row.assignment.resource_display_name || row.assignment.resource_id}{row.assignment.business_role_name && <div className="user-email">🏷 {row.assignment.business_role_name}</div>}</td><td>{row.assignment.resource_type}</td><td>{row.assignment.assignment_type}</td><td><StatusBadge status={row.assignment.status}/></td><td>{formatDateTime(row.assignment.created_at, timezone)}</td><td><span className="footer-note">Decided</span></td></tr> : row.kind === 'batch' ? <>
        <tr key={row.batch.package_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.package_assignment_id)}>
          <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
          <td>📦 {row.batch.package_name} <span className="footer-note">({row.assignments.length} items)</span></td>
          <td>PACKAGE</td>
          <td>{row.assignments[0]?.assignment_type}</td>
          <td><StatusBadge status={new Set(row.assignments.map(a => a.status)).size === 1 ? row.assignments[0].status : 'MIXED'}/></td>
          <td>{formatDateTime(row.assignments[0].created_at, timezone)}</td>
          <td><span className="footer-note">Decided</span></td>
        </tr>
        {expandedBatches.has(row.batch.package_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{formatDateTime(a.created_at, timezone)}</td><td></td></tr>)}
      </> : <>
        <tr key={row.batch.role_assignment_id} style={{cursor:'pointer'}} onClick={() => toggleBatch(row.batch.role_assignment_id)}>
          <td className="user-name">{row.assignments[0]?.user_display_name || row.batch.user_id}</td>
          <td>🏷 {row.batch.role_name} <span className="footer-note">({row.assignments.length} items)</span></td>
          <td>BUSINESS ROLE</td>
          <td>{row.assignments[0]?.assignment_type}</td>
          <td><StatusBadge status={new Set(row.assignments.map(a => a.status)).size === 1 ? row.assignments[0].status : 'MIXED'}/></td>
          <td>{formatDateTime(row.assignments[0].created_at, timezone)}</td>
          <td><span className="footer-note">Decided</span></td>
        </tr>
        {expandedBatches.has(row.batch.role_assignment_id) && row.assignments.map(a => <tr key={a.id} style={{opacity:0.8}}><td className="user-name">↳</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{formatDateTime(a.created_at, timezone)}</td><td></td></tr>)}
      </>)}
    </tbody></table>}</TablePanel>
  </Page>;
}
interface ApiBusinessRoleItem { id: string; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; it_role_label: string | null; provider_id: string | null; provider_name: string | null; resource_code: string | null; naming_convention: string | null; }
interface ApiBusinessRoleOwner { user_id: string; display_name: string | null; email: string | null; }
interface ApiBusinessRole { id: string; name: string; description: string | null; role_type: string; department: string | null; status: string; risk_level: string; is_privileged: boolean; items: ApiBusinessRoleItem[]; owners: ApiBusinessRoleOwner[]; default_approver_id: string | null; default_fallback_approver_id: string | null; fallback_unlock_hours: number | null; review_frequency_days: number | null; assigned_user_count: number; created_at: string; updated_at: string; }
interface ApiBusinessRoleTally { name: string; count: number; }
interface ApiBusinessRoleAnalytics { total_roles: number; active_roles: number; draft_roles: number; disabled_roles: number; archived_roles: number; privileged_roles: number; roles_with_no_owner: number; roles_with_open_sod_conflicts: number; unmapped_entitlements: number; total_assigned_users: number; top_roles_by_holders: ApiBusinessRoleTally[]; }
function BusinessRoleAnalyticsPanel({ analytics }: { analytics: ApiBusinessRoleAnalytics | null }) {
  const na = '—';
  const tiles: Array<[string, string, string?]> = [
    ['Total roles', analytics ? String(analytics.total_roles) : na],
    ['Active', analytics ? String(analytics.active_roles) : na],
    ['Draft', analytics ? String(analytics.draft_roles) : na],
    ['Disabled', analytics ? String(analytics.disabled_roles) : na],
    ['Archived', analytics ? String(analytics.archived_roles) : na],
    ['Privileged', analytics ? String(analytics.privileged_roles) : na],
    ['People with a role', analytics ? String(analytics.total_assigned_users) : na],
    ['Unmapped entitlements', analytics ? String(analytics.unmapped_entitlements) : na, analytics && analytics.unmapped_entitlements > 0 ? 'warning' : undefined],
    ['Roles with no owner', analytics ? String(analytics.roles_with_no_owner) : na, analytics && analytics.roles_with_no_owner > 0 ? 'warning' : undefined],
    ['Roles with open SoD conflicts', analytics ? String(analytics.roles_with_open_sod_conflicts) : na, analytics && analytics.roles_with_open_sod_conflicts > 0 ? 'danger' : undefined],
  ];
  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Business Role Analytics</h2></div>
    <div className="detail-section">
      <div className="stats">{tiles.map(([label, value, tone]) => <div className="stat" key={label} style={tone === 'danger' ? {borderColor:'#c0392b'} : tone === 'warning' ? {borderColor:'#c98a12'} : undefined}><div className="stat-top"><span>{label}</span></div><div className="stat-value" style={tone === 'danger' ? {color:'#c0392b'} : tone === 'warning' ? {color:'#c98a12'} : undefined}>{value}</div></div>)}</div>
      {analytics && analytics.top_roles_by_holders.length > 0 && <div style={{marginTop:18}}>
        <div className="key" style={{marginBottom:6}}><span>Most-held roles</span></div>
        {analytics.top_roles_by_holders.map(t => <div key={t.name} className="user-cell" style={{padding:'4px 0'}}><span>{t.name}</span><span className="badge neutral" style={{marginLeft:'auto'}}>{t.count}</span></div>)}
      </div>}
    </div>
  </section>;
}
const emptyBusinessRoleForm = { name: '', description: '', role_type: 'BUSINESS', department: '', risk_level: 'LOW', is_privileged: false, owner_ids: [] as string[], items: [] as { resource_type: string; resource_id: string; app_role_external_id: string; it_role_label: string }[] };
const ROLE_TYPE_LABELS: Record<string, string> = { BUSINESS: 'Business', IT: 'IT', APPLICATION: 'Application', DIRECTORY: 'Directory', PRIVILEGED: 'Privileged', COMPOSITE: 'Composite' };
interface ApiBusinessRoleAssignItemResult { item_id: string; resource_type: string; resource_id: string; status: string; error_message: string | null; }
interface ApiBusinessRoleHolder { user_id: string; user_display_name: string | null; user_email: string | null; role_assignment_id: string; assigned_at: string; items: ApiBusinessRoleAssignItemResult[]; }
const emptyAssignForm = { user_id: '', assignment_type: 'PERMANENT', expiration_time: '', approver_id: '', justification: '' };
// Business Roles: a named, owned bundle of real entitlements (groups/directory roles/application roles) that raw
// IdP-side access maps onto — Step 1 of the Role Management & Entitlement Mapping plan. See AccessPackages'
// own create/edit pattern above, which this deliberately mirrors.
function BusinessRolesPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: roleList, error, loading, reload } = useApiResource<ApiBusinessRole[]>('/api/v1/business-roles');
  const { data: analytics, reload: reloadAnalytics } = useApiResource<ApiBusinessRoleAnalytics>('/api/v1/business-roles/analytics');
  const { data: unmapped, reload: reloadUnmapped } = useApiResource<ApiBusinessRoleItem[]>('/api/v1/business-roles/unmapped-entitlements');
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyBusinessRoleForm);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [refTarget, setRefTarget] = useState<{ type: string; id: string; code: string; convention: string } | null>(null);
  const [refSaving, setRefSaving] = useState(false);
  const [refMessage, setRefMessage] = useState('');
  const [assigningRole, setAssigningRole] = useState<ApiBusinessRole | null>(null);
  const [assignForm, setAssignForm] = useState(emptyAssignForm);
  const [assignSaving, setAssignSaving] = useState(false);
  const [assignMessage, setAssignMessage] = useState('');
  const [holdersRoleId, setHoldersRoleId] = useState<string | null>(null);
  const { data: holders, reload: reloadHolders } = useApiResource<ApiBusinessRoleHolder[]>(holdersRoleId ? `/api/v1/business-roles/${holdersRoleId}/holders` : '', !!holdersRoleId);

  const targetsFor = (resourceType: string) => resourceType === 'GROUP' ? (groups || []) : resourceType === 'ROLE' ? (roles || []) : (applications || []);
  const addItem = () => setForm({ ...form, items: [...form.items, { resource_type: 'GROUP', resource_id: '', app_role_external_id: '', it_role_label: '' }] });
  const removeItem = (index: number) => setForm({ ...form, items: form.items.filter((_, i) => i !== index) });
  const updateItem = (index: number, patch: Partial<{ resource_type: string; resource_id: string; app_role_external_id: string; it_role_label: string }>) => setForm({ ...form, items: form.items.map((item, i) => i === index ? { ...item, ...patch } : item) });

  const openCreate = () => { setEditingId(null); setForm(emptyBusinessRoleForm); setMessage(''); setOpen(true); };
  const openEdit = (role: ApiBusinessRole) => {
    setEditingId(role.id);
    setForm({ name: role.name, description: role.description || '', role_type: role.role_type, department: role.department || '', risk_level: role.risk_level, is_privileged: role.is_privileged, owner_ids: role.owners.map(o => o.user_id), items: role.items.map(item => ({ resource_type: item.resource_type, resource_id: item.resource_id, app_role_external_id: item.app_role_external_id || '', it_role_label: item.it_role_label || '' })) });
    setMessage(''); setOpen(true);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.name.trim()) { setMessage('Enter a Business Role name.'); return; }
    if (form.items.some(item => !item.resource_id || (item.resource_type === 'APPLICATION' && !item.app_role_external_id))) { setMessage('Complete every item (select a target, and an application role where needed).'); return; }
    setSaving(true); setMessage('');
    try {
      const payload: Record<string, unknown> = {
        name: form.name.trim(), description: form.description.trim() || undefined, role_type: form.role_type, department: form.department.trim() || undefined,
        risk_level: form.risk_level, is_privileged: form.is_privileged, owner_ids: form.owner_ids,
        items: form.items.map(item => ({ resource_type: item.resource_type, resource_id: item.resource_id, app_role_external_id: item.resource_type === 'APPLICATION' ? item.app_role_external_id : undefined, it_role_label: item.it_role_label.trim() || undefined })),
      };
      const response = editingId
        ? await auth.apiRequest(`/api/v1/business-roles/${editingId}`, { method: 'PATCH', body: JSON.stringify(payload) })
        : await auth.apiRequest('/api/v1/business-roles', { method: 'POST', body: JSON.stringify(payload) });
      if (response.ok) { setOpen(false); reload(); reloadUnmapped(); reloadAnalytics(); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to save this Business Role.'); }
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };

  const setStatus = async (role: ApiBusinessRole, status: string) => {
    if (status === 'ARCHIVED' && !window.confirm(`Archive "${role.name}"? It will no longer be assignable.`)) return;
    await auth.apiRequest(`/api/v1/business-roles/${role.id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    reload(); reloadAnalytics();
  };
  const remove = async (role: ApiBusinessRole) => {
    if (!window.confirm(`Delete "${role.name}"? This cannot be undone.`)) return;
    await auth.apiRequest(`/api/v1/business-roles/${role.id}`, { method: 'DELETE' });
    reload(); reloadUnmapped(); reloadAnalytics();
  };

  const openRef = (item: ApiBusinessRoleItem) => { setRefTarget({ type: item.resource_type, id: item.resource_id, code: item.resource_code || '', convention: item.naming_convention || '' }); setRefMessage(''); };
  const saveRef = async () => {
    if (!refTarget) return;
    setRefSaving(true); setRefMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/business-roles/resources/${refTarget.type.toLowerCase()}/${refTarget.id}/reference`, { method: 'PATCH', body: JSON.stringify({ resource_code: refTarget.code.trim() || null, naming_convention: refTarget.convention.trim() || null }) });
      if (response.ok) { setRefTarget(null); reload(); reloadUnmapped(); reloadAnalytics(); }
      else { const body = await response.json().catch(() => null); setRefMessage(body?.error?.message || 'Unable to save this.'); }
    } catch { setRefMessage('Unable to reach the backend.'); } finally { setRefSaving(false); }
  };

  const openAssign = (role: ApiBusinessRole) => { setAssigningRole(role); setAssignForm(emptyAssignForm); setAssignMessage(''); };
  const submitAssign = async () => {
    if (!assigningRole) return;
    if (!assignForm.user_id) { setAssignMessage('Select a person to assign this role to.'); return; }
    if (!assignForm.justification.trim()) { setAssignMessage('Enter a justification.'); return; }
    if (assignForm.assignment_type === 'TEMPORARY' && !assignForm.expiration_time) { setAssignMessage('Set an expiration for a temporary assignment.'); return; }
    setAssignSaving(true); setAssignMessage('');
    try {
      const payload: Record<string, unknown> = { user_id: assignForm.user_id, assignment_type: assignForm.assignment_type, justification: assignForm.justification.trim() };
      if (assignForm.assignment_type === 'TEMPORARY') payload.expiration_time = new Date(assignForm.expiration_time).toISOString();
      if (assignForm.approver_id) payload.approver_id = assignForm.approver_id;
      const response = await auth.apiRequest(`/api/v1/business-roles/${assigningRole.id}/assign`, { method: 'POST', body: JSON.stringify(payload) });
      const body = await response.json().catch(() => null);
      if (response.ok) {
        const failed = (body.results || []).filter((r: ApiBusinessRoleAssignItemResult) => r.status !== 'CREATED');
        setAssignMessage(failed.length === 0 ? `Assigned — every item created for ${body.user_display_name || 'this person'}.` : `Assigned with ${failed.length} item(s) failed: ${failed.map((r: ApiBusinessRoleAssignItemResult) => r.error_message).join('; ')}`);
        reload(); reloadAnalytics(); if (holdersRoleId === assigningRole.id) reloadHolders();
      } else { setAssignMessage(body?.error?.message || 'Unable to assign this Business Role.'); }
    } catch { setAssignMessage('Unable to reach the backend.'); } finally { setAssignSaving(false); }
  };
  const toggleHolders = (role: ApiBusinessRole) => setHoldersRoleId(holdersRoleId === role.id ? null : role.id);

  return <Page eyebrow="ENTITLEMENT MANAGEMENT" title="Business Roles" subtitle="A named, owned bundle of real entitlements — map IdP-side groups, directory roles and application roles onto one business-facing role, with a reference code and naming convention for each." action={<button className="btn btn-primary" onClick={openCreate}><Plus size={14}/> New Business Role</button>}>
    {open && <div className="panel" style={{marginTop:18}}><div className="detail-section">
      <div className="detail-title"><h2>{editingId ? 'Edit Business Role' : 'New Business Role'}</h2></div>
      <form onSubmit={submit}>
        <div className="key-grid" style={{marginBottom:12}}>
          <label className="key"><span>Name</span><input className="select" value={form.name} onChange={event => setForm({...form, name: event.target.value})} placeholder="e.g. Finance Analyst"/></label>
          <label className="key"><span>Role type</span><select className="select" value={form.role_type} onChange={event => setForm({...form, role_type: event.target.value})}>{Object.entries(ROLE_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="key"><span>Department</span><input className="select" value={form.department} onChange={event => setForm({...form, department: event.target.value})}/></label>
          <label className="key"><span>Risk level</span><select className="select" value={form.risk_level} onChange={event => setForm({...form, risk_level: event.target.value})}><option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option><option value="CRITICAL">Critical</option></select></label>
        </div>
        <label className="key" style={{display:'block',marginBottom:12}}><span>Description</span><textarea className="select" style={{width:'100%',minHeight:50}} value={form.description} onChange={event => setForm({...form, description: event.target.value})}/></label>
        <label style={{display:'flex',alignItems:'center',gap:8,fontSize:13,marginBottom:14}}><input type="checkbox" checked={form.is_privileged} onChange={event => setForm({...form, is_privileged: event.target.checked})}/> Privileged role</label>
        <label className="key" style={{display:'block',marginBottom:14}}><span>Owners</span>
          <select className="select" value="" onChange={event => { const id = event.target.value; if (id && !form.owner_ids.includes(id)) setForm({...form, owner_ids: [...form.owner_ids, id]}); }}><option value="">Add an owner…</option>{(users || []).filter(u => !form.owner_ids.includes(u.id)).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select>
          {form.owner_ids.length > 0 && <div style={{display:'flex',flexWrap:'wrap',gap:8,marginTop:8}}>{form.owner_ids.map(id => <span key={id} className="badge neutral" style={{display:'inline-flex',alignItems:'center',gap:6}}>{(users || []).find(u => u.id === id)?.display_name || id}<button type="button" className="btn" aria-label="Remove owner" onClick={() => setForm({...form, owner_ids: form.owner_ids.filter(x => x !== id)})} style={{padding:'0 6px',minWidth:0}}><X size={11}/></button></span>)}</div>}
          <div className="key" style={{marginTop:8}}><span>Owners — can rename this Business Role and remove mapped items from it in their own portal (My Business Roles), nothing else</span></div>
        </label>
        <div className="key" style={{marginBottom:8}}><span>IT Role mapping — the real entitlements this Business Role grants</span></div>
        {form.items.map((item, index) => {
          const itemTargets = targetsFor(item.resource_type);
          const selectedApp = item.resource_type === 'APPLICATION' ? (applications || []).find(a => a.id === item.resource_id) : undefined;
          return <div key={index} style={{display:'flex',gap:10,alignItems:'flex-end',marginBottom:10,flexWrap:'wrap'}}>
            <label className="key" style={{flex:'1 1 140px'}}><span>IT Role label</span><input className="select" style={{width:'100%'}} value={item.it_role_label} onChange={event => updateItem(index, { it_role_label: event.target.value })} placeholder="e.g. Finance-L2-ReadWrite"/></label>
            <label className="key" style={{flex:'1 1 110px'}}><span>Type</span><select className="select" style={{width:'100%'}} value={item.resource_type} onChange={event => updateItem(index, { resource_type: event.target.value, resource_id: '', app_role_external_id: '' })}><option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option></select></label>
            <label className="key" style={{flex:'1 1 160px'}}><span>{item.resource_type === 'GROUP' ? 'Group' : item.resource_type === 'ROLE' ? 'Role' : 'Application'} (available IdP entitlement)</span><select className="select" style={{width:'100%'}} value={item.resource_id} onChange={event => updateItem(index, { resource_id: event.target.value, app_role_external_id: '' })}><option value="">Select...</option>{itemTargets.map((t: ApiGroup | ApiRole | ApiApplication) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
            {item.resource_type === 'APPLICATION' && <label className="key" style={{flex:'1 1 140px'}}><span>Application role</span><select className="select" style={{width:'100%'}} value={item.app_role_external_id} onChange={event => updateItem(index, { app_role_external_id: event.target.value })} disabled={!selectedApp}><option value="">Select a role</option>{(selectedApp?.app_roles || []).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}
            <button type="button" className="btn" aria-label="Remove item" onClick={() => removeItem(index)}><X size={14}/></button>
          </div>;
        })}
        <button type="button" className="btn" onClick={addItem}><Plus size={14}/> Add entitlement</button>
        {message && <div className="notice" style={{marginTop:14}}>{message}</div>}
        <div style={{display:'flex',gap:8,marginTop:18}}><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving...' : 'Save'}</button><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button></div>
      </form>
    </div></div>}
    <BusinessRoleAnalyticsPanel analytics={analytics}/>
    <TablePanel toolbar={undefined}>
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !roleList || roleList.length === 0 ? <div className="empty">No Business Roles yet.</div> : <table><thead><tr><th>Name</th><th>Type</th><th>Department</th><th>Items</th><th>Owners</th><th>Risk</th><th>Status</th><th>Holders</th><th></th></tr></thead><tbody>
        {roleList.map(role => <Fragment key={role.id}>
        <tr>
          <td className="user-name">{role.name}{role.is_privileged && <span className="badge warning" style={{marginLeft:6}}>Privileged</span>}<div className="user-email">{role.description}</div></td>
          <td>{ROLE_TYPE_LABELS[role.role_type] || role.role_type}</td>
          <td>{role.department || '—'}</td>
          <td style={{whiteSpace:'normal',maxWidth:280}}>{role.items.length === 0 ? '—' : role.items.map(item => <div key={item.id} className="user-email" style={{margin:'2px 0'}}>{item.it_role_label ? `${item.it_role_label} · ` : ''}{item.resource_display_name}{item.resource_code && <span className="footer-note"> ({item.resource_code})</span>} <button type="button" className="btn" style={{padding:'0 6px',minWidth:0,fontSize:11}} onClick={() => openRef(item)}>Code/Naming</button></div>)}</td>
          <td>{role.owners.map(o => o.display_name).join(', ') || '—'}</td>
          <td><span className={`badge ${role.risk_level === 'CRITICAL' || role.risk_level === 'HIGH' ? 'danger' : role.risk_level === 'MEDIUM' ? 'warning' : 'neutral'}`}>{role.risk_level}</span></td>
          <td><StatusBadge status={role.status === 'ACTIVE' ? 'Active' : role.status === 'DRAFT' ? 'SCHEDULED' : role.status === 'ARCHIVED' ? 'Disabled' : 'FAILED'}/></td>
          <td>{role.assigned_user_count > 0 ? <button className="btn" style={{padding:'2px 10px',fontSize:12}} onClick={() => toggleHolders(role)}>{role.assigned_user_count} {holdersRoleId === role.id ? '▲' : '▼'}</button> : '0'}</td>
          <td><span style={{display:'flex',gap:6,flexWrap:'wrap'}}>
            <button className="btn" onClick={() => openEdit(role)}>Edit</button>
            {role.status === 'ACTIVE' && <button className="btn btn-primary" onClick={() => openAssign(role)}>Assign</button>}
            {role.status === 'DRAFT' && <button className="btn" onClick={() => void setStatus(role, 'ACTIVE')}>Activate</button>}
            {role.status === 'ACTIVE' && <button className="btn" onClick={() => void setStatus(role, 'DISABLED')}>Disable</button>}
            {role.status === 'DISABLED' && <button className="btn" onClick={() => void setStatus(role, 'ACTIVE')}>Re-enable</button>}
            {role.status !== 'ARCHIVED' && role.assigned_user_count === 0 && <button className="btn" onClick={() => void remove(role)}>Delete</button>}
            {role.status !== 'ARCHIVED' && role.assigned_user_count > 0 && <button className="btn" onClick={() => void setStatus(role, 'ARCHIVED')}>Archive</button>}
          </span></td>
        </tr>
        {holdersRoleId === role.id && <tr><td colSpan={9} style={{padding:0,background:'#fafbfb'}}>
          <div style={{padding:12}}>
            {!holders ? <p className="subtitle" style={{margin:0}}>Loading holders...</p> : holders.length === 0 ? <p className="subtitle" style={{margin:0}}>Nobody currently holds this role.</p> : holders.map(holder => <div key={holder.role_assignment_id} style={{marginBottom:10}}>
              <strong style={{fontSize:13}}>{holder.user_display_name}</strong>{holder.user_email && <span className="user-email" style={{marginLeft:6}}>{holder.user_email}</span>}
              <div style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:4}}>{holder.items.map(item => <span key={item.item_id} className="badge neutral">{item.resource_type}: <StatusBadge status={item.status === 'ACTIVE' ? 'Active' : item.status}/></span>)}</div>
            </div>)}
          </div>
        </td></tr>}
        </Fragment>)}
      </tbody></table>}
    </TablePanel>

    {assigningRole && <div className="panel" style={{marginTop:18}}><div className="detail-section">
      <div className="detail-title"><h2>Assign "{assigningRole.name}"</h2></div>
      <p className="subtitle" style={{marginTop:0}}>Grants every mapped entitlement to this person — through the same approval/activation flow as any other access. Leave the approver blank to land directly ELIGIBLE, ready to activate.</p>
      <div className="key-grid" style={{marginBottom:12}}>
        <label className="key"><span>Person</span><select className="select" value={assignForm.user_id} onChange={event => setAssignForm({...assignForm, user_id: event.target.value})}><option value="">Select a person</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
        <label className="key"><span>Duration</span><select className="select" value={assignForm.assignment_type} onChange={event => setAssignForm({...assignForm, assignment_type: event.target.value})}><option value="PERMANENT">Permanent (eligible indefinitely)</option><option value="TEMPORARY">Temporary</option></select></label>
        {assignForm.assignment_type === 'TEMPORARY' && <label className="key"><span>Expires</span><input className="select" type="datetime-local" value={assignForm.expiration_time} onChange={event => setAssignForm({...assignForm, expiration_time: event.target.value})}/></label>}
        <label className="key"><span>Approver (optional)</span><select className="select" value={assignForm.approver_id} onChange={event => setAssignForm({...assignForm, approver_id: event.target.value})}><option value="">No approval needed</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
      </div>
      <label className="key" style={{display:'block',marginBottom:12}}><span>Justification</span><input className="select" style={{width:'100%'}} value={assignForm.justification} onChange={event => setAssignForm({...assignForm, justification: event.target.value})} placeholder="Why does this person need this role?"/></label>
      {assignMessage && <div className="notice" style={{marginBottom:12}}>{assignMessage}</div>}
      <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={assignSaving} onClick={() => void submitAssign()}>{assignSaving ? 'Assigning...' : 'Assign'}</button><button className="btn" onClick={() => setAssigningRole(null)}>Close</button></div>
    </div></div>}

    <section className="panel" style={{marginTop:18}}>
      <div className="panel-head"><h2>Unmapped entitlements</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{marginTop:0,marginBottom:12}}>Every Group, Role, and Application not currently mapped to any Business Role — a live view, not a stored list.</p>
        {!unmapped || unmapped.length === 0 ? <p className="subtitle" style={{margin:0}}>Everything is mapped to a Business Role.</p> : <div className="table-wrap"><table><thead><tr><th>Entitlement</th><th>Type</th><th>Provider</th><th>Resource Code</th><th>Naming Convention</th><th></th></tr></thead><tbody>
          {unmapped.map(item => <tr key={`${item.resource_type}-${item.resource_id}`}>
            <td className="user-name">{item.resource_display_name}</td>
            <td>{item.resource_type}</td>
            <td>{item.provider_name || '—'}</td>
            <td>{item.resource_code || '—'}</td>
            <td>{item.naming_convention || '—'}</td>
            <td><button className="btn" onClick={() => openRef(item)}>Edit reference</button></td>
          </tr>)}
        </tbody></table></div>}
      </div>
    </section>

    {refTarget && <div className="panel" style={{marginTop:18}}><div className="detail-section">
      <div className="detail-title"><h2>Resource reference</h2></div>
      <p className="subtitle" style={{marginTop:0}}>Purely for reference — never used to look anything up internally, and never enforced.</p>
      <div className="key-grid" style={{marginBottom:10}}>
        <label className="key"><span>Resource Code</span><input className="select" value={refTarget.code} onChange={event => setRefTarget({...refTarget, code: event.target.value})} placeholder="e.g. RES-GRP-014"/></label>
        <label className="key"><span>Naming Convention</span><input className="select" value={refTarget.convention} onChange={event => setRefTarget({...refTarget, convention: event.target.value})} placeholder="e.g. SG-{DEPT}-{LEVEL}"/></label>
      </div>
      {refMessage && <div className="notice" style={{marginBottom:10}}>{refMessage}</div>}
      <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={refSaving} onClick={() => void saveRef()}>{refSaving ? 'Saving...' : 'Save'}</button><button className="btn" onClick={() => setRefTarget(null)}>Cancel</button></div>
    </div></div>}
  </Page>;
}
interface ApiNamedPolicyRef { id: string; name: string; }
interface ApiGroupAccessSummary { member_count: number; active_assignment_count: number; birthright_policies: ApiNamedPolicyRef[]; sod_policies: ApiNamedPolicyRef[]; access_packages: ApiNamedPolicyRef[]; }
function GroupsPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
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
    <TablePanel toolbar={<Toolbar placeholder="Search groups" searchValue={search} onSearchChange={setSearch} filterLabel="All groups" filterValue={privilegedFilter} onFilterChange={setPrivilegedFilter} filterOptions={[{value:'true',label:'Privileged'},{value:'false',label:'Standard'}]}/>}>{loading ? <div className="empty">Loading groups...</div> : error ? <div className="empty">{error}</div> : !groups || groups.length === 0 ? <div className="empty">No groups found.</div> : filteredGroups.length === 0 ? <div className="empty">No groups match this filter.</div> : <table><thead><tr><th>Name</th><th>Description</th><th>Privileged</th><th>Status</th><th>Last synced</th><th></th></tr></thead><tbody>{filteredGroups.map(g => <tr key={g.id}><td><Link to={`/admin/groups/${g.id}`} className="user-name">{g.name}</Link></td><td>{g.description || '—'}</td><td><span className={`risk ${g.is_privileged ? 'risk-high' : 'risk-low'}`}>{g.is_privileged ? 'Privileged' : 'Standard'}</span></td><td><StatusBadge status={g.status}/></td><td>{g.last_synced_at ? formatDateTime(g.last_synced_at, timezone) : 'Never'}</td><td><Link to={`/admin/groups/${g.id}`}><ChevronRight size={15} color="#829198"/></Link></td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}

interface ApiGroupRoleMapping { id: string; source_group_id: string; source_group_name: string; resource_type: string; resource_id: string; resource_display_name: string; app_role_external_id: string | null; assignment_type: string; status: string; created_at: string; updated_at: string; }
const emptyGroupRoleMappingForm = { resource_type: 'ROLE', resource_id: '', app_role_external_id: '', assignment_type: 'PERMANENT' };
interface ApiGroupOwner { user_id: string; display_name: string | null; email: string | null; }
// AccessPilot-side group owners (nothing is written to Entra) — used e.g. to suggest a reviewer when an Access
// Review is scoped to this group.
function GroupOwnersSection({ groupId }: { groupId: string }) {
  const auth = useAuth();
  const { data: owners, reload } = useApiResource<ApiGroupOwner[]>(`/api/v1/groups/${groupId}/owners`);
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const [message, setMessage] = useState('');
  const save = async (userIds: string[]) => {
    setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/groups/${groupId}/owners`, { method: 'PUT', body: JSON.stringify({ user_ids: userIds }) });
      if (response.ok) reload(); else setMessage((await response.json().catch(() => null))?.error?.message || 'Unable to update owners.');
    } catch { setMessage('Unable to reach the backend.'); }
  };
  const ids = (owners || []).map(o => o.user_id);
  return <>
    <div className="detail-title"><h2>Owners</h2></div>
    <p className="subtitle" style={{marginTop:-8,marginBottom:12}}>People accountable for this group. Recorded in AccessPilot only — used to suggest the reviewer of an Access Review for this group; nothing changes in Entra.</p>
    <select className="select" value="" onChange={event => { const id = event.target.value; if (id && !ids.includes(id)) void save([...ids, id]); }}><option value="">Add an owner…</option>{(users || []).filter(u => !ids.includes(u.id)).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select>
    {message && <div className="notice" style={{marginTop:10}}>{message}</div>}
    {(owners || []).length === 0 ? <p className="subtitle" style={{marginTop:10}}>No owners yet.</p> : <div style={{display:'flex',flexWrap:'wrap',gap:8,marginTop:10}}>{(owners || []).map(o => <span key={o.user_id} className="badge neutral" style={{display:'inline-flex',alignItems:'center',gap:6}}>{o.display_name || o.user_id}<button type="button" className="btn" aria-label="Remove owner" onClick={() => void save(ids.filter(x => x !== o.user_id))} style={{padding:'0 6px',minWidth:0}}><X size={11}/></button></span>)}</div>}
  </>;
}
function GroupDetail() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { id } = useParams();
  const { data: group, error, loading } = useApiResource<ApiGroup>(`/api/v1/groups/${id}`);
  const { data: members, error: membersError, loading: membersLoading } = useApiResource<ApiUser[]>(`/api/v1/groups/${id}/members`);
  const { data: summary } = useApiResource<ApiGroupAccessSummary>(`/api/v1/groups/${id}/access-summary`);
  const { data: mappings, error: mappingsError, loading: mappingsLoading, reload: reloadMappings } = useApiResource<ApiGroupRoleMapping[]>(`/api/v1/policies/group-role-mappings?group_id=${id}`);
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const { data: businessRoles } = useApiResource<ApiBusinessRole[]>('/api/v1/business-roles');
  const [mappingOpen, setMappingOpen] = useState(false);
  const [mappingSaving, setMappingSaving] = useState(false);
  const [mappingMessage, setMappingMessage] = useState('');
  const [mappingForm, setMappingForm] = useState(emptyGroupRoleMappingForm);
  const mappingTargets: Array<ApiRole | ApiApplication | ApiBusinessRole> = mappingForm.resource_type === 'ROLE' ? (roles || []) : mappingForm.resource_type === 'BUSINESS_ROLE' ? (businessRoles || []) : (applications || []);
  const selectedApplication = mappingForm.resource_type === 'APPLICATION' ? (applications || []).find(a => a.id === mappingForm.resource_id) : undefined;

  const createMapping = async () => {
    if (!mappingForm.resource_id) { setMappingMessage('Select a target.'); return; }
    setMappingSaving(true); setMappingMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/policies/group-role-mappings', { method: 'POST', body: JSON.stringify({ source_group_id: id, resource_type: mappingForm.resource_type, resource_id: mappingForm.resource_id, app_role_external_id: mappingForm.app_role_external_id || undefined, assignment_type: mappingForm.assignment_type }) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setMappingOpen(false); setMappingForm(emptyGroupRoleMappingForm); reloadMappings(); }
      else setMappingMessage(body?.error?.message || 'Unable to create this mapping.');
    } catch { setMappingMessage('Unable to reach the backend.'); } finally { setMappingSaving(false); }
  };
  const toggleMappingStatus = async (mapping: ApiGroupRoleMapping) => {
    await auth.apiRequest(`/api/v1/policies/group-role-mappings/${mapping.id}`, { method: 'PATCH', body: JSON.stringify({ status: mapping.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' }) });
    reloadMappings();
  };
  const removeMapping = async (mapping: ApiGroupRoleMapping) => {
    if (!window.confirm(`Delete this mapping to "${mapping.resource_display_name}"? This does not remove access already granted.`)) return;
    await auth.apiRequest(`/api/v1/policies/group-role-mappings/${mapping.id}`, { method: 'DELETE' });
    reloadMappings();
  };

  if (loading) return <Page eyebrow="ADMINISTRATION" title="Loading..." subtitle=""><div className="empty">Loading group...</div></Page>;
  if (error || !group) return <Page eyebrow="ADMINISTRATION" title="Group" subtitle=""><div className="empty">{error || 'Group not found.'}</div></Page>;

  return <Page eyebrow="ADMINISTRATION" title={group.name} subtitle={group.description || 'No description on file'} action={<Link to="/admin/groups" className="btn">Back to groups</Link>}>
    <div className="detail-layout">
      <section className="panel">
        <div className="detail-section">
          <div className="detail-title"><h2>Overview</h2><StatusBadge status={group.status}/></div>
          <div className="key-grid">
            <div className="key"><span>Privileged</span><strong>{group.is_privileged ? 'Yes — assignable to a directory role' : 'No'}</strong></div>
            <div className="key"><span>External ID</span><strong>{group.external_id}</strong></div>
            <div className="key"><span>Members</span><strong>{summary ? summary.member_count : '…'}</strong></div>
            <div className="key"><span>Active AccessPilot grants</span><strong>{summary ? summary.active_assignment_count : '…'}</strong></div>
            <div className="key"><span>Last synced</span><strong>{group.last_synced_at ? formatDateTime(group.last_synced_at, timezone) : 'Never'}</strong></div>
          </div>
        </div>
        <div className="detail-section"><GroupOwnersSection groupId={id || ''}/></div>
        <div className="detail-section">
          <div className="detail-title"><h2>Members</h2></div>
          {membersLoading ? <div className="empty">Loading members...</div> : membersError ? <div className="empty">{membersError}</div> : !members || members.length === 0 ? <div className="empty">No members synced for this group.</div> : <div className="table-wrap"><table><thead><tr><th>User</th><th>Department</th><th>Status</th></tr></thead><tbody>
            {members.map(member => <tr key={member.id}><td><Link to={`/admin/users/${member.id}`} className="user-cell"><span className="avatar">{initialsFor(member.display_name)}</span><span><span className="user-name">{member.display_name}</span><span className="user-email">{member.email}</span></span></Link></td><td>{member.department || '—'}</td><td><StatusBadge status={member.status}/></td></tr>)}
          </tbody></table></div>}
        </div>
      </section>
      <section className="panel">
        <div className="detail-section">
          <div className="detail-title"><h2>What's attached to this group</h2></div>
          <p className="subtitle" style={{ marginTop: 0, marginBottom: 16 }}>Groups have no native "apps/roles" of their own — this is everything else in AccessPilot that references this group.</p>
          <div className="key" style={{ marginBottom: 6 }}><span>Access packages ({summary?.access_packages.length ?? 0})</span></div>
          {!summary || summary.access_packages.length === 0 ? <div className="empty" style={{ padding: '8px 0' }}>Not included in any access package.</div> : summary.access_packages.map(item => <div key={item.id} style={{ padding: '4px 0' }}>{item.name}</div>)}
        </div>
        <div className="detail-section">
          <div className="key" style={{ marginBottom: 6 }}><span>Birthright policies ({summary?.birthright_policies.length ?? 0})</span></div>
          {!summary || summary.birthright_policies.length === 0 ? <div className="empty" style={{ padding: '8px 0' }}>No birthright policy grants this group.</div> : summary.birthright_policies.map(item => <div key={item.id} style={{ padding: '4px 0' }}>{item.name}</div>)}
        </div>
        <div className="detail-section">
          <div className="key" style={{ marginBottom: 6 }}><span>Separation of Duties rules ({summary?.sod_policies.length ?? 0})</span></div>
          {!summary || summary.sod_policies.length === 0 ? <div className="empty" style={{ padding: '8px 0' }}>Not referenced by any SoD rule.</div> : summary.sod_policies.map(item => <div key={item.id} style={{ padding: '4px 0' }}>{item.name}</div>)}
        </div>
      </section>
    </div>

    <section className="panel">
      <div className="panel-head"><h2>Role &amp; app mappings</h2><button className="btn btn-primary" onClick={() => { setMappingOpen(true); setMappingMessage(''); }}><Plus size={14}/> Add mapping</button></div>
      <div className="detail-section">
        <p className="subtitle" style={{ marginTop: 0, marginBottom: 14 }}>Membership-driven auto-assignment: everyone currently in <strong>{group.name}</strong> becomes <strong>eligible</strong> for the linked Role or Application(+app role) — the same real, audited grant a birthright policy makes, just triggered by group membership instead of a department/job-title match. Re-evaluated automatically whenever someone joins or leaves this group, and immediately whenever a mapping here is added, disabled, or removed.</p>
        {mappingOpen && <div className="notice" style={{ marginBottom: 14 }}>
          <div className="key-grid" style={{ marginBottom: 10 }}>
            <label className="key"><span>Grant</span><select className="select" value={mappingForm.resource_type} onChange={event => setMappingForm({ ...mappingForm, resource_type: event.target.value, resource_id: '', app_role_external_id: '' })}><option value="ROLE">Directory role</option><option value="APPLICATION">Application</option><option value="BUSINESS_ROLE">Business Role</option></select></label>
            <label className="key"><span>Target</span><select className="select" value={mappingForm.resource_id} onChange={event => setMappingForm({ ...mappingForm, resource_id: event.target.value, app_role_external_id: '' })}><option value="">Select a target</option>{mappingTargets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
            {mappingForm.resource_type === 'APPLICATION' && selectedApplication && selectedApplication.app_roles && selectedApplication.app_roles.length > 0 && <label className="key"><span>App role</span><select className="select" value={mappingForm.app_role_external_id} onChange={event => setMappingForm({ ...mappingForm, app_role_external_id: event.target.value })}><option value="">Default access</option>{selectedApplication.app_roles.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>}
            <label className="key"><span>Assignment type</span><select className="select" value={mappingForm.assignment_type} onChange={event => setMappingForm({ ...mappingForm, assignment_type: event.target.value })}><option value="PERMANENT">Permanent</option><option value="TEMPORARY">Temporary</option></select></label>
          </div>
          <div style={{ display: 'flex', gap: 8 }}><button className="btn btn-primary" disabled={mappingSaving} onClick={() => void createMapping()}>{mappingSaving ? 'Saving...' : 'Create mapping'}</button><button className="btn" onClick={() => { setMappingOpen(false); setMappingForm(emptyGroupRoleMappingForm); }}>Cancel</button></div>
        </div>}
        {mappingMessage && <div className="notice" style={{ marginBottom: 14 }}>{mappingMessage}</div>}
        <div className="table-wrap">{mappingsLoading ? <div className="empty">Loading...</div> : mappingsError ? <div className="empty">{mappingsError}</div> : !mappings || mappings.length === 0 ? <div className="empty">No role or app mappings for this group yet.</div> : <table><thead><tr><th>Grants</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>
          {mappings.map(mapping => <tr key={mapping.id}><td className="user-name">{mapping.resource_type === 'ROLE' ? 'Role' : mapping.resource_type === 'BUSINESS_ROLE' ? 'Business Role' : 'Application'}: {mapping.resource_display_name}</td><td>{mapping.assignment_type}</td><td><StatusBadge status={mapping.status}/></td><td style={{ display: 'flex', gap: 6 }}><button className="btn" onClick={() => void toggleMappingStatus(mapping)}>{mapping.status === 'ACTIVE' ? 'Disable' : 'Enable'}</button><button className="btn" onClick={() => void removeMapping(mapping)}>Delete</button></td></tr>)}
        </tbody></table>}</div>
      </div>
    </section>
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
interface ApiBirthrightPolicy { id: string; name: string; match_field: string | null; match_value: string | null; resource_type: string | null; resource_id: string | null; app_role_external_id: string | null; assignment_type: string; status: string; external_policy_id: string | null; is_advanced: boolean; conditions_count: number; actions_count: number; reconciliation_enabled: boolean; created_at: string; }
interface ApiBirthrightActionResolution { resourceType: string; resource: string; found: boolean; resolvedId: string | null; resolvedName: string | null; error: string | null; }
const birthrightJsonTemplate = `{
  "policyId": "BR-001",
  "policyType": "BIRTHRIGHT",
  "name": "IT Employee Access",
  "scope": { "identityType": "EMPLOYEE" },
  "rule": {
    "operator": "AND",
    "conditions": [
      { "field": "department", "operator": "EQUALS", "value": "IT" },
      { "field": "employmentStatus", "operator": "EQUALS", "value": "ACTIVE" }
    ]
  },
  "actions": [
    { "action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES" },
    { "action": "ASSIGN", "resourceType": "APPLICATION", "resource": "Microsoft-365", "appRoleExternalId": "" }
  ],
  "reconciliation": { "enabled": true, "removeWhenConditionFails": true },
  "audit": { "enabled": true }
}`;
function BirthrightPoliciesPanel() {
  const auth = useAuth();
  const { data: birthrightPolicies, error: birthrightError, loading: birthrightLoading, reload: reloadBirthright } = useApiResource<ApiBirthrightPolicy[]>('/api/v1/policies/birthright');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const { data: packages } = useApiResource<ApiPackage[]>('/api/v1/packages');
  const { data: businessRoles } = useApiResource<ApiBusinessRole[]>('/api/v1/business-roles');
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const emptyForm = { name: '', match_field: 'department', match_value: '', resource_type: 'GROUP', resource_id: '', assignment_type: 'PERMANENT' };
  const [form, setForm] = useState(emptyForm);
  const { data: departmentList } = useApiResource<{ id: string; name: string }[]>('/api/v1/policies/departments');
  const [editingPolicyId, setEditingPolicyId] = useState<string | null>(null);
  const recheckText = (r: { users_checked: number; granted: number; revoked: number } | null | undefined) => r ? `Saved. Re-checked ${r.users_checked} ${r.users_checked === 1 ? 'person' : 'people'}: ${r.granted} newly eligible, ${r.revoked} access removed.` : 'Saved.';
  const openPolicyEdit = (policy: ApiBirthrightPolicy) => { setEditingPolicyId(policy.id); setForm({ ...emptyForm, name: policy.name, match_field: policy.match_field || 'department', match_value: policy.match_value || '' }); setOpen(true); setMessage(''); };
  const targets: Array<ApiGroup | ApiRole | ApiApplication | ApiPackage | ApiBusinessRole> = form.resource_type === 'GROUP' ? (groups || []) : form.resource_type === 'ROLE' ? (roles || []) : form.resource_type === 'APPLICATION' ? (applications || []) : form.resource_type === 'BUSINESS_ROLE' ? (businessRoles || []) : (packages || []);
  const resourceLabel = (p: ApiBirthrightPolicy) => (p.resource_type === 'GROUP' ? groups : p.resource_type === 'ROLE' ? roles : p.resource_type === 'APPLICATION' ? applications : p.resource_type === 'BUSINESS_ROLE' ? businessRoles : packages)?.find(t => t.id === p.resource_id)?.name || p.resource_id;

  // JSON create/view/edit — a separate, additive path alongside the simple form above. `jsonPolicyId === 'new'`
  // means creating (POST .../json); any other id means viewing/editing that existing policy (GET then PUT
  // .../json) — works uniformly whether that policy was originally created here or via the simple form, since
  // the backend represents any policy in this same JSON shape.
  const [jsonPolicyId, setJsonPolicyId] = useState<string | null>(null);
  const [jsonText, setJsonText] = useState('');
  const [jsonSaving, setJsonSaving] = useState(false);
  const [jsonMessage, setJsonMessage] = useState('');
  const [checkResults, setCheckResults] = useState<ApiBirthrightActionResolution[] | null>(null);
  const [checking, setChecking] = useState(false);
  const openJsonCreate = () => { setJsonPolicyId('new'); setJsonText(birthrightJsonTemplate); setJsonMessage(''); setCheckResults(null); };
  const openJsonView = async (policy: ApiBirthrightPolicy) => {
    setJsonPolicyId(policy.id); setJsonText('Loading...'); setJsonMessage(''); setCheckResults(null);
    try {
      const response = await auth.apiRequest(`/api/v1/policies/birthright/${policy.id}/json`);
      const body = await response.json().catch(() => null);
      setJsonText(response.ok ? JSON.stringify(body, null, 2) : '');
      if (!response.ok) setJsonMessage(body?.error?.message || 'Unable to load this policy.');
    } catch { setJsonText(''); setJsonMessage('Unable to reach the backend.'); }
  };
  // "Check resources" — resolves every action's `resource` name (e.g. an Application name) against the real
  // directory and shows what it found, so the admin can confirm (Yes) or go back and fix it (No) before saving.
  // A preview only: hitting this never creates or changes anything.
  const checkResources = async () => {
    let parsed: { actions?: unknown };
    try { parsed = JSON.parse(jsonText); } catch { setJsonMessage('Not valid JSON — fix the syntax and try again.'); return; }
    if (!Array.isArray(parsed.actions) || parsed.actions.length === 0) { setJsonMessage('Add at least one entry under "actions" first.'); return; }
    setChecking(true); setJsonMessage(''); setCheckResults(null);
    try {
      const response = await auth.apiRequest('/api/v1/policies/birthright/resolve-actions', { method: 'POST', body: JSON.stringify({ actions: parsed.actions }) });
      const body = await response.json().catch(() => null);
      if (response.ok) setCheckResults(body.results);
      else setJsonMessage(body?.error?.message || 'Unable to check these resources.');
    } catch { setJsonMessage('Unable to reach the backend.'); } finally { setChecking(false); }
  };
  const saveJson = async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(jsonText); } catch { setJsonMessage('Not valid JSON — fix the syntax and try again.'); return; }
    setJsonSaving(true); setJsonMessage('');
    try {
      const isNew = jsonPolicyId === 'new';
      const response = await auth.apiRequest(isNew ? '/api/v1/policies/birthright/json' : `/api/v1/policies/birthright/${jsonPolicyId}/json`, { method: isNew ? 'POST' : 'PUT', body: JSON.stringify(parsed) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setJsonPolicyId(null); reloadBirthright(); setMessage('Saved. Everyone this policy matches (or used to match) was re-checked.'); }
      else setJsonMessage(body?.error?.message || 'Unable to save this policy.');
    } catch { setJsonMessage('Unable to reach the backend.'); } finally { setJsonSaving(false); }
  };

  const create = async () => {
    if (!form.name.trim() || !form.match_value.trim() || (!editingPolicyId && !form.resource_id)) { setMessage('Complete every field.'); return; }
    setSaving(true); setMessage('');
    try {
      const response = editingPolicyId
        ? await auth.apiRequest(`/api/v1/policies/birthright/${editingPolicyId}`, { method: 'PATCH', body: JSON.stringify({ name: form.name.trim(), match_value: form.match_value.trim() }) })
        : await auth.apiRequest('/api/v1/policies/birthright', { method: 'POST', body: JSON.stringify(form) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setOpen(false); setEditingPolicyId(null); setForm(emptyForm); reloadBirthright(); setMessage(recheckText(body?.recheck) + (body?.warnings?.length ? ` Warning: ${body.warnings.join(' ')}` : '')); }
      else setMessage(body?.error?.message || 'Unable to save this policy.');
    } catch { setMessage('Unable to save this policy.'); } finally { setSaving(false); }
  };
  const toggleStatus = async (policy: ApiBirthrightPolicy) => {
    const response = await auth.apiRequest(`/api/v1/policies/birthright/${policy.id}`, { method: 'PATCH', body: JSON.stringify({ status: policy.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' }) });
    const body = await response.json().catch(() => null);
    setMessage(response.ok ? recheckText(body?.recheck) : (body?.error?.message || 'Unable to change this policy.'));
    reloadBirthright();
  };
  const remove = async (policy: ApiBirthrightPolicy) => {
    if (!window.confirm(`Delete birthright policy "${policy.name}"? This does not remove access already granted.`)) return;
    await auth.apiRequest(`/api/v1/policies/birthright/${policy.id}`, { method: 'DELETE' });
    reloadBirthright();
  };

  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Birthright policies</h2><div style={{display:'flex',gap:8}}><button className="btn" onClick={openJsonCreate}><Plus size={14}/> New via JSON</button><button className="btn btn-primary" onClick={() => { setEditingPolicyId(null); setForm(emptyForm); setOpen(true); setMessage(''); }}><Plus size={14}/> Add rule</button></div></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginBottom:14}}>Attribute-driven auto-assignment: when a joiner or mover's <code>department</code> or <code>job title</code> matches a rule, they're automatically made <strong>eligible</strong> for that Group, Role, Application, or every item in an Access Package — same as any other assignment, still activated by hand. A Package rule grants each of its items individually (not as one grouped package request), so mover reconciliation can revoke exactly the items whose rule no longer applies. For a rule with several AND/OR conditions or several grants at once, use <strong>New via JSON</strong> — any existing policy can also be opened as JSON via its <strong>View/Edit JSON</strong> action. Evaluated automatically whenever an Onboarding CSV import is committed.</p>
      {jsonPolicyId && <form role="dialog" aria-modal="true" className="notice" style={{marginBottom:14}} onSubmit={event => { event.preventDefault(); void saveJson(); }}>
        <div className="key" style={{marginBottom:8}}><span>{jsonPolicyId === 'new' ? 'New policy — JSON' : 'Edit policy — JSON'}</span></div>
        <textarea className="select" style={{width:'100%',minHeight:320,fontFamily:'monospace',fontSize:12,whiteSpace:'pre'}} value={jsonText} onChange={event => { setJsonText(event.target.value); setCheckResults(null); }} spellCheck={false}/>
        {jsonMessage && <div className="notice" style={{marginTop:10}}>{jsonMessage}</div>}
        {checkResults && <div className="key-grid" style={{marginTop:12,marginBottom:4}}>
          {checkResults.map((r, i) => <div key={i} className="key"><span>{r.resourceType}: {r.resource}</span><strong style={{color: r.found ? '#1a7f4f' : '#b3261e'}}>{r.found ? `✓ Found — "${r.resolvedName}"` : `✗ Not found${r.error ? ` — ${r.error}` : ''}`}</strong></div>)}
        </div>}
        <div style={{display:'flex',gap:8,marginTop:12,flexWrap:'wrap'}}>
          <button type="button" className="btn" disabled={checking} onClick={() => void checkResources()}>{checking ? 'Checking...' : 'Check resources'}</button>
          {checkResults && (checkResults.every(r => r.found)
            ? <><button type="button" className="btn btn-primary" disabled={jsonSaving} onClick={() => void saveJson()}>Yes, looks correct — Save</button><button type="button" className="btn" onClick={() => setCheckResults(null)}>No, let me fix it</button></>
            : <span className="footer-note">Fix the resource(s) marked ✗ above, then check again.</span>)}
          <button type="submit" className="btn btn-primary" disabled={jsonSaving}>{jsonSaving ? 'Saving...' : 'Save'}</button>
          <button type="button" className="btn" onClick={() => setJsonPolicyId(null)}>Cancel</button>
        </div>
      </form>}
      {open && <div className="notice" style={{marginBottom:14}}>
        <div className="key-grid" style={{marginBottom:10}}>
          <label className="key"><span>Rule name</span><input className="select" value={form.name} onChange={event => setForm({...form, name: event.target.value})} placeholder="e.g. Finance department access"/></label>
          <label className="key"><span>Match on</span><select className="select" disabled={Boolean(editingPolicyId)} value={form.match_field} onChange={event => setForm({...form, match_field: event.target.value, match_value: ''})}><option value="department">Department</option><option value="job_title">Job title</option></select></label>
          <label className="key"><span>Equals</span>{form.match_field === 'department'
            ? <select className="select" value={form.match_value} onChange={event => setForm({...form, match_value: event.target.value})}><option value="">Select a department</option>{form.match_value && !(departmentList || []).some(d => d.name === form.match_value) && <option value={form.match_value}>{form.match_value} (not in the department list)</option>}{(departmentList || []).map(d => <option key={d.id} value={d.name}>{d.name}</option>)}</select>
            : <input className="select" value={form.match_value} onChange={event => setForm({...form, match_value: event.target.value})} placeholder="e.g. Senior Analyst"/>}</label>
          {!editingPolicyId && <>
          <label className="key"><span>Grant</span><select className="select" value={form.resource_type} onChange={event => setForm({...form, resource_type: event.target.value, resource_id: ''})}><option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option><option value="PACKAGE">Access Package</option><option value="BUSINESS_ROLE">Business Role</option></select></label>
          <label className="key"><span>Target</span><select className="select" value={form.resource_id} onChange={event => setForm({...form, resource_id: event.target.value})}><option value="">Select a target</option>{targets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
          <label className="key"><span>Assignment type</span><select className="select" value={form.assignment_type} onChange={event => setForm({...form, assignment_type: event.target.value})}><option value="PERMANENT">Permanent</option><option value="TEMPORARY">Temporary</option></select></label>
          </>}
        </div>
        <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={saving} onClick={() => void create()}>{saving ? 'Saving...' : editingPolicyId ? 'Save changes' : 'Create rule'}</button><button className="btn" onClick={() => { setOpen(false); setEditingPolicyId(null); setForm(emptyForm); }}>Cancel</button></div>
      </div>}
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      <div className="table-wrap">{birthrightLoading ? <div className="empty">Loading...</div> : birthrightError ? <div className="empty">{birthrightError}</div> : !birthrightPolicies || birthrightPolicies.length === 0 ? <div className="empty">No birthright policies yet.</div> : <table><thead><tr><th>Rule</th><th>Condition</th><th>Grants</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{birthrightPolicies.map(p => <tr key={p.id}>
        <td className="user-name">{p.name}{p.external_policy_id ? <span className="footer-note" style={{display:'block'}}>{p.external_policy_id}</span> : null}</td>
        <td>{p.is_advanced ? `${p.conditions_count} condition${p.conditions_count === 1 ? '' : 's'} (JSON)` : `${p.match_field} = ${p.match_value}`}</td>
        <td>{p.is_advanced ? `${p.actions_count} grant${p.actions_count === 1 ? '' : 's'}` : `${(p.resource_type || '').toLowerCase()}: ${resourceLabel(p)}`}</td>
        <td>{p.is_advanced ? '—' : p.assignment_type}{!p.reconciliation_enabled && <span className="badge neutral" style={{marginLeft:6}} title="This policy's grants are never auto-revoked, even once the condition stops matching.">Sticky</span>}</td>
        <td><StatusBadge status={p.status}/></td>
        <td style={{display:'flex',gap:6}}>{!p.is_advanced && <button className="btn" onClick={() => openPolicyEdit(p)}>Edit</button>}<button className="btn" onClick={() => void openJsonView(p)}>View/Edit JSON</button><button className="btn" onClick={() => void toggleStatus(p)}>{p.status === 'ACTIVE' ? 'Disable' : 'Enable'}</button><button className="btn" onClick={() => void remove(p)}>Delete</button></td>
      </tr>)}</tbody></table>}</div>
    </div>
  </section>;
}
function PrivilegedAccountsPanel() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: puPolicy, reload: reloadPu } = useApiResource<ApiPrivilegedAccountPolicy>('/api/v1/privileged-accounts/policy/PU');
  const { data: tuPolicy, reload: reloadTu } = useApiResource<ApiPrivilegedAccountPolicy>('/api/v1/privileged-accounts/policy/TU');
  const { data: requests, error: requestsError, loading: requestsLoading, reload: reloadRequests } = useApiResource<ApiPrivilegedAccountRequest[]>('/api/v1/privileged-accounts/requests');
  const [approverDrafts, setApproverDrafts] = useState<{ PU: string; TU: string }>({ PU: '', TU: '' });
  const [savingType, setSavingType] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [decisionBusyId, setDecisionBusyId] = useState<string | null>(null);
  const normalUsers = (users || []).filter(u => u.account_type === 'NORMAL');

  const saveApprover = async (accountType: 'PU' | 'TU') => {
    setSavingType(accountType); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/privileged-accounts/policy/${accountType}`, { method: 'PATCH', body: JSON.stringify({ default_approver_id: approverDrafts[accountType] || null }) });
      if (response.ok) { setApproverDrafts(prev => ({ ...prev, [accountType]: '' })); accountType === 'PU' ? reloadPu() : reloadTu(); }
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to save this policy.'); }
    } catch { setMessage('Unable to save this policy.'); } finally { setSavingType(null); }
  };

  const decide = async (request: ApiPrivilegedAccountRequest, action: 'approve' | 'reject') => {
    if (action === 'reject') {
      const justification = window.prompt('Reason for rejecting this request?') || '';
      if (justification.trim().length < 3) return;
      setDecisionBusyId(request.id);
      try { await auth.apiRequest(`/api/v1/privileged-accounts/requests/${request.id}/reject`, { method: 'POST', body: JSON.stringify({ justification: justification.trim() }) }); reloadRequests(); }
      finally { setDecisionBusyId(null); }
      return;
    }
    setDecisionBusyId(request.id);
    try { await auth.apiRequest(`/api/v1/privileged-accounts/requests/${request.id}/approve`, { method: 'POST' }); reloadRequests(); }
    finally { setDecisionBusyId(null); }
  };

  const renderPolicyRow = (accountType: 'PU' | 'TU', policy: ApiPrivilegedAccountPolicy | null) => <div className="key-grid" key={accountType} style={{marginBottom:14}}>
    <div className="key"><span>{accountType === 'PU' ? 'Privileged (PU)' : 'Test (TU)'} accounts</span><strong>{policy?.approval_required ? `Requires approval — ${policy.default_approver_display_name || 'approver set'}` : 'Auto-provisions immediately (no approver configured)'}</strong></div>
    <label className="key"><span>Set approver (leave blank for ASAP/auto)</span><select className="select" value={approverDrafts[accountType]} onChange={event => setApproverDrafts(prev => ({ ...prev, [accountType]: event.target.value }))}><option value="">No approver — auto-provision</option>{normalUsers.map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
    <div style={{display:'flex',alignItems:'flex-end'}}><button className="btn btn-primary" disabled={savingType === accountType} onClick={() => void saveApprover(accountType)}>{savingType === accountType ? 'Saving...' : 'Save'}</button></div>
  </div>;

  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Privileged (PU) / Test (TU) accounts</h2></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginBottom:14}}>A separate, mailbox-free Entra identity used only for elevated admin work (PU) or QA/UAT (TU) — deliberately excluded from Birthright and Group Role Mapping automation. Access to anything is always granted manually, one grant at a time, and only once the account is linked to the real person requesting it.</p>
      {renderPolicyRow('PU', puPolicy)}
      {renderPolicyRow('TU', tuPolicy)}
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      <div className="panel-head" style={{padding:'14px 0 10px'}}><h2 style={{fontSize:15}}>Requests</h2></div>
      <div className="table-wrap">{requestsLoading ? <div className="empty">Loading...</div> : requestsError ? <div className="empty">{requestsError}</div> : !requests || requests.length === 0 ? <div className="empty">No privileged/test account requests yet.</div> : <table><thead><tr><th>Requester</th><th>Type</th><th>Justification</th><th>Status</th><th>Requested</th><th></th></tr></thead><tbody>{requests.map(r => <tr key={r.id}><td className="user-name">{r.requester_display_name || r.requester_id}</td><td>{r.account_type}</td><td>{r.justification || '—'}</td><td><StatusBadge status={r.status}/></td><td>{formatDateTime(r.created_at, timezone)}</td><td>{r.status === 'PENDING_APPROVAL' ? <span style={{display:'flex',gap:6}}><button className="btn" disabled={decisionBusyId === r.id} onClick={() => void decide(r, 'approve')}>Approve</button><button className="btn" disabled={decisionBusyId === r.id} onClick={() => void decide(r, 'reject')}>Reject</button></span> : r.failure_reason ? <span className="footer-note">{r.failure_reason}</span> : null}</td></tr>)}</tbody></table>}</div>
    </div>
  </section>;
}
interface ApiDepartment { id: string; name: string; created_at: string; }
function DepartmentsPanel() {
  const auth = useAuth();
  const { data: departments, error, loading, reload } = useApiResource<ApiDepartment[]>('/api/v1/policies/departments');
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');

  const add = async () => {
    if (!name.trim()) return;
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/policies/departments', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setName(''); reload(); }
      else setMessage(body?.error?.message || 'Unable to add this department.');
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };
  const remove = async (department: ApiDepartment) => {
    if (!window.confirm(`Remove "${department.name}" from the department list? This does not change any user's existing department value.`)) return;
    await auth.apiRequest(`/api/v1/policies/departments/${department.id}`, { method: 'DELETE' });
    reload();
  };

  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Departments</h2></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginBottom:14}}>The managed list of department names — populates the Department dropdown on each user's detail page, so it's picked from a known, consistent set instead of free-typed.</p>
      <div style={{display:'flex',gap:8,alignItems:'flex-end',marginBottom:14,flexWrap:'wrap'}}>
        <label className="key" style={{minWidth:220}}><span>Department name</span><input className="select" style={{width:'100%'}} value={name} onChange={event => setName(event.target.value)} placeholder="e.g. AppDev" onKeyDown={event => { if (event.key === 'Enter') void add(); }}/></label>
        <button className="btn btn-primary" disabled={saving || !name.trim()} onClick={() => void add()}><Plus size={14}/> {saving ? 'Adding...' : 'Add'}</button>
      </div>
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !departments || departments.length === 0 ? <div className="empty">No departments added yet.</div> : <div style={{display:'flex',flexWrap:'wrap',gap:8}}>{departments.map(d => <span key={d.id} className="badge neutral" style={{display:'inline-flex',alignItems:'center',gap:6}}>{d.name}<button type="button" className="btn" aria-label={`Remove ${d.name}`} onClick={() => void remove(d)} style={{padding:'0 6px',minWidth:0}}><X size={11}/></button></span>)}</div>}
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
    <DepartmentsPanel/>
    <BirthrightPoliciesPanel/>
    <PrivilegedAccountsPanel/>
    <TablePanel toolbar={<Toolbar placeholder="Search policies"/>}><table><thead><tr><th>Policy name</th><th>Description</th><th>Scope</th><th>Max duration</th><th>Approval</th><th>MFA</th><th>Ticket</th><th>Status</th><th></th></tr></thead><tbody>{policies.map(p => <tr key={p.name}><td className="user-name">{p.name}</td><td>{p.description}</td><td>{p.scope}</td><td>{p.max}</td><td>{p.approval}</td><td>{p.mfa}</td><td>{p.ticket}</td><td><StatusBadge status={p.status}/></td><td><button className="btn">Edit</button></td></tr>)}</tbody></table></TablePanel>
  </Page>;
}
interface ApiAuditLog { id: string; timestamp: string; actor_user_id: string | null; actor_display_name: string | null; action: string; target_type: string; target_id: string | null; provider_id: string | null; provider_name: string | null; request_id: string; result: string; target_user_display_name: string | null; target_user_email: string | null; }
function AuditPage() {
  const timezone = useAppTimezone();
  const { data: logs, error, loading, reload } = useApiResource<ApiAuditLog[]>('/api/v1/audit-logs');
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') || '';
  const resultFilter = searchParams.get('result') || '';
  const setSearch = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('q', value); else next.delete('q'); return next; });
  const setResultFilter = (value: string) => setSearchParams(prev => { const next = new URLSearchParams(prev); if (value) next.set('result', value); else next.delete('result'); return next; });
  const resultOptions = useMemo(() => Array.from(new Set((logs || []).map(l => l.result))).sort().map(r => ({ value: r, label: r })), [logs]);
  const filteredLogs = (logs || []).filter(l => (!resultFilter || l.result === resultFilter) && (!search || `${l.action} ${l.actor_display_name || ''} ${l.target_type} ${l.target_user_display_name || ''}`.toLowerCase().includes(search.toLowerCase())));
  return <Page eyebrow="GOVERNANCE" title="Audit logs" subtitle="A tamper-evident record of identity and access activity." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}><TablePanel toolbar={<Toolbar placeholder="Search audit events" searchValue={search} onSearchChange={setSearch} filterLabel="All results" filterValue={resultFilter} onFilterChange={setResultFilter} filterOptions={resultOptions}/>}>{loading ? <div className="empty">Loading audit logs...</div> : error ? <div className="empty">{error}</div> : !logs || logs.length === 0 ? <div className="empty">No audit events found.</div> : filteredLogs.length === 0 ? <div className="empty">No audit events match this filter.</div> : <table><thead><tr><th>Timestamp</th><th>Actor</th><th>Action</th><th>Target</th><th>User</th><th>Provider</th><th>Result</th><th>Request ID</th></tr></thead><tbody>{filteredLogs.map(entry => <tr key={entry.id}><td>{formatDateTime(entry.timestamp, timezone)}</td><td className="user-name">{entry.actor_display_name || 'System'}</td><td>{entry.action}</td><td>{entry.target_type}</td><td>{entry.target_user_display_name ? `${entry.target_user_display_name}${entry.target_user_email ? ` (${entry.target_user_email})` : ''}` : '—'}</td><td>{entry.provider_name || '—'}</td><td><StatusBadge status={entry.result}/></td><td>{entry.request_id}</td></tr>)}</tbody></table>}</TablePanel></Page>;
}
interface ApiPrivilegedAccountActivity { id: string; display_name: string; email: string; account_type: string; status: string; linked_user_id: string | null; linked_user_display_name: string | null; created_at: string; last_sign_in_at: string | null; last_non_interactive_sign_in_at: string | null; sign_in_data_available: boolean; event_count: number; last_activity_at: string | null; }
function PrivilegedAccountActivityPage() {
  const timezone = useAppTimezone();
  const { data: accounts, error, loading, reload } = useApiResource<ApiPrivilegedAccountActivity[]>('/api/v1/privileged-accounts/activity');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: timeline, loading: timelineLoading } = useApiResource<ApiAuditLog[]>(selectedId ? `/api/v1/privileged-accounts/${selectedId}/timeline` : '', Boolean(selectedId));
  const selected = accounts?.find(a => a.id === selectedId) || null;

  return <Page eyebrow="ADMINISTRATION" title="Privileged & Test Account Activity" subtitle="Every PU/TU shadow account — who it belongs to, when it was created, its last real sign-in, and everything AccessPilot has recorded about it." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    <TablePanel toolbar={undefined}>
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !accounts || accounts.length === 0 ? <div className="empty">No Privileged (PU) or Test (TU) accounts exist yet.</div> : <table><thead><tr><th>Account</th><th>Type</th><th>Status</th><th>Linked to</th><th>Created</th><th>Last sign-in</th><th>Events</th><th>Last activity</th><th></th></tr></thead><tbody>
        {accounts.map(a => <tr key={a.id} style={selectedId === a.id ? {background:'#f4f8f8'} : undefined}>
          <td className="user-name">{a.display_name}</td>
          <td><span className="badge neutral">{a.account_type}</span></td>
          <td><StatusBadge status={a.status}/></td>
          <td>{a.linked_user_id ? <Link to={`/admin/users/${a.linked_user_id}`} className="user-name">{a.linked_user_display_name || a.linked_user_id}</Link> : <span className="footer-note">Unassociated</span>}</td>
          <td>{formatDateTime(a.created_at, timezone)}</td>
          <td>{a.sign_in_data_available ? (a.last_sign_in_at ? formatDateTime(a.last_sign_in_at, timezone) : 'Never signed in') : <span className="footer-note" title="Needs the AuditLog.Read.All Graph permission granted on this tenant">Unknown</span>}</td>
          <td>{a.event_count}</td>
          <td>{a.last_activity_at ? formatDateTime(a.last_activity_at, timezone) : '—'}</td>
          <td><button className="btn" onClick={() => setSelectedId(a.id)}>View timeline</button></td>
        </tr>)}
      </tbody></table>}
    </TablePanel>
    {selectedId && <div className="panel" style={{marginTop:18}}>
      <div className="panel-head"><h2>Timeline — {selected?.display_name || 'account'}</h2><button className="btn" aria-label="Close" onClick={() => setSelectedId(null)}><X size={14}/></button></div>
      <div className="detail-section">
        {timelineLoading ? <div className="empty">Loading timeline...</div> : !timeline || timeline.length === 0 ? <div className="empty">No activity recorded yet.</div> : <div className="timeline" style={{padding:0}}>{timeline.map(entry => <div key={entry.id} className="timeline-item"><strong>{entry.action.replace(/_/g, ' ')}</strong><small>{formatDateTime(entry.timestamp, timezone)} · {entry.actor_display_name || 'System'}{entry.result !== 'SUCCESS' ? ` · ${entry.result}` : ''}</small></div>)}</div>}
      </div>
    </div>}
  </Page>;
}
interface ApiScopeTargetResolved { resource_type: string; resource_id: string; resource_display_name: string | null; }
interface ApiAccessReviewCampaign { id: string; name: string; description: string | null; scope_type: string; scope_resource_type: string | null; scope_resource_id: string | null; scope_targets: ApiScopeTargetResolved[] | null; scope_user_id: string | null; scope_account_type: string | null; scope_inactive_days: number | null; reviewer_id: string; reviewer_display_name: string | null; fallback_reviewer_id: string | null; fallback_reviewer_display_name: string | null; fallback_unlock_hours: number | null; status: string; due_at: string; on_no_response?: string; frequency_days: number | null; schedule_day_of_month: number | null; schedule_time: string | null; schedule_every_months: number | null; schedule_due_days: number | null; next_run_at: string | null; parent_campaign_id: string | null; created_by: string | null; created_at: string; completed_at: string | null; item_count: number; decided_count: number; approved_count: number; revoked_count: number; auto_revoked_count: number; }
interface ApiAccessReviewItem { id: string; campaign_id: string; campaign_name: string | null; assignment_id: string; user_id: string; user_display_name: string | null; user_email: string | null; granted_via: string | null; package_id: string | null; package_name: string | null; business_role_id: string | null; business_role_name: string | null; resource_type: string; resource_id: string; resource_display_name: string | null; app_role_external_id: string | null; assignment_status_at_snapshot: string; decision: string; decided_by: string | null; decided_by_display_name: string | null; decided_at: string | null; justification: string | null; created_at: string; }
interface ApiResourceTally { name: string; count: number; }
interface ApiAccessReviewDashboard { total_campaigns: number; active_campaigns: number; completed_campaigns: number; recurring_campaigns: number; total_items: number; pending_items: number; approved_items: number; revoked_items: number; auto_revoked_items: number; top_groups: ApiResourceTally[]; top_applications: ApiResourceTally[]; }
const emptyCampaignForm = { name: '', description: '', scope_type: 'ALL', scope_resource_type: 'GROUP', scope_resource_id: '', scope_targets: [] as { resource_type: string; resource_id: string; name: string }[], scope_user_id: '', scope_account_type: 'PU', scope_inactive_days: '90', reviewer_id: '', fallback_reviewer_id: '', fallback_unlock_hours: '', due_days: '30', on_no_response: 'REVOKE', frequency_days: '30', recurrence: 'none', schedule_day: '15', schedule_time: '09:00', schedule_every_months: '1' };
const emptyEditForm = { name: '', description: '', reviewer_id: '', fallback_reviewer_id: '', fallback_unlock_hours: '', due_at: '', frequency_days: '30', recurrence: 'none', schedule_day: '15', schedule_time: '09:00', schedule_every_months: '1' };
// Same local-time construction as the SoD exception form's datetime-local default (see defaultExpiry above) —
// slicing an ISO string's UTC representation would silently shift a due date by the browser's UTC offset.
function toLocalDateTimeInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function campaignFrequencyLabel(c: ApiAccessReviewCampaign): string {
  if (c.schedule_day_of_month) return `Day ${c.schedule_day_of_month} · ${c.schedule_time} · ${c.schedule_every_months && c.schedule_every_months > 1 ? `every ${c.schedule_every_months} months` : 'monthly'}`;
  return frequencyLabel(c.frequency_days);
}
interface RecurrenceState { recurrence: string; frequency_days: string; schedule_day: string; schedule_time: string; schedule_every_months: string; }
// Two ways for a campaign to repeat (mutually exclusive): (1) "after it completes" — Monthly/Quarterly/Yearly, the
// next one is created the moment this one closes; (2) "fixed day and time" — a new campaign STARTS on that day of
// the month at that time (app timezone) whether or not the previous one has been closed early or late.
function RecurrenceFields({ value, onChange, timezone }: { value: RecurrenceState; onChange: (patch: Partial<RecurrenceState>) => void; timezone: string }) {
  const on = value.recurrence !== 'none';
  return <div style={{marginBottom:14}}>
    <label style={{display:'flex',alignItems:'center',gap:8,fontWeight:600,fontSize:13}}><input type="checkbox" checked={on} onChange={event => onChange(event.target.checked ? { recurrence: 'after', frequency_days: value.frequency_days || '30' } : { recurrence: 'none' })}/> Make this a recurring campaign</label>
    {on && <div style={{marginTop:8,display:'grid',gap:10}}>
      <label style={{display:'flex',alignItems:'center',gap:8,fontSize:13,flexWrap:'wrap'}}><input type="radio" name="recurrence-mode" checked={value.recurrence === 'after'} onChange={() => onChange({ recurrence: 'after' })}/> Repeat after it completes:
        <select className="select" disabled={value.recurrence !== 'after'} value={value.frequency_days} onChange={event => onChange({ frequency_days: event.target.value })}>{FREQUENCY_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}</select></label>
      <label style={{display:'flex',alignItems:'center',gap:8,fontSize:13,flexWrap:'wrap'}}><input type="radio" name="recurrence-mode" checked={value.recurrence === 'fixed'} onChange={() => onChange({ recurrence: 'fixed' })}/> Start a new one on day
        <input className="select" type="number" min={1} max={31} style={{width:70}} disabled={value.recurrence !== 'fixed'} value={value.schedule_day} onChange={event => onChange({ schedule_day: event.target.value })}/> at
        <input className="select" type="time" disabled={value.recurrence !== 'fixed'} value={value.schedule_time} onChange={event => onChange({ schedule_time: event.target.value })}/> every
        <select className="select" disabled={value.recurrence !== 'fixed'} value={value.schedule_every_months} onChange={event => onChange({ schedule_every_months: event.target.value })}><option value="1">month</option><option value="3">3 months</option><option value="6">6 months</option><option value="12">12 months</option></select></label>
      <p className="subtitle" style={{margin:0}}>{value.recurrence === 'fixed' ? `A fresh campaign starts automatically on that day and time (${timezone}), with the same scope and reviewer, and stays open for the "Due in (days)" you set above. Day 31 means the last day of shorter months. If the previous one is still open then, that start is skipped.` : 'When this campaign completes, a new one with the same scope and reviewer is created automatically.'}</p>
    </div>}
  </div>;
}
function scopeSummary(c: ApiAccessReviewCampaign, groups?: ApiGroup[] | null, roles?: ApiRole[] | null, applications?: ApiApplication[] | null, packages?: ApiPackage[] | null, users?: ApiUser[] | null, businessRoles?: ApiBusinessRole[] | null): string {
  if (c.scope_type === 'ALL') return 'Every current grant';
  if (c.scope_type === 'ACCOUNT_TYPE') return `${c.scope_account_type} accounts`;
  if (c.scope_type === 'INACTIVE_USERS') return `No sign-in in ${c.scope_inactive_days}+ days`;
  if (c.scope_type === 'MOVER') return `Mover review: ${users?.find(u => u.id === c.scope_user_id)?.display_name || 'one user'} — access no policy granted`;
  if (c.scope_type === 'USER') return users?.find(u => u.id === c.scope_user_id)?.display_name || 'One user';
  if (c.scope_type === 'MULTIPLE_RESOURCES') return (c.scope_targets || []).map(t => t.resource_display_name || t.resource_id).join(', ') || '—';
  const list = c.scope_resource_type === 'GROUP' ? groups : c.scope_resource_type === 'ROLE' ? roles : c.scope_resource_type === 'APPLICATION' ? applications : c.scope_resource_type === 'BUSINESS_ROLE' ? businessRoles : packages;
  const name = list?.find(t => t.id === c.scope_resource_id)?.name;
  return c.scope_type === 'RESOURCE_TYPE' ? `Every ${(c.scope_resource_type || '').toLowerCase().replace('_', ' ')}` : (name || c.scope_resource_type || '—');
}
const FREQUENCY_PRESETS: Array<{ value: string; label: string }> = [
  { value: '30', label: 'Monthly' },
  { value: '90', label: 'Quarterly' },
  { value: '365', label: 'Yearly' },
];
function frequencyLabel(days: number | null): string {
  if (!days) return 'One-time';
  const preset = FREQUENCY_PRESETS.find(p => Number(p.value) === days);
  return preset ? preset.label : `Every ${days} days`;
}
const OUTCOME_STYLES = {
  approved: { label: 'Approved', hint: 'Reviewer confirmed the access is still needed', color: '#3b9c7f', soft: '#e5f5ef', ink: '#277b67' },
  revoked: { label: 'Revoked', hint: 'Reviewer removed the access', color: '#c95a5a', soft: '#fbe7e5', ink: '#ae4949' },
  auto: { label: 'Auto-revoked', hint: 'Removed because the deadline passed or the campaign was closed undecided', color: '#e0a24d', soft: '#fff2df', ink: '#a66b21' },
  pending: { label: 'Pending', hint: 'Still waiting for a decision', color: '#cdd6da', soft: '#edf1f3', ink: '#667780' },
} as const;
// One stacked bar showing how a set of review items ended up — used on the dashboard (all items) and in each
// campaign row (that campaign's items), so the two always read the same way.
function OutcomeBar({ approved, revoked, auto, pending, height = 10 }: { approved: number; revoked: number; auto: number; pending: number; height?: number }) {
  const total = approved + revoked + auto + pending;
  const parts: Array<[keyof typeof OUTCOME_STYLES, number]> = [['approved', approved], ['revoked', revoked], ['auto', auto], ['pending', pending]];
  const title = parts.map(([key, value]) => `${OUTCOME_STYLES[key].label}: ${value}`).join(' · ');
  return <div title={title} aria-label={title} style={{display:'flex',height,borderRadius:height,overflow:'hidden',background:'#edf1f3',minWidth:90}}>
    {total > 0 && parts.filter(([, value]) => value > 0).map(([key, value]) => <div key={key} style={{width:`${(value / total) * 100}%`,background:OUTCOME_STYLES[key].color}}/>)}
  </div>;
}
function AccessReviewDashboardPanel({ dashboard }: { dashboard: ApiAccessReviewDashboard | null }) {
  const na = '—';
  const tiles: Array<[string, string]> = [
    ['Total reviews', dashboard ? String(dashboard.total_campaigns) : na],
    ['Active', dashboard ? String(dashboard.active_campaigns) : na],
    ['Completed', dashboard ? String(dashboard.completed_campaigns) : na],
    ['Recurring', dashboard ? String(dashboard.recurring_campaigns) : na],
  ];
  const d = dashboard;
  const outcomes: Array<[keyof typeof OUTCOME_STYLES, number]> = d ? [['approved', d.approved_items], ['revoked', d.revoked_items], ['auto', d.auto_revoked_items], ['pending', d.pending_items]] : [];
  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Access Review Dashboard</h2></div>
    <div className="detail-section">
      <div className="stats">{tiles.map(([label, value]) => <div className="stat" key={label}><div className="stat-top"><span>{label}</span></div><div className="stat-value">{value}</div></div>)}</div>
      <div className="key" style={{marginBottom:10}}><span>What happened to the reviewed items</span></div>
      {!d ? <p className="subtitle" style={{margin:0}}>Loading...</p> : d.total_items === 0 ? <p className="subtitle" style={{margin:0}}>No items have been reviewed yet.</p> : <div style={{display:'flex',gap:18,alignItems:'stretch',flexWrap:'wrap'}}>
        <div style={{border:'2px solid #28424c',borderRadius:10,padding:'18px 26px',display:'flex',flexDirection:'column',justifyContent:'center',minWidth:130}}>
          <span className="user-email" style={{margin:0,textTransform:'uppercase',letterSpacing:1}}>Items</span>
          <span style={{fontFamily:'Space Grotesk',fontSize:34,fontWeight:700,lineHeight:1.1}}>{d.total_items}</span>
        </div>
        <div style={{display:'flex',alignItems:'center',color:'#829198'}}><ArrowRight size={22}/></div>
        <div style={{flex:1,minWidth:280,display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(170px,1fr))',gap:12}}>
          {outcomes.map(([key, value]) => { const st = OUTCOME_STYLES[key]; const pct = d.total_items ? Math.round((value / d.total_items) * 100) : 0; return <div key={key} title={st.hint} style={{border:`1px solid ${st.color}`,borderLeft:`6px solid ${st.color}`,background:st.soft,borderRadius:8,padding:'12px 14px'}}>
            <div style={{fontSize:12,fontWeight:700,color:st.ink}}>{st.label}</div>
            <div style={{display:'flex',alignItems:'baseline',gap:8}}><span style={{fontFamily:'Space Grotesk',fontSize:26,fontWeight:700,color:'#1d2b31'}}>{value}</span><span style={{fontSize:11,color:st.ink}}>{pct}%</span></div>
            <div style={{fontSize:10,color:'#718088',marginTop:2}}>{st.hint}</div>
          </div>; })}
        </div>
      </div>}
      {d && d.total_items > 0 && <div style={{marginTop:14}}><OutcomeBar approved={d.approved_items} revoked={d.revoked_items} auto={d.auto_revoked_items} pending={d.pending_items} height={12}/></div>}
      {d && (d.top_groups.length > 0 || d.top_applications.length > 0) && <div className="grid-2" style={{marginTop:22}}>
        <div><div className="key" style={{marginBottom:6}}><span>Most-reviewed groups</span></div>{d.top_groups.length === 0 ? <p className="subtitle" style={{margin:0}}>None yet.</p> : d.top_groups.map(g => <div key={g.name} className="user-cell" style={{padding:'4px 0'}}><span>{g.name}</span><span className="badge neutral" style={{marginLeft:'auto'}}>{g.count}</span></div>)}</div>
        <div><div className="key" style={{marginBottom:6}}><span>Most-reviewed applications</span></div>{d.top_applications.length === 0 ? <p className="subtitle" style={{margin:0}}>None yet.</p> : d.top_applications.map(a => <div key={a.name} className="user-cell" style={{padding:'4px 0'}}><span>{a.name}</span><span className="badge neutral" style={{marginLeft:'auto'}}>{a.count}</span></div>)}</div>
      </div>}
    </div>
  </section>;
}
function AccessReviewsPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: campaigns, error, loading, reload } = useApiResource<ApiAccessReviewCampaign[]>('/api/v1/access-reviews');
  const { data: dashboard, reload: reloadDashboard } = useApiResource<ApiAccessReviewDashboard>('/api/v1/access-reviews/dashboard');
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const { data: applications } = useApiResource<ApiApplication[]>('/api/v1/applications');
  const { data: packages } = useApiResource<ApiPackage[]>('/api/v1/packages');
  const { data: businessRoles } = useApiResource<ApiBusinessRole[]>('/api/v1/business-roles');
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyCampaignForm);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState(emptyEditForm);
  const [editSaving, setEditSaving] = useState(false);
  const [editMessage, setEditMessage] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const resourceTargets: Array<ApiGroup | ApiRole | ApiApplication | ApiPackage | ApiBusinessRole> = form.scope_resource_type === 'GROUP' ? (groups || []) : form.scope_resource_type === 'ROLE' ? (roles || []) : form.scope_resource_type === 'APPLICATION' ? (applications || []) : form.scope_resource_type === 'BUSINESS_ROLE' ? (businessRoles || []) : (packages || []);

  // "ASAP" live updates: nobody has to manually refresh to see a decision someone else just made land in the
  // list/progress counts or the dashboard tiles — same 20-30s polling convention every other live page here uses.
  useEffect(() => {
    const timer = setInterval(() => { reload(); reloadDashboard(); }, 20000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reviewer suggestion: when scoping to a specific Application, pre-fill from its real ApplicationOwner
  // record(s) via the NHI API — best-effort only. That endpoint is NHI_READ-gated (NHIAdmin-exclusive, not
  // folded into plain Admin), so a plain Admin creating a campaign will simply get no suggestion and picks a
  // reviewer manually — exactly the "Admin can always override" fallback the feature already relies on either way.
  useEffect(() => {
    if (!open || form.scope_type !== 'SPECIFIC_RESOURCE' || form.scope_resource_type !== 'APPLICATION' || !form.scope_resource_id) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await auth.apiRequest(`/api/v1/nhi/${form.scope_resource_id}`);
        if (!response.ok) return;
        const body = await response.json();
        const ownerId = body?.owners?.[0]?.user_id;
        if (ownerId && !cancelled) setForm(prev => prev.reviewer_id ? prev : { ...prev, reviewer_id: ownerId });
      } catch { /* best-effort suggestion only — silently skip */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, form.scope_type, form.scope_resource_type, form.scope_resource_id]);

  // Reviewer suggestion #2 (manager, from the Org Chart's User.manager_id): shown as a visible, one-click
  // suggestion under the Reviewer field — and still pre-filled if no reviewer is chosen yet. Never overrides a
  // reviewer the Admin already picked. USER scope suggests that user's own manager; a specific GROUP scope
  // suggests the manager who manages the most current members. Other scopes have no single natural manager.
  // Owner suggestions: for a specific Package / Application / Group scope, the resource's owners (package and
  // application owners are AccessPilot records; group owners are read live from Entra) — best-effort, one click
  // to use, never overriding a reviewer already picked.
  const [ownerSuggestions, setOwnerSuggestions] = useState<{ id: string; label: string }[]>([]);
  useEffect(() => {
    setOwnerSuggestions([]);
    if (!open || form.scope_type !== 'SPECIFIC_RESOURCE' || !form.scope_resource_id || !['PACKAGE', 'APPLICATION', 'GROUP', 'BUSINESS_ROLE'].includes(form.scope_resource_type)) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await auth.apiRequest(`/api/v1/access-reviews/owner-suggestions?resource_type=${form.scope_resource_type}&resource_id=${form.scope_resource_id}`);
        if (!response.ok) return;
        const rows = (await response.json()) as { user_id: string; display_name: string; source: string }[];
        if (!cancelled) setOwnerSuggestions(rows.map(r => ({ id: r.user_id, label: `${r.display_name} — ${r.source}` })));
      } catch { /* best-effort suggestion only */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, form.scope_type, form.scope_resource_type, form.scope_resource_id]);
  useEffect(() => {
    if (ownerSuggestions.length > 0) setForm(prev => prev.reviewer_id ? prev : { ...prev, reviewer_id: ownerSuggestions[0].id });
  }, [ownerSuggestions]);
  const [managerSuggestion, setManagerSuggestion] = useState<{ id: string; label: string } | null>(null);
  useEffect(() => {
    setManagerSuggestion(null);
    if (!open || !users) return;
    const nameOf = (id: string) => users.find(u => u.id === id)?.display_name || 'manager';
    if (form.scope_type === 'USER' && form.scope_user_id) {
      const target = users.find(u => u.id === form.scope_user_id);
      if (target?.manager_id) setManagerSuggestion({ id: target.manager_id, label: `${nameOf(target.manager_id)} — ${target.display_name}'s manager` });
      return;
    }
    if (form.scope_type === 'SPECIFIC_RESOURCE' && form.scope_resource_type === 'GROUP' && form.scope_resource_id) {
      let cancelled = false;
      (async () => {
        try {
          const response = await auth.apiRequest(`/api/v1/groups/${form.scope_resource_id}/members`);
          if (!response.ok) return;
          const members = (await response.json()) as ApiUser[];
          const counts = new Map<string, number>();
          for (const m of members) if (m.manager_id) counts.set(m.manager_id, (counts.get(m.manager_id) || 0) + 1);
          const top = Array.from(counts.entries()).sort((x, y) => y[1] - x[1])[0];
          if (top && !cancelled) setManagerSuggestion({ id: top[0], label: `${nameOf(top[0])} — manages ${top[1]} of this group's ${members.length} members` });
        } catch { /* best-effort suggestion only */ }
      })();
      return () => { cancelled = true; };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, form.scope_type, form.scope_user_id, form.scope_resource_type, form.scope_resource_id, users]);
  useEffect(() => {
    if (managerSuggestion) setForm(prev => prev.reviewer_id ? prev : { ...prev, reviewer_id: managerSuggestion.id });
  }, [managerSuggestion]);

  const startCreate = () => { setForm(emptyCampaignForm); setOpen(true); setMessage(''); };
  const create = async () => {
    if (!form.name.trim() || !form.reviewer_id || !form.due_days) { setMessage('Complete every required field.'); return; }
    if (form.scope_type === 'MULTIPLE_RESOURCES' && form.scope_targets.length === 0) { setMessage('Add at least one resource to review.'); return; }
    setSaving(true); setMessage('');
    try {
      const payload: Record<string, unknown> = {
        name: form.name.trim(), description: form.description.trim() || undefined, scope_type: form.scope_type,
        reviewer_id: form.reviewer_id, fallback_reviewer_id: form.fallback_reviewer_id || undefined,
        fallback_unlock_hours: form.fallback_unlock_hours ? Number(form.fallback_unlock_hours) : undefined,
        due_at: new Date(Date.now() + Number(form.due_days) * 86400000).toISOString(), on_no_response: form.on_no_response,
        frequency_days: form.recurrence === 'after' ? Number(form.frequency_days) : undefined,
        ...(form.recurrence === 'fixed' ? { schedule_day_of_month: Number(form.schedule_day), schedule_time: form.schedule_time, schedule_every_months: Number(form.schedule_every_months), schedule_due_days: Number(form.due_days) } : {}),
      };
      if (form.scope_type === 'RESOURCE_TYPE') payload.scope_resource_type = form.scope_resource_type;
      if (form.scope_type === 'SPECIFIC_RESOURCE') { payload.scope_resource_type = form.scope_resource_type; payload.scope_resource_id = form.scope_resource_id; }
      if (form.scope_type === 'MULTIPLE_RESOURCES') payload.scope_targets = form.scope_targets.map(t => ({ resource_type: t.resource_type, resource_id: t.resource_id }));
      if (form.scope_type === 'USER') payload.scope_user_id = form.scope_user_id;
      if (form.scope_type === 'ACCOUNT_TYPE') payload.scope_account_type = form.scope_account_type;
      if (form.scope_type === 'INACTIVE_USERS') payload.scope_inactive_days = Number(form.scope_inactive_days);
      const response = await auth.apiRequest('/api/v1/access-reviews', { method: 'POST', body: JSON.stringify(payload) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setOpen(false); setForm(emptyCampaignForm); reload(); }
      else setMessage(body?.error?.message || 'Unable to create this campaign.');
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };
  const complete = async (campaign: ApiAccessReviewCampaign) => {
    if (!window.confirm(`Close "${campaign.name}" now? Every still-pending item will be auto-revoked immediately.`)) return;
    await auth.apiRequest(`/api/v1/access-reviews/${campaign.id}/complete`, { method: 'POST' });
    reload();
  };
  const startEdit = (campaign: ApiAccessReviewCampaign) => {
    setOpen(false);
    setEditingId(campaign.id);
    setEditMessage('');
    setEditForm({
      name: campaign.name, description: campaign.description || '',
      reviewer_id: campaign.reviewer_id, fallback_reviewer_id: campaign.fallback_reviewer_id || '',
      fallback_unlock_hours: campaign.fallback_unlock_hours ? String(campaign.fallback_unlock_hours) : '',
      due_at: toLocalDateTimeInput(campaign.due_at),
      frequency_days: campaign.frequency_days ? String(campaign.frequency_days) : '30',
      recurrence: campaign.schedule_day_of_month ? 'fixed' : campaign.frequency_days ? 'after' : 'none',
      schedule_day: String(campaign.schedule_day_of_month || 15), schedule_time: campaign.schedule_time || '09:00', schedule_every_months: String(campaign.schedule_every_months || 1),
    });
  };
  const saveEdit = async () => {
    if (!editingId) return;
    if (!editForm.name.trim() || !editForm.reviewer_id || !editForm.due_at) { setEditMessage('Complete every required field.'); return; }
    setEditSaving(true); setEditMessage('');
    try {
      const payload: Record<string, unknown> = {
        name: editForm.name.trim(), description: editForm.description.trim() || undefined,
        reviewer_id: editForm.reviewer_id, due_at: new Date(editForm.due_at).toISOString(),
        fallback_unlock_hours: editForm.fallback_unlock_hours ? Number(editForm.fallback_unlock_hours) : undefined,
      };
      if (editForm.fallback_reviewer_id) payload.fallback_reviewer_id = editForm.fallback_reviewer_id;
      else payload.clear_fallback_reviewer = true;
      if (editForm.recurrence === 'after') { payload.frequency_days = Number(editForm.frequency_days); payload.clear_schedule = true; }
      else if (editForm.recurrence === 'fixed') { payload.schedule_day_of_month = Number(editForm.schedule_day); payload.schedule_time = editForm.schedule_time; payload.schedule_every_months = Number(editForm.schedule_every_months); payload.clear_frequency = true; }
      else { payload.clear_frequency = true; payload.clear_schedule = true; }
      const response = await auth.apiRequest(`/api/v1/access-reviews/${editingId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setEditingId(null); reload(); }
      else setEditMessage(body?.error?.message || 'Unable to save these changes.');
    } catch { setEditMessage('Unable to reach the backend.'); } finally { setEditSaving(false); }
  };

  return <Page eyebrow="ACCESS REVIEW" title="Access Reviews" subtitle="Periodic recertification — confirm who still needs the access they hold, or revoke it." action={<button className="btn btn-primary" onClick={startCreate}><Plus size={14}/> New campaign</button>}>
    {open && <div className="panel" style={{marginBottom:18}}><div className="detail-section">
      <div className="detail-title"><h2>New campaign</h2></div>
      <div className="key-grid" style={{marginBottom:10}}>
        <label className="key"><span>Name</span><input className="select" value={form.name} onChange={event => setForm({...form, name: event.target.value})} placeholder="e.g. Finance Team Q1 Review"/></label>
        <label className="key"><span>Description (optional)</span><input className="select" value={form.description} onChange={event => setForm({...form, description: event.target.value})}/></label>
        <label className="key"><span>Scope</span><select className="select" value={form.scope_type} onChange={event => setForm({...form, scope_type: event.target.value, scope_targets: []})}><option value="ALL">Every current grant</option><option value="RESOURCE_TYPE">Every Group / Role / Application / Package / Business Role grant</option><option value="SPECIFIC_RESOURCE">One specific Group / Role / Application / Package / Business Role</option><option value="MULTIPLE_RESOURCES">Several specific Groups / Roles / Applications / Packages / Business Roles, mixed</option><option value="USER">One user's entire access</option><option value="ACCOUNT_TYPE">Every Privileged (PU) or Test (TU) account</option><option value="INACTIVE_USERS">Users inactive for N+ days</option></select></label>
        {(form.scope_type === 'RESOURCE_TYPE' || form.scope_type === 'SPECIFIC_RESOURCE' || form.scope_type === 'MULTIPLE_RESOURCES') && <label className="key"><span>Resource type</span><select className="select" value={form.scope_resource_type} onChange={event => setForm({...form, scope_resource_type: event.target.value, scope_resource_id: ''})}><option value="GROUP">Group</option><option value="ROLE">Role</option><option value="APPLICATION">Application</option><option value="PACKAGE">Access Package</option><option value="BUSINESS_ROLE">Business Role</option></select></label>}
        {form.scope_type === 'SPECIFIC_RESOURCE' && <label className="key"><span>Target</span><select className="select" value={form.scope_resource_id} onChange={event => setForm({...form, scope_resource_id: event.target.value})}><option value="">Select a target</option>{resourceTargets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>}
        {form.scope_type === 'MULTIPLE_RESOURCES' && <label className="key"><span>Add a target</span><div style={{display:'flex',gap:8}}><select className="select" style={{flex:1}} value={form.scope_resource_id} onChange={event => setForm({...form, scope_resource_id: event.target.value})}><option value="">Select a target</option>{resourceTargets.filter(t => !form.scope_targets.some(existing => existing.resource_type === form.scope_resource_type && existing.resource_id === t.id)).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select><button type="button" className="btn" disabled={!form.scope_resource_id} onClick={() => { const target = resourceTargets.find(t => t.id === form.scope_resource_id); if (!target) return; setForm(prev => ({...prev, scope_resource_id: '', scope_targets: [...prev.scope_targets, { resource_type: prev.scope_resource_type, resource_id: target.id, name: target.name }]})); }}>Add</button></div></label>}
        {form.scope_type === 'USER' && <label className="key"><span>User</span><select className="select" value={form.scope_user_id} onChange={event => setForm({...form, scope_user_id: event.target.value})}><option value="">Select a user</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>}
        {form.scope_type === 'ACCOUNT_TYPE' && <label className="key"><span>Account type</span><select className="select" value={form.scope_account_type} onChange={event => setForm({...form, scope_account_type: event.target.value})}><option value="PU">Privileged (PU)</option><option value="TU">Test (TU)</option></select></label>}
        {form.scope_type === 'INACTIVE_USERS' && <label className="key"><span>Inactive for at least (days)</span><input className="select" type="number" min={1} value={form.scope_inactive_days} onChange={event => setForm({...form, scope_inactive_days: event.target.value})}/></label>}
        <label className="key"><span>Reviewer</span><select className="select" value={form.reviewer_id} onChange={event => setForm({...form, reviewer_id: event.target.value})}><option value="">Select a reviewer</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select>{ownerSuggestions.map(sg => <small key={sg.id} style={{display:'block',marginTop:4,fontWeight:400}}>Suggested (owner): {sg.label}{form.reviewer_id !== sg.id && <> · <button type="button" className="btn" style={{padding:'0 8px',minHeight:0}} onClick={() => setForm(prev => ({...prev, reviewer_id: sg.id}))}>Use</button></>}</small>)}{managerSuggestion && <small style={{display:'block',marginTop:4,fontWeight:400}}>Suggested (manager): {managerSuggestion.label}{form.reviewer_id !== managerSuggestion.id && <> · <button type="button" className="btn" style={{padding:'0 8px',minHeight:0}} onClick={() => setForm(prev => ({...prev, reviewer_id: managerSuggestion.id}))}>Use</button></>}</small>}</label>
        <label className="key"><span>Fallback reviewer (optional)</span><select className="select" value={form.fallback_reviewer_id} onChange={event => setForm({...form, fallback_reviewer_id: event.target.value})}><option value="">None</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
        {form.fallback_reviewer_id && <label className="key"><span>Fallback unlocks after (hours)</span><input className="select" type="number" min={1} value={form.fallback_unlock_hours} onChange={event => setForm({...form, fallback_unlock_hours: event.target.value})}/></label>}
        <label className="key"><span>Due in (days)</span><input className="select" type="number" min={1} value={form.due_days} onChange={event => setForm({...form, due_days: event.target.value})}/></label>
        <label className="key"><span>If nobody decides by the due date</span><select className="select" value={form.on_no_response} onChange={event => setForm({...form, on_no_response: event.target.value})}><option value="REVOKE">Revoke the access (default)</option><option value="KEEP">Keep the access (auto-approve)</option></select></label>
      </div>
      <RecurrenceFields value={form} onChange={patch => setForm(prev => ({...prev, ...patch}))} timezone={timezone}/>
      {form.scope_type === 'INACTIVE_USERS' && <p className="subtitle" style={{marginTop:-4,marginBottom:14}}>Needs Microsoft Graph AuditLog.Read.All to read last-sign-in data — if that permission isn't granted on this tenant, creating this campaign will fail with a clear error rather than silently reviewing nobody.</p>}
      {form.scope_type === 'MULTIPLE_RESOURCES' && <div style={{marginBottom:14}}>
        <div className="key" style={{marginBottom:6}}><span>In this review ({form.scope_targets.length})</span></div>
        {form.scope_targets.length === 0 ? <p className="subtitle" style={{margin:0}}>Nothing added yet — pick a resource type and target above, then Add.</p> : <div style={{display:'flex',flexWrap:'wrap',gap:8}}>{form.scope_targets.map((t, i) => <span key={`${t.resource_type}-${t.resource_id}`} className="badge neutral" style={{display:'inline-flex',alignItems:'center',gap:6}}>{t.resource_type.toLowerCase()}: {t.name}<button type="button" className="btn" aria-label={`Remove ${t.name}`} onClick={() => setForm(prev => ({...prev, scope_targets: prev.scope_targets.filter((_, idx) => idx !== i)}))} style={{padding:'0 6px',minWidth:0}}><X size={11}/></button></span>)}</div>}
      </div>}
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={saving} onClick={() => void create()}>{saving ? 'Creating...' : 'Create campaign'}</button><button className="btn" onClick={() => setOpen(false)}>Cancel</button></div>
    </div></div>}
    {editingId && <div className="panel" style={{marginBottom:18}}><div className="detail-section">
      <div className="detail-title"><h2>Edit campaign</h2></div>
      <p className="subtitle" style={{marginTop:-4,marginBottom:14}}>Scope and its snapshotted items can't be changed after creation — only the campaign's own details below.</p>
      <div className="key-grid" style={{marginBottom:10}}>
        <label className="key"><span>Name</span><input className="select" value={editForm.name} onChange={event => setEditForm({...editForm, name: event.target.value})}/></label>
        <label className="key"><span>Description (optional)</span><input className="select" value={editForm.description} onChange={event => setEditForm({...editForm, description: event.target.value})}/></label>
        <label className="key"><span>Reviewer</span><select className="select" value={editForm.reviewer_id} onChange={event => setEditForm({...editForm, reviewer_id: event.target.value})}><option value="">Select a reviewer</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
        <label className="key"><span>Fallback reviewer (optional)</span><select className="select" value={editForm.fallback_reviewer_id} onChange={event => setEditForm({...editForm, fallback_reviewer_id: event.target.value})}><option value="">None</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
        {editForm.fallback_reviewer_id && <label className="key"><span>Fallback unlocks after (hours)</span><input className="select" type="number" min={1} value={editForm.fallback_unlock_hours} onChange={event => setEditForm({...editForm, fallback_unlock_hours: event.target.value})}/></label>}
        <label className="key" style={{display:'block'}}><span>Due (your device's local time)</span><input className="select" style={{width:'100%'}} type="datetime-local" value={editForm.due_at} onChange={event => setEditForm({...editForm, due_at: event.target.value})}/></label>
      </div>
      <RecurrenceFields value={editForm} onChange={patch => setEditForm(prev => ({...prev, ...patch}))} timezone={timezone}/>
      {editMessage && <div className="notice" style={{marginBottom:14}}>{editMessage}</div>}
      <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={editSaving} onClick={() => void saveEdit()}>{editSaving ? 'Saving...' : 'Save changes'}</button><button className="btn" onClick={() => setEditingId(null)}>Cancel</button></div>
    </div></div>}
    <AccessReviewDashboardPanel dashboard={dashboard}/>
    <TablePanel toolbar={undefined}>
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !campaigns || campaigns.length === 0 ? <div className="empty">No access review campaigns yet.</div> : <table><thead><tr><th>Campaign</th><th>Scope</th><th>Reviewer</th><th>Progress</th><th>Status</th><th>Due</th><th>Frequency</th><th></th></tr></thead><tbody>
        {campaigns.map(c => <Fragment key={c.id}>
        <tr>
          <td className="user-name"><button type="button" className="btn" style={{border:'none',background:'none',padding:0,display:'inline-flex',alignItems:'center',gap:6,fontWeight:600,color:'inherit'}} onClick={() => setExpandedId(expandedId === c.id ? null : c.id)}><ChevronRight size={14} style={{transform: expandedId === c.id ? 'rotate(90deg)' : 'none', transition:'transform 0.1s'}}/> {c.name}</button></td>
          <td style={{whiteSpace:'normal',maxWidth:260}}>{scopeSummary(c, groups, roles, applications, packages, users, businessRoles)}</td>
          <td>{c.reviewer_display_name || c.reviewer_id}</td>
          <td style={{minWidth:190}}>
            <div style={{fontSize:12,marginBottom:5}}>{c.decided_count} / {c.item_count} decided</div>
            <OutcomeBar approved={c.approved_count} revoked={c.revoked_count} auto={c.auto_revoked_count} pending={c.item_count - c.decided_count}/>
            <div style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:5,fontSize:10}}>
              {c.approved_count > 0 && <span style={{color:OUTCOME_STYLES.approved.ink}}>✓ {c.approved_count} approved</span>}
              {c.revoked_count > 0 && <span style={{color:OUTCOME_STYLES.revoked.ink}}>✕ {c.revoked_count} revoked</span>}
              {c.auto_revoked_count > 0 && <span style={{color:OUTCOME_STYLES.auto.ink}} title={OUTCOME_STYLES.auto.hint}>⏱ {c.auto_revoked_count} auto-revoked</span>}
              {c.item_count - c.decided_count > 0 && <span style={{color:OUTCOME_STYLES.pending.ink}}>{c.item_count - c.decided_count} pending</span>}
            </div>
          </td>
          <td><StatusBadge status={c.status}/></td>
          <td>{formatDateTime(c.due_at, timezone)}</td>
          <td style={{whiteSpace:'normal'}}>{campaignFrequencyLabel(c)}{c.next_run_at && <div className="user-email">Next start {formatDateTime(c.next_run_at, timezone)}</div>}</td>
          <td><span style={{display:'flex',gap:6}}>{c.status === 'ACTIVE' && <><button className="btn" onClick={() => startEdit(c)}>Edit</button><button className="btn" onClick={() => void complete(c)}>Close now</button></>}<Link to={`/admin/access-reviews/${c.id}`} className="btn" aria-label="Open full page"><ExternalLink size={13}/></Link></span></td>
        </tr>
        {expandedId === c.id && <tr><td colSpan={8} style={{padding:0,background:'#fafbfb'}}><CampaignItemsPanel campaignId={c.id} onChanged={reload}/></td></tr>}
        </Fragment>)}
      </tbody></table>}
    </TablePanel>
  </Page>;
}
type ReviewRow =
  | { kind: 'single'; item: ApiAccessReviewItem }
  | { kind: 'package'; key: string; packageName: string; items: ApiAccessReviewItem[] }
  | { kind: 'businessRole'; key: string; roleName: string; items: ApiAccessReviewItem[] };
// Items granted through the same access package (or Business Role) to the same user collapse into ONE row
// (approve/revoke the whole batch for that user in one action, or expand it and decide each item individually).
function groupReviewItems(items: ApiAccessReviewItem[]): ReviewRow[] {
  const rows: ReviewRow[] = [];
  const index = new Map<string, number>();
  for (const item of items) {
    if (item.package_id) {
      const key = `pkg:${item.user_id}:${item.package_id}`;
      const at = index.get(key);
      if (at === undefined) { index.set(key, rows.length); rows.push({ kind: 'package', key, packageName: item.package_name || 'Package', items: [item] }); }
      else (rows[at] as Extract<ReviewRow, { kind: 'package' }>).items.push(item);
      continue;
    }
    if (item.business_role_id) {
      const key = `br:${item.user_id}:${item.business_role_id}`;
      const at = index.get(key);
      if (at === undefined) { index.set(key, rows.length); rows.push({ kind: 'businessRole', key, roleName: item.business_role_name || 'Business Role', items: [item] }); }
      else (rows[at] as Extract<ReviewRow, { kind: 'businessRole' }>).items.push(item);
      continue;
    }
    rows.push({ kind: 'single', item });
  }
  return rows;
}
function useReviewDecisions(onChanged: () => void) {
  const auth = useAuth();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const decide = async (targets: ApiAccessReviewItem[], decision: 'APPROVED' | 'REVOKED', label: string, key: string) => {
    const pending = targets.filter(t => t.decision === 'PENDING');
    if (pending.length === 0) return;
    const justification = window.prompt(`Justification for ${decision === 'APPROVED' ? 'approving' : 'revoking'} ${label}?`);
    if (justification === null) return;
    if (justification.trim().length < 3) { window.alert('A justification (at least 3 characters) is required.'); return; }
    setBusyKey(key);
    let failed = 0; let firstError = '';
    try {
      for (const target of pending) {
        const response = await auth.apiRequest(`/api/v1/access-reviews/items/${target.id}/decide`, { method: 'POST', body: JSON.stringify({ decision, justification: justification.trim() }) });
        if (!response.ok) { failed += 1; if (!firstError) firstError = (await response.json().catch(() => null))?.error?.message || ''; }
      }
    } finally { setBusyKey(null); onChanged(); }
    if (failed > 0) window.alert(`${failed} of ${pending.length} decision${pending.length === 1 ? '' : 's'} could not be recorded${firstError ? `: ${firstError}` : '.'}`);
  };
  return { busyKey, decide };
}
function ReviewItemsTable({ items, variant, onChanged }: { items: ApiAccessReviewItem[]; variant: 'admin' | 'mine'; onChanged: () => void }) {
  const { busyKey, decide } = useReviewDecisions(onChanged);
  const [expanded, setExpanded] = useState<string[]>([]);
  const admin = variant === 'admin';
  const rows = useMemo(() => groupReviewItems(items), [items]);
  const userCell = (item: ApiAccessReviewItem) => {
    const body = <><span className="avatar">{initialsFor(item.user_display_name || '?')}</span><span><span className="user-name">{item.user_display_name || item.user_id}</span>{item.user_email && <span className="user-email">{item.user_email}</span>}</span></>;
    return admin ? <Link to={`/admin/users/${item.user_id}`} className="user-cell">{body}</Link> : <span className="user-cell">{body}</span>;
  };
  const buttons = (targets: ApiAccessReviewItem[], label: string, key: string) => {
    const pending = targets.filter(t => t.decision === 'PENDING').length;
    if (pending === 0) return null;
    const all = targets.length > 1;
    return <span style={{display:'flex',gap:6}}><button className="btn" disabled={busyKey === key} onClick={() => void decide(targets, 'APPROVED', label, key)}>{all ? 'Approve all' : 'Approve'}</button><button className="btn" disabled={busyKey === key} onClick={() => void decide(targets, 'REVOKED', label, key)}>{all ? 'Revoke all' : 'Revoke'}</button></span>;
  };
  const itemRow = (item: ApiAccessReviewItem, nested: boolean) => <tr key={item.id} style={nested ? { background: '#fafbfb' } : undefined}>
    <td>{nested ? <span style={{paddingLeft:28,display:'inline-block'}} className="user-email">↳ individual item</span> : userCell(item)}</td>
    <td style={{whiteSpace:'normal',maxWidth:280}}>{item.resource_type.toLowerCase()}: {item.resource_display_name || item.resource_id}</td>
    <td style={{whiteSpace:'normal',maxWidth:240}}>{item.granted_via || '—'}</td>
    {admin && <><td>{item.assignment_status_at_snapshot}</td><td><StatusBadge status={item.decision}/></td><td>{item.decided_by_display_name || '—'}</td></>}
    <td>{buttons([item], `${item.user_display_name || 'this user'}'s access to ${item.resource_display_name || item.resource_type}`, item.id)}</td>
  </tr>;
  return <div className="table-wrap"><table><thead><tr><th>User</th><th>Resource</th><th>Granted via</th>{admin && <><th>Held as of snapshot</th><th>Decision</th><th>Decided by</th></>}<th></th></tr></thead><tbody>
    {rows.map(row => {
      if (row.kind === 'single') return itemRow(row.item, false);
      const first = row.items[0];
      const pending = row.items.filter(i => i.decision === 'PENDING').length;
      const open = expanded.includes(row.key);
      const icon = row.kind === 'package' ? '📦' : '🏷';
      const label = row.kind === 'package' ? row.packageName : row.roleName;
      const grantedViaLabel = row.kind === 'package' ? `Package: ${label}` : `Business Role: ${label}`;
      const kindNoun = row.kind === 'package' ? 'package' : 'Business Role';
      return <Fragment key={row.key}>
        <tr>
          <td>{userCell(first)}</td>
          <td><button type="button" onClick={() => setExpanded(prev => open ? prev.filter(k => k !== row.key) : [...prev, row.key])} style={{border:'none',background:'none',padding:0,display:'inline-flex',alignItems:'center',gap:6,fontWeight:700,color:'inherit',cursor:'pointer',font:'inherit'}}><ChevronRight size={14} style={{transform: open ? 'rotate(90deg)' : 'none', transition:'transform 0.1s'}}/>{icon} {label} · {row.items.length} item{row.items.length === 1 ? '' : 's'}</button></td>
          <td style={{whiteSpace:'normal',maxWidth:240}}>{grantedViaLabel}</td>
          {admin && <><td>—</td><td>{pending > 0 ? <span className="badge warning">{pending} PENDING</span> : <span className="badge success">ALL DECIDED</span>}</td><td>—</td></>}
          <td>{buttons(row.items, `${first.user_display_name || 'this user'}'s access to ${kindNoun} "${label}" (${row.items.length} items)`, row.key)}</td>
        </tr>
        {open && row.items.map(item => itemRow(item, true))}
      </Fragment>;
    })}
  </tbody></table></div>;
}
// Shared by AccessReviewDetailPage (the full standalone page) and the inline expandable row on the list page —
// one place for the items fetch, so the two surfaces can never drift apart.
function CampaignItemsPanel({ campaignId, onChanged }: { campaignId: string; onChanged?: () => void }) {
  const { data: items, loading, error, reload: reloadItems } = useApiResource<ApiAccessReviewItem[]>(`/api/v1/access-reviews/${campaignId}/items`);
  if (loading) return <div className="empty">Loading...</div>;
  if (error) return <div className="empty">{error}</div>;
  if (!items || items.length === 0) return <div className="empty">Nothing was in scope when this campaign started.</div>;
  return <ReviewItemsTable items={items} variant="admin" onChanged={() => { reloadItems(); onChanged?.(); }}/>;
}
function AccessReviewDetailPage() {
  const { id } = useParams();
  const timezone = useAppTimezone();
  const { data: campaign, reload: reloadCampaign } = useApiResource<ApiAccessReviewCampaign>(`/api/v1/access-reviews/${id}`);
  return <Page eyebrow="ACCESS REVIEW" title={campaign?.name || 'Campaign'} subtitle={campaign?.description || undefined} action={<Link to="/admin/access-reviews" className="btn">Back to list</Link>}>
    {campaign && <section className="panel" style={{marginBottom:18}}><div className="detail-section"><div className="key-grid">
      <div className="key"><span>Status</span><strong><StatusBadge status={campaign.status}/></strong></div>
      <div className="key"><span>Reviewer</span><strong>{campaign.reviewer_display_name || campaign.reviewer_id}</strong></div>
      <div className="key"><span>Due</span><strong>{formatDateTime(campaign.due_at, timezone)}</strong></div>
      <div className="key"><span>Frequency</span><strong>{campaignFrequencyLabel(campaign)}</strong></div>
      <div className="key"><span>Progress</span><strong>{campaign.decided_count} / {campaign.item_count} decided</strong></div>
    </div></div></section>}
    <section className="panel">{id && <CampaignItemsPanel campaignId={id} onChanged={reloadCampaign}/>}</section>
  </Page>;
}
function MyAccessReviewsPage() {
  const { data: items, loading, error, reload } = useApiResource<ApiAccessReviewItem[]>('/api/v1/access-reviews/items/mine');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // Grouped by campaign, collapsed by default — same shape as the admin Access Reviews list. No second fetch:
  // /items/mine already returns every pending item's full shape (a non-admin reviewer can't call the
  // admin-gated per-campaign items endpoint anyway).
  const campaigns = useMemo(() => {
    const map = new Map<string, { campaign_id: string; campaign_name: string; items: ApiAccessReviewItem[] }>();
    for (const item of items || []) {
      if (!map.has(item.campaign_id)) map.set(item.campaign_id, { campaign_id: item.campaign_id, campaign_name: item.campaign_name || 'Campaign', items: [] });
      map.get(item.campaign_id)!.items.push(item);
    }
    return Array.from(map.values());
  }, [items]);
  useEffect(() => {
    const timer = setInterval(() => reload(), 20000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <Page eyebrow="SELF-SERVICE" title="My Access Reviews" subtitle="Items you've been asked to certify — confirm the access is still needed, or revoke it. Package-granted access is grouped: decide the whole package or expand it for individual items." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    <LeaverRequestsPanel scope="mine"/>
    <ReenableRequestsPanel scope="mine"/>
    <TablePanel toolbar={undefined}>
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : campaigns.length === 0 ? <div className="empty">Nothing pending your review right now.</div> : <table><thead><tr><th>Campaign</th><th>Pending items</th></tr></thead><tbody>
        {campaigns.map(c => <Fragment key={c.campaign_id}>
        <tr>
          <td className="user-name"><button type="button" className="btn" style={{border:'none',background:'none',padding:0,display:'inline-flex',alignItems:'center',gap:6,fontWeight:600,color:'inherit'}} onClick={() => setExpandedId(expandedId === c.campaign_id ? null : c.campaign_id)}><ChevronRight size={14} style={{transform: expandedId === c.campaign_id ? 'rotate(90deg)' : 'none', transition:'transform 0.1s'}}/> {c.campaign_name}</button></td>
          <td>{c.items.length}</td>
        </tr>
        {expandedId === c.campaign_id && <tr><td colSpan={2} style={{padding:0,background:'#fafbfb'}}><ReviewItemsTable items={c.items} variant="mine" onChanged={reload}/></td></tr>}
        </Fragment>)}
      </tbody></table>}
    </TablePanel>
  </Page>;
}
// Package owners' narrow portal: rename a package they own, or remove an item from it. Nothing else — adding
// items, eligibility, approvers, assignment and deletion all stay Admin-only.
function MyPackagesPage() {
  const auth = useAuth();
  const { data: packages, loading, error, reload } = useApiResource<ApiPackage[]>('/api/v1/packages/owned');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [message, setMessage] = useState('');
  const call = async (path: string, init: RequestInit, failure: string): Promise<boolean> => {
    setMessage('');
    try {
      const response = await auth.apiRequest(path, init);
      if (response.ok) { reload(); return true; }
      setMessage((await response.json().catch(() => null))?.error?.message || failure);
    } catch { setMessage(failure); }
    return false;
  };
  const rename = async (pkg: ApiPackage) => {
    if (!newName.trim()) { setMessage('Enter a package name.'); return; }
    if (await call(`/api/v1/packages/${pkg.id}/owner-rename`, { method: 'PATCH', body: JSON.stringify({ name: newName.trim() }) }, 'Unable to rename this package.')) setRenamingId(null);
  };
  const removeItem = async (pkg: ApiPackage, item: ApiPackageItem) => {
    if (!window.confirm(`Remove "${item.resource_display_name || item.resource_id}" from "${pkg.name}"? Access already granted from this package is not affected — only future assignments.`)) return;
    await call(`/api/v1/packages/${pkg.id}/items/${item.id}`, { method: 'DELETE' }, 'Unable to remove this item.');
  };
  return <Page eyebrow="SELF-SERVICE" title="My Packages" subtitle="Packages you own. You can rename them or remove items — nothing else; ask an administrator for anything more." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
    {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !packages || packages.length === 0 ? <div className="panel"><div className="empty">You don't own any access packages.</div></div> : packages.map(pkg => <section key={pkg.id} className="panel" style={{marginBottom:18}}>
      <div className="panel-head">
        {renamingId === pkg.id
          ? <div style={{display:'flex',gap:8,alignItems:'center'}}><input className="select" value={newName} onChange={event => setNewName(event.target.value)} aria-label="New package name"/><button className="btn btn-primary" onClick={() => void rename(pkg)}>Save</button><button className="btn" onClick={() => setRenamingId(null)}>Cancel</button></div>
          : <><h2>📦 {pkg.name}</h2><button className="btn" onClick={() => { setRenamingId(pkg.id); setNewName(pkg.name); setMessage(''); }}>Rename</button></>}
      </div>
      <div className="detail-section">
        {pkg.items.map(item => <div key={item.id} className="activity-row" style={{gridTemplateColumns:'1fr auto'}}><div className="activity-copy"><strong>{item.resource_display_name || item.resource_id}</strong><small>{item.resource_type}</small></div><button className="btn" disabled={pkg.items.length <= 1} title={pkg.items.length <= 1 ? 'A package must keep at least one item' : undefined} onClick={() => void removeItem(pkg, item)}>Remove</button></div>)}
      </div>
    </section>)}
  </Page>;
}
function MyBusinessRolesPage() {
  const auth = useAuth();
  const { data: roles, loading, error, reload } = useApiResource<ApiBusinessRole[]>('/api/v1/business-roles/owned');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [message, setMessage] = useState('');
  const call = async (path: string, init: RequestInit, failure: string): Promise<boolean> => {
    setMessage('');
    try {
      const response = await auth.apiRequest(path, init);
      if (response.ok) { reload(); return true; }
      setMessage((await response.json().catch(() => null))?.error?.message || failure);
    } catch { setMessage(failure); }
    return false;
  };
  const rename = async (role: ApiBusinessRole) => {
    if (!newName.trim()) { setMessage('Enter a role name.'); return; }
    if (await call(`/api/v1/business-roles/${role.id}/owner-rename`, { method: 'PATCH', body: JSON.stringify({ name: newName.trim() }) }, 'Unable to rename this Business Role.')) setRenamingId(null);
  };
  const removeItem = async (role: ApiBusinessRole, item: ApiBusinessRoleItem) => {
    if (!window.confirm(`Remove "${item.resource_display_name || item.resource_id}" from "${role.name}"? Access already granted from this role is not affected — only future assignments.`)) return;
    await call(`/api/v1/business-roles/${role.id}/items/${item.id}`, { method: 'DELETE' }, 'Unable to remove this item.');
  };
  return <Page eyebrow="SELF-SERVICE" title="My Business Roles" subtitle="Business Roles you own. You can rename them or remove mapped items — nothing else; ask an administrator for anything more." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
    {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !roles || roles.length === 0 ? <div className="panel"><div className="empty">You don't own any Business Roles.</div></div> : roles.map(role => <section key={role.id} className="panel" style={{marginBottom:18}}>
      <div className="panel-head">
        {renamingId === role.id
          ? <div style={{display:'flex',gap:8,alignItems:'center'}}><input className="select" value={newName} onChange={event => setNewName(event.target.value)} aria-label="New Business Role name"/><button className="btn btn-primary" onClick={() => void rename(role)}>Save</button><button className="btn" onClick={() => setRenamingId(null)}>Cancel</button></div>
          : <><h2>🏷 {role.name}</h2><button className="btn" onClick={() => { setRenamingId(role.id); setNewName(role.name); setMessage(''); }}>Rename</button></>}
      </div>
      <div className="detail-section">
        {role.items.map(item => <div key={item.id} className="activity-row" style={{gridTemplateColumns:'1fr auto'}}><div className="activity-copy"><strong>{item.resource_display_name || item.resource_id}</strong><small>{item.resource_type}{item.it_role_label ? ` · ${item.it_role_label}` : ''}</small></div><button className="btn" disabled={role.items.length <= 1} title={role.items.length <= 1 ? 'A Business Role must keep at least one mapped item' : undefined} onClick={() => void removeItem(role, item)}>Remove</button></div>)}
      </div>
    </section>)}
  </Page>;
}
interface ApiLeaverPolicy { id: string; name: string; priority: number; delete_after_days: number | null; scope_type: string; scope_value: string | null; effective_time: string; notify_days_before: number[]; revoke_access: boolean; disable_accounts: boolean; disable_privileged_accounts: boolean; remove_group_memberships: boolean; status: string; is_default: boolean; }
interface ApiScheduledLeaver { user_id: string; user_display_name: string | null; user_email: string | null; department: string | null; employment_type: string | null; leaver_date: string; policy_name: string; due_at: string; status: string; }
// People with a leaver date that has not run yet. "Run now" starts the leaver process immediately; "Cancel" clears the date.
function ScheduledLeaversPanel() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: leavers, reload } = useApiResource<ApiScheduledLeaver[]>('/api/v1/lifecycle/leavers/scheduled');
  const [message, setMessage] = useState('');
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [justification, setJustification] = useState('');
  const [runBusy, setRunBusy] = useState(false);
  useEffect(() => { const timer = setInterval(() => reload(), 30000); return () => clearInterval(timer); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const runNow = async (leaver: ApiScheduledLeaver) => {
    if (justification.trim().length < 10) { setMessage('Enter a justification of at least 10 characters.'); return; }
    setRunBusy(true); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/lifecycle/people/${leaver.user_id}/leave-now`, { method: 'POST', body: JSON.stringify({ justification: justification.trim() }) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? `Accounts of ${leaver.user_display_name} disabled. Waiting for approval from ${(body?.approvers || []).join(', ') || 'an admin'}.` : (body?.error?.message || 'Unable to start the leaver process.'));
      if (response.ok) { setConfirmingId(null); setJustification(''); }
      reload();
    } catch { setMessage('Unable to reach the backend.'); } finally { setRunBusy(false); }
  };
  const cancel = async (leaver: ApiScheduledLeaver) => {
    if (!window.confirm(`Remove the leaver date for ${leaver.user_display_name}? Nothing will happen to them.`)) return;
    await auth.apiRequest(`/api/v1/lifecycle/people/${leaver.user_id}`, { method: 'PATCH', body: JSON.stringify({ clear_leaver_date: true }) });
    reload();
  };
  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Scheduled leavers</h2></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginTop:0,marginBottom:12}}>Set a leaver date on a person's page (or with a <strong>leaverDate</strong> column in a CSV import). On that date, at the matching policy's time, their access is revoked and their accounts are disabled in every connected IdP.</p>
      {message && <div className="notice" style={{marginBottom:12}}>{message}</div>}
      {!leavers || leavers.length === 0 ? <p className="subtitle" style={{margin:0}}>No leavers are scheduled.</p> : <div className="table-wrap"><table><thead><tr><th>Person</th><th>Leaver date</th><th>Policy</th><th>Runs at</th><th>Status</th><th></th></tr></thead><tbody>
        {leavers.map(leaver => <Fragment key={leaver.user_id}>
        <tr>
          <td><Link to={`/admin/users/${leaver.user_id}`} className="user-cell"><span className="avatar">{initialsFor(leaver.user_display_name || '?')}</span><span><span className="user-name">{leaver.user_display_name || leaver.user_id}</span>{leaver.user_email && <span className="user-email">{leaver.user_email}</span>}</span></Link></td>
          <td>{leaver.leaver_date}</td><td>{leaver.policy_name}</td><td>{formatDateTime(leaver.due_at, timezone)}</td>
          <td><StatusBadge status={leaver.status === 'DUE' ? 'PENDING' : 'SCHEDULED'}/></td>
          <td><span style={{display:'flex',gap:6}}><button className="btn" onClick={() => { setConfirmingId(leaver.user_id); setJustification(''); setMessage(''); }}>Run now</button><button className="btn" onClick={() => void cancel(leaver)}>Cancel</button></span></td>
        </tr>
        {confirmingId === leaver.user_id && <tr><td colSpan={6} style={{padding:0,background:'#fafbfb'}}>
          <div style={{padding:12,margin:8,border:'1px solid #e0a3a3',borderRadius:8,background:'#fff8f7'}}>
            <p className="subtitle" style={{marginTop:0}}>Start the leaver process for <strong>{leaver.user_display_name}</strong> now instead of {leaver.leaver_date}? Their accounts are disabled now; the manager must approve before the process runs.</p>
            <label className="key" style={{display:'block',marginBottom:10}}><span>Justification (at least 10 characters)</span><textarea className="select" style={{width:'100%',minHeight:60,resize:'vertical'}} value={justification} onChange={event => setJustification(event.target.value)} placeholder="Why start this now?"/></label>
            <div style={{display:'flex',gap:8}}>
              <button className="btn btn-primary" style={{background:'#ae4949',borderColor:'#ae4949'}} disabled={runBusy || justification.trim().length < 10} onClick={() => void runNow(leaver)}>{runBusy ? 'Working...' : 'Disable accounts and request approval'}</button>
              <button className="btn" disabled={runBusy} onClick={() => { setConfirmingId(null); setJustification(''); }}>Cancel</button>
            </div>
          </div>
        </td></tr>}
        </Fragment>)}
      </tbody></table></div>}
    </div>
  </section>;
}
const emptyPolicyForm = { name: '', priority: '100', scope_type: 'ALL', scope_value: '', effective_time: '23:59', notify_days_before: '7, 1', revoke_access: true, disable_accounts: true, disable_privileged_accounts: true, remove_group_memberships: false, delete_after_days: '', status: 'ACTIVE' };
// Admin-defined leaver policies: who they apply to, when they run, which actions they take. First matching policy by
// priority wins; the Default covers everyone else.
function LeaverPoliciesPanel() {
  const auth = useAuth();
  const { data: policies, reload } = useApiResource<ApiLeaverPolicy[]>('/api/v1/lifecycle/leaver-policies');
  const { data: departments } = useApiResource<{ id: string; name: string }[]>('/api/v1/policies/departments');
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyPolicyForm);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const editing = (policies || []).find(p => p.id === editingId);
  const openNew = () => { setEditingId(null); setForm(emptyPolicyForm); setMessage(''); setOpen(true); };
  const openEdit = (policy: ApiLeaverPolicy) => { setEditingId(policy.id); setForm({ name: policy.name, priority: String(policy.priority), scope_type: policy.scope_type, scope_value: policy.scope_value || '', effective_time: policy.effective_time, notify_days_before: policy.notify_days_before.join(', '), revoke_access: policy.revoke_access, disable_accounts: policy.disable_accounts, disable_privileged_accounts: policy.disable_privileged_accounts, remove_group_memberships: policy.remove_group_memberships, delete_after_days: policy.delete_after_days ? String(policy.delete_after_days) : '', status: policy.status }); setMessage(''); setOpen(true); };
  const save = async () => {
    if (!form.name.trim()) { setMessage('Enter a policy name.'); return; }
    if (form.scope_type !== 'ALL' && !form.scope_value) { setMessage('Choose who this policy applies to.'); return; }
    const days = form.notify_days_before.split(',').map(x => x.trim()).filter(Boolean).map(Number);
    if (days.some(d => !Number.isInteger(d) || d < 0 || d > 90)) { setMessage('Reminders must be whole numbers of days between 0 and 90, e.g. 7, 1.'); return; }
    setSaving(true); setMessage('');
    try {
      const payload = { name: form.name.trim(), priority: Number(form.priority) || 100, scope_type: form.scope_type, scope_value: form.scope_type === 'ALL' ? null : form.scope_value, effective_time: form.effective_time, notify_days_before: days, revoke_access: form.revoke_access, disable_accounts: form.disable_accounts, disable_privileged_accounts: form.disable_privileged_accounts, remove_group_memberships: form.remove_group_memberships, ...(form.delete_after_days.trim() ? { delete_after_days: Number(form.delete_after_days) } : { clear_delete_after_days: true }), status: form.status };
      const response = editingId ? await auth.apiRequest(`/api/v1/lifecycle/leaver-policies/${editingId}`, { method: 'PATCH', body: JSON.stringify(payload) }) : await auth.apiRequest('/api/v1/lifecycle/leaver-policies', { method: 'POST', body: JSON.stringify(payload) });
      if (response.ok) { setOpen(false); setEditingId(null); reload(); }
      else setMessage((await response.json().catch(() => null))?.error?.message || 'Unable to save this policy.');
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };
  const remove = async (policy: ApiLeaverPolicy) => {
    if (!window.confirm(`Delete the leaver policy "${policy.name}"? People it covered fall back to the next matching policy or the default.`)) return;
    await auth.apiRequest(`/api/v1/lifecycle/leaver-policies/${policy.id}`, { method: 'DELETE' });
    reload();
  };
  const actionSummary = (p: ApiLeaverPolicy) => [p.revoke_access && 'revoke access', p.disable_accounts && 'disable accounts in all IdPs', p.disable_privileged_accounts && 'disable PU/TU', p.remove_group_memberships && 'remove from all groups'].filter(Boolean).join(' · ') || 'nothing (record only)';
  const check = (label: string, key: 'revoke_access' | 'disable_accounts' | 'disable_privileged_accounts' | 'remove_group_memberships') => <label style={{display:'flex',alignItems:'center',gap:8,fontSize:13}}><input type="checkbox" checked={form[key]} onChange={event => setForm({...form, [key]: event.target.checked})}/> {label}</label>;
  return <section className="panel" style={{marginBottom:18}}>
    <div className="panel-head"><h2>Leaver policies</h2><button className="btn" onClick={openNew}><Plus size={14}/> Add policy</button></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginTop:0,marginBottom:12}}>What happens when someone leaves — whether triggered by their leaver date, a CSV termination, the directory showing them disabled, or the manual button. The first active policy (lowest priority number) whose scope matches the person is used; the <strong>Default</strong> covers everyone else.</p>
      {open && <div style={{border:'1px solid #e1e8ea',borderRadius:8,padding:14,marginBottom:14}}>
        <div className="key-grid" style={{marginBottom:10}}>
          <label className="key"><span>Name</span><input className="select" value={form.name} onChange={event => setForm({...form, name: event.target.value})}/></label>
          {!editing?.is_default && <label className="key"><span>Priority (lower runs first)</span><input className="select" type="number" min={0} max={999} value={form.priority} onChange={event => setForm({...form, priority: event.target.value})}/></label>}
          {!editing?.is_default && <label className="key"><span>Applies to</span><select className="select" value={form.scope_type} onChange={event => setForm({...form, scope_type: event.target.value, scope_value: ''})}><option value="ALL">Everyone</option><option value="DEPARTMENT">A department</option><option value="EMPLOYMENT_TYPE">An employment type</option></select></label>}
          {!editing?.is_default && form.scope_type === 'DEPARTMENT' && <label className="key"><span>Department</span><select className="select" value={form.scope_value} onChange={event => setForm({...form, scope_value: event.target.value})}><option value="">Select a department</option>{(departments || []).map(d => <option key={d.id} value={d.name}>{d.name}</option>)}</select></label>}
          {!editing?.is_default && form.scope_type === 'EMPLOYMENT_TYPE' && <label className="key"><span>Employment type</span><select className="select" value={form.scope_value} onChange={event => setForm({...form, scope_value: event.target.value})}><option value="">Select a type</option>{EMPLOYMENT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}</select></label>}
          <label className="key"><span>Runs at (on the leaver date, app timezone)</span><input className="select" type="time" value={form.effective_time} onChange={event => setForm({...form, effective_time: event.target.value})}/></label>
          <label className="key"><span>Remind manager/owners (days before)</span><input className="select" value={form.notify_days_before} onChange={event => setForm({...form, notify_days_before: event.target.value})} placeholder="7, 1"/></label>
        </div>
        <div style={{display:'grid',gap:6,marginBottom:12}}>
          {check('Revoke all AccessPilot access (including in Entra)', 'revoke_access')}
          {check('Disable the account in every connected IdP', 'disable_accounts')}
          {check('Disable linked privileged (PU) / test (TU) accounts', 'disable_privileged_accounts')}
          {check('Remove from ALL groups (including ones AccessPilot did not grant)', 'remove_group_memberships')}
          <label className="key"><span>Delete accounts from all IdPs after (days, blank = never)</span><input className="select" type="number" min={1} max={3650} value={form.delete_after_days} onChange={event => setForm({...form, delete_after_days: event.target.value})} placeholder="e.g. 90"/></label>
          {form.delete_after_days.trim() && <div className="notice">Irreversible: this many days after the leaver process runs, the person's accounts are deleted in every connected IdP (Entra keeps a deleted user recoverable for 30 days). AccessPilot keeps the record, labelled Deleted, for audit.</div>}
          {!editing?.is_default && <label style={{display:'flex',alignItems:'center',gap:8,fontSize:13}}><input type="checkbox" checked={form.status === 'ACTIVE'} onChange={event => setForm({...form, status: event.target.checked ? 'ACTIVE' : 'DISABLED'})}/> Policy is active</label>}
        </div>
        {message && <div className="notice" style={{marginBottom:10}}>{message}</div>}
        <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={saving} onClick={() => void save()}>{saving ? 'Saving...' : editingId ? 'Save changes' : 'Create policy'}</button><button className="btn" onClick={() => { setOpen(false); setEditingId(null); }}>Cancel</button></div>
      </div>}
      <div className="table-wrap"><table><thead><tr><th>Policy</th><th>Applies to</th><th>Runs at</th><th>Reminders</th><th>Actions</th><th>Deletes accounts</th><th>Status</th><th></th></tr></thead><tbody>
        {(policies || []).map(policy => <tr key={policy.id}>
          <td className="user-name">{policy.name}{policy.is_default && <span className="badge neutral" style={{marginLeft:6}}>Default</span>}<div className="user-email">priority {policy.priority}</div></td>
          <td>{policy.scope_type === 'ALL' ? 'Everyone else' : `${policy.scope_type === 'DEPARTMENT' ? 'Department' : 'Type'}: ${policy.scope_value}`}</td>
          <td>{policy.effective_time}</td>
          <td>{policy.notify_days_before.length ? policy.notify_days_before.map(d => `${d}d`).join(', ') : '—'}</td>
          <td style={{whiteSpace:'normal',maxWidth:320}}>{actionSummary(policy)}</td>
          <td>{policy.delete_after_days ? `after ${policy.delete_after_days} days` : 'never'}</td>
          <td><StatusBadge status={policy.status === 'ACTIVE' ? 'Active' : 'Disabled'}/></td>
          <td><span style={{display:'flex',gap:6}}><button className="btn" onClick={() => openEdit(policy)}>Edit</button>{!policy.is_default && <button className="btn" onClick={() => void remove(policy)}>Delete</button>}</span></td>
        </tr>)}
      </tbody></table></div>
    </div>
  </section>;
}
interface ApiLifecycleEvent { id: string; event_type: string; source: string; created_at: string; user_id: string; user_display_name: string | null; user_email: string | null; changes: Record<string, { from: string | null; to: string | null }>; revoked_count: number; granted_count: number; privileged_flagged_count: number; review_campaign_id: string | null; review_campaign_name: string | null; review_status: string | null; review_reviewer_name: string | null; review_item_count: number; review_decided_count: number; review_note: string | null; notified: string[]; }
interface ApiPendingMove { id: string; user_id: string; user_display_name: string | null; user_email: string | null; current_department: string | null; current_job_title: string | null; new_department: string | null; new_job_title: string | null; effective_at: string; source: string; status: string; failure_reason: string | null; created_at: string; applied_at: string | null; }
interface ApiLifecycleSettings { mover_review_enabled: boolean; revoke_on_directory_disable: boolean; review_due_days: number; lifecycle_owners: { user_id: string; display_name: string | null; email: string | null }[]; }
const MOVER_NOTES: Record<string, string> = {
  NO_LEFTOVER_ACCESS: 'Nothing else to review',
  AUTO_REVOKE_OFF: 'Automatic revocation is switched off — access was kept',
  ACCOUNT_DISABLE_FAILED: 'Some accounts could not be disabled — see the audit log and retry from the person\'s page',
  NO_REVIEWER: 'No reviewer available — set a manager for this person or add a lifecycle owner above',
  REVIEW_ALREADY_OPEN: 'A review was already open',
  REVIEW_DISABLED: 'Automatic review is switched off',
};
const SOURCE_LABELS: Record<string, string> = { SYNC: 'Directory sync', ADMIN_EDIT: 'Edited in AccessPilot', CSV: 'CSV import' };
// Set up how movers are handled, and see every detected move: who moved, what changed, what access was taken away
// or newly made eligible, the review of whatever access no policy granted, and who was told.
// Downloads the full Joiner / Mover / Leaver PDF report.
function JmlReportButton() {
  const auth = useAuth();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const download = async () => {
    setBusy(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/lifecycle/report.pdf');
      if (!response.ok) { setMessage((await response.json().catch(() => null))?.error?.message || 'Unable to generate the report.'); return; }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = `jml-report-${new Date().toISOString().slice(0, 10)}.pdf`;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    } catch { setMessage('Unable to reach the backend.'); } finally { setBusy(false); }
  };
  return <div style={{display:'flex',alignItems:'center',gap:10,margin:'0 0 12px',flexWrap:'wrap'}}>
    <button className="btn btn-primary" disabled={busy} onClick={() => void download()}>{busy ? 'Generating PDF...' : 'Download JML report (PDF)'}</button>
    <span className="subtitle" style={{margin:0}}>Every joiner, mover and leaver process with the items each touched, policies, re-enable requests and account deletions.</span>
    {message && <span className="notice">{message}</span>}
  </div>;
}
interface ApiLeaverRequest { id: string; user_id: string; user_display_name: string | null; user_email: string | null; requested_by_name: string | null; justification: string; status: string; approvers: string[]; decided_by_name: string | null; decision_note: string | null; decided_at: string | null; created_at: string; can_decide: boolean; outcome: string | null; }
// Manual leaver requests: accounts are already disabled; the manager (lifecycle owners when none) approves -> leaver process, or denies -> accounts enabled again.
function LeaverRequestsPanel({ scope }: { scope: 'admin' | 'mine' }) {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: rows, error, reload } = useApiResource<ApiLeaverRequest[]>(scope === 'admin' ? '/api/v1/lifecycle/leaver-requests' : '/api/v1/lifecycle/leaver-requests/mine');
  const [message, setMessage] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [noteById, setNoteById] = useState<Record<string, string>>({});
  const decide = async (row: ApiLeaverRequest, approve: boolean) => {
    setBusyId(row.id); setMessage('');
    try {
      const note = (noteById[row.id] || '').trim();
      const response = await auth.apiRequest(`/api/v1/lifecycle/leaver-requests/${row.id}/decision`, { method: 'POST', body: JSON.stringify({ approve, note: note || undefined }) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? (body?.outcome || 'Done.') : (body?.error?.message || 'Unable to record the decision.'));
      reload();
    } catch { setMessage('Unable to reach the backend.'); } finally { setBusyId(null); }
  };
  if (scope === 'mine' && (!rows || rows.length === 0)) return null;
  return <section className="panel">
    <div className="panel-head"><h2>{scope === 'mine' ? 'Approvals: start a leaver process' : 'Leaver requests (manual start)'}</h2></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginTop:0,marginBottom:10}}>Flow: justification, then the accounts are disabled straight away, then the manager approves (lifecycle owners when there is no manager). Approved: the leaver process runs. Denied: the accounts are enabled again and the admin who started it is notified.</p>
      {message && <div className="notice" style={{marginBottom:10}}>{message}</div>}
      {error ? <div className="notice">{error}</div> : !rows ? <p className="subtitle">Loading...</p> : rows.length === 0 ? <p className="subtitle" style={{margin:0}}>No requests.</p> : <div className="table-wrap"><table><thead><tr><th>Person</th><th>Justification</th><th>Requested</th><th>Approver</th><th>Status</th><th></th></tr></thead><tbody>
        {rows.map(row => <tr key={row.id}>
          <td className="user-name">{row.user_display_name}{row.user_email && <div className="user-email">{row.user_email}</div>}</td>
          <td style={{whiteSpace:'normal',maxWidth:320}}>{row.justification}<div className="user-email">by {row.requested_by_name || 'admin'}</div></td>
          <td>{formatDateTime(row.created_at, timezone)}</td>
          <td style={{whiteSpace:'normal'}}>{row.approvers.join(', ') || 'Admins'}</td>
          <td style={{whiteSpace:'normal'}}><StatusBadge status={row.status === 'APPROVED' ? 'Active' : row.status === 'PENDING' ? 'SCHEDULED' : 'Disabled'}/><div className="user-email">{row.status}{row.decided_by_name ? ` by ${row.decided_by_name}` : ''}{row.decision_note ? `: ${row.decision_note}` : ''}{row.outcome ? ` - ${row.outcome}` : ''}</div></td>
          <td>{row.can_decide && <div style={{display:'flex',flexDirection:'column',gap:6,minWidth:200}}>
            <input className="select" placeholder="Optional note" value={noteById[row.id] || ''} onChange={event => setNoteById({...noteById, [row.id]: event.target.value})}/>
            <span style={{display:'flex',gap:6}}><button className="btn btn-primary" disabled={busyId === row.id} onClick={() => void decide(row, true)}>Approve</button><button className="btn" disabled={busyId === row.id} onClick={() => void decide(row, false)}>Deny</button></span>
          </div>}</td>
        </tr>)}
      </tbody></table></div>}
    </div>
  </section>;
}
interface ApiReenableRequest { id: string; user_id: string; user_display_name: string | null; user_email: string | null; requested_by_name: string | null; reason: string; status: string; approvers: string[]; decided_by_name: string | null; decision_note: string | null; decided_at: string | null; created_at: string; can_decide: boolean; scope_label: string; }
// Requests to enable a leaver's account again: reason + approval by the person's manager (lifecycle owners when none).
function ReenableRequestsPanel({ scope }: { scope: 'admin' | 'mine' }) {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: rows, error, reload } = useApiResource<ApiReenableRequest[]>(scope === 'admin' ? '/api/v1/lifecycle/reenable-requests' : '/api/v1/lifecycle/reenable-requests/mine');
  const [message, setMessage] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [noteById, setNoteById] = useState<Record<string, string>>({});
  const decide = async (row: ApiReenableRequest, approve: boolean) => {
    setBusyId(row.id); setMessage('');
    try {
      const note = (noteById[row.id] || '').trim();
      const response = await auth.apiRequest(`/api/v1/lifecycle/reenable-requests/${row.id}/decision`, { method: 'POST', body: JSON.stringify({ approve, note: note || undefined }) });
      const body = await response.json().catch(() => null);
      setMessage(response.ok ? (approve ? `Approved. ${body?.accounts_note || ''}` : 'Rejected.') : (body?.error?.message || 'Unable to record the decision.'));
      reload();
    } catch { setMessage('Unable to reach the backend.'); } finally { setBusyId(null); }
  };
  if (scope === 'mine' && (!rows || rows.length === 0)) return null;
  return <section className="panel">
    <div className="panel-head"><h2>{scope === 'mine' ? 'Approvals: enable a leaver\'s account again' : 'Re-enable requests (after leaving)'}</h2></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginTop:0,marginBottom:10}}>Once the leaver process has run, an account can only be enabled again with a valid reason and the manager's approval (lifecycle owners when there is no manager). Approval re-enables the accounts only; access is not restored.</p>
      {message && <div className="notice" style={{marginBottom:10}}>{message}</div>}
      {error ? <div className="notice">{error}</div> : !rows ? <p className="subtitle">Loading...</p> : rows.length === 0 ? <p className="subtitle" style={{margin:0}}>No requests.</p> : <div className="table-wrap"><table><thead><tr><th>Person</th><th>Scope</th><th>Reason</th><th>Requested</th><th>Approver</th><th>Status</th><th></th></tr></thead><tbody>
        {rows.map(row => <tr key={row.id}>
          <td className="user-name">{row.user_display_name}{row.user_email && <div className="user-email">{row.user_email}</div>}</td>
          <td><span className="badge neutral">{row.scope_label}</span></td>
          <td style={{whiteSpace:'normal',maxWidth:320}}>{row.reason}<div className="user-email">by {row.requested_by_name || 'admin'}</div></td>
          <td>{formatDateTime(row.created_at, timezone)}</td>
          <td style={{whiteSpace:'normal'}}>{row.approvers.join(', ') || 'Admins'}</td>
          <td style={{whiteSpace:'normal'}}><StatusBadge status={row.status === 'APPROVED' ? 'Active' : row.status === 'PENDING' ? 'SCHEDULED' : 'Disabled'}/><div className="user-email">{row.status}{row.decided_by_name ? ` by ${row.decided_by_name}` : ''}{row.decision_note ? `: ${row.decision_note}` : ''}</div></td>
          <td>{row.can_decide && <div style={{display:'flex',flexDirection:'column',gap:6,minWidth:200}}>
            <input className="select" placeholder="Optional note" value={noteById[row.id] || ''} onChange={event => setNoteById({...noteById, [row.id]: event.target.value})}/>
            <span style={{display:'flex',gap:6}}><button className="btn btn-primary" disabled={busyId === row.id} onClick={() => void decide(row, true)}>Approve</button><button className="btn" disabled={busyId === row.id} onClick={() => void decide(row, false)}>Reject</button></span>
          </div>}</td>
        </tr>)}
      </tbody></table></div>}
    </div>
  </section>;
}
function MoversPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const [eventType, setEventType] = useState('MOVER');
  const { data: events, loading, error, reload } = useApiResource<ApiLifecycleEvent[]>(`/api/v1/lifecycle/events?event_type=${eventType}`);
  const { data: settings, reload: reloadSettings } = useApiResource<ApiLifecycleSettings>('/api/v1/lifecycle/settings');
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: allMoves, reload: reloadMoves } = useApiResource<ApiPendingMove[]>('/api/v1/lifecycle/moves');
  const { data: departments } = useApiResource<{ id: string; name: string }[]>('/api/v1/policies/departments');
  const moves = (allMoves || []).filter(m => m.status === 'SCHEDULED' || m.status === 'FAILED');
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveForm, setMoveForm] = useState({ user_id: '', department: '', job_title: '', effective_at: '' });
  const [moveSaving, setMoveSaving] = useState(false);
  const [moveMessage, setMoveMessage] = useState('');
  const scheduleMove = async () => {
    if (!moveForm.user_id || !moveForm.effective_at || (!moveForm.department.trim() && !moveForm.job_title.trim())) { setMoveMessage('Pick a person, a date and a new department and/or job title.'); return; }
    setMoveSaving(true); setMoveMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/lifecycle/moves', { method: 'POST', body: JSON.stringify({ user_id: moveForm.user_id, department: moveForm.department.trim() || undefined, job_title: moveForm.job_title.trim() || undefined, effective_at: new Date(moveForm.effective_at).toISOString() }) });
      if (response.ok) { setMoveOpen(false); setMoveForm({ user_id: '', department: '', job_title: '', effective_at: '' }); reloadMoves(); }
      else setMoveMessage((await response.json().catch(() => null))?.error?.message || 'Unable to schedule this move.');
    } catch { setMoveMessage('Unable to reach the backend.'); } finally { setMoveSaving(false); }
  };
  const cancelMove = async (move: ApiPendingMove) => {
    if (!window.confirm(`Cancel the scheduled move for ${move.user_display_name || 'this person'}? Nothing about them will change.`)) return;
    await auth.apiRequest(`/api/v1/lifecycle/moves/${move.id}`, { method: 'DELETE' });
    reloadMoves();
  };
  const [enabled, setEnabled] = useState(true);
  const [revokeOnDisable, setRevokeOnDisable] = useState(true);
  const [dueDays, setDueDays] = useState('14');
  const [ownerIds, setOwnerIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (!settings) return;
    setEnabled(settings.mover_review_enabled); setRevokeOnDisable(settings.revoke_on_directory_disable); setDueDays(String(settings.review_due_days)); setOwnerIds(settings.lifecycle_owners.map(o => o.user_id));
  }, [settings]);
  useEffect(() => {
    const timer = setInterval(() => { reload(); reloadMoves(); }, 30000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const save = async () => {
    if (!dueDays || Number(dueDays) < 1) { setMessage('Enter a due window of at least 1 day.'); return; }
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/lifecycle/settings', { method: 'PUT', body: JSON.stringify({ mover_review_enabled: enabled, revoke_on_directory_disable: revokeOnDisable, review_due_days: Number(dueDays), lifecycle_owner_ids: ownerIds }) });
      if (response.ok) { setMessage('Saved.'); reloadSettings(); }
      else setMessage((await response.json().catch(() => null))?.error?.message || 'Unable to save these settings.');
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };
  return <Page eyebrow="JOINER · MOVER · LEAVER" title="Movers" subtitle="People whose department or job title changed — what happened to their access and who reviews what is left. Switch the list below to joiners too. Leavers now have their own page." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>Mover setup</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{marginTop:0,marginBottom:14}}>When someone's department or job title changes, AccessPilot removes the access their old attributes granted and grants what the new ones qualify for. It can then start a review of the access <strong>no policy granted</strong> (manual, package or requested access). The reviewer is the person's manager from the Org Chart; if they have none, the first lifecycle owner below reviews it, and the next owner is the fallback.</p>
        <label style={{display:'flex',alignItems:'center',gap:8,fontWeight:600,fontSize:13,marginBottom:12}}><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)}/> Automatically review a mover's remaining access</label>
        <label style={{display:'flex',alignItems:'center',gap:8,fontWeight:600,fontSize:13,marginBottom:4}}><input type="checkbox" checked={revokeOnDisable} onChange={event => setRevokeOnDisable(event.target.checked)}/> Leavers: revoke all access when the directory shows someone as disabled</label>
        <p className="subtitle" style={{marginTop:0,marginBottom:12}}>When directory sync sees a person switched from active to disabled in Entra/Okta, all their AccessPilot access is revoked (including in Entra) and their linked privileged/test accounts are disabled — like a CSV termination. Switch this off if people are sometimes disabled only temporarily; the leaver is then just recorded.</p>
        <div className="key-grid" style={{marginBottom:12}}>
          <label className="key"><span>Review is due in (days)</span><input className="select" type="number" min={1} max={365} value={dueDays} onChange={event => setDueDays(event.target.value)}/></label>
          <label className="key"><span>Lifecycle owners (notified about every move; review it when there is no manager)</span>
            <select className="select" value="" onChange={event => { const id = event.target.value; if (id && !ownerIds.includes(id)) setOwnerIds([...ownerIds, id]); }}><option value="">Add an owner…</option>{(users || []).filter(u => !ownerIds.includes(u.id)).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
        </div>
        {ownerIds.length > 0 && <div style={{display:'flex',flexWrap:'wrap',gap:8,marginBottom:12}}>{ownerIds.map((id, index) => <span key={id} className="badge neutral" style={{display:'inline-flex',alignItems:'center',gap:6}}>{index === 0 ? '1st · ' : ''}{(users || []).find(u => u.id === id)?.display_name || id}<button type="button" className="btn" aria-label="Remove owner" onClick={() => setOwnerIds(ownerIds.filter(x => x !== id))} style={{padding:'0 6px',minWidth:0}}><X size={11}/></button></span>)}</div>}
        {message && <div className="notice" style={{marginBottom:12}}>{message}</div>}
        <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>{saving ? 'Saving...' : 'Save mover setup'}</button>
      </div>
    </section>
    <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>Scheduled moves</h2><button className="btn" onClick={() => { setMoveOpen(!moveOpen); setMoveMessage(''); }}><Plus size={14}/> Schedule a move</button></div>
      <div className="detail-section">
        <p className="subtitle" style={{marginTop:0,marginBottom:12}}>A move with a future date changes nothing until then. On that date the department/job title is updated, old birthright access is removed, new access is granted and the leftover-access review starts — all together. A CSV import can schedule moves too, with an <strong>effectiveDate</strong> column (YYYY-MM-DD).</p>
        {moveOpen && <div style={{border:'1px solid #e1e8ea',borderRadius:8,padding:14,marginBottom:14}}>
          <div className="key-grid" style={{marginBottom:10}}>
            <label className="key"><span>Person</span><select className="select" value={moveForm.user_id} onChange={event => setMoveForm({...moveForm, user_id: event.target.value})}><option value="">Select a person</option>{(users || []).filter(u => u.account_type === 'NORMAL').map(u => <option key={u.id} value={u.id}>{u.display_name}{u.department ? ` — ${u.department}` : ''}</option>)}</select></label>
            <label className="key"><span>Takes effect (your device's local time)</span><input className="select" type="datetime-local" value={moveForm.effective_at} onChange={event => setMoveForm({...moveForm, effective_at: event.target.value})}/></label>
            <label className="key"><span>New department</span><input className="select" list="mover-departments" value={moveForm.department} onChange={event => setMoveForm({...moveForm, department: event.target.value})} placeholder="leave empty to keep the current one"/><datalist id="mover-departments">{(departments || []).map(d => <option key={d.id} value={d.name}/>)}</datalist></label>
            <label className="key"><span>New job title</span><input className="select" value={moveForm.job_title} onChange={event => setMoveForm({...moveForm, job_title: event.target.value})} placeholder="leave empty to keep the current one"/></label>
          </div>
          {moveMessage && <div className="notice" style={{marginBottom:10}}>{moveMessage}</div>}
          <div style={{display:'flex',gap:8}}><button className="btn btn-primary" disabled={moveSaving} onClick={() => void scheduleMove()}>{moveSaving ? 'Scheduling...' : 'Schedule move'}</button><button className="btn" onClick={() => setMoveOpen(false)}>Cancel</button></div>
        </div>}
        {moves.length === 0 ? <p className="subtitle" style={{margin:0}}>No moves are scheduled.</p> : <div className="table-wrap"><table><thead><tr><th>Person</th><th>Now</th><th>Will become</th><th>Takes effect</th><th>Source</th><th>Status</th><th></th></tr></thead><tbody>
          {moves.map(move => <tr key={move.id}>
            <td><Link to={`/admin/users/${move.user_id}`} className="user-cell"><span className="avatar">{initialsFor(move.user_display_name || '?')}</span><span><span className="user-name">{move.user_display_name || move.user_id}</span>{move.user_email && <span className="user-email">{move.user_email}</span>}</span></Link></td>
            <td style={{whiteSpace:'normal'}}>{move.current_department || '—'}<div className="user-email">{move.current_job_title || '—'}</div></td>
            <td style={{whiteSpace:'normal'}}><strong>{move.new_department || (move.current_department ? '(unchanged)' : '—')}</strong><div className="user-email">{move.new_job_title || '(unchanged)'}</div></td>
            <td>{formatDateTime(move.effective_at, timezone)}</td>
            <td>{SOURCE_LABELS[move.source] || move.source}</td>
            <td style={{whiteSpace:'normal'}}><StatusBadge status={move.status === 'FAILED' ? 'FAILED' : 'SCHEDULED'}/>{move.failure_reason && move.status === 'FAILED' && <div className="user-email">{move.failure_reason}</div>}</td>
            <td>{move.status === 'SCHEDULED' && <button className="btn" onClick={() => void cancelMove(move)}>Cancel</button>}</td>
          </tr>)}
        </tbody></table></div>}
      </div>
    </section>
    <div className="toolbar"><div className="toolbar-left"><select className="select" aria-label="Event type" value={eventType} onChange={event => setEventType(event.target.value)}><option value="MOVER">Movers</option><option value="JOINER">Joiners</option></select></div></div>
    <TablePanel toolbar={undefined}>
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !events || events.length === 0 ? <div className="empty">No {eventType.toLowerCase()}s recorded yet. A move shows up here after a directory sync, an edit, or a CSV import changes someone's department or job title.</div> : <table><thead><tr><th>Person</th><th>What changed</th><th>Source</th><th>Access removed</th><th>New eligible</th><th>{eventType === 'MOVER' ? 'Review of remaining access' : 'Note'}</th><th>Notified</th><th>When</th></tr></thead><tbody>
        {events.map(event => <tr key={event.id}>
          <td><Link to={`/admin/users/${event.user_id}`} className="user-cell"><span className="avatar">{initialsFor(event.user_display_name || '?')}</span><span><span className="user-name">{event.user_display_name || event.user_id}</span>{event.user_email && <span className="user-email">{event.user_email}</span>}</span></Link></td>
          <td style={{whiteSpace:'normal'}}>{event.event_type === 'JOINER' ? <strong>New person</strong> : event.event_type === 'LEAVER' ? <><strong>Disabled in the directory</strong>{Number((event.changes as Record<string, unknown>).linked_accounts_disabled) > 0 && <div className="user-email">{String((event.changes as Record<string, unknown>).linked_accounts_disabled)} linked PU/TU account(s) disabled</div>}</> : null}{event.event_type === 'MOVER' && Object.entries(event.changes).filter(([field]) => field === 'department' || field === 'job_title').map(([field, change]) => <div key={field}><span className="user-email" style={{display:'inline',marginRight:6}}>{field.replace('_', ' ')}</span>{change.from || '—'} → <strong>{change.to || '—'}</strong></div>)}</td>
          <td>{SOURCE_LABELS[event.source] || event.source}</td>
          <td>{event.revoked_count > 0 ? <span className="badge danger">{event.revoked_count}</span> : '0'}</td>
          <td>{event.granted_count > 0 ? <span className="badge success">{event.granted_count}</span> : '0'}</td>
          <td style={{whiteSpace:'normal',minWidth:220}}>{event.review_campaign_id
            ? <><Link to="/admin/access-reviews" className="user-name">{event.review_campaign_name}</Link><div style={{display:'flex',gap:8,alignItems:'center',marginTop:4}}><StatusBadge status={event.review_status || 'ACTIVE'}/><span className="user-email" style={{margin:0}}>{event.review_decided_count}/{event.review_item_count} decided · reviewer {event.review_reviewer_name || '—'}</span></div>{event.privileged_flagged_count > 0 && <div style={{marginTop:4}}><span className="badge warning" title="Access held by this person's linked privileged (PU) or test (TU) accounts is part of this review">{event.privileged_flagged_count} PU/TU access item{event.privileged_flagged_count === 1 ? '' : 's'} flagged</span></div>}</>
            : <span className="user-email" style={{margin:0}}>{(event.review_note && MOVER_NOTES[event.review_note]) || 'No review started'}</span>}</td>
          <td style={{whiteSpace:'normal'}}>{event.notified.length > 0 ? event.notified.join(', ') : '—'}</td>
          <td>{formatDateTime(event.created_at, timezone)}</td>
        </tr>)}
      </tbody></table>}
    </TablePanel>
  </Page>;
}
// Leavers get their own page, same as Joiners and Movers: requests waiting on a manager's approval, scheduled
// leaver dates, the leaver policies that decide what happens and when, and the log of every leaver run.
function LeaversPage() {
  const timezone = useAppTimezone();
  const { data: events, loading, error, reload } = useApiResource<ApiLifecycleEvent[]>('/api/v1/lifecycle/events?event_type=LEAVER');
  useEffect(() => { const timer = setInterval(() => reload(), 30000); return () => clearInterval(timer); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return <Page eyebrow="JOINER · MOVER · LEAVER" title="Leavers" subtitle="People who left, or are about to — manual start requests waiting on a manager, scheduled leaver dates, the policies that decide what happens, and the log of every run. Global settings (revoke access when a directory shows someone disabled, lifecycle owners) are on the Movers page." action={<button className="btn" aria-label="Refresh" onClick={() => reload()}><RefreshCw size={14}/></button>}>
    <JmlReportButton/>
    <LeaverRequestsPanel scope="admin"/>
    <ReenableRequestsPanel scope="admin"/>
    <ScheduledLeaversPanel/>
    <LeaverPoliciesPanel/>
    <TablePanel toolbar={<div className="panel-head" style={{border:'none',padding:0,marginBottom:0}}><h2 style={{fontSize:15}}>Leaver log</h2></div>}>
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !events || events.length === 0 ? <div className="empty">No leavers recorded yet. A leaver shows up here after their leaver date runs, a manual request is approved, a CSV termination, or a directory disable.</div> : <table><thead><tr><th>Person</th><th>What changed</th><th>Source</th><th>Access removed</th><th>Note</th><th>Notified</th><th>When</th></tr></thead><tbody>
        {events.map(event => <tr key={event.id}>
          <td><Link to={`/admin/users/${event.user_id}`} className="user-cell"><span className="avatar">{initialsFor(event.user_display_name || '?')}</span><span><span className="user-name">{event.user_display_name || event.user_id}</span>{event.user_email && <span className="user-email">{event.user_email}</span>}</span></Link></td>
          <td style={{whiteSpace:'normal'}}><strong>Disabled in the directory</strong>{Number((event.changes as Record<string, unknown>).linked_accounts_disabled) > 0 && <div className="user-email">{String((event.changes as Record<string, unknown>).linked_accounts_disabled)} linked PU/TU account(s) disabled</div>}{Number((event.changes as Record<string, unknown>).groups_removed) > 0 && <div className="user-email">removed from {String((event.changes as Record<string, unknown>).groups_removed)} group(s)</div>}</td>
          <td>{SOURCE_LABELS[event.source] || event.source}</td>
          <td>{event.revoked_count > 0 ? <span className="badge danger">{event.revoked_count}</span> : '0'}</td>
          <td style={{whiteSpace:'normal'}}><span className="user-email" style={{margin:0}}>{(event.review_note && MOVER_NOTES[event.review_note]) || ((event.changes as Record<string, unknown>).policy ? `Policy: ${(event.changes as Record<string, unknown>).policy}` : '—')}</span></td>
          <td style={{whiteSpace:'normal'}}>{event.notified.length > 0 ? event.notified.join(', ') : '—'}</td>
          <td>{formatDateTime(event.created_at, timezone)}</td>
        </tr>)}
      </tbody></table>}
    </TablePanel>
  </Page>;
}
interface ApiJoinerTarget { provider_id: string; name: string; provider_type: string; status: string; provision_joiners: boolean; provisioning_domain: string | null; username_convention: string | null; }
interface ApiJoinerAccount { provider_id: string; provider_name: string; username: string; status: string; error: string | null; temporary_password: string | null; }
interface ApiJoiner { id: string; user_id: string | null; display_name: string; work_email: string; employee_id: string | null; department: string | null; job_title: string | null; start_at: string; leaver_date: string | null; status: string; targets: ApiJoinerAccount[]; created_at: string; activated_at: string | null; }
// Mirrors the backend's provisioning policy (services/joiner._username_for) so the admin sees the account name that will be created.
function joinerUsernamePreview(t: { provisioning_domain: string | null; username_convention: string | null }, first: string, last: string, workEmail: string): string {
  if (!t.provisioning_domain && !t.username_convention) return workEmail;
  const slug = (v: string) => v.toLowerCase().replace(/[^a-z0-9]/g, '');
  const [f, l] = [slug(first), slug(last)];
  const fallback = (workEmail.split('@')[0] || workEmail).toLowerCase();
  let local = fallback;
  if (t.username_convention && f && l) local = t.username_convention.replace(/\{first\}/g, f).replace(/\{last\}/g, l).replace(/\{f\}/g, f.slice(0, 1)).replace(/\{l\}/g, l.slice(0, 1)).trim().toLowerCase() || fallback;
  const domain = t.provisioning_domain || (workEmail.includes('@') ? workEmail.split('@')[1] : '');
  return domain ? `${local}@${domain}` : local;
}
const emptyJoinerForm = { first_name: '', last_name: '', work_email: '', employee_id: '', department: '', job_title: '', manager_id: '', employee_category: 'EMPLOYEE', employment_type: 'EMPLOYEE', start_now: false, start_at: '', leaver_date: '' };
// The joiner process: fill in the person once, choose the IdPs, and AccessPilot creates their account in each one
// DISABLED, shows each one-time temporary password, and enables everything (and grants their birthright access) on the
// start date. A leaver date entered here is picked up by the leaver process later.
function JoinersPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: joiners, loading, error, reload } = useApiResource<ApiJoiner[]>('/api/v1/lifecycle/joiners');
  const { data: targets, reload: reloadTargets } = useApiResource<ApiJoinerTarget[]>('/api/v1/lifecycle/joiner-targets');
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: departments } = useApiResource<{ id: string; name: string }[]>('/api/v1/policies/departments');
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyJoinerForm);
  const [picked, setPicked] = useState<Record<string, { checked: boolean; username: string }>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [result, setResult] = useState<ApiJoiner | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => {
    const timer = setInterval(() => reload(), 30000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const activeTargets = (targets || []).filter(t => t.provision_joiners);
  // Work email is generated from the primary directory's provisioning policy (Entra first) as the name is typed,
  // until the admin types their own address.
  const [emailEdited, setEmailEdited] = useState(false);
  useEffect(() => {
    if (emailEdited || !form.first_name.trim() || !form.last_name.trim()) return;
    const primary = activeTargets.find(t => t.provider_type === 'ENTRA' && t.provisioning_domain) || activeTargets.find(t => t.provisioning_domain);
    if (!primary) return;
    const generated = joinerUsernamePreview(primary, form.first_name, form.last_name, '');
    if (generated && generated !== form.work_email) setForm(current => ({ ...current, work_email: generated }));
  }, [form.first_name, form.last_name, targets, emailEdited]); // eslint-disable-line react-hooks/exhaustive-deps
  const openForm = () => {
    setEmailEdited(false); setForm({ ...emptyJoinerForm, start_at: toLocalDateTimeInput(new Date(Date.now() + 86400000).toISOString()) });
    setPicked(Object.fromEntries(activeTargets.map(t => [t.provider_id, { checked: true, username: '' }])));
    setMessage(''); setOpen(true);
  };
  const toggleTarget = async (target: ApiJoinerTarget) => {
    await auth.apiRequest(`/api/v1/lifecycle/joiner-targets/${target.provider_id}`, { method: 'PUT', body: JSON.stringify({ enabled: !target.provision_joiners }) });
    reloadTargets();
  };
  const submit = async () => {
    const chosen = Object.entries(picked).filter(([, v]) => v.checked);
    if (!form.first_name.trim() || !form.last_name.trim() || !form.work_email.trim() || !form.department.trim()) { setMessage('First name, last name, work email and department are required.'); return; }
    if (chosen.length === 0) { setMessage('Choose at least one IdP to create the account in.'); return; }
    if (!form.start_now && !form.start_at) { setMessage('Pick a start date and time, or tick "Start immediately".'); return; }
    setSaving(true); setMessage('');
    try {
      const payload: Record<string, unknown> = {
        first_name: form.first_name.trim(), last_name: form.last_name.trim(), work_email: form.work_email.trim(), department: form.department.trim(),
        job_title: form.job_title.trim() || undefined, employee_id: form.employee_id.trim() || undefined, manager_id: form.manager_id || undefined,
        employee_category: form.employee_category || undefined, employment_type: form.employment_type || undefined,
        start_at: form.start_now ? new Date().toISOString() : new Date(form.start_at).toISOString(), leaver_date: form.leaver_date || undefined,
        targets: chosen.map(([provider_id, v]) => ({ provider_id, username: v.username.trim() || undefined })),
      };
      const response = await auth.apiRequest('/api/v1/lifecycle/joiners', { method: 'POST', body: JSON.stringify(payload) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setResult(body); setOpen(false); reload(); }
      else setMessage(body?.error?.message || 'Unable to create this joiner.');
    } catch { setMessage('Unable to reach the backend.'); } finally { setSaving(false); }
  };
  const retry = async (joiner: ApiJoiner) => {
    const response = await auth.apiRequest(`/api/v1/lifecycle/joiners/${joiner.id}/retry`, { method: 'POST' });
    const body = await response.json().catch(() => null);
    if (response.ok) setResult(body); else setMessage(body?.error?.message || 'Unable to retry.');
    reload();
  };
  const cancel = async (joiner: ApiJoiner) => {
    if (!window.confirm(`Cancel ${joiner.display_name}'s start? The accounts already created stay in the directories, disabled.`)) return;
    const alsoDelete = window.confirm('Also DELETE the accounts that were created in the directories? This cannot be undone (Entra keeps a deleted user recoverable for 30 days). OK = delete, Cancel = keep them disabled.');
    const response = await auth.apiRequest(`/api/v1/lifecycle/joiners/${joiner.id}${alsoDelete ? '?delete_accounts=true' : ''}`, { method: 'DELETE' });
    if (!response.ok) setMessage((await response.json().catch(() => null))?.error?.message || 'Unable to cancel.');
    reload();
  };
  const copy = async (key: string, text: string) => { try { await navigator.clipboard.writeText(text); setCopied(key); setTimeout(() => setCopied(null), 1500); } catch { /* clipboard unavailable */ } };
  const statusBadge = (status: string) => <StatusBadge status={status === 'ACTIVE' ? 'ACTIVE' : status === 'CANCELLED' ? 'REJECTED' : status === 'PARTIAL' ? 'PARTIAL' : 'SCHEDULED'}/>;
  return <Page eyebrow="JOINER · MOVER · LEAVER" title="Joiners" subtitle="Onboard a new person once — accounts are created in every chosen IdP and switched on for their start date." action={<button className="btn btn-primary" onClick={openForm}><Plus size={14}/> New joiner</button>}>
    {result && <section className="panel" style={{marginBottom:18,borderColor:'#e0a24d'}}>
      <div className="panel-head"><h2>Accounts for {result.display_name}</h2><button className="btn" aria-label="Dismiss" onClick={() => setResult(null)}><X size={14}/></button></div>
      <div className="detail-section">
        <div className="notice" style={{marginBottom:12}}>Copy each temporary password now and pass it on securely — <strong>it is not stored and cannot be shown again</strong>. The person must change it at first sign-in.</div>
        {result.targets.map(t => <div key={t.provider_id} style={{display:'flex',alignItems:'center',gap:12,flexWrap:'wrap',padding:'8px 0',borderBottom:'1px solid #edf1f2'}}>
          <div style={{minWidth:180}}><strong>{t.provider_name}</strong><div className="user-email">{t.username}</div></div>
          <StatusBadge status={t.status === 'FAILED' ? 'FAILED' : t.status === 'ENABLED' ? 'ACTIVE' : 'SCHEDULED'}/>
          {t.temporary_password ? <><code style={{background:'#f4f7f8',padding:'4px 8px',borderRadius:4}}>{t.temporary_password}</code><button className="btn" onClick={() => void copy(t.provider_id, t.temporary_password || '')}>{copied === t.provider_id ? 'Copied' : 'Copy'}</button></> : t.error ? <span className="user-email" style={{margin:0,color:'#ae4949'}}>{t.error}</span> : null}
        </div>)}
        <p className="subtitle" style={{marginBottom:0,marginTop:10}}>{result.status === 'ACTIVE' ? 'The accounts are enabled — the person can sign in now.' : `The accounts are disabled until ${formatDateTime(result.start_at, timezone)}, when they are enabled automatically.`}</p>
      </div>
    </section>}
    <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>Where joiners get accounts</h2></div>
      <div className="detail-section">
        <p className="subtitle" style={{marginTop:0,marginBottom:10}}>Each connected directory can be a default target for new joiners. The username comes from the directory's naming convention and provisioning domain (Providers page) unless you type one in the form.</p>
        {!targets || targets.length === 0 ? <p className="subtitle" style={{margin:0}}>No directory is connected yet.</p> : targets.map(t => <label key={t.provider_id} style={{display:'flex',alignItems:'center',gap:8,fontSize:13,padding:'4px 0'}}><input type="checkbox" checked={t.provision_joiners} onChange={() => void toggleTarget(t)}/> <strong>{t.name}</strong> <span className="user-email" style={{margin:0}}>{t.provider_type}{t.provisioning_domain ? ` · @${t.provisioning_domain}` : ''}{t.username_convention ? ` · ${t.username_convention}` : ''}</span></label>)}
      </div>
    </section>
    {open && <section className="panel" style={{marginBottom:18}}>
      <div className="panel-head"><h2>New joiner</h2></div>
      <div className="detail-section">
        <div className="key-grid" style={{marginBottom:12}}>
          <label className="key"><span>First name</span><input className="select" value={form.first_name} onChange={event => setForm({...form, first_name: event.target.value})}/></label>
          <label className="key"><span>Last name</span><input className="select" value={form.last_name} onChange={event => setForm({...form, last_name: event.target.value})}/></label>
          <label className="key"><span>Work email</span><input className="select" type="email" value={form.work_email} onChange={event => { setEmailEdited(true); setForm({...form, work_email: event.target.value}); }} placeholder="generated from the directory's naming policy"/></label>
          <label className="key"><span>Employee ID (optional)</span><input className="select" value={form.employee_id} onChange={event => setForm({...form, employee_id: event.target.value})}/></label>
          <label className="key"><span>Department</span><select className="select" value={form.department} onChange={event => setForm({...form, department: event.target.value})}><option value="">Select a department</option>{(departments || []).map(d => <option key={d.id} value={d.name}>{d.name}</option>)}</select></label>
          <label className="key"><span>Job title</span><input className="select" value={form.job_title} onChange={event => setForm({...form, job_title: event.target.value})}/></label>
          <label className="key"><span>Manager</span><select className="select" value={form.manager_id} onChange={event => setForm({...form, manager_id: event.target.value})}><option value="">No manager</option>{(users || []).filter(u => u.account_type === 'NORMAL' && u.status === 'ACTIVE').map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
          <label className="key"><span>Role in the org chart</span><select className="select" value={form.employee_category} onChange={event => setForm({...form, employee_category: event.target.value})}><option value="EMPLOYEE">Employee</option><option value="MANAGER">Manager</option></select></label>
          <label className="key"><span>Employment type</span><select className="select" value={form.employment_type} onChange={event => setForm({...form, employment_type: event.target.value})}>{EMPLOYMENT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}</select></label>
          <label className="key"><span>Leaver date (optional — known end of contract)</span><input className="select" type="date" value={form.leaver_date} onChange={event => setForm({...form, leaver_date: event.target.value})}/></label>
        </div>
        <label style={{display:'flex',alignItems:'center',gap:8,fontWeight:600,fontSize:13,marginBottom:8}}><input type="checkbox" checked={form.start_now} onChange={event => setForm({...form, start_now: event.target.checked})}/> Start immediately (accounts are enabled right away)</label>
        {!form.start_now && <label className="key" style={{display:'block',marginBottom:12,maxWidth:340}}><span>Starts (your device's local time) — accounts stay disabled until then</span><input className="select" style={{width:'100%'}} type="datetime-local" value={form.start_at} onChange={event => setForm({...form, start_at: event.target.value})}/></label>}
        <div className="key" style={{marginBottom:6}}><span>Create accounts in</span></div>
        {activeTargets.length === 0 ? <div className="notice" style={{marginBottom:12}}>No directory is switched on for joiners — turn one on above.</div> : activeTargets.map(t => <div key={t.provider_id} style={{display:'flex',alignItems:'center',gap:10,flexWrap:'wrap',padding:'4px 0'}}>
          <label style={{display:'flex',alignItems:'center',gap:8,fontSize:13,minWidth:200}}><input type="checkbox" checked={picked[t.provider_id]?.checked ?? false} onChange={event => setPicked({...picked, [t.provider_id]: { checked: event.target.checked, username: picked[t.provider_id]?.username || '' }})}/> <strong>{t.name}</strong></label>
          <input className="select" style={{minWidth:280}} placeholder={(t.provisioning_domain || t.username_convention) ? `per provider policy: ${joinerUsernamePreview(t, form.first_name, form.last_name, form.work_email) || '...'}` : 'username (defaults to the work email)'} value={picked[t.provider_id]?.username || ''} onChange={event => setPicked({...picked, [t.provider_id]: { checked: picked[t.provider_id]?.checked ?? true, username: event.target.value }})}/>
        </div>)}
        {message && <div className="notice" style={{margin:'12px 0'}}>{message}</div>}
        <div style={{display:'flex',gap:8,marginTop:12}}><button className="btn btn-primary" disabled={saving} onClick={() => void submit()}>{saving ? 'Creating accounts...' : form.start_now ? 'Create and start now' : 'Create and schedule'}</button><button className="btn" onClick={() => setOpen(false)}>Cancel</button></div>
      </div>
    </section>}
    {!open && message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
    <TablePanel toolbar={undefined}>
      {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !joiners || joiners.length === 0 ? <div className="empty">No joiners yet. Use "New joiner" to onboard someone.</div> : <table><thead><tr><th>Person</th><th>Department</th><th>Starts</th><th>Leaver date</th><th>Accounts</th><th>Status</th><th></th></tr></thead><tbody>
        {joiners.map(joiner => <tr key={joiner.id}>
          <td>{joiner.user_id ? <Link to={`/admin/users/${joiner.user_id}`} className="user-cell"><span className="avatar">{initialsFor(joiner.display_name)}</span><span><span className="user-name">{joiner.display_name}</span><span className="user-email">{joiner.work_email}</span></span></Link> : joiner.display_name}</td>
          <td style={{whiteSpace:'normal'}}>{joiner.department || '—'}<div className="user-email">{joiner.job_title || ''}</div></td>
          <td>{formatDateTime(joiner.start_at, timezone)}</td>
          <td>{joiner.leaver_date || '—'}</td>
          <td style={{whiteSpace:'normal'}}>{joiner.targets.map(t => <div key={t.provider_id} title={t.error || undefined}><span className={`badge ${t.status === 'ENABLED' ? 'success' : t.status === 'FAILED' ? 'danger' : 'neutral'}`}>{t.provider_name}: {t.status === 'ENABLED' ? 'enabled' : t.status === 'FAILED' ? 'failed' : 'created, disabled'}</span></div>)}</td>
          <td>{statusBadge(joiner.status)}</td>
          <td><span style={{display:'flex',gap:6}}>{joiner.targets.some(t => t.status === 'FAILED') && joiner.status !== 'CANCELLED' && <button className="btn" onClick={() => void retry(joiner)}>Retry failed</button>}{joiner.status === 'SCHEDULED' && <button className="btn" onClick={() => void cancel(joiner)}>Cancel</button>}</span></td>
        </tr>)}
      </tbody></table>}
    </TablePanel>
  </Page>;
}
function ProvidersPage() { return <ProviderConfiguration />; }
interface ApiProvider { id: string; name: string; provider_type: string; status: string; sync_interval_minutes: number | null; last_sync_at: string | null; max_self_activation_hours: number; }
interface ApiSyncRun { id: string; status: string; started_at: string; completed_at: string | null; users_processed: number; groups_processed: number; roles_processed: number; errors_count: number; }
function SyncPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
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
    {provider && <section className="panel" style={{marginBottom:18}}><div className="panel-head"><h2>Scheduled sync</h2><span className="badge neutral">{provider.sync_interval_minutes ? `Every ${provider.sync_interval_minutes} min` : 'Not scheduled'}</span></div><div className="detail-section"><div className="key-grid" style={{marginBottom:14}}><div className="key"><span>Current schedule</span><strong>{provider.sync_interval_minutes ? `Every ${provider.sync_interval_minutes} minutes` : 'Manual only'}</strong></div><div className="key"><span>Last sync</span><strong>{provider.last_sync_at ? formatDateTime(provider.last_sync_at, timezone) : 'Never'}</strong></div></div><div style={{display:'flex',gap:8,alignItems:'flex-end',flexWrap:'wrap'}}><label className="key"><span>Run every (minutes)</span><input className="select" type="number" min={1} max={10080} placeholder="e.g. 60" value={intervalValue} onChange={event => setIntervalValue(event.target.value)}/></label><button className="btn btn-primary" disabled={scheduleSaving || !intervalValue} onClick={() => void saveSchedule(Number(intervalValue))}><Clock3 size={14}/> {scheduleSaving ? 'Saving...' : 'Schedule sync'}</button>{provider.sync_interval_minutes && <button className="btn" disabled={scheduleSaving} onClick={() => void saveSchedule(null)}>Disable schedule</button>}</div>{scheduleMessage && <div className="notice" style={{marginTop:12}}>{scheduleMessage}</div>}</div></section>}
    <div className="notice" style={{marginBottom:18}}>Looking for the self-activation time limit (PIM)? That's now under <strong>Policies</strong> in the Governance section.</div>
    <TablePanel toolbar={<Toolbar placeholder="" filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={statusOptions}/>}>{providersLoading || loading ? <div className="empty">Loading sync history...</div> : !provider ? <div className="empty">No identity provider is configured.</div> : error ? <div className="empty">{error}</div> : !runs || runs.length === 0 ? <div className="empty">No sync runs yet.</div> : filteredRuns.length === 0 ? <div className="empty">No sync runs match this filter.</div> : <table><thead><tr><th>Started</th><th>Completed</th><th>Users</th><th>Groups</th><th>Roles</th><th>Errors</th><th>Status</th></tr></thead><tbody>{filteredRuns.map(run => <tr key={run.id}><td className="user-name">{formatDateTime(run.started_at, timezone)}</td><td>{run.completed_at ? formatDateTime(run.completed_at, timezone) : '—'}</td><td>{run.users_processed}</td><td>{run.groups_processed}</td><td>{run.roles_processed}</td><td>{run.errors_count}</td><td><StatusBadge status={run.status}/></td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
function actionBadgeClass(action: string): string { return action === 'CREATE' || action === 'UPDATE' ? 'success' : action === 'DISABLE' ? 'warning' : action === 'ERROR' ? 'danger' : 'neutral'; }
function OnboardingPage() {
  const auth = useAuth();
  const timezone = useAppTimezone();
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
          {currentImport.status === 'COMMITTED' && <><div className="key"><span>Real accounts provisioned</span><strong>{currentImport.real_accounts_provisioned_count}</strong></div><div className="key"><span>Birthright grants</span><strong>{currentImport.birthright_assignments_created_count}</strong></div><div className="key"><span>Birthright grants removed (movers)</span><strong>{currentImport.birthright_assignments_revoked_count}</strong></div><div className="key"><span>Moves scheduled (future effectiveDate)</span><strong>{currentImport.moves_scheduled_count}</strong></div><div className="key"><span>Access revoked</span><strong>{currentImport.access_revoked_count}</strong></div><div className="key"><span>Revoke failures</span><strong>{currentImport.access_revoke_failed_count}</strong></div></>}
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
      <div className="table-wrap">{importsLoading ? <div className="empty">Loading...</div> : importsError ? <div className="empty">{importsError}</div> : !imports || imports.length === 0 ? <div className="empty">No imports yet.</div> : <table><thead><tr><th>Filename</th><th>Status</th><th>Rows</th><th>Created</th><th>Updated</th><th>Disabled</th><th>Errors</th><th>Uploaded</th></tr></thead><tbody>{imports.map(imp => <tr key={imp.id}><td className="user-name">{imp.filename}</td><td><StatusBadge status={imp.status}/></td><td>{imp.total_records}</td><td>{imp.created_count}</td><td>{imp.updated_count}</td><td>{imp.disabled_count}</td><td>{imp.failed_count}</td><td>{formatDateTime(imp.created_at, timezone)}</td></tr>)}</tbody></table>}</div>
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
  const timezone = useAppTimezone();
  const signedIn = Boolean(auth.account) || auth.breakglassActive;
  const { data: me, reload: reloadMe } = useApiResource<ApiCurrentUser>('/api/v1/me', signedIn);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState('');
  const handleRefresh = async () => {
    setRefreshing(true); setRefreshMessage('');
    try {
      const ok = await auth.refreshAccess();
      if (ok) { reloadMe(); setRefreshMessage('Refreshed — your roles and permissions are now up to date.'); }
      else setRefreshMessage('Could not get a fresh session just now — your role may not have changed on the identity provider yet, or this needs a moment. Try again shortly, or sign out and back in if it persists.');
    }
    catch { setRefreshMessage('Unable to refresh right now.'); }
    finally { setRefreshing(false); }
  };
  const displayName = auth.account?.name || me?.displayName || (auth.breakglassActive ? `Break-Glass (${auth.breakglassUsername})` : currentUser.name);
  const email = auth.account?.username || me?.email || (auth.authConfigured ? '' : currentUser.email);
  const department = me?.department || (!auth.authConfigured ? currentUser.department : null);
  const jobTitle = me?.jobTitle || (!auth.authConfigured ? currentUser.title : null);
  const roleLabel = auth.role === 'admin' ? 'AccessPilot.Admin' : 'AccessPilot.User';
  const providerLabel = auth.breakglassActive ? 'Break-Glass (emergency access)' : auth.authConfigured ? 'Microsoft Entra ID' : 'Local (mock/dev mode)';
  const sessionStarted = signedIn && auth.sessionStartedAt ? formatDateTime(auth.sessionStartedAt, timezone) : '—';
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
    {signedIn && <PrivilegedAccountRequestPanel/>}
  </Page>;
}
function PrivilegedAccountRequestPanel() {
  const auth = useAuth();
  const timezone = useAppTimezone();
  const { data: myRequests, loading, error, reload } = useApiResource<ApiPrivilegedAccountRequest[]>('/api/v1/privileged-accounts/requests/mine');
  const [accountType, setAccountType] = useState<'PU' | 'TU'>('PU');
  const [justification, setJustification] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');

  const submit = async () => {
    if (justification.trim().length < 3) { setMessage('A justification (at least 3 characters) is required.'); return; }
    setSaving(true); setMessage('');
    try {
      const response = await auth.apiRequest('/api/v1/privileged-accounts/requests', { method: 'POST', body: JSON.stringify({ account_type: accountType, justification: justification.trim() }) });
      const body = await response.json().catch(() => null);
      if (response.ok) { setJustification(''); setMessage(body.status === 'PROVISIONED' ? `Your ${accountType} account was created immediately.` : 'Submitted — awaiting approval.'); reload(); }
      else setMessage(body?.error?.message || 'Unable to submit this request.');
    } catch { setMessage('Unable to submit this request.'); } finally { setSaving(false); }
  };

  return <section className="panel" style={{marginTop:18}}>
    <div className="panel-head"><h2>Privileged / Test accounts</h2></div>
    <div className="detail-section">
      <p className="subtitle" style={{marginBottom:14}}>Request a separate, mailbox-free account for elevated admin work (Privileged) or QA/UAT (Test) — kept apart from your day-to-day identity for security. Access to anything is always granted to it manually by an Admin, one grant at a time.</p>
      <div style={{display:'flex',gap:10,alignItems:'flex-end',flexWrap:'wrap',marginBottom:10}}>
        <label className="key"><span>Account type</span><select className="select" value={accountType} onChange={event => setAccountType(event.target.value as 'PU' | 'TU')}><option value="PU">Privileged (PU)</option><option value="TU">Test (TU)</option></select></label>
        <label className="key" style={{flex:1,minWidth:220}}><span>Justification</span><input className="select" style={{width:'100%'}} value={justification} onChange={event => setJustification(event.target.value)} placeholder="Why do you need this account?"/></label>
        <button className="btn btn-primary" disabled={saving} onClick={() => void submit()}>{saving ? 'Submitting...' : 'Request'}</button>
      </div>
      {message && <div className="notice" style={{marginBottom:14}}>{message}</div>}
      <div className="table-wrap">{loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : !myRequests || myRequests.length === 0 ? <div className="empty">You haven't requested any privileged/test accounts yet.</div> : <table><thead><tr><th>Type</th><th>Status</th><th>Requested</th><th>Note</th></tr></thead><tbody>{myRequests.map(r => <tr key={r.id}><td className="user-name">{r.account_type}</td><td><StatusBadge status={r.status}/></td><td>{formatDateTime(r.created_at, timezone)}</td><td>{r.failure_reason || '—'}</td></tr>)}</tbody></table>}</div>
    </div>
  </section>;
}
export default App;
