import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '../api/client'
import type { MeetingAccessUser, MeetingShareLink } from '../types'

interface Props {
  meetingId: string
  initialVisibleToAll: boolean
  onVisibilityChange: (value: boolean) => void
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

export default function ShareAccessModal({ meetingId, initialVisibleToAll, onVisibilityChange, onClose }: Props) {
  const [visibleAll, setVisibleAll] = useState(initialVisibleToAll)
  const [users, setUsers] = useState<MeetingAccessUser[]>([])
  const [selected, setSelected] = useState<number[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [links, setLinks] = useState<MeetingShareLink[]>([])
  const [granted, setGranted] = useState<string>('')
  const [password, setPassword] = useState('')
  const [shareUrl, setShareUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([api.meetings.access(meetingId), api.meetings.shares(meetingId)])
      .then(([access, published]) => {
        if (cancelled) return
        setUsers(access.users); setVisibleAll(access.visible_to_all); setLinks(published.shares)
      })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [meetingId])

  const refreshAccess = async () => {
    const r = await api.meetings.access(meetingId)
    setUsers(r.users); setVisibleAll(r.visible_to_all)
  }

  const toggleAll = async () => {
    const v = !visibleAll
    setBusy(true); setError(''); setGranted('')
    try {
      await api.meetings.setVisibleToAll(meetingId, v)
      setVisibleAll(v); onVisibilityChange(v); setSelected([])
      await refreshAccess()
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const grant = async () => {
    if (!selected.length) return
    setBusy(true); setError(''); setGranted('')
    try {
      await api.meetings.grantAccess(meetingId, selected)
      setUsers(current => current.map(u => selected.includes(u.id) ? { ...u, explicit_grant: true } : u))
      setGranted(`Доступ выдан выбранным коллегам: ${selected.length}`)
      setSelected([])
      await refreshAccess()
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const createLink = async () => {
    if (password.length < 4) { setError('Пароль минимум 4 символа'); return }
    setBusy(true); setError('')
    try {
      const r = await api.meetings.createShare(meetingId, password)
      setShareUrl(`${window.location.origin}${new URL(r.url).pathname}`)
      setLinks((await api.meetings.shares(meetingId)).shares)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const revokeLink = async (token: string) => {
    setBusy(true); setError('')
    try {
      await api.meetings.revokeShare(meetingId, token)
      setLinks(current => current.filter(link => link.token !== token))
      if (shareUrl.endsWith(`/share/${token}`)) { setShareUrl(''); setPassword('') }
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text) }
    catch { setError('Не удалось скопировать. Выделите ссылку и скопируйте вручную.') }
  }
  const already = users.filter(u => u.has_access || u.explicit_grant)
  const candidates = users.filter(u => u.is_active && !u.has_access && !u.explicit_grant
    && `${u.name || ''} ${u.email}`.toLowerCase().includes(search.toLowerCase()))

  return (
    <div style={overlay} onClick={onClose}>
      <div style={panel} role="dialog" aria-modal="true" aria-labelledby="access-title" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 id="access-title" style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, margin: 0 }}>Доступ и публикация</h2>
          <button className="btn btn-ghost" aria-label="Закрыть" onClick={onClose} style={{ padding: 4, height: 'auto' }}>✕</button>
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
            <input type="checkbox" aria-label="Видна всем пользователям" checked={visibleAll} disabled={busy || loading} onChange={toggleAll} />
            <span className="toggle-slider" />
          </label>
        </div>

        {/* Named access is available to every non-preview meeting viewer. */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={label}>Дать доступ коллегам</span>
          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>
            Выберите одного или нескольких пользователей Sifox. Они смогут читать встречу и делиться ею дальше.
          </div>
          <input style={field} aria-label="Поиск коллег" placeholder="Имя или почта" value={search} onChange={e => setSearch(e.target.value)} />
          {loading ? <span role="status">Загружаем список…</span> : (
            <div style={{ maxHeight: 190, overflowY: 'auto', display: 'grid', gap: 8 }}>
              {candidates.map(u => (
                <label key={u.id} style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <input type="checkbox" checked={selected.includes(u.id)} disabled={busy || (!selected.includes(u.id) && selected.length >= 100)}
                    onChange={e => setSelected(current => e.target.checked ? [...current, u.id] : current.filter(id => id !== u.id))} />
                  <span>{u.name || u.email}{u.name && <small style={{ display: 'block', color: 'var(--color-text-secondary)' }}>{u.email}</small>}</span>
                </label>
              ))}
              {!candidates.length && <span>Нет коллег без доступа, подходящих под поиск.</span>}
            </div>
          )}
          <button className="btn btn-secondary" onClick={grant} disabled={busy || loading || !selected.length}>
            Выдать доступ{selected.length > 0 ? ` (${selected.length})` : ''}
          </button>
          {granted && <div role="status" style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-success, #16a34a)' }}>{granted}</div>}
        </div>

        <div style={{ display: 'grid', gap: 8 }}>
          <span style={label}>Кому уже доступно</span>
          {visibleAll && <span>Всем пользователям Sifox.</span>}
          <div style={{ maxHeight: 190, overflowY: 'auto', display: 'grid', gap: 8 }}>
            {already.map(u => <div key={u.id}>
              <div>{u.name || u.email}{u.name && <small> · {u.email}</small>}</div>
              <small style={{ color: 'var(--color-text-secondary)' }}>
                {u.explicit_grant ? 'Доступ выдан вручную' : 'Доступ уже есть по правилам встречи'}
                {!u.is_active ? ' · аккаунт отключён' : !u.has_access ? ' · после обработки встречи' : ''}
              </small>
            </div>)}
          </div>
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
              <button className="btn btn-primary" onClick={createLink} disabled={busy || loading}>Создать</button>
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
          {links.length > 0 && <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
            <span style={label}>Опубликованные ссылки</span>
            {links.map(link => <div key={link.token} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input style={{ ...field, flex: 1, minWidth: 160 }} aria-label="Опубликованная ссылка" readOnly value={`${window.location.origin}/share/${link.token}`} onFocus={e => e.currentTarget.select()} />
              <button className="btn btn-secondary" onClick={() => copy(`${window.location.origin}/share/${link.token}`)}>Копировать</button>
              <button className="btn btn-ghost" disabled={busy} onClick={() => revokeLink(link.token)}>Отозвать</button>
            </div>)}
          </div>}
        </div>
      </div>
    </div>
  )
}
