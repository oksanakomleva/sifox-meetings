import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import StatusBadge from '../components/StatusBadge'
import type { Meeting } from '../types'

export default function Meetings() {
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    api.meetings.list()
      .then(r => setMeetings(r.meetings))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const fmt = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('ru-RU', {
      day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
    })
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Мои встречи</h1>
        <p className="page-subtitle">Записи встреч, в которых вы участвовали</p>
      </div>

      <div className="page-body">
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 'var(--space-12)' }}>
            <span className="spinner" style={{ width: 32, height: 32, borderWidth: 3 }} />
          </div>
        )}

        {!loading && error && (
          <div style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>
        )}

        {!loading && !error && meetings.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">📅</div>
            <div className="empty-state-title">Нет записей встреч</div>
            <p className="empty-state-text">
              Когда вас пригласят на встречу через Яндекс Телемост, запись появится здесь автоматически.
            </p>
          </div>
        )}

        {!loading && meetings.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {meetings.map(m => (
              <div
                key={m.id}
                className="card card-hover"
                onClick={() => navigate(`/meetings/${m.id}`)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-4)' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontWeight: 'var(--font-weight-semibold)',
                      fontSize: 'var(--font-size-base)',
                      marginBottom: 'var(--space-1)',
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    }}>
                      {m.topic || m.title || 'Без названия'}
                    </div>
                    <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                      {fmt(m.start_time)}
                      {m.end_time && (
                        <> — {new Date(m.end_time).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</>
                      )}
                    </div>
                    {m.tags && m.tags.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-1)', marginTop: 'var(--space-2)' }}>
                        {m.tags.map(tag => (
                          <span key={tag} style={{
                            fontSize: 'var(--font-size-xs)',
                            background: 'var(--color-accent-6)',
                            color: 'var(--color-accent)',
                            padding: '2px 8px',
                            borderRadius: 'var(--radius-full)',
                            fontWeight: 'var(--font-weight-medium)',
                          }}>
                            #{tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <StatusBadge status={m.status} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
