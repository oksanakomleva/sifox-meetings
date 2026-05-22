import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import StatusBadge from '../../components/StatusBadge'
import type { Meeting } from '../../types'

export default function AdminMeetings() {
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    api.admin.allMeetings().then(r => setMeetings(r.meetings)).finally(() => setLoading(false))
  }, [])

  const fmt = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Все встречи</h1>
        <p className="page-subtitle">Полный список — {meetings.length} записей</p>
      </div>

      <div className="page-body">
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}>
            <span className="spinner" style={{ width: 28, height: 28 }} />
          </div>
        ) : meetings.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📭</div>
            <div className="empty-state-title">Встреч пока нет</div>
            <p className="empty-state-text">Включите запись для нужных календарей</p>
          </div>
        ) : (
          <div className="card" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                  {['Тема', 'Дата', 'Тип', 'Статус', 'Аудио'].map(h => (
                    <th key={h} style={{
                      padding: 'var(--space-3) var(--space-4)',
                      textAlign: 'left',
                      fontSize: 'var(--font-size-xs)',
                      fontWeight: 600,
                      color: 'var(--color-text-secondary)',
                      textTransform: 'uppercase', letterSpacing: '0.06em',
                      background: 'var(--color-surface-2)',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {meetings.map((m, i) => (
                  <tr
                    key={m.id}
                    onClick={() => navigate(`/meetings/${m.id}`)}
                    style={{
                      borderBottom: i < meetings.length - 1 ? '1px solid var(--color-border)' : 'none',
                      cursor: 'pointer',
                      transition: 'background var(--transition-fast)',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-surface-2)')}
                    onMouseLeave={e => (e.currentTarget.style.background = '')}
                  >
                    <td style={{ padding: 'var(--space-3) var(--space-4)', maxWidth: 280 }}>
                      <div style={{ fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {m.topic || m.title || 'Без названия'}
                      </div>
                      {m.error_message && (
                        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-error)', marginTop: 2 }}>
                          {m.error_message.slice(0, 60)}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)', whiteSpace: 'nowrap' }}>
                      {fmt(m.start_time)}
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                      {m.meeting_type || '—'}
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                      <StatusBadge status={m.status} />
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)' }}>
                      {m.audio_size ? `${(m.audio_size / 1_000_000).toFixed(0)} МБ` : '—'}
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
