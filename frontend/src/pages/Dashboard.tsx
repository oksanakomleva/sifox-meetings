import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import ChatWidget from '../components/ChatWidget'
import type { Meeting, ChatMessage } from '../types'

export default function Dashboard() {
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    Promise.all([
      api.meetings.week().catch(() => ({ meetings: [] as Meeting[] })),
      api.chat.history(undefined).catch(() => ({ messages: [] as ChatMessage[] })),
    ]).then(([weekData, historyData]) => {
      setMeetings(weekData.meetings)
      setChatHistory(historyData.messages)
    }).finally(() => setLoading(false))
  }, [])

  const fmt = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('ru-RU', {
      weekday: 'short', day: 'numeric', month: 'short',
      hour: '2-digit', minute: '2-digit',
    })
  }

  const meetingTypeLabel: Record<string, string> = {
    sales: 'Продажи',
    internal: 'Внутренняя',
    planning: 'Планирование',
    review: 'Ревью',
    interview: 'Интервью',
    partner: 'Партнёр',
    other: 'Другое',
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Главная</h1>
        <p className="page-subtitle">Сводка за неделю и AI-ассистент</p>
      </div>

      <div className="page-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>

        {/* ── Weekly summary ── */}
        <section>
          <h2 style={{
            fontSize: 'var(--font-size-lg)',
            fontWeight: 'var(--font-weight-semibold)',
            marginBottom: 'var(--space-4)',
            color: 'var(--color-text)',
          }}>
            Встречи за последние 7 дней
          </h2>

          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-8)' }}>
              <span className="spinner" style={{ width: 28, height: 28 }} />
            </div>
          ) : meetings.length === 0 ? (
            <div className="card" style={{
              color: 'var(--color-text-secondary)',
              fontSize: 'var(--font-size-sm)',
              textAlign: 'center',
              padding: 'var(--space-10)',
            }}>
              Нет завершённых встреч за последние 7 дней
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              {meetings.map(m => (
                <div
                  key={m.id}
                  className="card card-hover"
                  onClick={() => navigate(`/meetings/${m.id}`)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-4)' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {/* Title + type badge */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-1)' }}>
                        <span style={{
                          fontWeight: 'var(--font-weight-semibold)',
                          fontSize: 'var(--font-size-base)',
                          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                        }}>
                          {m.topic || m.title || 'Без названия'}
                        </span>
                        {m.meeting_type && (
                          <span style={{
                            flexShrink: 0,
                            fontSize: 'var(--font-size-xs)',
                            background: 'var(--color-surface-2)',
                            color: 'var(--color-text-secondary)',
                            padding: '1px 7px',
                            borderRadius: 'var(--radius-full)',
                            fontWeight: 500,
                          }}>
                            {meetingTypeLabel[m.meeting_type] ?? m.meeting_type}
                          </span>
                        )}
                      </div>

                      {/* Date */}
                      <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-2)' }}>
                        {fmt(m.start_time)}
                      </div>

                      {/* Summary excerpt */}
                      {m.summary && (
                        <div style={{
                          fontSize: 'var(--font-size-sm)',
                          color: 'var(--color-text-secondary)',
                          lineHeight: 'var(--line-height-relaxed)',
                          display: '-webkit-box',
                          WebkitLineClamp: 3,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                        }}>
                          {m.summary}
                        </div>
                      )}

                      {/* Tags */}
                      {m.tags && m.tags.length > 0 && (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-1)', marginTop: 'var(--space-2)' }}>
                          {m.tags.slice(0, 5).map(tag => (
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

                    {/* Arrow */}
                    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
                      style={{ width: 18, height: 18, flexShrink: 0, color: 'var(--color-text-muted)' }}>
                      <path d="M7 4l6 6-6 6"/>
                    </svg>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── Global AI chat ── */}
        <section>
          <h2 style={{
            fontSize: 'var(--font-size-lg)',
            fontWeight: 'var(--font-weight-semibold)',
            marginBottom: 'var(--space-1)',
            color: 'var(--color-text)',
          }}>
            AI-ассистент
          </h2>
          <p style={{
            fontSize: 'var(--font-size-sm)',
            color: 'var(--color-text-secondary)',
            marginBottom: 'var(--space-4)',
          }}>
            Задайте вопрос по всем доступным вам встречам
          </p>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <ChatWidget initialHistory={chatHistory} />
          </div>
        </section>

      </div>
    </div>
  )
}
