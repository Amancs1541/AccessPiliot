import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useAuth } from './auth';

interface SecuritySettings { blur_enabled: boolean; blur_after_minutes: number; lock_enabled: boolean; lock_after_minutes: number; }

const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'];
const CHECK_INTERVAL_MS = 1000;

// Lets the Security settings page tell an already-mounted IdleGuard "the settings just changed, re-fetch now" —
// without this, IdleGuard (which fetches once when the authenticated app first loads and stays mounted across
// every route change) would keep enforcing whatever was configured at that first load until a full page refresh,
// even though the Security page itself shows the freshly-saved values.
const SecuritySettingsRefreshContext = createContext<(() => void) | null>(null);
export function useRefreshSecuritySettings() {
  return useContext(SecuritySettingsRefreshContext);
}

// Wraps the normal authenticated app (Shell + Routes) for both Admin and end-user sessions. Tracks real user
// activity to show a dismissible blur after `blur_after_minutes`, and a click-to-resume lock screen after
// `lock_after_minutes` — independent toggles/timers, per the Security settings page. The lock is deliberately
// NOT dismissed by mere activity (only an explicit "Continue" click clears it) — it never signs the user out,
// the underlying session is untouched.
export function IdleGuard({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [settings, setSettings] = useState<SecuritySettings | null>(null);
  const [blurred, setBlurred] = useState(false);
  const [locked, setLocked] = useState(false);
  const lastActivity = useRef(Date.now());

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
    if (!settings || (!settings.blur_enabled && !settings.lock_enabled)) return;
    const markActive = () => {
      lastActivity.current = Date.now();
      setBlurred(false);
      // Deliberately does NOT clear `locked` here — the lock requires an explicit "Continue" click to dismiss.
    };
    ACTIVITY_EVENTS.forEach(eventName => window.addEventListener(eventName, markActive));
    const interval = setInterval(() => {
      const idleMs = Date.now() - lastActivity.current;
      if (settings.lock_enabled && idleMs >= settings.lock_after_minutes * 60000) setLocked(true);
      else if (settings.blur_enabled && idleMs >= settings.blur_after_minutes * 60000) setBlurred(true);
    }, CHECK_INTERVAL_MS);
    return () => {
      ACTIVITY_EVENTS.forEach(eventName => window.removeEventListener(eventName, markActive));
      clearInterval(interval);
    };
  }, [settings]);

  const resume = () => { lastActivity.current = Date.now(); setBlurred(false); setLocked(false); };

  return (
    <SecuritySettingsRefreshContext.Provider value={fetchSettings}>
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
    </SecuritySettingsRefreshContext.Provider>
  );
}
