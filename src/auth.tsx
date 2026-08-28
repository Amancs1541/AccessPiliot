import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { PublicClientApplication, type AccountInfo, type AuthenticationResult, type Configuration } from '@azure/msal-browser';
import { MsalProvider, useIsAuthenticated, useMsal } from '@azure/msal-react';

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID as string | undefined;
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID as string | undefined;
const redirectUri = import.meta.env.VITE_ENTRA_REDIRECT_URI as string | undefined;
const apiScope = import.meta.env.VITE_ACCESSPILOT_API_SCOPE as string | undefined;
export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://localhost:8000';
export const entraConfigured = Boolean(tenantId && clientId && redirectUri && apiScope);
const msalConfig: Configuration = { auth: { clientId: clientId || 'unconfigured', authority: tenantId ? `https://login.microsoftonline.com/${tenantId}` : undefined, redirectUri: redirectUri || window.location.origin }, cache: { cacheLocation: 'sessionStorage' } };
const msal = new PublicClientApplication(msalConfig);

type AppRole = 'user' | 'admin';
interface AuthContextValue { role: AppRole; account: AccountInfo | null; loading: boolean; signIn: () => Promise<void>; signOut: () => Promise<void>; apiRequest: (path: string, init?: RequestInit) => Promise<Response>; }
const AuthContext = createContext<AuthContextValue | null>(null);

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

function AuthState({ children }: { children: ReactNode }) {
  const { instance, accounts, inProgress } = useMsal(); const authenticated = useIsAuthenticated();
  // apiLoading MUST default to true, not false: it starts false-to-true only inside the effect below, which runs
  // AFTER the first render commits. With a false default, that first render already has `role` at its initial
  // 'user' value AND `loading` already false (once MSAL's own inProgress has settled to 'none', which it already
  // has by the time this component mounts) — so the app briefly (or, if the /me call is slow, not-so-briefly)
  // renders the end-user panel for an Admin before the real role check catches up. Defaulting to true keeps the
  // app on the loading screen until the effect has actually determined a role one way or the other.
  const [role, setRole] = useState<AppRole>('user'); const [account, setAccount] = useState<AccountInfo | null>(accounts[0] || null); const [apiLoading, setApiLoading] = useState(true);
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

    let ignore = false;
    const controller = new AbortController();
    const loadProfile = async () => {
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
        authDebug('Requested API scope:', apiScope);
        authDebug('Calling acquireTokenSilent().');

        const result: AuthenticationResult = await instance.acquireTokenSilent({ account: current, scopes: [apiScope] });
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
        const response = await fetch(`${apiBaseUrl}/api/v1/me`, { headers: { Authorization: `Bearer ${result.accessToken}` }, signal: controller.signal });
        if (ignore) return;
        let profile: Record<string, unknown> | null = null;
        try { profile = await response.json() as Record<string, unknown>; } catch { /* Response is not JSON; status is logged below. */ }
        authDebug('API /me STATUS:', response.status);
        authDebug('API /me PROFILE:', profile);
        authDebug('API /me PROFILE ROLES:', profile?.roles);
        if (authDebugEnabled) {
          console.groupEnd();
          apiDebugGroupOpen = false;
        }

        const nextRole: AppRole = Array.isArray(profile?.roles) && profile.roles.includes(adminAppRole) ? 'admin' : 'user';
        setRole(nextRole);
        if (authDebugEnabled) console.group('ACCESSPILOT AUTH STATE DEBUG — TEMPORARY DEVELOPMENT LOGGING');
        authDebug('FINAL FRONTEND ROLE:', nextRole);
        authDebug('Final authenticated state:', authenticated);
        if (authDebugEnabled) console.groupEnd();
      } catch (error) {
        if (ignore) return;
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setRole('user');
        if (authDebugEnabled && apiDebugGroupOpen) console.groupEnd();
        if (authDebugEnabled && tokenDebugGroupOpen) console.groupEnd();
        if (authDebugEnabled) console.group('ACCESSPILOT TOKEN DEBUG — TEMPORARY DEVELOPMENT LOGGING');
        authDebug('Token successfully acquired:', false);
        authDebug('acquireTokenSilent() or API request error:', authDebugError(error));
        if (authDebugEnabled) console.groupEnd();
        if (authDebugEnabled) console.group('ACCESSPILOT AUTH STATE DEBUG — TEMPORARY DEVELOPMENT LOGGING');
        authDebug('FINAL FRONTEND ROLE:', 'user');
        authDebug('Final authenticated state:', authenticated);
        if (authDebugEnabled) console.groupEnd();
      } finally {
        if (!ignore) setApiLoading(false);
      }
    };

    void loadProfile();
    return () => { ignore = true; controller.abort(); };
  }, [accounts, apiScope, instance]);
  const signIn = async () => {
    if (authDebugEnabled) console.group('ACCESSPILOT LOGIN DEBUG — TEMPORARY DEVELOPMENT LOGGING');
    authDebug('loginRedirect() invoked by the sign-in button.');
    authDebug('Entra configured:', entraConfigured);
    authDebug('Requested API scope:', apiScope);
    try {
      if (!entraConfigured) return;
      await instance.loginRedirect({ scopes: [apiScope!] });
      authDebug('loginRedirect() returned without an immediate error.');
    } catch (error) {
      authDebug('loginRedirect() error:', authDebugError(error));
    } finally {
      if (authDebugEnabled) console.groupEnd();
    }
  };
  const signOut = async () => { if (entraConfigured) await instance.logoutRedirect(); };
  const apiRequest = async (path: string, init: RequestInit = {}) => { const headers = new Headers(init.headers); headers.set('Content-Type', 'application/json'); if (entraConfigured) { if (!accounts[0]) throw new Error('AUTHENTICATION_REQUIRED'); const result = await instance.acquireTokenSilent({ account: accounts[0], scopes: [apiScope!] }); headers.set('Authorization', `Bearer ${result.accessToken}`); } return fetch(`${apiBaseUrl}${path}`, { ...init, headers }); };
  return <AuthContext.Provider value={{ role: authenticated ? role : 'user', account, loading: inProgress !== 'none' || apiLoading, signIn, signOut, apiRequest }}>{children}</AuthContext.Provider>;
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const bootstrap = async () => {
      if (authDebugEnabled) console.group('ACCESSPILOT LOGIN DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      try {
        authDebug('Calling msal.initialize().');
        await msal.initialize();
        authDebug('msal.initialize() completed.');
      } catch (error) {
        authDebug('msal.initialize() error:', authDebugError(error));
      } finally {
        if (authDebugEnabled) console.groupEnd();
      }

      if (authDebugEnabled) console.group('ACCESSPILOT REDIRECT DEBUG — TEMPORARY DEVELOPMENT LOGGING');
      try {
        authDebug('Calling msal.handleRedirectPromise().');
        const redirectResult = await msal.handleRedirectPromise();
        authDebug('handleRedirectPromise() returned an AuthenticationResult:', Boolean(redirectResult));
        authDebug('Accounts after redirect processing:', msal.getAllAccounts().map(safeAccount));
      } catch (error) {
        authDebug('handleRedirectPromise() error:', authDebugError(error));
        authDebug('Accounts after redirect-processing error:', msal.getAllAccounts().map(safeAccount));
      } finally {
        if (authDebugEnabled) console.groupEnd();
        setReady(true);
      }
    };
    void bootstrap();
  }, []);
  return ready ? <MsalProvider instance={msal}><AuthState>{children}</AuthState></MsalProvider> : <div className="empty">Loading authentication...</div>;
}
export function useAuth() { const context = useContext(AuthContext); if (!context) throw new Error('useAuth must be used inside AuthProvider'); return context; }
