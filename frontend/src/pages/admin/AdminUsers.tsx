import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../hooks/useAuth'
import type { Invitation, User } from '../../types'

export default function AdminUsers() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteLoading, setInviteLoading] = useState(false)
  const [inviteError, setInviteError] = useState('')
  const [inviteSuccess, setInviteSuccess] = useState('')
  const [loading, setLoading] = useState(true)
  const [previewLoading, setPreviewLoading] = useState(false)

  useEffect(() => { load() }, [])

  const load = async () => {
    setLoading(true)
    try {
      const [usersData, invitationsData] = await Promise.all([api.admin.users(), api.admin.listInvitations()])
      setUsers(usersData.users)
      setInvitations(invitationsData.invitations)
    } catch (e: any) {
      setInviteError(e.message || 'Не удалось загрузить пользователей')
    }
    setLoading(false)
  }

  const sendInvite = async (email: string) => {
    setInviteLoading(true)
    setInviteError('')
    setInviteSuccess('')
    try {
      await api.admin.createInvitation(email)
      setInviteSuccess(`Приглашение отправлено на ${email}`)
      setInviteEmail('')
      await load()
    } catch (e: any) {
      setInviteError(e.message || 'Не удалось отправить приглашение')
      await load()
    } finally {
      setInviteLoading(false)
    }
  }

  const invite = async (event: React.FormEvent) => {
    event.preventDefault()
    await sendInvite(inviteEmail.trim())
  }

  const pending = invitations.filter((inv, index) =>
    !inv.accepted_at &&
    !users.some(u => u.email.toLowerCase() === inv.email.toLowerCase()) &&
    invitations.findIndex(x => x.email.toLowerCase() === inv.email.toLowerCase()) === index,
  )

  const invitationStatus = (inv: Invitation) => {
    if (new Date(inv.expires_at).getTime() <= Date.now()) return 'Срок приглашения истёк'
    if (!inv.sent_at) return 'Письмо не отправлено'
    return 'Ожидает первого входа'
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

        <form className="card" onSubmit={invite} style={{ display: 'flex', alignItems: 'end', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 220 }}>
            <label htmlFor="invite-email" style={{ display: 'block', fontWeight: 600, marginBottom: 6 }}>Пригласить пользователя</label>
            <input id="invite-email" className="input" type="email" required value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} placeholder="name@example.com" style={{ width: '100%' }} />
          </div>
          <button className="btn btn-primary" type="submit" disabled={inviteLoading}>{inviteLoading ? 'Отправляем…' : 'Отправить приглашение'}</button>
          {inviteError && <div role="alert" style={{ width: '100%', color: 'var(--color-danger)' }}>
            {inviteError}
            {inviteError.includes('Gmail') && <a href={api.auth.connectGmailSendUrl('/admin/users')} style={{ marginLeft: 8 }}>Подключить Gmail</a>}
          </div>}
          {inviteSuccess && <div role="status" style={{ width: '100%', color: 'var(--color-success)' }}>{inviteSuccess}</div>}
        </form>

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
            <div className="card table-scroll mobile-table-container" style={{ padding: 0 }}>
              <table className="mobile-card-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                    {['Пользователь', 'Email', 'Статус', 'Последний вход', 'Роль'].map(h => (
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
                      <td data-label="Пользователь" style={{ padding: 'var(--space-4) var(--space-5)' }}>
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
                      <td data-label="Email" style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                        {u.email}
                      </td>
                      <td data-label="Статус" style={{ padding: 'var(--space-4) var(--space-5)' }}>{u.is_active === false ? 'Отключён' : 'Активен'}</td>
                      <td data-label="Последний вход" style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                        {u.last_login ? new Date(u.last_login).toLocaleString('ru-RU') : '—'}
                      </td>
                      <td data-label="Роль" style={{ padding: 'var(--space-4) var(--space-5)' }}>
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
                  {pending.map(inv => (
                    <tr key={`invite-${inv.id}`} style={{ borderTop: '1px solid var(--color-border)' }}>
                      <td data-label="Пользователь" style={{ padding: 'var(--space-4) var(--space-5)' }}>Ожидает входа</td>
                      <td data-label="Email" style={{ padding: 'var(--space-4) var(--space-5)' }}>{inv.email}</td>
                      <td data-label="Статус" style={{ padding: 'var(--space-4) var(--space-5)' }}>{invitationStatus(inv)}</td>
                      <td data-label="Последний вход" style={{ padding: 'var(--space-4) var(--space-5)' }}>—</td>
                      <td data-label="Роль" style={{ padding: 'var(--space-4) var(--space-5)' }}>
                        <button className="btn btn-secondary" disabled={inviteLoading} onClick={() => sendInvite(inv.email)}>Отправить повторно</button>
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
