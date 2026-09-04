import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { PublicClientApplication, type AccountInfo, type AuthenticationResult, type Configuration } from '@azure/msal-browser';
import { MsalProvider, useIsAuthenticated, useMsal } from '@azure/msal-react';

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID as string | undefined;
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID as string | undefined;
const redirectUri = import.meta.env.VITE_ENTRA_REDIRECT_URI as string | undefined;
const staticApiScope = import.meta.env.VITE_ACCESSPILOT_API_SCOPE as string | undefined;
export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://localhost:8000';
export const entraConfigured = Boolean(tenantId && clientId && redirectUri && staticApiScope);
const staticMsalConfig: Configuration = { auth: { clientId: clientId || 'unconfigured', authority: tenantId ? `https://login.microsoftonline.com/${tenantId}` : undefined, redirectUri: redirectUri || window.location.origin }, cache: { cacheLocation: 'sessionStorage' } };

type AppRole = 'user' | 'admin';
interface AuthContextValue {
  role: AppRole; account: AccountInfo | null; loading: boolean; signIn: () => Promise<void>; signOut: () => Promise<void>; apiRequest: (path: string, init?: RequestInit) => Promise<Response>;
  // Whether the signed-in user additionally holds AccessPilot.SoDAdmin — a DB-driven flag (not an Entra App
  // Role, unlike `role` above), granted/revoked by a plain Admin from inside the app. Independent of `role`: a
  // plain end-user can hold this without being 'admin', and an Admin can hold it too.
  isSodAdmin: boolean;
  // Unix-ms timestamp of when the CURRENT session actually began — derived once, in one place, from the real
  // token claims for whichever auth path is active (MSAL ID token's auth_time/iat, or the Break-Glass JWT's
  // iat), never a placeholder string. See the Profile page (src/App.tsx), the only current consumer.
  sessionStartedAt: number | null;
  // authConfigured reflects EITHER the static VITE_ENTRA_* build-time env vars OR (only when those are absent) a
  // dynamically-fetched active PortalAuthConfig from the backend — see AuthProvider's bootstrap effect. For a
  // deployment with the static env vars set, this is always identical to the old module-level `entraConfigured`.
  authConfigured: boolean;
  // Break-Glass: reachable only via the hidden /emergency-access/:token page (src/EmergencyAccess.tsx), never
  // from this file. A fresh session lands in the restricted AccessPilot.BreakGlassAdmin role (breakglassElevated
  // === false); elevateBreakglass() is the explicit, single-click escalation to full AccessPilot.Admin.
  breakglassActive: boolean; breakglassUsername: string | null; breakglassElevated: boolean; idpUnreachable: boolean;
  elevateBreakglass: () => Promise<{ ok: boolean; error?: string }>;
  // Manual "Refresh my access" action (see the Profile page) — forces a fresh token instead of waiting for
  // MSAL's cached one to expire (up to ~60 minutes), so a real Entra App Role change (e.g. AccessPilot.SoDAdmin
  // assigned/removed in the IDP) can take effect without a full sign-out/sign-in. A no-op-but-safe call for
  // Break-Glass sessions too — re-verifies against /me, useful mainly for consistency. Returns whether it
  // actually succeeded, so the caller can tell the user the truth instead of always claiming success.
  refreshAccess: () => Promise<boolean>;
}
const AuthContext = createContext<AuthContextValue | null>(null);

export const BREAKGLASS_STORAGE_KEY = 'accesspilot.breakglassToken';
function loadStoredBreakglassToken(): string | null {
  try { return sessionStorage.getItem(BREAKGLASS_STORAGE_KEY); } catch { return null; }
}
function storeBreakglassToken(token: string) {
  try { sessionStorage.setItem(BREAKGLASS_STORAGE_KEY, token); } catch { /* private browsing or storage disabled — session just won't survive a refresh */ }
}
function clearStoredBreakglassToken() {
  try { sessionStorage.removeItem(BREAKGLASS_STORAGE_KEY); } catch { /* nothing to clean up */ }
}

const authDebugEnabled = import.meta.env.DEV;
const adminAppRole = 'AccessPilot.Admin';

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const json = decodeURIComponent(Array.from(atob(base64), character => `%${character.charCodeAt(0).toString(16).padStart(2, '0')}`).join(''));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function safeClaims(claims: Record<string, unknown> | undefined | null, keys: string[]) {
  return Object.fromEntries(keys.filter(key => claims?.[key] !== undefined).map(key => [key, claims?.[key]]));
}

function authDebug(label: string, value?: unknown) {
  if (!authDebugEnabled) return;
  if (value === undefined) console.log(`[TEMPORARY DEBUG] ${label}`);
  else console.log(`[TEMPORARY DEBUG] ${label}`, value);
}

function authDebugError(error: unknown) {
  const candidate = error as { name?: unknown; message?: unknown; errorCode?: unknown; interactionRequired?: unknown } | null;
  return {
    name: candidate?.name ?? 'UnknownError',
    message: candidate?.message ?? String(error),
    errorCode: candidate?.errorCode,
    interactionRequired: candidate?.interactionRequired === true || candidate?.errorCode === 'interaction_required' || candidate?.name === 'InteractionRequiredAuthError',
  };
}

function safeAccount(account: AccountInfo) {
  return {
    name: account.name,
    username: account.username,
    homeAccountId: account.homeAccountId,
    localAccountId: account.localAccountId,
    tenantId: account.tenantId,
  };
}

function AuthState({ children, authConfigured, apiScope }: { children: ReactNode; authConfigured: boolean; apiScope: string | undefined }) {
  const { instance, accounts, inProgress } = useMsal(); const authenticated = useIsAuthenticated();
  // apiLoading MUST default to true, not false: it starts false-to-true only inside the effect below, which runs
  // AFTER the first render commits. With a false default, that first render already has `role` at its initial
  // 'user' value AND `loading` already false (once MSAL's own inProgress has settled to 'none', which it already
  // has by the time this component mounts) — so the app briefly (or, if the /me call is slow, not-so-briefly)
  // renders the end-user panel for an Admin before the real role check catches up. Defaulting to true keeps the
  // app on the loading screen until the effect has actually determined a role one way or the other.
  const [role, setRole] = useState<AppRole>('user'); const [account, setAccount] = useState<AccountInfo | null>(accounts[0] || null); const [apiLoading, setApiLoading] = useState(true);
  const [sessionStartedAt, setSessionStartedAt] = useState<number | null>(null);
  const [isSodAdmin, setIsSodAdmin] = useState(false);

  // Break-Glass emergency login — mutually exclusive with a real MSAL account. A token found in sessionStorage
  // (survives a page refresh, cleared when the tab closes, matching MSAL's own cacheLocation choice above) is
  // re-verified against /me on load rather than trusted blindly, since it may have expired (8h TTL server-side).
  // It's written there by the standalone /emergency-access/:token page (src/EmergencyAccess.tsx), which renders
  // outside this provider entirely and then hard-navigates to `/` so this effect picks it up fresh.
  const [breakglassToken, setBreakglassTokenState] = useState<string | null>(() => loadStoredBreakglassToken());
  const [breakglassUsername, setBreakglassUsername] = useState<string | null>(null);
  const [breakglassElevated, setBreakglassElevated] = useState(false);
  const [breakglassChecking, setBreakglassChecking] = useState<boolean>(() => Boolean(loadStoredBreakglassToken()));
  // Only ever set from signIn()'s own catch below (the redirect to the IDP itself failed to even start) — NOT
  // from AuthProvider's bootstrap handleRedirectPromise() check, which throws for all sorts of benign leftover
  // MSAL state (an earlier interrupted sign-in attempt, stale browser cache) that have nothing to do with the
  // IDP actually being down, and had no way to reset back to false once wrongly triggered.
  const [idpUnreachable, setIdpUnreachable] = useState(false);
  const breakglassActive = Boolean(breakglassToken && breakglassUsername);

  const verifyBreakglassToken = useCallback(async (token: string, options?: { signal?: AbortSignal }) => {
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/me`, { headers: { Authorization: `Bearer ${token}` }, signal: options?.signal });
      if (!response.ok) { clearStoredBreakglassToken(); setBreakglassTokenState(null); return; }
      const profile = await response.json() as Record<string, unknown>;
      setBreakglassUsername(typeof profile.displayName === 'string' ? profile.displayName : 'breakglass');
      // A fresh Break-Glass session lands in the restricted BreakGlassAdmin role — only a session that has gone
      // through the explicit elevateBreakglass() escalation reports the Admin app role here.
      const elevated = Array.isArray(profile.roles) && profile.roles.includes(adminAppRole);
      setBreakglassElevated(elevated);
      setRole(elevated ? 'admin' : 'user');
      setIsSodAdmin(Array.isArray(profile.roles) && profile.roles.includes('AccessPilot.SoDAdmin'));
      const breakglassClaims = decodeJwtPayload(token);
      const breakglassIat = breakglassClaims?.iat as number | undefined;
      setSessionStartedAt(breakglassIat ? breakglassIat * 1000 : Date.now());
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      // Network hiccup verifying — keep the stored token rather than discarding it on a transient failure;
      // a genuinely dead/expired token will still be rejected the next time an actual API call uses it.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!breakglassToken) { setBreakglassChecking(false); return; }
    const controller = new AbortController();
    void verifyBreakglassToken(breakglassToken, { signal: controller.signal }).finally(() => setBreakglassChecking(false));
    return () => controller.abort();
  }, [breakglassToken, verifyBreakglassToken]);
  const loadProfile = useCallback(async (current: AccountInfo, scope: string, options?: { forceRefresh?: boolean; signal?: AbortSignal }) => {
    setApiLoading(true);
    let tokenDebugGroupOpen = false;
    let apiDebugGroupOpen = false;
    try {
      if (authDebugEnabled) {
        console.group('ACCESSPILOT TOKEN DEBUG — TEMPORARY DEVELOPMENT LOGGING');
        tokenDebugGroupOpen = true;
      }
      authDebug('Account:', safeAccount(current));
      authDebug('ID TOKEN SAFE CLAIMS:', safeClaims(current.idTokenClaims, ['roles', 'preferred_username', 'name', 'tid', 'oid', 'aud', 'iss']));
      authDebug('Requested API scope:', scope);
      authDebug('Calling acquireTokenSilent().', options?.forceRefresh ? '(forceRefresh)' : '');

      const result: AuthenticationResult = await instance.acquireTokenSilent({ account: current, scopes: [scope], forceRefresh: options?.forceRefresh });
      const accessTokenClaims = decodeJwtPayload(result.accessToken);
      const safeAccessTokenClaims = safeClaims(accessTokenClaims, ['aud', 'iss', 'tid', 'oid', 'roles', 'scp', 'azp', 'appid', 'exp']);
      const apiTokenHasAdminRole = Array.isArray(accessTokenClaims?.roles) && accessTokenClaims.roles.includes(adminAppRole);
      authDebug('Token successfully acquired:', true);
      authDebug('API TOKEN SAFE CLAIMS:', safeAccessTokenClaims);
      authDebug('Token expiration time:', typeof accessTokenClaims?.exp === 'number' ? new Date(accessTokenClaims.exp * 1000).toISOString() : null);
      authDebug('ADMIN ROLE PRESENT IN API TOKEN:', apiTokenHasAdminRole);
      if (!apiTokenHasAdminRole) authDebug('ADMIN ROLE IS NOT PRESENT IN THE API ACCESS TOKEN');
      if (authDebugEnabled) {
        console.groupEnd();
        tokenDebugGroupOpen = false;
      }

      if (authDebugEnabled) {
        console.group('ACCESSPILOT API DEBUG — TEMPORARY DEVELOPMENT LOGGING');
        apiDebugGroupOpen = true;
      }
      authDebug('Calling API URL:', `${apiBaseUrl}/api/v1/me`);
      const response = await fetch(`${apiBaseUrl}/api/v1/me`, { headers: { Authorization: `Bearer ${result.accessToken}` }, signal: options?.signal });
      let profile: Record<string, unknown> | null = null;
      try { profile = await response.json() as Record<string, unknown>; } catch { /* Response is not JSON; status is logged below. */ }
      authDebug('API /me STATUS:', response.status);
      authDebug('API /me PROFILE:', profile);
      authDebug('API /me PROFILE ROLES:', profile?.roles);
      if (authDebugEnabled) {
        console.groupEnd();
        apiDebugGroupOpen = false;
      }
      // A failed /me call (expired session, transient 5xx, etc.) must not be silently treated as "this user has
      // no roles" — that body has no `roles` array either way, so without this check the code below would fall
      // straight through the success path and quietly compute nextRole as 'user', indistinguishable from an
      // actual role change. Route it through the catch block instead, where a forced refresh's failure is
      // reported honestly rather than misread as "your role is now User."
      if (!response.ok) throw new Error(`GET /me failed with status ${response.status}`);

      const nextRole: AppRole = Array.isArray(profile?.roles) && profile.roles.includes(adminAppRole) ? 'admin' : 'user';
      setRole(nextRole);
      setIsSodAdmin(Array.isArray(profile?.roles) && profile.roles.includes('AccessPilot.SoDAdmin'));
      // auth_time is an OPTIONAL ID-token claim Entra does not always emit; iat (issued-at, always present on
      // any valid token) is the reliable fallback — either way this is a real timestamp, never a placeholder.
      const idClaims = current.idTokenClaims as Record<string, unknown> | undefined;
      const authMoment = (idClaims?.auth_time as number | undefined) ?? (idClaims?.iat as number | undefined) ?? (accessTokenClaims?.iat as number | undefined);
      setSessionStartedAt(authMoment ? authMoment * 1000 : Date.now());
      if (authDebugEnabled) console.group('ACCESSPILOT AUTH STATE DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      authDebug('FINAL FRONTEND ROLE:', nextRole);
      authDebug('Final authenticated state:', authenticated);
      if (authDebugEnabled) console.groupEnd();
      return true;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return true;
      // On the very first load, no role has been established yet, so falling back to the least-privilege
      // default is correct and safe. On a manual "Refresh my access" retry, a role that was already established
      // must NOT be silently wiped by a transient failure (a flaky network blip, or acquireTokenSilent needing
      // interaction) — that would make the button actively worse than doing nothing, downgrading a real Admin to
      // User on a hiccup instead of just failing to pick up a change. Leave existing state untouched instead and
      // let the caller (refreshAccess) report the failure honestly.
      if (!options?.forceRefresh) setRole('user');
      // Unlike the rest of this function's logging, this line is NOT gated behind authDebugEnabled (dev-mode
      // only) — a "Refresh my access" failure needs to be diagnosable against a real production build too, not
      // just `npm run dev`. Kept to one concise line, not the full verbose debug block above.
      if (options?.forceRefresh) console.error('AccessPilot: "Refresh my access" failed —', authDebugError(error));
      if (authDebugEnabled && apiDebugGroupOpen) console.groupEnd();
      if (authDebugEnabled && tokenDebugGroupOpen) console.groupEnd();
      if (authDebugEnabled) console.group('ACCESSPILOT TOKEN DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      authDebug('Token successfully acquired:', false);
      authDebug('acquireTokenSilent() or API request error:', authDebugError(error));
      if (authDebugEnabled) console.groupEnd();
      if (authDebugEnabled) console.group('ACCESSPILOT AUTH STATE DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      authDebug('FINAL FRONTEND ROLE:', options?.forceRefresh ? '(unchanged — refresh failed)' : 'user');
      authDebug('Final authenticated state:', authenticated);
      if (authDebugEnabled) console.groupEnd();
      return false;
    } finally {
      setApiLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instance, authenticated]);

  useEffect(() => {
    const current = accounts[0] || null;
    setAccount(current);
    if (authDebugEnabled) {
      console.group('ACCESSPILOT AUTH STATE DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      authDebug('Current MSAL accounts:', accounts.map(safeAccount));
      authDebug('Active account selected:', current ? safeAccount(current) : null);
      authDebug('MSAL authenticated state:', authenticated);
      authDebug('MSAL interaction state:', inProgress);
      authDebug('API scope available:', Boolean(apiScope));
      console.groupEnd();
    }
    if (!current || !apiScope) {
      authDebug('Profile/token loading skipped because account or API scope is unavailable.');
      setApiLoading(false); // no account to check a role for — don't get stuck on the loading screen forever
      return;
    }
    const controller = new AbortController();
    void loadProfile(current, apiScope, { signal: controller.signal });
    return () => controller.abort();
  }, [accounts, apiScope, loadProfile]);
  const signIn = async () => {
    if (authDebugEnabled) console.group('ACCESSPILOT LOGIN DEBUG — TEMPORARY DEVELOPMENT LOGGING');
    authDebug('loginRedirect() invoked by the sign-in button.');
    authDebug('Auth configured:', authConfigured);
    authDebug('Requested API scope:', apiScope);
    try {
      if (!authConfigured) return;
      await instance.loginRedirect({ scopes: [apiScope!] });
      authDebug('loginRedirect() returned without an immediate error.');
    } catch (error) {
      authDebug('loginRedirect() error:', authDebugError(error));
      const errorCode = (error as { errorCode?: string })?.errorCode;
      if (errorCode === 'interaction_in_progress') {
        // A stale flag left in sessionStorage from an earlier interrupted/interleaved sign-in attempt — not a
        // real IDP problem. MSAL refuses every loginRedirect() while it thinks this, with no way to clear it
        // itself; removing the one key it tracks this with and retrying once is the standard recovery.
        authDebug('Clearing stale msal.interaction.status and retrying loginRedirect() once.');
        try {
          sessionStorage.removeItem('msal.interaction.status');
          await instance.loginRedirect({ scopes: [apiScope!] });
          return;
        } catch (retryError) {
          authDebug('Retry after clearing interaction_in_progress also failed:', authDebugError(retryError));
        }
      }
      // A failure here means the redirect to the IDP couldn't even be initiated (e.g. no network route to it) —
      // the clearest possible "the IDP is unreachable" signal. Surfaced only as a generic notice on the sign-in
      // screen — never a hint that Break-Glass exists (see src/App.tsx's SignInScreen).
      setIdpUnreachable(true);
    } finally {
      if (authDebugEnabled) console.groupEnd();
    }
  };
  const signOut = async () => {
    if (breakglassToken) { clearStoredBreakglassToken(); setBreakglassTokenState(null); setBreakglassUsername(null); setBreakglassElevated(false); }
    if (authConfigured && account) await instance.logoutRedirect();
  };
  const elevateBreakglass = async (): Promise<{ ok: boolean; error?: string }> => {
    if (!breakglassToken) return { ok: false, error: 'No active Break-Glass session.' };
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/auth/breakglass-elevate`, { method: 'POST', headers: { Authorization: `Bearer ${breakglassToken}` } });
      if (!response.ok) {
        let message = 'Could not elevate this session.';
        try { const data = await response.json(); message = data?.error?.message || message; } catch { /* non-JSON error body */ }
        return { ok: false, error: message };
      }
      const data = await response.json();
      storeBreakglassToken(data.access_token);
      setBreakglassTokenState(data.access_token); // re-fires the verification effect above, which flips breakglassElevated/role on its own
      return { ok: true };
    } catch {
      return { ok: false, error: 'Could not reach the backend. Confirm it is running and try again.' };
    }
  };
  const refreshAccess = async (): Promise<boolean> => {
    if (breakglassToken) { await verifyBreakglassToken(breakglassToken); return true; }
    const current = accounts[0] || null;
    if (!current || !apiScope) return false;
    return loadProfile(current, apiScope, { forceRefresh: true });
  };
  const apiRequest = async (path: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    headers.set('Content-Type', 'application/json');
    if (breakglassToken) {
      headers.set('Authorization', `Bearer ${breakglassToken}`);
    } else if (authConfigured) {
      if (!accounts[0]) throw new Error('AUTHENTICATION_REQUIRED');
      const result = await instance.acquireTokenSilent({ account: accounts[0], scopes: [apiScope!] });
      headers.set('Authorization', `Bearer ${result.accessToken}`);
    }
    return fetch(`${apiBaseUrl}${path}`, { ...init, headers });
  };
  return <AuthContext.Provider value={{ role: (authenticated || breakglassActive) ? role : 'user', account, loading: inProgress !== 'none' || apiLoading || breakglassChecking, signIn, signOut, apiRequest, isSodAdmin, sessionStartedAt, authConfigured, breakglassActive, breakglassUsername, breakglassElevated, idpUnreachable, elevateBreakglass, refreshAccess }}>{children}</AuthContext.Provider>;
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authConfigured, setAuthConfigured] = useState(entraConfigured);
  const [resolvedApiScope, setResolvedApiScope] = useState<string | undefined>(staticApiScope);
  const [msalInstance, setMsalInstance] = useState<PublicClientApplication | null>(null);
  const bootstrapped = useRef(false);
  useEffect(() => {
    // React 18 StrictMode (dev only) double-invokes effects that have no cleanup function, specifically to
    // surface exactly this class of bug: two separately-constructed PublicClientApplication instances would
    // otherwise both call initialize()/handleRedirectPromise() and race against the SAME underlying
    // sessionStorage keys MSAL uses to track interaction state (there is only one browser tab, one storage).
    // That race can leave a stale `msal.interaction.status` flag behind, which then silently blocks every
    // future loginRedirect() call — this guard ensures the real bootstrap logic (and the one MSAL instance it
    // creates) runs exactly once per page load, regardless of how many times the effect itself fires.
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    const bootstrap = async () => {
      let instance = new PublicClientApplication(staticMsalConfig);
      let configured = entraConfigured;
      let scope = staticApiScope;

      if (!entraConfigured) {
        // No build-time VITE_ENTRA_* env vars — try the actively-configured PortalAuthConfig from the backend
        // instead, so real end-user login can work without a rebuild once an admin activates one via the setup
        // wizard or the Break-Glass IDP-recovery dashboard. Purely additive: for a deployment with the static env
        // vars present (this one, today), `entraConfigured` is already true and this whole branch never runs.
        try {
          const response = await fetch(`${apiBaseUrl}/api/v1/auth/portal-config`);
          const data = await response.json();
          if (data?.configured && data.idp_type === 'ENTRA' && data.client_id) {
            const dynamicConfig: Configuration = { auth: { clientId: data.client_id, authority: data.tenant_id ? `https://login.microsoftonline.com/${data.tenant_id}` : data.authority, redirectUri: data.redirect_uri || window.location.origin }, cache: { cacheLocation: 'sessionStorage' } };
            instance = new PublicClientApplication(dynamicConfig);
            configured = true;
            scope = data.scope || undefined;
          }
        } catch {
          // Backend unreachable, or no active config yet — fall through exactly as today (mock-mode role
          // switcher / SetupWizard, whichever the rest of the app decides based on authConfigured/setup status).
        }
      }

      if (authDebugEnabled) console.group('ACCESSPILOT LOGIN DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      try {
        authDebug('Calling msal.initialize().');
        await instance.initialize();
        authDebug('msal.initialize() completed.');
      } catch (error) {
        authDebug('msal.initialize() error:', authDebugError(error));
      } finally {
        if (authDebugEnabled) console.groupEnd();
      }

      if (authDebugEnabled) console.group('ACCESSPILOT REDIRECT DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      try {
        authDebug('Calling msal.handleRedirectPromise().');
        const redirectResult = await instance.handleRedirectPromise();
        authDebug('handleRedirectPromise() returned an AuthenticationResult:', Boolean(redirectResult));
        authDebug('Accounts after redirect processing:', instance.getAllAccounts().map(safeAccount));
      } catch (error) {
        authDebug('handleRedirectPromise() error:', authDebugError(error));
        authDebug('Accounts after redirect-processing error:', instance.getAllAccounts().map(safeAccount));
        // Deliberately NOT treated as "IDP unreachable" — this throws for all sorts of benign leftover MSAL
        // state (an earlier interrupted sign-in attempt, stale sessionStorage) that have nothing to do with a
        // real outage. Logged for debugging only.
      } finally {
        if (authDebugEnabled) console.groupEnd();
        setAuthConfigured(configured);
        setResolvedApiScope(scope);
        setMsalInstance(instance);
        setReady(true);
      }
    };
    void bootstrap();
  }, []);
  if (!ready || !msalInstance) return <div className="empty">Loading authentication...</div>;
  return <MsalProvider instance={msalInstance}><AuthState authConfigured={authConfigured} apiScope={resolvedApiScope}>{children}</AuthState></MsalProvider>;
}
export function useAuth() { const context = useContext(AuthContext); if (!context) throw new Error('useAuth must be used inside AuthProvider'); return context; }
