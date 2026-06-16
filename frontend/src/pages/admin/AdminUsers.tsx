import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../hooks/useAuth'
import type { User } from '../../types'

export default function AdminUsers() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [previewLoading, setPreviewLoading] = useState(false)

  useEffect(() => { load() }, [])

  const load = async () => {
    setLoading(true)
    const usersData = await api.admin.users().catch(() => ({ users: [] as User[] }))
    setUsers(usersData.users)
    setLoading(false)
  }

  const toggleAdmin = async (u: User) => {
    if (u.id === me?.id) return
    await api.admin.setAdmin(u.id, !u.is_admin)
    setUsers(prev => prev.map(x => x.id === u.id ? { ...x, is_admin: !x.is_admin } : x))
  }

  const previewSelf = async () => {
    setPreviewLoading(true)
    try {
      const res = await api.admin.previewSelf()
      // Navigate to the activation URL — it sets a separate `preview` cookie and
      // lands on the dashboard in user mode. The admin session stays intact.
      window.location.href = res.url
    } catch (e: any) {
      alert(e.message || 'Ошибка')
      setPreviewLoading(false)
    }
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Пользователи</h1>
        <p className="page-subtitle">Управление доступом и ролями</p>
      </div>

      <div className="page-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>

        {/* ── Preview own account as a regular user ── */}
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontWeight: 'var(--font-weight-semibold)', marginBottom: 2 }}>Режим пользователя</div>
            <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
              Посмотрите своё приложение так, как его видит обычный пользователь (без прав администратора).
              Вернуться к админскому виду можно по кнопке в верхней плашке.
            </div>
          </div>
          <button className="btn btn-secondary" onClick={previewSelf} disabled={previewLoading}>
            {previewLoading ? 'Открываем…' : '👁 Посмотреть как пользователь'}
          </button>
        </div>

        {/* ── Users table ── */}
        <section>
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
        </section>

      </div>
    </div>
  )
}
