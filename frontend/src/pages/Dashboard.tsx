import { useEffect, useState } from 'react'
import { api } from '../api/client'
import ChatWidget from '../components/ChatWidget'
import type { ChatMessage } from '../types'

export default function Dashboard() {
  const [summary, setSummary] = useState<string | null>(null)
  const [meetingCount, setMeetingCount] = useState(0)
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])
  const [loadingSummary, setLoadingSummary] = useState(true)
  const [loadingChat, setLoadingChat] = useState(true)

  useEffect(() => {
    api.meetings.weekSummary()
      .then(r => { setSummary(r.summary); setMeetingCount(r.count) })
      .catch(() => setSummary(null))
      .finally(() => setLoadingSummary(false))

    api.chat.history(undefined)
      .then(r => setChatHistory(r.messages))
      .catch(() => {})
      .finally(() => setLoadingChat(false))
  }, [])

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Главная</h1>
        <p className="page-subtitle">Сводка за неделю и AI-ассистент</p>
      </div>

      <div className="page-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>

        {/* ── Weekly AI summary ── */}
        <section>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-3)', marginBottom: 'var(--space-4)' }}>
            <h2 style={{
              fontSize: 'var(--font-size-lg)',
              fontWeight: 'var(--font-weight-semibold)',
              color: 'var(--color-text)',
              margin: 0,
            }}>
              Итоги недели
            </h2>
            {!loadingSummary && meetingCount > 0 && (
              <span style={{
                fontSize: 'var(--font-size-xs)',
                color: 'var(--color-text-muted)',
                fontWeight: 400,
              }}>
                {meetingCount} {meetingCount === 1 ? 'встреча' : meetingCount < 5 ? 'встречи' : 'встреч'}
              </span>
            )}
          </div>

          <div className="card">
            {loadingSummary ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-sm)' }}>
                <span className="spinner" style={{ width: 18, height: 18, flexShrink: 0 }} />
                Генерирую сводку…
              </div>
            ) : summary ? (
              <p style={{
                fontSize: 'var(--font-size-base)',
                lineHeight: 'var(--line-height-relaxed)',
                color: 'var(--color-text)',
                margin: 0,
                whiteSpace: 'pre-wrap',
              }}>
                {summary}
              </p>
            ) : (
              <p style={{
                fontSize: 'var(--font-size-sm)',
                color: 'var(--color-text-muted)',
                margin: 0,
                textAlign: 'center',
                padding: 'var(--space-6) 0',
              }}>
                Нет завершённых встреч за последние 7 дней
              </p>
            )}
          </div>
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
            {loadingChat ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-8)' }}>
                <span className="spinner" style={{ width: 24, height: 24 }} />
              </div>
            ) : (
              <ChatWidget
                initialHistory={chatHistory}
                emptyPlaceholder="Спросите о любой встрече, решениях, задачах или участниках…"
              />
            )}
          </div>
        </section>

      </div>
    </div>
  )
}
