import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { Activity, AlertTriangle, ArrowRight, BarChart3, Bell, BookOpen, Box, Check, ChevronRight, Clock3, Cloud, Database, FileCheck2, FolderKanban, Gauge, KeyRound, LayoutDashboard, LifeBuoy, ListChecks, Menu, Network, Plus, RefreshCw, Search, Settings2, Shield, ShieldCheck, SlidersHorizontal, UserRound, Users, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { auditEvents, currentUser, policies, requests, type AccessRequest, type RequestStatus, type Role } from './mock';
import { mockService, useMockState } from './mockService';
import { entraConfigured, useAuth } from './auth';
import ProviderConfiguration from './ProviderConfiguration';

interface ApiUser { id: string; external_id: string; email: string; display_name: string; given_name: string | null; surname: string | null; department: string | null; job_title: string | null; status: string; last_synced_at: string | null; }
interface ApiGroup { id: string; external_id: string; name: string; description: string | null; is_privileged: boolean; status: string; last_synced_at: string | null; }
interface ApiRole { id: string; external_id: string; name: string; description: string | null; role_type: string; is_privileged: boolean; status: string; }
interface DashboardAdmin { users: number; groups: number; roles: number; privilegedRoles: number; provider: { id: string; name: string; status: string; lastSyncAt: string | null } | null; lastSync: { id: string; status: string; startedAt: string; completedAt: string | null; usersProcessed: number; groupsProcessed: number; rolesProcessed: number; errorsCount: number } | null; }

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

const nav = [
  { label: 'Dashboard', icon: LayoutDashboard, to: '/dashboard', roles: ['user','admin'] },
  { label: 'My Access', icon: KeyRound, to: '/my-access', roles: ['user'] },
  { label: 'Request Access', icon: Plus, to: '/request-access', roles: ['user'] },
  { label: 'My Requests', icon: ListChecks, to: '/my-requests', roles: ['user'] },
  { label: 'Approvals', icon: Check, to: '/approvals', roles: ['user','admin'] },
  { label: 'Profile', icon: UserRound, to: '/profile', roles: ['user'] },
  { label: 'Users', icon: Users, to: '/admin/users', roles: ['admin'], section: 'ADMINISTRATION' },
  { label: 'Groups', icon: Network, to: '/admin/groups', roles: ['admin'] },
  { label: 'Roles', icon: Shield, to: '/admin/roles', roles: ['admin'] },
  { label: 'Access Requests', icon: FolderKanban, to: '/admin/access-requests', roles: ['admin'], section: 'ACCESS MANAGEMENT' },
  { label: 'Assignments', icon: KeyRound, to: '/admin/assignments', roles: ['admin'] },
  { label: 'Policies', icon: SlidersHorizontal, to: '/admin/policies', roles: ['admin'], section: 'GOVERNANCE' },
  { label: 'Audit Logs', icon: BookOpen, to: '/admin/audit', roles: ['admin'] },
  { label: 'Providers', icon: Cloud, to: '/admin/providers', roles: ['admin'], section: 'SYSTEM' },
  { label: 'Sync', icon: RefreshCw, to: '/admin/sync', roles: ['admin'] },
];

function App() {
  const auth = useAuth();
  const [mockRole, setMockRole] = useState<Role>(() => (localStorage.getItem('accesspilot.mockRole') as Role) || 'admin');
  if (entraConfigured && auth.loading) return <div className="empty">Loading AccessPilot authentication...</div>;
  if (entraConfigured && !auth.account) return <div className="empty"><h1>Sign in to AccessPilot</h1><p className="subtitle">Use your Microsoft Entra account to continue.</p><button className="btn btn-primary" onClick={auth.signIn} style={{marginTop:18}}>Sign in</button></div>;
  const role = entraConfigured ? auth.role : mockRole;
  const changeRole = (nextRole: Role) => { localStorage.setItem('accesspilot.mockRole', nextRole); setMockRole(nextRole); };
  return <Shell role={role} setRole={changeRole}><Routes><Route path="/" element={<Navigate to="/dashboard" replace />} /><Route path="/dashboard" element={<Dashboard role={role} />} /><Route path="/my-access" element={<MyAccess />} /><Route path="/request-access" element={<RequestAccess />} /><Route path="/my-requests" element={<Requests mine />} /><Route path="/approvals" element={<MyApprovalsPage />} /><Route path="/profile" element={<Profile />} /><Route path="/admin/users" element={<AdminOnly role={role}><UsersPage /></AdminOnly>} /><Route path="/admin/users/:id" element={<AdminOnly role={role}><UserDetail /></AdminOnly>} /><Route path="/admin/groups" element={<AdminOnly role={role}><GroupsPage /></AdminOnly>} /><Route path="/admin/roles" element={<AdminOnly role={role}><RolesPage /></AdminOnly>} /><Route path="/admin/access-requests" element={<AdminOnly role={role}><Requests /></AdminOnly>} /><Route path="/admin/access-requests/:id" element={<AdminOnly role={role}><RequestDetailInteractive /></AdminOnly>} /><Route path="/admin/assignments" element={<AdminOnly role={role}><AssignmentsInteractive /></AdminOnly>} /><Route path="/admin/policies" element={<AdminOnly role={role}><PoliciesPage /></AdminOnly>} /><Route path="/admin/audit" element={<AdminOnly role={role}><AuditPage /></AdminOnly>} /><Route path="/admin/providers" element={<AdminOnly role={role}><ProvidersPage /></AdminOnly>} /><Route path="/admin/sync" element={<AdminOnly role={role}><SyncPage /></AdminOnly>} /><Route path="*" element={<Navigate to="/dashboard" replace />} /></Routes></Shell>;
}
function AdminOnly({ role, children }: { role: Role; children: React.ReactNode }) { return role === 'admin' ? children : <Navigate to="/dashboard" replace />; }
function Shell({ role, setRole, children }: { role: Role; setRole: (r: Role) => void; children: React.ReactNode }) {
  const location = useLocation(); const navigate = useNavigate();
  const auth = useAuth();
  const visible = nav.filter(item => item.roles.includes(role));
  const path = location.pathname;
  return <div className="app"><aside className="sidebar"><Link to="/dashboard" className="brand"><span className="brand-mark">A</span> AccessPilot</Link>{visible.map((item, index) => { const I = item.icon; const previous = visible[index - 1]; return <div key={item.to}>{item.section && item.section !== previous?.section && <div className="nav-label">{item.section}</div>}<Link className={`nav-item ${path === item.to || (item.to !== '/dashboard' && path.startsWith(item.to)) ? 'active' : ''}`} to={item.to}><I />{item.label}</Link></div> })}<div className="sidebar-foot"><div>ACCESSPILOT CONSOLE</div><div style={{marginTop:5}}>v0.1.0 · Mock environment</div></div></aside><main className="main"><header className="topbar"><button className="mobile-menu" aria-label="Open navigation"><Menu size={20}/></button><div className="crumb">Workspace / <strong>{role === 'admin' ? 'Administration' : 'Self-service'}</strong></div><div className="top-actions">{entraConfigured ? <button className="btn" onClick={() => auth.account ? auth.signOut() : auth.signIn()}>{auth.account ? 'Sign out' : 'Sign in'}</button> : <div className="role-switch" aria-label="Development role switcher"><button className={role === 'user' ? 'active' : ''} onClick={() => { setRole('user'); navigate('/dashboard'); }}>User</button><button className={role === 'admin' ? 'active' : ''} onClick={() => { setRole('admin'); navigate('/dashboard'); }}>Admin</button></div>}<Bell size={17} color="#718088"/><div className="profile"><span>{auth.account?.name || currentUser.name}</span><span className="avatar">{currentUser.initials}</span></div></div></header>{children}</main></div>;
}
function Page({ eyebrow, title, subtitle, action, children }: { eyebrow?: string; title: string; subtitle?: string; action?: React.ReactNode; children: React.ReactNode }) { return <div className="content"><div className="page-head"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{subtitle && <p className="subtitle">{subtitle}</p>}</div>{action}</div>{children}</div>; }
function StatCards({ admin = false, dashboard }: { admin?: boolean; dashboard?: DashboardAdmin | null }) { const na = '—'; const stats: Array<[string, string, string, LucideIcon]> = admin ? [['Total users', dashboard ? String(dashboard.users) : na, 'Synced from Microsoft Entra ID', Users],['Groups', dashboard ? String(dashboard.groups) : na, 'Synced from Microsoft Entra ID', Network],['Privileged roles', dashboard ? String(dashboard.privilegedRoles) : na, `${dashboard ? dashboard.roles : na} directory roles total`, ShieldCheck],['Active JIT sessions', na, 'Not available in this release', Clock3],['Pending requests', na, 'Not available in this release', FolderKanban],['Expiring access', na, 'Not available in this release', AlertTriangle],['Provider health', dashboard?.provider?.status || na, dashboard?.provider ? dashboard.provider.name : 'No provider configured', Cloud],['Policy coverage', na, 'Not available in this release', FileCheck2]] : [['Active access','—','Not available in this release',KeyRound],['Eligible access','—','Not available in this release',Shield],['Pending requests','—','Not available in this release',Clock3],['Expiring soon','—','Not available in this release',AlertTriangle]]; return <div className={`stats ${admin ? 'admin-stats' : ''}`}>{stats.map(([label,value,foot,I]) => <div className="stat" key={String(label)}><div className="stat-top"><span>{label}</span><span className="stat-icon"><I size={15}/></span></div><div className="stat-value">{value}</div><div className="stat-foot">{foot}</div></div>)}</div>; }
function Dashboard({ role }: { role: Role }) {
  const admin = role === 'admin';
  const auth = useAuth();
  const { data: dashboard, error, loading } = useApiResource<DashboardAdmin>('/api/v1/dashboard/admin', admin);
  const greetingName = auth.account?.name || (entraConfigured ? '' : currentUser.name);
  const lastSyncLabel = dashboard?.lastSync?.completedAt ? new Date(dashboard.lastSync.completedAt).toLocaleString() : dashboard?.lastSync ? 'In progress' : 'Never synced';
  return <Page eyebrow={admin ? 'ADMINISTRATION' : 'SELF-SERVICE'} title={admin ? (greetingName ? `Good morning, ${greetingName.split(' ')[0]}` : 'Good morning') : 'Your access overview'} subtitle={admin ? 'Here is what is happening across your identity environment.' : 'Review your current access and request what you need.'} action={<button className="btn btn-primary" onClick={() => {}}><ArrowRight size={15}/> {admin ? 'Review requests' : 'Request access'}</button>}><StatCards admin={admin} dashboard={dashboard}/>{admin && loading && <div className="empty">Loading dashboard...</div>}{admin && error && <div className="empty">{error}</div>}<div className="grid-2"><section className="panel"><div className="panel-head"><h2>{admin ? 'Recent access requests' : 'Recent activity'}</h2><Link to={admin ? '/admin/access-requests' : '/my-requests'} className="panel-link">View all <ChevronRight size={12}/></Link></div>{(admin ? requests : auditEvents.slice(0,5)).map((item, i) => <div className="activity" key={i}><div className="activity-row"><span className="activity-dot"/><div className="activity-copy"><strong>{admin ? (item as AccessRequest).resource : (item as string[])[2]}</strong><small>{admin ? `${(item as AccessRequest).requester} · ${(item as AccessRequest).duration}` : `${(item as string[])[1]} · ${(item as string[])[3]}`}</small></div>{admin ? <StatusBadge status={(item as AccessRequest).status}/> : <span className="time">{(item as string[])[0]}</span>}</div></div>)}</section><section className="panel"><div className="panel-head"><h2>{admin ? 'Provider status' : 'Current active access'}</h2>{admin && <StatusBadge status={dashboard?.provider?.status || 'NOT_CONFIGURED'}/>}</div>{admin ? <div className="detail-section"><div className="user-cell"><span className="avatar" style={{background:'#e4f1f5',color:'#33758a'}}><Cloud size={15}/></span><div><div className="user-name">{dashboard?.provider?.name || 'No provider configured'}</div><div className="user-email">{dashboard?.provider ? `${dashboard.provider.status} · Last sync ${lastSyncLabel}` : 'Configure a provider to begin syncing.'}</div></div></div><div className="key-grid" style={{marginTop:24}}><div className="key"><span>Users synced</span><strong>{dashboard ? dashboard.users : '—'}</strong></div><div className="key"><span>Groups synced</span><strong>{dashboard ? dashboard.groups : '—'}</strong></div><div className="key"><span>Directory roles</span><strong>{dashboard ? dashboard.roles : '—'}</strong></div><div className="key"><span>Last sync</span><strong>{lastSyncLabel}</strong></div></div></div> : <div className="detail-section"><div className="timeline"><div className="timeline-item"><strong>Not available in this release</strong><small>Access requests and assignments are not part of this phase.</small></div></div></div>}</section></div></Page>; }
function StatusBadge({ status }: { status: string }) { const cls = ['APPROVED','ACTIVE','COMPLETED','CONNECTED','ELIGIBLE','SUCCESS','Healthy','Active'].includes(status) ? 'success' : ['PENDING','PENDING_APPROVAL','SCHEDULED','RUNNING','PARTIAL','Medium'].includes(status) ? 'warning' : ['REJECTED','EXPIRED','REVOKED','FAILED','Disabled','High'].includes(status) ? 'danger' : 'neutral'; return <span className={`badge ${cls}`}>{status}</span>; }
function TablePanel({ children, toolbar }: { children: React.ReactNode; toolbar?: React.ReactNode }) { return <><div className="toolbar">{toolbar}</div><section className="panel"><div className="table-wrap">{children}</div></section></>; }
function Toolbar({ placeholder = 'Search', select = 'All statuses' }: { placeholder?: string; select?: string }) { return <><div className="toolbar-left"><div className="search-box"><Search size={15}/><input className="search" placeholder={placeholder}/></div><select className="select"><option>{select}</option><option>Active</option><option>Pending</option><option>Expired</option></select></div><div className="toolbar-right"><button className="btn"><SlidersHorizontal size={14}/> Filters</button></div></>; }
function initialsFor(name: string) { const parts = name.trim().split(/\s+/); return ((parts[0]?.[0] || '') + (parts[parts.length - 1]?.[0] || '')).toUpperCase() || '?'; }
function UsersPage() {
  const auth = useAuth();
  const { data: users, error, loading, reload } = useApiResource<ApiUser[]>('/api/v1/users');
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
    <TablePanel toolbar={<Toolbar placeholder="Search users by name or email" select="All departments"/>}>{loading ? <div className="empty">Loading users...</div> : error ? <div className="empty">{error}</div> : !users || users.length === 0 ? <div className="empty">No users found.</div> : <table><thead><tr><th>User</th><th>Department</th><th>Job title</th><th>Status</th><th>Last synced</th><th></th></tr></thead><tbody>{users.map(u => <tr key={u.id}><td><Link to={`/admin/users/${u.id}`} className="user-cell"><span className="avatar">{initialsFor(u.display_name)}</span><span><span className="user-name">{u.display_name}</span><span className="user-email">{u.email}</span></span></Link></td><td>{u.department || '—'}</td><td>{u.job_title || '—'}</td><td><StatusBadge status={u.status}/></td><td>{u.last_synced_at ? new Date(u.last_synced_at).toLocaleString() : 'Never'}</td><td><ChevronRight size={15} color="#829198"/></td></tr>)}</tbody></table>}</TablePanel>
    {users && users.length > 0 && <p className="footer-note">Showing {users.length} of {users.length} users</p>}
  </Page>;
}
function UserDetail() {
  const { id } = useParams();
  const { data: user, error, loading } = useApiResource<ApiUser>(`/api/v1/users/${id}`);
  if (loading) return <Page eyebrow="USER DIRECTORY" title="Loading..." subtitle=""><div className="empty">Loading user...</div></Page>;
  if (error || !user) return <Page eyebrow="USER DIRECTORY" title="User" subtitle=""><div className="empty">{error || 'User not found.'}</div></Page>;
  return <Page eyebrow="USER DIRECTORY" title={user.display_name} subtitle={user.email}><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="user-cell"><span className="avatar" style={{width:45,height:45}}>{initialsFor(user.display_name)}</span><div><h2>{user.job_title || 'No job title on file'}</h2><p className="subtitle">{user.department || 'No department on file'} · {user.status}</p></div></div></div><div className="detail-section"><div className="detail-title"><h2>Overview</h2><StatusBadge status={user.status}/></div><div className="key-grid"><div className="key"><span>Email</span><strong>{user.email}</strong></div><div className="key"><span>External ID</span><strong>{user.external_id}</strong></div><div className="key"><span>Given name</span><strong>{user.given_name || '—'}</strong></div><div className="key"><span>Surname</span><strong>{user.surname || '—'}</strong></div><div className="key"><span>Last synced</span><strong>{user.last_synced_at ? new Date(user.last_synced_at).toLocaleString() : 'Never'}</strong></div></div></div></section><aside className="panel"><div className="panel-head"><h2>Access summary</h2></div><div className="detail-section"><div className="notice">Group membership, roles, and access assignment details are not available in this release.</div></div></aside></div></Page>;
}
function Requests({ mine = false }: { mine?: boolean }) { const { requests: items } = useMockState(); const update = (id: string, status: RequestStatus) => { if (status === 'REJECTED' && !window.confirm('Reject this access request?')) return; mockService.transitionRequest(id, status); }; return <Page eyebrow={mine ? 'SELF-SERVICE' : 'ACCESS MANAGEMENT'} title={mine ? 'My requests' : 'Access requests'} subtitle={mine ? 'Track your access requests and approval history.' : 'Review and govern access requests across the environment.'} action={mine ? <Link to="/request-access" className="btn btn-primary"><Plus size={14}/> New request</Link> : undefined}><TablePanel toolbar={<Toolbar placeholder="Search requests"/>}><table><thead><tr><th>Requester</th><th>Resource</th><th>Type</th><th>Provider</th><th>Duration</th><th>Risk</th><th>Status</th><th>Created</th><th>Approval</th><th></th></tr></thead><tbody>{items.filter(r => !mine || r.requester === currentUser.name).map(r => <tr key={r.id}><td className="user-name">{r.requester}</td><td><Link to={`/admin/access-requests/${r.id}`} className="user-name">{r.resource}</Link></td><td>{r.type}</td><td>{r.provider}</td><td>{r.duration}</td><td><span className={`risk risk-${r.risk.toLowerCase()}`}>{r.risk}</span></td><td><StatusBadge status={r.status}/></td><td>{r.created}</td><td>{r.approval}</td><td>{r.status === 'PENDING' ? <span style={{display:'flex',gap:5}}>{mine ? <button className="btn" onClick={() => update(r.id,'CANCELLED')} aria-label="Cancel">Cancel</button> : <><button className="btn" onClick={() => update(r.id,'APPROVED')} aria-label="Approve"><Check size={14}/></button><button className="btn" onClick={() => update(r.id,'REJECTED')} aria-label="Reject"><X size={14}/></button></>}</span> : <ChevronRight size={15} color="#829198"/>}</td></tr>)}</tbody></table></TablePanel></Page>; }
function RequestDetail() { const { id } = useParams(); const req = requests.find(r => r.id === id) || requests[0]; return <Page eyebrow="ACCESS REQUEST" title={req.id} subtitle="Request details and approval history" action={<div style={{display:'flex',gap:8}}><button className="btn btn-primary"><Check size={14}/> Approve</button><button className="btn"><X size={14}/> Reject</button></div>}><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="detail-title"><h2>{req.resource}</h2><StatusBadge status={req.status}/></div><div className="key-grid"><div className="key"><span>Requester</span><strong>{req.requester}</strong></div><div className="key"><span>Provider</span><strong>{req.provider}</strong></div><div className="key"><span>Resource type</span><strong>{req.type}</strong></div><div className="key"><span>Requested duration</span><strong>{req.duration}</strong></div><div className="key"><span>Risk assessment</span><strong className={`risk risk-${req.risk.toLowerCase()}`}>{req.risk} risk</strong></div><div className="key"><span>Ticket number</span><strong>INC-48291</strong></div></div></div><div className="detail-section"><div className="detail-title"><h2>Justification</h2></div><p className="subtitle" style={{lineHeight:1.7,color:'#39525c'}}>{req.justification}</p></div><div className="detail-section"><div className="detail-title"><h2>Policy evaluation</h2><StatusBadge status="SUCCESS"/></div><div className="notice">MFA and a valid ticket are required before activation. The requested duration is within the policy maximum of 4 hours.</div></div></section><aside className="panel"><div className="panel-head"><h2>Request timeline</h2></div><div className="timeline"><div className="timeline-item"><strong>Request created</strong><small>{req.requester} · {req.created}</small></div><div className="timeline-item"><strong>Policy evaluated</strong><small>Passed · Today, 10:14</small></div><div className="timeline-item"><strong>Awaiting approval</strong><small>{req.approval}</small></div></div></aside></div></Page>; }
function RequestDetailInteractive() { const { id } = useParams(); const { requests: items } = useMockState(); const req = items.find(item => item.id === id) || items[0]; const update = (status: RequestStatus) => { if ((status === 'REJECTED' || status === 'CANCELLED') && !window.confirm(`${status === 'REJECTED' ? 'Reject' : 'Cancel'} this access request?`)) return; mockService.transitionRequest(req.id, status); }; return <Page eyebrow="ACCESS REQUEST" title={req.id} subtitle="Request details and approval history" action={<div style={{display:'flex',gap:8}}>{req.status === 'PENDING' && <><button className="btn btn-primary" onClick={() => update('APPROVED')}><Check size={14}/> Approve</button><button className="btn" onClick={() => update('REJECTED')}><X size={14}/> Reject</button></>}</div>}><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="detail-title"><h2>{req.resource}</h2><StatusBadge status={req.status}/></div><div className="key-grid"><div className="key"><span>Requester</span><strong>{req.requester}</strong></div><div className="key"><span>Provider</span><strong>{req.provider}</strong></div><div className="key"><span>Resource type</span><strong>{req.type}</strong></div><div className="key"><span>Requested duration</span><strong>{req.duration}</strong></div><div className="key"><span>Risk assessment</span><strong className={`risk risk-${req.risk.toLowerCase()}`}>{req.risk} risk</strong></div><div className="key"><span>Ticket number</span><strong>INC-48291</strong></div></div></div><div className="detail-section"><div className="detail-title"><h2>Justification</h2></div><p className="subtitle" style={{lineHeight:1.7,color:'#39525c'}}>{req.justification}</p></div><div className="detail-section"><div className="detail-title"><h2>Policy evaluation</h2><StatusBadge status="SUCCESS"/></div><div className="notice">MFA and a valid ticket are required before activation. The requested duration is within the policy maximum of 4 hours.</div></div></section><aside className="panel"><div className="panel-head"><h2>Request timeline</h2></div><div className="timeline"><div className="timeline-item"><strong>Request created</strong><small>{req.requester} · {req.created}</small></div><div className="timeline-item"><strong>Policy evaluated</strong><small>Passed · Today, 10:14</small></div><div className="timeline-item"><strong>{req.status === 'PENDING' ? 'Awaiting approval' : `Request ${req.status.toLowerCase()}`}</strong><small>{req.approval}</small></div></div></aside></div></Page>; }
function MyAccess() { const { assignments: items } = useMockState(); return <Page eyebrow="SELF-SERVICE" title="My access" subtitle="Your active and eligible access across connected providers."><div className="panel" style={{marginBottom:18}}><div className="panel-head"><h2>Eligible access</h2><span className="panel-link">12 available resources</span></div><div className="detail-section"><div className="activity-row"><span className="avatar"><Shield size={14}/></span><div className="activity-copy"><strong>Production Administrator</strong><small>Microsoft Entra ID · Up to 4 hours · Approval required</small></div><Link to="/request-access" className="btn btn-primary">Request <ArrowRight size={13}/></Link></div><div className="activity-row"><span className="avatar"><Users size={14}/></span><div className="activity-copy"><strong>Security Operations</strong><small>Group · Up to 8 hours · MFA required</small></div><Link to="/request-access" className="btn">Request <ArrowRight size={13}/></Link></div></div></div><TablePanel toolbar={undefined}><table><thead><tr><th>Resource</th><th>Type</th><th>Provider</th><th>Status</th><th>Activated</th><th>Expires</th><th>Remaining</th><th></th></tr></thead><tbody>{items.filter(a => a.user === currentUser.name || a.status === 'ACTIVE').slice(0,3).map(a => <tr key={a.id}><td className="user-name">{a.resource}</td><td>{a.type}</td><td>{a.provider}</td><td><StatusBadge status={a.status}/></td><td>{a.start}</td><td>{a.expiration}</td><td>{a.remaining}</td><td><button className="btn">Manage</button></td></tr>)}</tbody></table></TablePanel></Page>; }
function RequestAccess() { const [submitted, setSubmitted] = useState(false); return <Page eyebrow="SELF-SERVICE" title="Request access" subtitle="Request temporary access with a clear business justification."><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="detail-title"><h2>Access details</h2><span className="badge info">Step 1 of 2</span></div><label className="key"><span>Resource</span><select className="select" style={{width:'100%'}}><option>Production Administrator</option><option>Security Administrator</option><option>Product Analytics</option></select></label><div className="key-grid" style={{marginTop:20}}><label className="key"><span>Requested duration</span><select className="select" style={{width:'100%'}}><option>2 hours</option><option>1 hour</option><option>4 hours</option></select></label><label className="key"><span>Ticket number</span><input className="select" placeholder="INC-48291" style={{width:'100%'}}/></label></div><label className="key" style={{display:'block',marginTop:20}}><span>Business justification</span><textarea className="select" rows={5} placeholder="Explain why this access is needed and what you will do."></textarea></label></div><div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button className="btn">Cancel</button><button className="btn btn-primary" onClick={() => setSubmitted(true)}><SendIcon/> Submit request</button></div></section><aside className="panel"><div className="panel-head"><h2>Policy requirements</h2></div><div className="detail-section">{submitted && <div className="notice" style={{marginBottom:15}}>Request submitted successfully. It is now awaiting approval.</div>}<div className="notice"><strong>Privileged access</strong><br/>This resource requires MFA, an active ticket, and approval from a designated approver. Maximum duration is 4 hours.</div><div className="timeline" style={{padding:'22px 0 0'}}><div className="timeline-item"><strong>Policy evaluation</strong><small>Passed for your identity</small></div><div className="timeline-item"><strong>Approval required</strong><small>Security Operations</small></div><div className="timeline-item"><strong>Activation</strong><small>Available after approval</small></div></div></div></aside></div></Page>; }
function SendIcon() { return <ArrowRight size={14}/>; }
interface ApiAssignment { id: string; user_id: string; user_display_name: string | null; resource_type: string; resource_id: string; resource_display_name: string | null; assignment_type: string; status: string; start_time: string | null; expiration_time: string | null; justification: string | null; requested_by: string | null; approved_by: string | null; activated_at: string | null; created_at: string; }
function todayDateValue(date: Date) { const pad = (n: number) => String(n).padStart(2, '0'); return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`; }
const emptyAssignmentForm = { user_id: '', resource_type: 'GROUP', resource_id: '', assignment_type: 'PERMANENT', start_date: '', start_clock: '', end_date: '', end_clock: '', approver_id: '', justification: '' };
function AssignmentsInteractive() {
  const auth = useAuth();
  const { data: assignmentList, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments');
  const { data: users } = useApiResource<ApiUser[]>('/api/v1/users');
  const { data: groups } = useApiResource<ApiGroup[]>('/api/v1/groups');
  const { data: roles } = useApiResource<ApiRole[]>('/api/v1/roles');
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyAssignmentForm);
  const [saving, setSaving] = useState(false);
  const [formMessage, setFormMessage] = useState('');
  const [actioningId, setActioningId] = useState<string | null>(null);
  const targets = form.resource_type === 'GROUP' ? (groups || []) : (roles || []);
  const today = todayDateValue(new Date());

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.user_id || !form.resource_id) { setFormMessage('Select a user and a target.'); return; }
    if (form.assignment_type === 'TEMPORARY' && !form.end_date && !form.end_clock) { setFormMessage('Set an end date/time for a time-bound assignment.'); return; }
    setSaving(true); setFormMessage('');
    try {
      const payload: Record<string, unknown> = { user_id: form.user_id, resource_type: form.resource_type, resource_id: form.resource_id, assignment_type: form.assignment_type };
      if (form.start_date || form.start_clock) payload.start_time = new Date(`${form.start_date || today}T${form.start_clock || '00:00'}`).toISOString();
      if (form.assignment_type === 'TEMPORARY' && (form.end_date || form.end_clock)) payload.expiration_time = new Date(`${form.end_date || today}T${form.end_clock || '23:59'}`).toISOString();
      if (form.approver_id) payload.approver_id = form.approver_id;
      if (form.justification.trim()) payload.justification = form.justification.trim();
      const response = await auth.apiRequest('/api/v1/assignments', { method: 'POST', body: JSON.stringify(payload) });
      if (response.status === 201) { setOpen(false); setForm(emptyAssignmentForm); reload(); }
      else { const errorBody = await response.json().catch(() => null); setFormMessage(errorBody?.error?.message || 'Unable to create assignment.'); }
    } catch (err) {
      setFormMessage(err instanceof Error && err.message === 'AUTHENTICATION_REQUIRED' ? 'Please sign in to continue.' : 'Unable to create assignment.');
    } finally { setSaving(false); }
  };

  const decide = async (assignmentId: string, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm('Reject this assignment request?')) return;
    setActioningId(assignmentId);
    try {
      const response = await auth.apiRequest(`/api/v1/assignments/${assignmentId}/${decision}`, { method: 'POST' });
      if (response.ok) reload();
    } finally { setActioningId(null); }
  };

  return <Page eyebrow="ACCESS MANAGEMENT" title="Assignments" subtitle="Assign group or role access to users, with optional approval and time-bound expiration." action={<button className="btn btn-primary" onClick={() => { setOpen(true); setFormMessage(''); }}><Plus size={14}/> Add assignment</button>}>
    {open && <form role="dialog" aria-modal="true" className="panel" style={{maxWidth:720,marginBottom:18}} onSubmit={submit}>
      <div className="panel-head"><h2>Add assignment</h2><button type="button" className="btn" aria-label="Close" onClick={() => setOpen(false)}><X size={14}/></button></div>
      <div className="detail-section"><div className="key-grid">
        <label className="key"><span>User</span><select className="select" value={form.user_id} onChange={event => setForm({...form, user_id: event.target.value})}><option value="">Select a user</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name} ({u.email})</option>)}</select></label>
        <label className="key"><span>Target type</span><select className="select" value={form.resource_type} onChange={event => setForm({...form, resource_type: event.target.value, resource_id: ''})}><option value="GROUP">Group</option><option value="ROLE">Role</option></select></label>
        <label className="key"><span>{form.resource_type === 'GROUP' ? 'Group' : 'Role'}</span><select className="select" value={form.resource_id} onChange={event => setForm({...form, resource_id: event.target.value})}><option value="">Select {form.resource_type === 'GROUP' ? 'a group' : 'a role'}</option>{targets.map((t: ApiGroup | ApiRole) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
        <label className="key"><span>Duration</span><select className="select" value={form.assignment_type} onChange={event => setForm({...form, assignment_type: event.target.value})}><option value="PERMANENT">Permanent</option><option value="TEMPORARY">Time-bound</option></select></label>
        <label className="key"><span>Approver (optional)</span><select className="select" value={form.approver_id} onChange={event => setForm({...form, approver_id: event.target.value})}><option value="">No approval required</option>{(users || []).map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>
      </div>
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
      <label className="key" style={{display:'block',marginTop:14}}><span>Justification (optional)</span><input className="select" style={{width:'100%'}} value={form.justification} onChange={event => setForm({...form, justification: event.target.value})}/></label>
      {formMessage && <div className="notice" style={{marginTop:14}}>{formMessage}</div>}</div>
      <div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Creating...' : 'Create assignment'}</button></div>
    </form>}
    <TablePanel toolbar={<Toolbar placeholder="Search assignments"/>}>{loading ? <div className="empty">Loading assignments...</div> : error ? <div className="empty">{error}</div> : !assignmentList || assignmentList.length === 0 ? <div className="empty">No assignments found.</div> : <table><thead><tr><th>User</th><th>Resource</th><th>Type</th><th>Duration</th><th>Status</th><th>Start</th><th>Expiration</th><th></th></tr></thead><tbody>{assignmentList.map(a => <tr key={a.id}><td className="user-name">{a.user_display_name || a.user_id}</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{a.start_time ? new Date(a.start_time).toLocaleString() : '—'}</td><td>{a.expiration_time ? new Date(a.expiration_time).toLocaleString() : '—'}</td><td>{a.status === 'PENDING_APPROVAL' ? <span style={{display:'flex',gap:5}}><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'approve')} aria-label="Approve"><Check size={14}/></button><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'reject')} aria-label="Reject"><X size={14}/></button></span> : <span className="footer-note">No actions</span>}</td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
function AssignmentsPage() { return <AssignmentsInteractive />; }
function MyApprovalsPage() {
  const auth = useAuth();
  const { data: items, error, loading, reload } = useApiResource<ApiAssignment[]>('/api/v1/assignments/pending-approval');
  const [actioningId, setActioningId] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const decide = async (assignmentId: string, decision: 'approve' | 'reject') => {
    if (decision === 'reject' && !window.confirm('Reject this access request?')) return;
    setActioningId(assignmentId); setMessage('');
    try {
      const response = await auth.apiRequest(`/api/v1/assignments/${assignmentId}/${decision}`, { method: 'POST' });
      if (response.ok) reload();
      else { const body = await response.json().catch(() => null); setMessage(body?.error?.message || 'Unable to complete this action.'); }
    } catch { setMessage('Unable to complete this action.'); } finally { setActioningId(null); }
  };
  const pending = (items || []).filter(a => a.status === 'PENDING_APPROVAL');
  const decided = (items || []).filter(a => a.status !== 'PENDING_APPROVAL');
  return <Page eyebrow="ACCESS MANAGEMENT" title="Approvals" subtitle="Access assignments where you are the designated approver.">
    {message && <div className="detail-section" style={{marginBottom:14}}><div className="notice">{message}</div></div>}
    <TablePanel toolbar={undefined}>{loading ? <div className="empty">Loading approvals...</div> : error ? <div className="empty">{error}</div> : !items || items.length === 0 ? <div className="empty">No assignments are waiting on your approval.</div> : <table><thead><tr><th>User</th><th>Resource</th><th>Type</th><th>Duration</th><th>Status</th><th>Requested</th><th></th></tr></thead><tbody>{pending.map(a => <tr key={a.id}><td className="user-name">{a.user_display_name || a.user_id}</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{new Date(a.created_at).toLocaleString()}</td><td><span style={{display:'flex',gap:5}}><button className="btn btn-primary" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'approve')} aria-label="Approve"><Check size={14}/> Approve</button><button className="btn" disabled={actioningId === a.id} onClick={() => void decide(a.id, 'reject')} aria-label="Reject"><X size={14}/> Reject</button></span></td></tr>)}{decided.map(a => <tr key={a.id}><td className="user-name">{a.user_display_name || a.user_id}</td><td>{a.resource_display_name || a.resource_id}</td><td>{a.resource_type}</td><td>{a.assignment_type}</td><td><StatusBadge status={a.status}/></td><td>{new Date(a.created_at).toLocaleString()}</td><td><span className="footer-note">Decided</span></td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
function GroupsPage() {
  const auth = useAuth();
  const { data: groups, error, loading, reload } = useApiResource<ApiGroup[]>('/api/v1/groups');
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
    <TablePanel toolbar={<Toolbar placeholder="Search groups"/>}>{loading ? <div className="empty">Loading groups...</div> : error ? <div className="empty">{error}</div> : !groups || groups.length === 0 ? <div className="empty">No groups found.</div> : <table><thead><tr><th>Name</th><th>Description</th><th>Privileged</th><th>Status</th><th>Last synced</th></tr></thead><tbody>{groups.map(g => <tr key={g.id}><td className="user-name">{g.name}</td><td>{g.description || '—'}</td><td><span className={`risk ${g.is_privileged ? 'risk-high' : 'risk-low'}`}>{g.is_privileged ? 'Privileged' : 'Standard'}</span></td><td><StatusBadge status={g.status}/></td><td>{g.last_synced_at ? new Date(g.last_synced_at).toLocaleString() : 'Never'}</td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
function RolesPage() {
  const { data: roles, error, loading } = useApiResource<ApiRole[]>('/api/v1/roles');
  return <Page eyebrow="ADMINISTRATION" title="Directory roles" subtitle="Privileged and standard roles available through AccessPilot."><TablePanel toolbar={<Toolbar placeholder="Search roles"/>}>{loading ? <div className="empty">Loading roles...</div> : error ? <div className="empty">{error}</div> : !roles || roles.length === 0 ? <div className="empty">No roles found.</div> : <table><thead><tr><th>Role</th><th>Description</th><th>Provider</th><th>Privileged</th><th>Status</th></tr></thead><tbody>{roles.map(r => <tr key={r.id}><td className="user-name">{r.name}</td><td>{r.description || '—'}</td><td>Microsoft Entra ID</td><td><span className={`risk ${r.is_privileged ? 'risk-high' : 'risk-low'}`}>{r.is_privileged ? 'Yes' : 'No'}</span></td><td><StatusBadge status={r.status}/></td></tr>)}</tbody></table>}</TablePanel></Page>;
}
function PoliciesPage() { return <Page eyebrow="GOVERNANCE" title="Policies" subtitle="Rules that govern access duration, approvals, and assurance." action={<button className="btn btn-primary"><Plus size={14}/> Create policy</button>}><TablePanel toolbar={<Toolbar placeholder="Search policies"/>}><table><thead><tr><th>Policy name</th><th>Description</th><th>Scope</th><th>Max duration</th><th>Approval</th><th>MFA</th><th>Ticket</th><th>Status</th><th></th></tr></thead><tbody>{policies.map(p => <tr key={p.name}><td className="user-name">{p.name}</td><td>{p.description}</td><td>{p.scope}</td><td>{p.max}</td><td>{p.approval}</td><td>{p.mfa}</td><td>{p.ticket}</td><td><StatusBadge status={p.status}/></td><td><button className="btn">Edit</button></td></tr>)}</tbody></table></TablePanel></Page>; }
interface ApiAuditLog { id: string; timestamp: string; actor_user_id: string | null; actor_display_name: string | null; action: string; target_type: string; target_id: string | null; provider_id: string | null; provider_name: string | null; request_id: string; result: string; }
function AuditPage() {
  const { data: logs, error, loading } = useApiResource<ApiAuditLog[]>('/api/v1/audit-logs');
  return <Page eyebrow="GOVERNANCE" title="Audit logs" subtitle="A tamper-evident record of identity and access activity."><TablePanel toolbar={<Toolbar placeholder="Search audit events" select="All actions"/>}>{loading ? <div className="empty">Loading audit logs...</div> : error ? <div className="empty">{error}</div> : !logs || logs.length === 0 ? <div className="empty">No audit events found.</div> : <table><thead><tr><th>Timestamp</th><th>Actor</th><th>Action</th><th>Target</th><th>Provider</th><th>Result</th><th>Request ID</th></tr></thead><tbody>{logs.map(entry => <tr key={entry.id}><td>{new Date(entry.timestamp).toLocaleString()}</td><td className="user-name">{entry.actor_display_name || 'System'}</td><td>{entry.action}</td><td>{entry.target_type}</td><td>{entry.provider_name || '—'}</td><td><StatusBadge status={entry.result}/></td><td>{entry.request_id}</td></tr>)}</tbody></table>}</TablePanel></Page>;
}
function ProvidersPage() { return <ProviderConfiguration />; }
interface ApiProvider { id: string; name: string; provider_type: string; status: string; sync_interval_minutes: number | null; last_sync_at: string | null; }
interface ApiSyncRun { id: string; status: string; started_at: string; completed_at: string | null; users_processed: number; groups_processed: number; roles_processed: number; errors_count: number; }
function SyncPage() {
  const auth = useAuth();
  const { data: providers, loading: providersLoading, reload: reloadProviders } = useApiResource<ApiProvider[]>('/api/v1/providers');
  const provider = providers?.find(p => p.provider_type === 'ENTRA') || providers?.[0] || null;
  const { data: runs, error, loading, reload } = useApiResource<ApiSyncRun[]>(provider ? `/api/v1/providers/${provider.id}/sync-runs` : '', Boolean(provider));
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
    <TablePanel toolbar={<Toolbar placeholder="Search sync runs" select="All providers"/>}>{providersLoading || loading ? <div className="empty">Loading sync history...</div> : !provider ? <div className="empty">No identity provider is configured.</div> : error ? <div className="empty">{error}</div> : !runs || runs.length === 0 ? <div className="empty">No sync runs yet.</div> : <table><thead><tr><th>Started</th><th>Completed</th><th>Users</th><th>Groups</th><th>Roles</th><th>Errors</th><th>Status</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td className="user-name">{new Date(run.started_at).toLocaleString()}</td><td>{run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}</td><td>{run.users_processed}</td><td>{run.groups_processed}</td><td>{run.roles_processed}</td><td>{run.errors_count}</td><td><StatusBadge status={run.status}/></td></tr>)}</tbody></table>}</TablePanel>
  </Page>;
}
function Profile() { return <Page eyebrow="SELF-SERVICE" title="Profile" subtitle="Your AccessPilot identity and application role."><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="user-cell"><span className="avatar" style={{width:52,height:52}}>{currentUser.initials}</span><div><h2>{currentUser.name}</h2><p className="subtitle">{currentUser.title}</p></div></div></div><div className="detail-section"><div className="detail-title"><h2>Identity details</h2><StatusBadge status="Active"/></div><div className="key-grid"><div className="key"><span>Email</span><strong>{currentUser.email}</strong></div><div className="key"><span>Department</span><strong>{currentUser.department}</strong></div><div className="key"><span>Identity provider</span><strong>Microsoft Entra ID</strong></div><div className="key"><span>Last sign-in</span><strong>Today, 09:42 UTC</strong></div></div></div></section><aside className="panel"><div className="panel-head"><h2>Application role</h2></div><div className="detail-section"><div className="user-cell"><span className="stat-icon"><ShieldCheck size={16}/></span><div><strong>AccessPilot.Admin</strong><div className="user-email">Development role switcher active</div></div></div><p className="subtitle" style={{lineHeight:1.6,marginTop:18}}>Your role determines which console areas are visible. Authorization is enforced by the backend in production.</p></div></aside></div></Page>; }
export default App;
