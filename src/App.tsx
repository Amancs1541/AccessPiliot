import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Activity, AlertTriangle, ArrowRight, BarChart3, Bell, BookOpen, Box, Check, ChevronRight, Clock3, Cloud, Copy, Database, FileCheck2, FolderKanban, Gauge, KeyRound, LayoutDashboard, LifeBuoy, ListChecks, Menu, Network, Plus, RefreshCw, Search, Settings2, Shield, ShieldCheck, SlidersHorizontal, UserRound, Users, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { auditEvents, currentUser, policies, type RequestStatus, type Role } from './mock';
import { mockService, useMockState } from './mockService';
import { entraConfigured, useAuth } from './auth';
import ProviderConfiguration from './ProviderConfiguration';

interface ApiUser { id: string; external_id: string; email: string; display_name: string; given_name: string | null; surname: string | null; department: string | null; job_title: string | null; status: string; last_synced_at: string | null; }
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
  { label: 'Request Packages', icon: Box, to: '/request-packages', roles: ['user'] },
  { label: 'My Requests', icon: ListChecks, to: '/my-requests', roles: ['user'] },
  { label: 'Approvals', icon: Check, to: '/approvals', roles: ['user','admin'] },
  { label: 'Profile', icon: UserRound, to: '/profile', roles: ['user'] },
  { label: 'Users', icon: Users, to: '/admin/users', roles: ['admin'], section: 'ADMINISTRATION' },
  { label: 'Groups', icon: Network, to: '/admin/groups', roles: ['admin'] },
  { label: 'Roles', icon: Shield, to: '/admin/roles', roles: ['admin'] },
  { label: 'Access Requests', icon: FolderKanban, to: '/admin/access-requests', roles: ['admin'], section: 'ACCESS MANAGEMENT' },
  { label: 'Assignments', icon: KeyRound, to: '/admin/assignments', roles: ['admin'] },
  { label: 'Access Packages', icon: Box, to: '/admin/access-packages', roles: ['admin'] },
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
  return <Shell role={role} setRole={changeRole}><Routes><Route path="/" element={<Navigate to="/dashboard" replace />} /><Route path="/dashboard" element={<Dashboard role={role} />} /><Route path="/my-access" element={<MyAccess />} /><Route path="/request-access" element={<RequestAccess />} /><Route path="/request-packages" element={<RequestPackagesPage />} /><Route path="/my-requests" element={<Requests mine />} /><Route path="/approvals" element={<MyApprovalsPage />} /><Route path="/profile" element={<Profile />} /><Route path="/admin/users" element={<AdminOnly role={role}><UsersPage /></AdminOnly>} /><Route path="/admin/users/:id" element={<AdminOnly role={role}><UserDetail /></AdminOnly>} /><Route path="/admin/groups" element={<AdminOnly role={role}><GroupsPage /></AdminOnly>} /><Route path="/admin/roles" element={<AdminOnly role={role}><RolesPage /></AdminOnly>} /><Route path="/admin/access-requests" element={<AdminOnly role={role}><Requests /></AdminOnly>} /><Route path="/admin/access-requests/:id" element={<AdminOnly role={role}><RequestDetailInteractive /></AdminOnly>} /><Route path="/admin/assignments" element={<AdminOnly role={role}><AssignmentsInteractive /></AdminOnly>} /><Route path="/admin/access-packages" element={<AdminOnly role={role}><AccessPackagesInteractive /></AdminOnly>} /><Route path="/admin/policies" element={<AdminOnly role={role}><PoliciesPage /></AdminOnly>} /><Route path="/admin/audit" element={<AdminOnly role={role}><AuditPage /></AdminOnly>} /><Route path="/admin/providers" element={<AdminOnly role={role}><ProvidersPage /></AdminOnly>} /><Route path="/admin/sync" element={<AdminOnly role={role}><SyncPage /></AdminOnly>} /><Route path="*" element={<Navigate to="/dashboard" replace />} /></Routes></Shell>;
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
function StatCards({ admin = false, dashboard }: { admin?: boolean; dashboard?: DashboardAdmin | null }) { const na = '—'; const stats: Array<[string, string, string, LucideIcon, string?]> = admin ? [['Total users', dashboard ? String(dashboard.users) : na, 'Synced from Microsoft Entra ID', Users, '/admin/users'],['Groups', dashboard ? String(dashboard.groups) : na, 'Synced from Microsoft Entra ID', Network, '/admin/groups'],['Privileged roles', dashboard ? String(dashboard.privilegedRoles) : na, `${dashboard ? dashboard.roles : na} directory roles total`, ShieldCheck, '/admin/roles?privileged=true'],['Active JIT sessions', dashboard ? String(dashboard.activeSessions) : na, 'Currently active, real access grants', Clock3, '/admin/assignments?status=ACTIVE'],['Pending requests', dashboard ? String(dashboard.pendingRequests) : na, 'Awaiting approver decision', FolderKanban, '/admin/assignments?status=PENDING_APPROVAL'],['Expiring access', dashboard ? String(dashboard.expiringAccess) : na, 'Active access expiring within 24 hours', AlertTriangle, '/admin/assignments?status=ACTIVE&expiring=24h'],['Provider health', dashboard?.provider?.status || na, dashboard?.provider ? dashboard.provider.name : 'No provider configured', Cloud, '/admin/providers'],['Policy coverage', na, 'Not available in this release', FileCheck2, '/admin/policies']] : [['Active access','—','Not available in this release',KeyRound],['Eligible access','—','Not available in this release',Shield],['Pending requests','—','Not available in this release',Clock3],['Expiring soon','—','Not available in this release',AlertTriangle]]; return <div className={`stats ${admin ? 'admin-stats' : ''}`}>{stats.map(([label,value,foot,I,to]) => { const body = <><div className="stat-top"><span>{label}</span><span className="stat-icon"><I size={15}/></span></div><div className="stat-value">{value}</div><div className="stat-foot">{foot}</div></>; return to ? <Link to={to} className="stat stat-link" key={String(label)}>{body}</Link> : <div className="stat" key={String(label)}>{body}</div>; })}</div>; }
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
  const { data: dashboard, error, loading, reload: reloadDashboard } = useApiResource<DashboardAdmin>('/api/v1/dashboard/admin', admin);
  const { data: recentAudit, reload: reloadAudit } = useApiResource<ApiAuditLog[]>('/api/v1/audit-logs', admin);
  const { data: timeline, reload: reloadTimeline } = useApiResource<ApiActivationTimeline>('/api/v1/dashboard/privileged-role-activations?days=30', admin);
  const { data: segments, reload: reloadSegments } = useApiResource<ApiUserAccessSegments>('/api/v1/dashboard/user-access-segments', admin);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const { data: segmentMembers, loading: membersLoading } = useApiResource<ApiSegmentMember[]>(`/api/v1/dashboard/user-access-segments/${selectedSegment}`, Boolean(selectedSegment));
  const segmentTitle = selectedSegment === 'permanent-active' ? 'Permanent & Active' : selectedSegment === 'eligible' ? 'Eligible (not yet activated)' : '';
  const greetingName = auth.account?.name || (entraConfigured ? '' : currentUser.name);
  const lastSyncLabel = dashboard?.lastSync?.completedAt ? new Date(dashboard.lastSync.completedAt).toLocaleString() : dashboard?.lastSync ? 'In progress' : 'Never synced';

  useEffect(() => {
    if (!admin) return;
    const id = setInterval(() => { reloadDashboard(); reloadAudit(); reloadTimeline(); reloadSegments(); }, 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admin]);

  return <Page eyebrow={admin ? 'ADMINISTRATION' : 'SELF-SERVICE'} title={admin ? (greetingName ? `Good morning, ${greetingName.split(' ')[0]}` : 'Good morning') : 'Your access overview'} subtitle={admin ? 'Here is what is happening across your identity environment.' : 'Review your current access and request what you need.'} action={<button className="btn btn-primary" onClick={() => {}}><ArrowRight size={15}/> {admin ? 'Review requests' : 'Request access'}</button>}><StatCards admin={admin} dashboard={dashboard}/>{admin && loading && <div className="empty">Loading dashboard...</div>}{admin && error && <div className="empty">{error}</div>}
    {admin && <div className="grid-2" style={{marginBottom:18}}>
      <section className="panel"><div className="panel-head"><h2>Privileged role activations</h2><span className="panel-link">{timeline ? `Last ${timeline.days} days` : ''}</span></div><div className="detail-section">{timeline ? <ActivationTimelineChart series={timeline.series}/> : <div className="empty">Loading timeline...</div>}</div></section>
      <section className="panel"><div className="panel-head"><h2>User access mix</h2><span className="panel-link">Click a slice for the list</span></div><div className="detail-section">{segments ? <PieChart segments={[{key:'permanent-active', label:'Permanent & Active', value: segments.permanentActive, color:'#087f82'},{key:'eligible', label:'Eligible (not yet activated)', value: segments.eligible, color:'#f4b35d'}]} onSliceClick={setSelectedSegment}/> : <div className="empty">Loading...</div>}</div></section>
    </div>}
    {selectedSegment && <div className="overlay-backdrop" onClick={() => setSelectedSegment(null)}>
      <div className="overlay-card" onClick={event => event.stopPropagation()}>
        <div className="panel-head"><h2>{segmentTitle}</h2><button type="button" className="btn" aria-label="Close" onClick={() => setSelectedSegment(null)}><X size={14}/></button></div>
        <div className="table-wrap">{membersLoading ? <div className="empty">Loading users...</div> : !segmentMembers || segmentMembers.length === 0 ? <div className="empty">No users in this segment.</div> : <table><thead><tr><th>User</th><th>Email</th></tr></thead><tbody>{segmentMembers.map(m => <tr key={m.id}><td className="user-name">{m.display_name}</td><td>{m.email}</td></tr>)}</tbody></table>}</div>
      </div>
    </div>}
    <div className="grid-2"><section className="panel"><div className="panel-head"><h2>{admin ? 'Recent access requests' : 'Recent activity'}</h2><Link to={admin ? '/admin/audit' : '/my-requests'} className="panel-link">View all <ChevronRight size={12}/></Link></div>{admin ? (!recentAudit || recentAudit.length === 0 ? <div className="empty">No recent activity.</div> : recentAudit.slice(0,6).map(entry => <div className="activity" key={entry.id}><div className="activity-row"><span className="activity-dot"/><div className="activity-copy"><strong>{entry.action}</strong><small>{entry.actor_display_name || 'System'}{entry.target_user_display_name ? ` · ${entry.target_user_display_name}` : ''} · {new Date(entry.timestamp).toLocaleString()}</small></div><StatusBadge status={entry.result}/></div></div>)) : auditEvents.slice(0,5).map((item, i) => <div className="activity" key={i}><div className="activity-row"><span className="activity-dot"/><div className="activity-copy"><strong>{(item as string[])[2]}</strong><small>{(item as string[])[1]} · {(item as string[])[3]}</small></div><span className="time">{(item as string[])[0]}</span></div></div>)}</section><section className="panel"><div className="panel-head"><h2>{admin ? 'Provider status' : 'Current active access'}</h2>{admin && <StatusBadge status={dashboard?.provider?.status || 'NOT_CONFIGURED'}/>}</div>{admin ? <div className="detail-section"><div className="user-cell"><span className="avatar" style={{background:'#e4f1f5',color:'#33758a'}}><Cloud size={15}/></span><div><div className="user-name">{dashboard?.provider?.name || 'No provider configured'}</div><div className="user-email">{dashboard?.provider ? `${dashboard.provider.status} · Last sync ${lastSyncLabel}` : 'Configure a provider to begin syncing.'}</div></div></div><div className="key-grid" style={{marginTop:24}}><div className="key"><span>Users synced</span><strong>{dashboard ? dashboard.users : '—'}</strong></div><div className="key"><span>Groups synced</span><strong>{dashboard ? dashboard.groups : '—'}</strong></div><div className="key"><span>Directory roles</span><strong>{dashboard ? dashboard.roles : '—'}</strong></div><div className="key"><span>Last sync</span><strong>{lastSyncLabel}</strong></div></div></div> : <div className="detail-section"><div className="timeline"><div className="timeline-item"><strong>Not available in this release</strong><small>Access requests and assignments are not part of this phase.</small></div></div></div>}</section></div></Page>; }
function StatusBadge({ status }: { status: string }) { const cls = ['APPROVED','ACTIVE','COMPLETED','CONNECTED','ELIGIBLE','SUCCESS','Healthy','Active'].includes(status) ? 'success' : ['PENDING','PENDING_APPROVAL','SCHEDULED','RUNNING','PARTIAL','Medium'].includes(status) ? 'warning' : ['REJECTED','EXPIRED','REVOKED','FAILED','Disabled','High'].includes(status) ? 'danger' : 'neutral'; return <span className={`badge ${cls}`}>{status}</span>; }
function TablePanel({ children, toolbar }: { children: React.ReactNode; toolbar?: React.ReactNode }) { return <><div className="toolbar">{toolbar}</div><section className="panel"><div className="table-wrap">{children}</div></section></>; }
interface FilterOption { value: string; label: string; }
function Toolbar({ placeholder = 'Search', searchValue, onSearchChange, filterLabel = 'All statuses', filterValue = '', onFilterChange, filterOptions }: { placeholder?: string; searchValue?: string; onSearchChange?: (value: string) => void; filterLabel?: string; filterValue?: string; onFilterChange?: (value: string) => void; filterOptions?: FilterOption[]; }) {
  return <><div className="toolbar-left">{onSearchChange && <div className="search-box"><Search size={15}/><input className="search" placeholder={placeholder} value={searchValue ?? ''} onChange={event => onSearchChange(event.target.value)}/></div>}{filterOptions && <select className="select" value={filterValue} onChange={event => onFilterChange?.(event.target.value)}><option value="">{filterLabel}</option>{filterOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</div></>;
}
function initialsFor(name: string) { const parts = name.trim().split(/\s+/); return ((parts[0]?.[0] || '') + (parts[parts.length - 1]?.[0] || '')).toUpperCase() || '?'; }
function UsersPage() {
  const auth = useAuth();
  const { data: users, error, loading, reload } = useApiResource<ApiUser[]>('/api/v1/users');
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
    <TablePanel toolbar={<Toolbar placeholder="Search users by name or email" searchValue={search} onSearchChange={setSearch} filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={statusOptions}/>}>{loading ? <div className="empty">Loading users...</div> : error ? <div className="empty">{error}</div> : !users || users.length === 0 ? <div className="empty">No users found.</div> : filteredUsers.length === 0 ? <div className="empty">No users match this filter.</div> : <table><thead><tr><th>User</th><th>Department</th><th>Job title</th><th>Status</th><th>Last synced</th><th></th></tr></thead><tbody>{filteredUsers.map(u => <tr key={u.id}><td><Link to={`/admin/users/${u.id}`} className="user-cell"><span className="avatar">{initialsFor(u.display_name)}</span><span><span className="user-name">{u.display_name}</span><span className="user-email">{u.email}</span></span></Link></td><td>{u.department || '—'}</td><td>{u.job_title || '—'}</td><td><StatusBadge status={u.status}/></td><td>{u.last_synced_at ? new Date(u.last_synced_at).toLocaleString() : 'Never'}</td><td><ChevronRight size={15} color="#829198"/></td></tr>)}</tbody></table>}</TablePanel>
    {users && users.length > 0 && <p className="footer-note">Showing {filteredUsers.length} of {users.length} users</p>}
  </Page>;
}
function UserDetail() {
  const { id } = useParams();
  const { data: user, error, loading } = useApiResource<ApiUser>(`/api/v1/users/${id}`);
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
  return <Page eyebrow="USER DIRECTORY" title={user.display_name} subtitle={user.email} action={<button className="btn" aria-label="Refresh" onClick={() => reloadAccess()}><RefreshCw size={14}/></button>}><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="user-cell"><span className="avatar" style={{width:45,height:45}}>{initialsFor(user.display_name)}</span><div><h2>{user.job_title || 'No job title on file'}</h2><p className="subtitle">{user.department || 'No department on file'} · {user.status}</p></div></div></div><div className="detail-section"><div className="detail-title"><h2>Overview</h2><StatusBadge status={user.status}/></div><div className="key-grid"><div className="key"><span>Email</span><strong style={{display:'flex',alignItems:'center',gap:8}}>{user.email}<button type="button" className="btn" aria-label="Copy email" onClick={() => void copyEmail()} style={{padding:'2px 7px'}}><Copy size={12}/></button>{copied && <span className="footer-note">Copied</span>}</strong></div><div className="key"><span>External ID</span><strong>{user.external_id}</strong></div><div className="key"><span>Given name</span><strong>{user.given_name || '—'}</strong></div><div className="key"><span>Surname</span><strong>{user.surname || '—'}</strong></div><div className="key"><span>Last synced</span><strong>{user.last_synced_at ? new Date(user.last_synced_at).toLocaleString() : 'Never'}</strong></div><div className="key"><span>Groups</span><strong>{accessLoading ? '…' : groupCount}</strong></div><div className="key"><span>Applications</span><strong>{accessLoading ? '…' : applicationCount}</strong></div></div></div></section><aside className="panel">
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
        {loading ? <div className="empty">Loading...</div> : error ? <div className="empty">{error}</div> : eligibleRows.length === 0 ? <div className="notice">Nothing eligible to activate right now.</div> : eligibleRows.map(row => row.kind === 'single'
          ? <div key={row.assignment.id} className="activity-row"><span className="avatar"><Shield size={14}/></span><div className="activity-copy"><strong>{row.assignment.resource_display_name || row.assignment.resource_id}</strong><small>{row.assignment.resource_type}{row.assignment.package_name ? ` · ${row.assignment.package_name}` : ''} · {row.assignment.assignment_type === 'TEMPORARY' && row.assignment.expiration_time ? `Activate by ${new Date(row.assignment.expiration_time).toLocaleString()}` : 'No activation deadline'}</small></div><button className="btn btn-primary" onClick={() => openActivate([row.assignment.id], row.assignment.resource_display_name || 'this access')}>Activate <ArrowRight size={13}/></button></div>
          : <div key={row.batch.package_id} className="activity-row"><span className="avatar">📦</span><div className="activity-copy"><strong>{row.batch.package_name}</strong><small>PACKAGE · {row.assignments.length} items</small></div><button className="btn btn-primary" onClick={() => openActivate(row.assignments.map(a => a.id), `"${row.batch.package_name}" (${row.assignments.length} items)`)}>Activate all <ArrowRight size={13}/></button></div>
        )}
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
function RequestAccess() { const [submitted, setSubmitted] = useState(false); return <Page eyebrow="SELF-SERVICE" title="Request access" subtitle="Request temporary access with a clear business justification."><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="detail-title"><h2>Access details</h2><span className="badge info">Step 1 of 2</span></div><label className="key"><span>Resource</span><select className="select" style={{width:'100%'}}><option>Production Administrator</option><option>Security Administrator</option><option>Product Analytics</option></select></label><div className="key-grid" style={{marginTop:20}}><label className="key"><span>Requested duration</span><select className="select" style={{width:'100%'}}><option>2 hours</option><option>1 hour</option><option>4 hours</option></select></label><label className="key"><span>Ticket number</span><input className="select" placeholder="INC-48291" style={{width:'100%'}}/></label></div><label className="key" style={{display:'block',marginTop:20}}><span>Business justification</span><textarea className="select" rows={5} placeholder="Explain why this access is needed and what you will do."></textarea></label></div><div className="detail-section" style={{display:'flex',justifyContent:'flex-end',gap:8}}><button className="btn">Cancel</button><button className="btn btn-primary" onClick={() => setSubmitted(true)}><SendIcon/> Submit request</button></div></section><aside className="panel"><div className="panel-head"><h2>Policy requirements</h2></div><div className="detail-section">{submitted && <div className="notice" style={{marginBottom:15}}>Request submitted successfully. It is now awaiting approval.</div>}<div className="notice"><strong>Privileged access</strong><br/>This resource requires MFA, an active ticket, and approval from a designated approver. Maximum duration is 4 hours.</div><div className="timeline" style={{padding:'22px 0 0'}}><div className="timeline-item"><strong>Policy evaluation</strong><small>Passed for your identity</small></div><div className="timeline-item"><strong>Approval required</strong><small>Security Operations</small></div><div className="timeline-item"><strong>Activation</strong><small>Available after approval</small></div></div></div></aside></div></Page>; }
function SendIcon() { return <ArrowRight size={14}/>; }
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
            const options = principal.principal_type === 'USER' ? (users || []) : (groups || []);
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
          const options = principal.principal_type === 'USER' ? (users || []) : (groups || []);
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
    <TablePanel toolbar={<Toolbar placeholder="Search packages" searchValue={search} onSearchChange={setSearch} filterLabel="All statuses" filterValue={statusFilter} onFilterChange={setStatusFilter} filterOptions={[{value:'ACTIVE',label:'Active'},{value:'ARCHIVED',label:'Archived'}]}/>}>{loading ? <div className="empty">Loading packages...</div> : error ? <div className="empty">{error}</div> : !packageList || packageList.length === 0 ? <div className="empty">No packages found.</div> : filteredPackages.length === 0 ? <div className="empty">No packages match this filter.</div> : <table><thead><tr><th>Name</th><th>Description</th><th>Items</th><th>Status</th><th>Requestable by</th><th></th></tr></thead><tbody>{filteredPackages.map(p => <tr key={p.id}><td className="user-name">{p.name}</td><td>{p.description || '—'}</td><td>{p.items.map(i => i.resource_display_name || i.resource_id).join(', ')}</td><td><StatusBadge status={p.status}/></td><td>{p.eligible_principals.length === 0 ? '—' : `${p.eligible_principals.length} ${p.eligible_principals.length === 1 ? 'entry' : 'entries'}`}</td><td><span style={{display:'flex',gap:5}}>{p.status === 'ACTIVE' && <button className="btn btn-primary" onClick={() => { setAssigningPackage(p); setAssignForm(emptyPackageAssignForm); setAssignMessage(''); }}>Assign</button>}<button className="btn" onClick={() => openEdit(p)}>Edit</button>{p.status === 'ACTIVE' && <button className="btn" onClick={() => openEligibility(p)}>Eligibility</button>}{p.status === 'ACTIVE' && <button className="btn" disabled={archivingId === p.id} onClick={() => void deletePackage(p.id)}>Delete</button>}</span></td></tr>)}</tbody></table>}</TablePanel>
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
function Profile() { return <Page eyebrow="SELF-SERVICE" title="Profile" subtitle="Your AccessPilot identity and application role."><div className="detail-layout"><section className="panel"><div className="detail-section"><div className="user-cell"><span className="avatar" style={{width:52,height:52}}>{currentUser.initials}</span><div><h2>{currentUser.name}</h2><p className="subtitle">{currentUser.title}</p></div></div></div><div className="detail-section"><div className="detail-title"><h2>Identity details</h2><StatusBadge status="Active"/></div><div className="key-grid"><div className="key"><span>Email</span><strong>{currentUser.email}</strong></div><div className="key"><span>Department</span><strong>{currentUser.department}</strong></div><div className="key"><span>Identity provider</span><strong>Microsoft Entra ID</strong></div><div className="key"><span>Last sign-in</span><strong>Today, 09:42 UTC</strong></div></div></div></section><aside className="panel"><div className="panel-head"><h2>Application role</h2></div><div className="detail-section"><div className="user-cell"><span className="stat-icon"><ShieldCheck size={16}/></span><div><strong>AccessPilot.Admin</strong><div className="user-email">Development role switcher active</div></div></div><p className="subtitle" style={{lineHeight:1.6,marginTop:18}}>Your role determines which console areas are visible. Authorization is enforced by the backend in production.</p></div></aside></div></Page>; }
export default App;
