import { useState, type CSSProperties } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { isDemoOn, setDemo, clearDemo } from '../demo/demo'

const IconHome = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M3 9.5L10 3l7 6.5"/>
    <path d="M5 8.5V17h4v-4h2v4h4V8.5"/>
  </svg>
)
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
const IconStorage = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <ellipse cx="10" cy="5" rx="7" ry="2.5"/>
    <path d="M3 5v4c0 1.38 3.134 2.5 7 2.5S17 10.38 17 9V5"/>
    <path d="M3 9v4c0 1.38 3.134 2.5 7 2.5S17 14.38 17 13V9"/>
  </svg>
)
const IconCalls = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M4 3h3l1.5 4-2 1.5a11 11 0 005 5l1.5-2 4 1.5v3a1 1 0 01-1 1A14 14 0 013 4a1 1 0 011-1z"/>
  </svg>
)
const IconExtension = () => (
  <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M7 3h3v2.5a1.5 1.5 0 003 0V3h3v3h-2.5a1.5 1.5 0 000 3H17v3h-3v-2.5a1.5 1.5 0 00-3 0V15H7v-3H4.5a1.5 1.5 0 010-3H7V3z"/>
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
  const [menuOpen, setMenuOpen] = useState(false)
  const demoOn = isDemoOn()

  const toggleDemo = () => {
    setDemo(!demoOn)
    window.location.assign('/')   // reload at home so demo data loads fresh
  }
  const exitPreview = () => {
    clearDemo()
    window.location.href = '/api/auth/exit-preview'
  }

  const menuItemStyle: CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    width: '100%', gap: 8, padding: '8px 10px', background: 'none', border: 'none',
    borderRadius: 6, cursor: 'pointer', fontSize: 13, color: 'var(--color-text)',
    textAlign: 'left',
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <img src="/logo-white.svg" alt="Sifox" />
      </div>

      <nav className="sidebar-nav">
        <NavLink to="/" end className={({ isActive }) => isActive ? 'active' : ''}>
          <IconHome /> Главная
        </NavLink>
        <NavLink to="/meetings" className={({ isActive }) => isActive ? 'active' : ''}>
          <IconMeetings /> Встречи
        </NavLink>
        {demoOn && (
          <NavLink to="/calls" className={({ isActive }) => isActive ? 'active' : ''}>
            <IconCalls /> Звонки
          </NavLink>
        )}
        {!demoOn && (
          <NavLink to="/settings/extension" className={({ isActive }) => isActive ? 'active' : ''}>
            <IconExtension /> Запись в браузере
          </NavLink>
        )}

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
            <NavLink to="/admin/storage" className={({ isActive }) => isActive ? 'active' : ''}>
              <IconStorage /> Хранилище
            </NavLink>
          </>
        )}
      </nav>

      <div className="sidebar-footer" style={{ position: 'relative' }}>
        {/* Click the user block to open the account menu */}
        <button
          onClick={() => setMenuOpen(o => !o)}
          title="Аккаунт"
          style={{
            display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
            flex: 1, minWidth: 0, background: 'none', border: 'none',
            cursor: 'pointer', padding: 0, textAlign: 'left',
          }}
        >
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
        </button>

        {menuOpen && (
          <>
            <div
              onClick={() => setMenuOpen(false)}
              style={{ position: 'fixed', inset: 0, zIndex: 40 }}
            />
            <div style={{
              position: 'absolute', bottom: '100%', left: 0, right: 0, marginBottom: 8,
              background: 'var(--color-surface)', border: '1px solid var(--color-border)',
              borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
              padding: 6, zIndex: 50, display: 'flex', flexDirection: 'column', gap: 2,
            }}>
              {user?.is_preview && (
                <>
                  <button style={menuItemStyle} onClick={toggleDemo}>
                    <span>Режим демонстрации</span>
                    <span style={{
                      fontSize: 11, fontWeight: 700,
                      color: demoOn ? 'var(--color-accent)' : 'var(--color-text-muted)',
                    }}>{demoOn ? '● ВКЛ' : 'ВЫКЛ'}</span>
                  </button>
                  <button style={menuItemStyle} onClick={exitPreview}>
                    ← Вернуться к админскому виду
                  </button>
                  <div style={{ height: 1, background: 'var(--color-border)', margin: '4px 0' }} />
                </>
              )}
              <button style={menuItemStyle} onClick={logout}>
                <span>Выйти</span>
                <IconLogout />
              </button>
            </div>
          </>
        )}
      </div>
    </aside>
  )
}
