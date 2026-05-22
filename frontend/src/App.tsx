import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'
import Sidebar from './components/Sidebar'
import Login from './pages/Login'
import Meetings from './pages/Meetings'
import MeetingDetail from './pages/MeetingDetail'
import AdminCalendars from './pages/admin/AdminCalendars'
import AdminUsers from './pages/admin/AdminUsers'
import AdminMeetings from './pages/admin/AdminMeetings'

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
      <Routes>
        <Route path="/meetings" element={<Meetings />} />
        <Route path="/meetings/:id" element={<MeetingDetail />} />
        {user.is_admin && (
          <>
            <Route path="/admin/calendars" element={<AdminCalendars />} />
            <Route path="/admin/users" element={<AdminUsers />} />
            <Route path="/admin/meetings" element={<AdminMeetings />} />
          </>
        )}
        <Route path="*" element={<Navigate to="/meetings" replace />} />
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
