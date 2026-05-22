import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

const IconMeetings = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="2" y="4" width="16" height="13" rx="2"/>
    <path d="M7 4V2M13 4V2"/>
    <path d="M2 8h16"/>
  </svg>
)
const IconAdmin = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <circle cx="10" cy="7" r="3.5"/>
    <path d="M2 17c0-3.314 3.582-6 8-6s8 2.686 8 6"/>
  </svg>
)
const IconCalendar = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="2" y="4" width="16" height="14" rx="2"/>
    <path d="M7 2v4M13 2v4M2 9h16"/>
  </svg>
)
const IconLogout = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M13 15l4-5-4-5"/>
    <path d="M17 10H8"/>
    <path d="M8 3H4a1 1 0 00-1 1v12a1 1 0 001 1h4"/>
  </svg>
)

export default function Sidebar() {
  const { user, logout } = useAuth()

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <img src="/logo-white.svg" alt="Sifox" />
      </div>

      <nav className="sidebar-nav">
        <NavLink to="/meetings" className={({ isActive }) => isActive ? 'active' : ''}>
          <IconMeetings /> Встречи
        </NavLink>

        {user?.is_admin && (
          <>
            <span className="sidebar-section-label">Администратор</span>
            <NavLink to="/admin/calendars" className={({ isActive }) => isActive ? 'active' : ''}>
              <IconCalendar /> Календари
            </NavLink>
            <NavLink to="/admin/users" className={({ isActive }) => isActive ? 'active' : ''}>
              <IconAdmin /> Пользователи
            </NavLink>
            <NavLink to="/admin/meetings" className={({ isActive }) => isActive ? 'active' : ''}>
              <IconMeetings /> Все встречи
            </NavLink>
          </>
        )}
      </nav>

      <div className="sidebar-footer">
        {user?.avatar_url ? (
          <img src={user.avatar_url} alt={user.name} className="sidebar-avatar" />
        ) : (
          <div className="sidebar-avatar" style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '14px', fontWeight: 600, color: '#fff',
            background: 'rgba(255,255,255,0.15)',
          }}>
            {user?.name?.[0]?.toUpperCase() || '?'}
          </div>
        )}
        <div className="sidebar-user-info">
          <div className="sidebar-user-name">{user?.name}</div>
          <div className="sidebar-user-email">{user?.email}</div>
        </div>
        <button
          onClick={logout}
          title="Выйти"
          style={{ color: 'var(--color-sidebar-muted)', padding: '4px' }}
        >
          <IconLogout />
        </button>
      </div>
    </aside>
  )
}
