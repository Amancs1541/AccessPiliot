import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './auth';
import { SetupGate } from './SetupWizard';
import { EmergencyAccessPage } from './EmergencyAccess';
import './styles.css';

// The hidden emergency-access route is split out at the very top, before SetupGate/AuthProvider ever mount, so
// it can never be intercepted by the normal sign-in screen (there's no account/Break-Glass token yet at that
// point) — see src/EmergencyAccess.tsx for why it deliberately renders outside AuthProvider entirely.
function Root() {
  return (
    <Routes>
      <Route path="/emergency-access/:token" element={<EmergencyAccessPage />} />
      <Route path="*" element={<SetupGate><AuthProvider><App /></AuthProvider></SetupGate>} />
    </Routes>
  );
}

createRoot(document.getElementById('root')!).render(<StrictMode><BrowserRouter><Root /></BrowserRouter></StrictMode>);
