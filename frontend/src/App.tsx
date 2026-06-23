import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'
import { clearDemo } from './demo/demo'
import Sidebar from './components/Sidebar'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Meetings from './pages/Meetings'
import MeetingDetail from './pages/MeetingDetail'
import ShareView from './pages/ShareView'
import CallsFeed from './pages/demo/CallsFeed'
import CallDetail from './pages/demo/CallDetail'
import AdminCalendars from './pages/admin/AdminCalendars'
import AdminUsers from './pages/admin/AdminUsers'
import AdminMeetings from './pages/admin/AdminMeetings'
import AdminStorage from './pages/admin/AdminStorage'
import Communications from './pages/admin/Communications'
import ExtensionTokens from './pages/settings/ExtensionTokens'

function ProtectedLayout() {
  const { user, loading } = useAuth()

  // Demo mode is only meaningful inside preview; never let it linger in the real
  // admin/user view.
  useEffect(() => {
    if (user && !user.is_preview) clearDemo()
  }, [user])

  if (loading) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--color-bg)',
      }}>
        <span className="spinner" style={{ width: 36, height: 36, borderWidth: 3 }} />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return (
    <div className="app-layout">
      <Sidebar />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/meetings" element={<Meetings />} />
        <Route path="/meetings/:id" element={<MeetingDetail />} />
        {/* Demo-only "Calls" section (components redirect out when demo is off) */}
        <Route path="/calls" element={<CallsFeed />} />
        <Route path="/calls/:id" element={<CallDetail />} />
        <Route path="/settings/extension" element={<ExtensionTokens />} />
        {user.is_admin && (
          <>
            <Route path="/admin/calendars" element={<AdminCalendars />} />
            <Route path="/admin/users" element={<AdminUsers />} />
            <Route path="/admin/meetings" element={<AdminMeetings />} />
            <Route path="/admin/communications" element={<Communications />} />
            <Route path="/admin/storage" element={<AdminStorage />} />
          </>
        )}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          {/* Public, password-gated meeting view — outside the auth guard */}
          <Route path="/share/:token" element={<ShareView />} />
          <Route path="/*" element={<ProtectedLayout />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
