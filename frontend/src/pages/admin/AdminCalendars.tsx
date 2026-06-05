import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { Calendar } from '../../types'

export default function AdminCalendars() {
  const [calendars, setCalendars] = useState<Calendar[]>([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)

  useEffect(() => { load() }, [])

  const load = () => {
    api.admin.calendars()
      .then(r => setCalendars(r.calendars))
      .finally(() => setLoading(false))
  }

  const toggle = async (cal: Calendar) => {
    const updated = !cal.record_enabled
    setCalendars(prev => prev.map(c => c.id === cal.id ? { ...c, record_enabled: updated } : c))
    await api.admin.setCalendarEnabled(cal.id, updated).catch(() => load())
  }

  const sync = async () => {
    setSyncing(true)
    try {
      await api.admin.syncCalendars()  // refreshes the calendar list server-side
      load()                           // list is ready now
      // Events sync continues in the background — refresh again shortly to pick them up.
      setTimeout(load, 4000)
    } catch {
      alert('Не удалось синхронизировать. Возможно, истёк доступ к Google Calendar — переподключите календарь.')
    } finally {
      setSyncing(false)
    }
  }

  // Group by owner
  const byOwner = calendars.reduce<Record<string, Calendar[]>>((acc, c) => {
    const key = `${c.owner_name} (${c.owner_email})`
    ;(acc[key] ??= []).push(c)
    return acc
  }, {})

  return (
    <div className="main-content">
      <div className="page-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1 className="page-title">Календари</h1>
            <p className="page-subtitle">Управление записью встреч из Google Calendar</p>
          </div>
          <button className="btn btn-secondary" onClick={sync} disabled={syncing}>
            {syncing ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Синхронизация...</> : '🔄 Синхронизировать'}
          </button>
        </div>
      </div>

      <div className="page-body">
        {/* Connect banner */}
        <div className="card" style={{ background: 'var(--color-accent-6)', borderColor: 'var(--color-accent-4)', marginBottom: 'var(--space-6)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Подключить Google Calendar</div>
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                Подключите свой аккаунт чтобы система видела ваши встречи
              </div>
            </div>
            <a href="/api/auth/connect-calendar">
              <button className="btn btn-primary">Подключить Calendar</button>
            </a>
          </div>
        </div>

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}>
            <span className="spinner" style={{ width: 28, height: 28 }} />
          </div>
        ) : Object.keys(byOwner).length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📅</div>
            <div className="empty-state-title">Нет подключённых календарей</div>
            <p className="empty-state-text">Попросите сотрудников подключить Google Calendar через кнопку выше</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
            {Object.entries(byOwner).map(([owner, cals]) => (
              <div key={owner}>
                <div style={{
                  fontSize: 'var(--font-size-xs)', fontWeight: 600,
                  color: 'var(--color-text-secondary)',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                  marginBottom: 'var(--space-3)',
                }}>
                  {owner}
                </div>
                <div className="card" style={{ padding: 0 }}>
                  {cals.map((cal, i) => (
                    <div
                      key={cal.id}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 'var(--space-4)',
                        padding: 'var(--space-4) var(--space-5)',
                        borderBottom: i < cals.length - 1 ? '1px solid var(--color-border)' : 'none',
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 500, display: 'flex', alignItems: 'center', gap: 8 }}>
                          {cal.name}
                          {cal.is_primary && (
                            <span style={{
                              fontSize: 'var(--font-size-xs)', fontWeight: 600,
                              color: 'var(--color-accent)',
                              background: 'var(--color-primary-light)',
                              padding: '1px 6px', borderRadius: 'var(--radius-full)',
                            }}>основной</span>
                          )}
                        </div>
                        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)' }}>
                          {cal.google_calendar_id}
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>
                          {cal.record_enabled ? 'Запись включена' : 'Запись выключена'}
                        </span>
                        <label className="toggle">
                          <input
                            type="checkbox"
                            checked={cal.record_enabled}
                            onChange={() => toggle(cal)}
                          />
                          <span className="toggle-slider" />
                        </label>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
