import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../hooks/useAuth'
import type { User, Invitation } from '../../types'

export default function AdminUsers() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [loading, setLoading] = useState(true)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviting, setInviting] = useState(false)
  const [newInviteUrl, setNewInviteUrl] = useState<string | null>(null)
  const [copyDone, setCopyDone] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  useEffect(() => { load() }, [])

  const load = async () => {
    setLoading(true)
    const [usersData, invData] = await Promise.all([
      api.admin.users().catch(() => ({ users: [] as User[] })),
      api.admin.listInvitations().catch(() => ({ invitations: [] as Invitation[] })),
    ])
    setUsers(usersData.users)
    setInvitations(invData.invitations)
    setLoading(false)
  }

  const toggleAdmin = async (u: User) => {
    if (u.id === me?.id) return
    await api.admin.setAdmin(u.id, !u.is_admin)
    setUsers(prev => prev.map(x => x.id === u.id ? { ...x, is_admin: !x.is_admin } : x))
  }

  const createInvite = async () => {
    if (!inviteEmail.trim() || inviting) return
    setInviting(true)
    setNewInviteUrl(null)
    try {
      const res = await api.admin.createInvitation(inviteEmail.trim())
      setNewInviteUrl(res.invitation.url ?? null)
      setInviteEmail('')
      setInvitations(prev => [res.invitation, ...prev])
    } catch (e: any) {
      alert(e.message || 'Ошибка создания приглашения')
    } finally {
      setInviting(false)
    }
  }

  const deleteInvite = async (id: number) => {
    await api.admin.deleteInvitation(id)
    setInvitations(prev => prev.filter(i => i.id !== id))
    if (newInviteUrl) setNewInviteUrl(null)
  }

  const copyUrl = (url: string) => {
    navigator.clipboard.writeText(url).then(() => {
      setCopyDone(true)
      setTimeout(() => setCopyDone(false), 2000)
    })
  }

  const fmtDate = (iso: string) =>
    new Date(iso).toLocaleString('ru-RU', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    })

  const createPreview = async () => {
    setPreviewLoading(true)
    setPreviewUrl(null)
    try {
      const res = await api.admin.createPreviewSession()
      setPreviewUrl(res.url)
    } catch (e: any) {
      alert(e.message || 'Ошибка')
    } finally {
      setPreviewLoading(false)
    }
  }

  const pendingInvites = invitations.filter(i => !i.accepted_at)
  const acceptedInvites = invitations.filter(i => !!i.accepted_at)

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Пользователи</h1>
        <p className="page-subtitle">Управление доступом и ролями</p>
      </div>

      <div className="page-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>

        {/* ── Preview as regular user ── */}
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontWeight: 'var(--font-weight-semibold)', marginBottom: 2 }}>Предпросмотр как пользователь</div>
            <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
              Откройте ссылку в инкогнито — увидите интерфейс без прав администратора
            </div>
          </div>
          <button className="btn btn-secondary" onClick={createPreview} disabled={previewLoading}>
            {previewLoading ? 'Создаём…' : '👁 Открыть как пользователь'}
          </button>
          {previewUrl && (
            <div style={{
              width: '100%', display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
              background: 'var(--color-accent-6)', borderRadius: 'var(--radius-md)',
              padding: 'var(--space-3) var(--space-4)', marginTop: 'var(--space-2)',
            }}>
              <span style={{ flex: 1, fontSize: 'var(--font-size-sm)', wordBreak: 'break-all', color: 'var(--color-accent)' }}>
                {previewUrl}
              </span>
              <button className="btn btn-secondary" style={{ flexShrink: 0 }}
                onClick={() => navigator.clipboard.writeText(previewUrl)}>
                Скопировать
              </button>
              <a href={previewUrl} target="_blank" rel="noopener noreferrer"
                className="btn btn-primary" style={{ flexShrink: 0 }}>
                Открыть
              </a>
            </div>
          )}
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

        {/* ── Invite new user ── */}
        <section>
          <h2 style={{
            fontSize: 'var(--font-size-lg)',
            fontWeight: 'var(--font-weight-semibold)',
            marginBottom: 'var(--space-4)',
            color: 'var(--color-text)',
          }}>
            Пригласить пользователя
          </h2>

          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)', margin: 0 }}>
              Создайте ссылку-приглашение для любого email. Пользователь перейдёт по ссылке и войдёт через Google.
            </p>

            {/* Input row */}
            <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-end' }}>
              <div style={{ flex: 1 }}>
                <label style={{
                  display: 'block', fontSize: 'var(--font-size-sm)',
                  fontWeight: 500, marginBottom: 'var(--space-1)',
                  color: 'var(--color-text-secondary)',
                }}>
                  Email
                </label>
                <input
                  className="input"
                  type="email"
                  placeholder="user@example.com"
                  value={inviteEmail}
                  onChange={e => setInviteEmail(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && createInvite()}
                  disabled={inviting}
                />
              </div>
              <button
                className="btn btn-primary"
                onClick={createInvite}
                disabled={inviting || !inviteEmail.trim()}
                style={{ flexShrink: 0 }}
              >
                {inviting
                  ? <span className="spinner" style={{ width: 16, height: 16 }} />
                  : 'Создать ссылку'}
              </button>
            </div>

            {/* New invite link */}
            {newInviteUrl && (
              <div style={{
                background: 'var(--color-accent-6)',
                border: '1px solid var(--color-accent-5)',
                borderRadius: 'var(--radius-md)',
                padding: 'var(--space-4)',
                display: 'flex',
                flexDirection: 'column',
                gap: 'var(--space-2)',
              }}>
                <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--color-accent)' }}>
                  Ссылка готова — скопируйте и отправьте пользователю:
                </div>
                <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
                  <input
                    className="input"
                    readOnly
                    value={newInviteUrl}
                    style={{ flex: 1, fontSize: 'var(--font-size-xs)', fontFamily: 'monospace' }}
                    onClick={e => (e.target as HTMLInputElement).select()}
                  />
                  <button
                    className="btn btn-secondary"
                    onClick={() => copyUrl(newInviteUrl)}
                    style={{ flexShrink: 0 }}
                  >
                    {copyDone ? '✓ Скопировано' : 'Копировать'}
                  </button>
                </div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>
                  Ссылка действительна 7 дней. Войти можно только с указанного email.
                </div>
              </div>
            )}
          </div>
        </section>

        {/* ── Pending invitations ── */}
        {pendingInvites.length > 0 && (
          <section>
            <h2 style={{
              fontSize: 'var(--font-size-lg)',
              fontWeight: 'var(--font-weight-semibold)',
              marginBottom: 'var(--space-4)',
              color: 'var(--color-text)',
            }}>
              Ожидают принятия ({pendingInvites.length})
            </h2>
            <div className="card" style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                    {['Email', 'Создано', 'Истекает', ''].map(h => (
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
                  {pendingInvites.map((inv, i) => (
                    <tr key={inv.id} style={{
                      borderBottom: i < pendingInvites.length - 1 ? '1px solid var(--color-border)' : 'none',
                    }}>
                      <td style={{ padding: 'var(--space-4) var(--space-5)', fontWeight: 500 }}>
                        {inv.email}
                      </td>
                      <td style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                        {fmtDate(inv.created_at)}
                        {inv.created_by_name && <> · {inv.created_by_name}</>}
                      </td>
                      <td style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                        {fmtDate(inv.expires_at)}
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-5)', textAlign: 'right' }}>
                        <div style={{ display: 'flex', gap: 'var(--space-2)', justifyContent: 'flex-end' }}>
                          <button
                            className="btn btn-secondary"
                            style={{ fontSize: 'var(--font-size-xs)', padding: '4px 10px' }}
                            onClick={() => copyUrl(`${window.location.origin}/api/auth/invite/${inv.token}`)}
                          >
                            Копировать
                          </button>
                          <button
                            className="btn btn-secondary"
                            style={{ fontSize: 'var(--font-size-xs)', padding: '4px 10px', color: 'var(--color-error)' }}
                            onClick={() => deleteInvite(inv.id)}
                          >
                            Отозвать
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* ── Accepted invitations (recent) ── */}
        {acceptedInvites.length > 0 && (
          <section>
            <h2 style={{
              fontSize: 'var(--font-size-lg)',
              fontWeight: 'var(--font-weight-semibold)',
              marginBottom: 'var(--space-4)',
              color: 'var(--color-text)',
            }}>
              Принятые приглашения
            </h2>
            <div className="card" style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                    {['Email', 'Принято', 'Создано'].map(h => (
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
                  {acceptedInvites.slice(0, 20).map((inv, i) => (
                    <tr key={inv.id} style={{
                      borderBottom: i < Math.min(acceptedInvites.length, 20) - 1 ? '1px solid var(--color-border)' : 'none',
                    }}>
                      <td style={{ padding: 'var(--space-4) var(--space-5)', fontWeight: 500 }}>
                        {inv.email}
                      </td>
                      <td style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                        {inv.accepted_at ? fmtDate(inv.accepted_at) : '—'}
                      </td>
                      <td style={{ padding: 'var(--space-4) var(--space-5)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                        {fmtDate(inv.created_at)}
                        {inv.created_by_name && <> · {inv.created_by_name}</>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

      </div>
    </div>
  )
}
