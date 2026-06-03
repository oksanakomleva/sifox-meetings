import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'
import Sidebar from './components/Sidebar'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Meetings from './pages/Meetings'
import MeetingDetail from './pages/MeetingDetail'
import AdminCalendars from './pages/admin/AdminCalendars'
import AdminUsers from './pages/admin/AdminUsers'
import AdminMeetings from './pages/admin/AdminMeetings'
import AdminStorage from './pages/admin/AdminStorage'
import ExtensionTokens from './pages/settings/ExtensionTokens'

function ProtectedLayout() {
  const { user, loading } = useAuth()

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
      {user.is_preview && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 9999,
          background: '#f59e0b', color: '#1c1917',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          gap: '1rem', padding: '8px 16px',
          fontSize: '14px', fontWeight: 600,
        }}>
          👁 Режим пользователя — вы видите приложение как обычный пользователь
          <button
            onClick={() => { window.location.href = '/api/auth/exit-preview' }}
            style={{
              background: 'rgba(0,0,0,0.15)', border: 'none', borderRadius: 6,
              padding: '4px 12px', cursor: 'pointer', fontWeight: 700,
              color: '#1c1917', fontSize: '13px',
            }}
          >
            ← Вернуться к админскому виду
          </button>
        </div>
      )}
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/meetings" element={<Meetings />} />
        <Route path="/meetings/:id" element={<MeetingDetail />} />
        <Route path="/settings/extension" element={<ExtensionTokens />} />
        {user.is_admin && (
          <>
            <Route path="/admin/calendars" element={<AdminCalendars />} />
            <Route path="/admin/users" element={<AdminUsers />} />
            <Route path="/admin/meetings" element={<AdminMeetings />} />
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
          <Route path="/*" element={<ProtectedLayout />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
