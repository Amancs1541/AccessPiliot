import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useAuth } from './auth';

interface SecuritySettings { blur_enabled: boolean; blur_after_minutes: number; lock_enabled: boolean; lock_after_minutes: number; logout_enabled: boolean; logout_after_minutes: number; timezone: string; }

const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'];
const CHECK_INTERVAL_MS = 1000;
// Fallback only for the brief window before the real setting has loaded (or if the fetch fails) — matches the
// model's own default, so the very first render before data arrives is never visibly wrong.
const DEFAULT_TIMEZONE = 'Europe/Berlin';

// Lets the Security settings page tell an already-mounted IdleGuard "the settings just changed, re-fetch now" —
// without this, IdleGuard (which fetches once when the authenticated app first loads and stays mounted across
// every route change) would keep enforcing whatever was configured at that first load until a full page refresh,
// even though the Security page itself shows the freshly-saved values. Also exposes the fetched `timezone` (and
// the rest of the settings) directly — this is the ONE place every signed-in user's browser already fetches
// GET /security-settings (open to any authenticated user, not just Admin), so every date/time display anywhere
// in the app reads the configured timezone from here instead of a separate fetch.
interface SecuritySettingsContextValue { settings: SecuritySettings | null; refresh: () => void; }
const SecuritySettingsContext = createContext<SecuritySettingsContextValue>({ settings: null, refresh: () => {} });
export function useSecuritySettingsContext() {
  return useContext(SecuritySettingsContext);
}
/** The tenant-wide configured display timezone (an IANA zone id, e.g. "Europe/Berlin") — falls back to the
 * model's own default before the real setting has loaded, never to each viewer's own browser-local timezone. */
export function useAppTimezone(): string {
  return useContext(SecuritySettingsContext).settings?.timezone ?? DEFAULT_TIMEZONE;
}
// Deprecated name kept as an alias so any not-yet-migrated call site keeps working — new code should call
// useSecuritySettingsContext().refresh instead.
export function useRefreshSecuritySettings() {
  return useContext(SecuritySettingsContext).refresh;
}

// Wraps the normal authenticated app (Shell + Routes) for both Admin and end-user sessions. Tracks real user
// activity to show a dismissible blur after `blur_after_minutes`, a click-to-resume lock screen after
// `lock_after_minutes`, and — a third, independent tier — an actual sign-out after `logout_after_minutes`. The
// lock is deliberately NOT dismissed by mere activity (only an explicit "Continue" click clears it) and never
// signs the user out; logout is the only tier that ends the session.
export function IdleGuard({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [settings, setSettings] = useState<SecuritySettings | null>(null);
  const [blurred, setBlurred] = useState(false);
  const [locked, setLocked] = useState(false);
  const lastActivity = useRef(Date.now());
  const loggedOut = useRef(false);

  const fetchSettings = useCallback(async () => {
    try {
      const response = await auth.apiRequest('/api/v1/security-settings');
      if (response.ok) setSettings(await response.json());
    } catch {
      // Fail open — if settings can't be loaded, no idle behavior is applied rather than guessing.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { void fetchSettings(); }, [fetchSettings]);

  useEffect(() => {
    if (!settings || (!settings.blur_enabled && !settings.lock_enabled && !settings.logout_enabled)) return;
    const markActive = () => {
      lastActivity.current = Date.now();
      setBlurred(false);
      // Deliberately does NOT clear `locked` here — the lock requires an explicit "Continue" click to dismiss.
    };
    ACTIVITY_EVENTS.forEach(eventName => window.addEventListener(eventName, markActive));
    const interval = setInterval(() => {
      const idleMs = Date.now() - lastActivity.current;
      if (settings.logout_enabled && idleMs >= settings.logout_after_minutes * 60000) {
        if (!loggedOut.current) {
          loggedOut.current = true;
          void auth.signOut();
        }
      } else if (settings.lock_enabled && idleMs >= settings.lock_after_minutes * 60000) {
        setLocked(true);
      } else if (settings.blur_enabled && idleMs >= settings.blur_after_minutes * 60000) {
        setBlurred(true);
      }
    }, CHECK_INTERVAL_MS);
    return () => {
      ACTIVITY_EVENTS.forEach(eventName => window.removeEventListener(eventName, markActive));
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const resume = () => { lastActivity.current = Date.now(); setBlurred(false); setLocked(false); };
  const contextValue = useMemo(() => ({ settings, refresh: fetchSettings }), [settings, fetchSettings]);

  return (
    <SecuritySettingsContext.Provider value={contextValue}>
      {children}
      {blurred && !locked && <div aria-hidden="true" style={{ position: 'fixed', inset: 0, backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)', background: 'rgba(23,33,43,0.12)', zIndex: 9998, pointerEvents: 'none' }} />}
      {locked && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(18,57,68,0.94)', backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 18, zIndex: 9999 }}>
          <span style={{ width: 52, height: 52, borderRadius: 14, background: 'rgba(255,255,255,0.12)', display: 'grid', placeItems: 'center', fontSize: 22 }}>🔒</span>
          <div style={{ color: '#fff', fontFamily: "'Space Grotesk', sans-serif", fontSize: 19, fontWeight: 700 }}>Session locked due to inactivity</div>
          <p style={{ color: '#bcd5d5', fontSize: 12, margin: 0 }}>You're still signed in — click below to continue.</p>
          <button type="button" className="btn btn-primary" onClick={resume}>Continue</button>
        </div>
      )}
    </SecuritySettingsContext.Provider>
  );
}
