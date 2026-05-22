import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../hooks/useAuth'
import type { User } from '../../types'

export default function AdminUsers() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => { load() }, [])

  const load = () => {
    api.admin.users().then(r => setUsers(r.users)).finally(() => setLoading(false))
  }

  const toggleAdmin = async (u: User) => {
    if (u.id === me?.id) return
    await api.admin.setAdmin(u.id, !u.is_admin)
    setUsers(prev => prev.map(x => x.id === u.id ? { ...x, is_admin: !x.is_admin } : x))
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Пользователи</h1>
        <p className="page-subtitle">Управление доступом и ролями</p>
      </div>

      <div className="page-body">
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}>
            <span className="spinner" style={{ width: 28, height: 28 }} />
          </div>
        ) : (
          <div className="card" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                  {['Пользователь', 'Email', 'Последний вход', 'Роль'].map(h => (
                    <th key={h} style={{
                      padding: 'var(--space-3) var(--space-5)',
                      textAlign: 'left',
                      fontSize: 'var(--font-size-xs)',
                      fontWeight: 600,
                      color: 'var(--color-text-secondary)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.06em',
                      background: 'var(--color-surface-2)',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map((u, i) => (
                  <tr
                    key={u.id}
                    style={{
                      borderBottom: i < users.length - 1 ? '1px solid var(--color-border)' : 'none',
                    }}
                  >
                    <td style={{ padding: 'var(--space-4) var(--space-5)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
                        <div style={{
                          width: 32, height: 32, borderRadius: '50%',
                          background: 'var(--color-primary-light)',
                          color: 'var(--color-primary)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontWeight: 700, fontSize: 'var(--font-size-sm)', flexShrink: 0,
                        }}>
                          {u.name?.[0]?.toUpperCase() || '?'}
                        </div>
                        <span style={{ fontWeight: 500 }}>{u.name}</span>
                        {u.id === me?.id && (
                          <span style={{
                            fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)',
                            fontStyle: 'italic',
                          }}>(вы)</span>
                        )}
                      </div>
                    </td>
                    <td style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                      {u.email}
                    </td>
                    <td style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                      {/* last_login not in User type — fine */}
                      —
                    </td>
                    <td style={{ padding: 'var(--space-4) var(--space-5)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
                        <span style={{
                          fontSize: 'var(--font-size-xs)',
                          background: u.is_admin ? 'var(--color-accent-6)' : 'var(--color-surface-2)',
                          color: u.is_admin ? 'var(--color-accent)' : 'var(--color-text-secondary)',
                          padding: '2px 8px', borderRadius: 'var(--radius-full)', fontWeight: 600,
                        }}>
                          {u.is_admin ? 'Админ' : 'Пользователь'}
                        </span>
                        {u.id !== me?.id && (
                          <label className="toggle" title={u.is_admin ? 'Убрать роль админа' : 'Назначить админом'}>
                            <input
                              type="checkbox"
                              checked={u.is_admin}
                              onChange={() => toggleAdmin(u)}
                            />
                            <span className="toggle-slider" />
                          </label>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
