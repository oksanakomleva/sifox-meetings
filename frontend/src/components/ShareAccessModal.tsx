import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '../api/client'
import type { User } from '../types'

interface Props {
  meetingId: string
  initialVisibleToAll: boolean
  onClose: () => void
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 200,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
}
const panel: CSSProperties = {
  background: 'var(--color-surface)', border: '1px solid var(--color-border)',
  borderRadius: 'var(--radius-lg)', boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
  width: 'min(560px, 96vw)', maxHeight: '90vh', overflow: 'auto', padding: 'var(--space-6)',
  display: 'flex', flexDirection: 'column', gap: 'var(--space-5)',
}
const label: CSSProperties = {
  fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--color-text-secondary)',
  textTransform: 'uppercase', letterSpacing: '0.06em',
}
const field: CSSProperties = {
  padding: '8px 10px', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
  background: 'var(--color-surface-2)', color: 'var(--color-text)', fontSize: 'var(--font-size-sm)',
}

export default function ShareAccessModal({ meetingId, initialVisibleToAll, onClose }: Props) {
  const [visibleAll, setVisibleAll] = useState(initialVisibleToAll)
  const [users, setUsers] = useState<User[]>([])
  const [grantUserId, setGrantUserId] = useState<number | ''>('')
  const [granted, setGranted] = useState<string>('')
  const [password, setPassword] = useState('')
  const [shareUrl, setShareUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.admin.users().then(r => setUsers(r.users)).catch(() => {})
  }, [])

  const toggleAll = async () => {
    const v = !visibleAll
    setVisibleAll(v)
    try { await api.admin.setVisibleToAll(meetingId, v) }
    catch (e: any) { setVisibleAll(!v); setError(e.message) }
  }

  const grant = async () => {
    if (!grantUserId) return
    setBusy(true); setError('')
    try {
      await api.admin.grantAccess(Number(grantUserId), meetingId)
      const u = users.find(x => x.id === Number(grantUserId))
      setGranted(`Доступ выдан: ${u?.name || u?.email || grantUserId}`)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const createLink = async () => {
    if (password.length < 4) { setError('Пароль минимум 4 символа'); return }
    setBusy(true); setError('')
    try {
      const r = await api.admin.createShare(meetingId, password)
      setShareUrl(`${window.location.origin}${new URL(r.url).pathname}`)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const copy = (text: string) => navigator.clipboard?.writeText(text)

  return (
    <div style={overlay} onClick={onClose}>
      <div style={panel} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, margin: 0 }}>Доступ и публикация</h2>
          <button className="btn btn-ghost" onClick={onClose} style={{ padding: 4, height: 'auto' }}>✕</button>
        </div>
        {error && <div style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>}

        {/* Visible to all */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <div style={{ fontWeight: 600 }}>Видна всем пользователям</div>
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>
              Появится в «Мои встречи» у всех залогиненных пользователей.
            </div>
          </div>
          <label className="toggle">
            <input type="checkbox" checked={visibleAll} onChange={toggleAll} />
            <span className="toggle-slider" />
          </label>
        </div>

        {/* Grant to a specific user */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={label}>Выдать доступ пользователю</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <select style={{ ...field, flex: 1 }} value={grantUserId} onChange={e => setGrantUserId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">Выберите пользователя…</option>
              {users.map(u => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
            </select>
            <button className="btn btn-secondary" onClick={grant} disabled={busy || !grantUserId}>Выдать</button>
          </div>
          {granted && <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-success, #16a34a)' }}>{granted}</div>}
        </div>

        {/* Public link with password */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={label}>Публичная ссылка с паролем</span>
          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>
            Доступна всем (и без аккаунта) по ссылке с этим паролем.
          </div>
          {!shareUrl ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <input style={{ ...field, flex: 1 }} type="text" placeholder="Задайте пароль" value={password} onChange={e => setPassword(e.target.value)} />
              <button className="btn btn-primary" onClick={createLink} disabled={busy}>Создать</button>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input style={{ ...field, flex: 1 }} readOnly value={shareUrl} onFocus={e => e.currentTarget.select()} />
                <button className="btn btn-secondary" onClick={() => copy(shareUrl)}>Копировать ссылку</button>
              </div>
              <div style={{ fontSize: 'var(--font-size-sm)' }}>
                Пароль: <b>{password}</b> <button className="btn btn-ghost" style={{ padding: '0 6px', height: 'auto' }} onClick={() => copy(password)}>копировать</button>
              </div>
              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)' }}>
                Сохраните пароль — он не хранится в открытом виде и больше не покажется.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
