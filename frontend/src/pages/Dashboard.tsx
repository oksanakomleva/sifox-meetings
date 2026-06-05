import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../hooks/useAuth'
import ChatWidget from '../components/ChatWidget'
import { isDemoOn } from '../demo/demo'
import { callsForDay, callsForWeek, isToday, isWithinDays, type DemoCall } from '../demo/calls'
import type { ChatMessage, Meeting } from '../types'

export default function Dashboard() {
  const { user } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const [summary, setSummary] = useState<string | null>(null)
  const [meetingCount, setMeetingCount] = useState(0)
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])
  const [loadingSummary, setLoadingSummary] = useState(true)
  const [loadingChat, setLoadingChat] = useState(true)
  const [calStatus, setCalStatus] = useState<{ connected: boolean; has_enabled_calendar: boolean } | null>(null)
  const [calendarJustConnected, setCalendarJustConnected] = useState(false)
  const [demoMeetings, setDemoMeetings] = useState<Meeting[]>([])

  useEffect(() => {
    // Check if redirected back after connecting calendar
    if (searchParams.get('calendar_connected') === '1') {
      setCalendarJustConnected(true)
      setSearchParams({}, { replace: true })
    }

    // Weekly summary is generated over all of the user's meetings, so it would
    // leak non-demo content — skip it in demo (the section is hidden too).
    if (isDemoOn()) {
      setLoadingSummary(false)
      // Demo dashboard rolls up calls + "демо"-tagged meetings.
      api.meetings.list(100).then(r => setDemoMeetings(r.meetings)).catch(() => {})
    } else {
      api.meetings.weekSummary()
        .then(r => { setSummary(r.summary); setMeetingCount(r.count) })
        .catch(() => setSummary(null))
        .finally(() => setLoadingSummary(false))
    }

    api.chat.history(undefined)
      .then(r => setChatHistory(r.messages))
      .catch(() => {})
      .finally(() => setLoadingChat(false))

    // Check calendar connection status (for non-admins)
    api.meetings.calendarStatus()
      .then(s => setCalStatus(s))
      .catch(() => {})
  }, [])

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Главная</h1>
        <p className="page-subtitle">Сводка за неделю и AI-ассистент</p>
      </div>

      <div className="page-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>

        {/* ── Success: calendar just connected ── */}
        {calendarJustConnected && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: 'var(--space-4)',
            background: 'var(--color-success-bg)', border: '1px solid #bbf7d0',
            borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)',
          }}>
            <span style={{ fontSize: 24, lineHeight: 1 }}>✅</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 'var(--font-weight-semibold)', color: 'var(--color-success)', marginBottom: 4 }}>
                Google Calendar подключён!
              </div>
              <div style={{ fontSize: 'var(--font-size-sm)', color: '#166534', lineHeight: 'var(--line-height-relaxed)' }}>
                Ваш основной календарь включён для записи. Бот автоматически присоединится к вашим встречам в Яндекс Телемост
                и после каждой встречи подготовит транскрипт и протокол. Встречи появятся в разделе «Встречи».
              </div>
            </div>
            <button
              onClick={() => setCalendarJustConnected(false)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#166534', fontSize: 18, lineHeight: 1, padding: 0 }}
            >×</button>
          </div>
        )}

        {/* ── Onboarding: connect calendar (non-admins who haven't connected yet) ── */}
        {!user?.is_admin && calStatus !== null && !calStatus.connected && !calendarJustConnected && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: 'var(--space-4)',
            background: 'var(--color-accent-6)', border: '1px solid var(--color-accent-4)',
            borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)',
          }}>
            <span style={{ fontSize: 24, lineHeight: 1 }}>📅</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 'var(--font-weight-semibold)', marginBottom: 4 }}>
                Подключите Google Calendar
              </div>
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-4)', lineHeight: 'var(--line-height-relaxed)' }}>
                Чтобы бот автоматически записывал ваши встречи в Яндекс Телемост, нужно дать доступ к вашему Google Calendar.
                Мы будем видеть только названия и время встреч.
              </div>
              <a href="/api/auth/connect-calendar">
                <button className="btn btn-primary">Подключить Google Calendar</button>
              </a>
            </div>
          </div>
        )}

        {/* ── Demo: call roll-ups (left) + AI chat (right) ── */}
        {isDemoOn() && (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 420px)', gap: 'var(--space-5)', alignItems: 'start' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
              <RollUp
                title="Итоги дня"
                calls={callsForDay()}
                meetings={demoMeetings.filter(m => isToday(m.start_time))}
              />
              <RollUp
                title="Итоги недели"
                calls={callsForWeek()}
                meetings={demoMeetings.filter(m => isWithinDays(m.start_time, 7))}
              />
            </div>
            <div>
              <h2 style={{ fontSize: 'var(--font-size-lg)', fontWeight: 'var(--font-weight-semibold)', color: 'var(--color-text)', margin: '0 0 var(--space-4)' }}>
                AI-ассистент
              </h2>
              <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                {loadingChat ? (
                  <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-8)' }}>
                    <span className="spinner" style={{ width: 24, height: 24 }} />
                  </div>
                ) : (
                  <ChatWidget initialHistory={chatHistory} emptyPlaceholder="Спросите о встречах или звонках…" />
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── Weekly AI summary (hidden in demo) ── */}
        {!isDemoOn() && (
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
        )}

        {/* ── Global AI chat (in demo it lives in the grid above) ── */}
        {!isDemoOn() && (
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
        )}

      </div>
    </div>
  )
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few
  return many
}

function RollUp({ title, calls, meetings }: { title: string; calls: DemoCall[]; meetings: Meeting[] }) {
  const empty = calls.length === 0 && meetings.length === 0
  return (
    <section>
      <h2 style={{ fontSize: 'var(--font-size-lg)', fontWeight: 'var(--font-weight-semibold)', color: 'var(--color-text)', margin: '0 0 var(--space-4)' }}>
        {title}
      </h2>
      <div className="card">
        {empty ? (
          <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-text-muted)' }}>Событий нет.</p>
        ) : (
          <>
            <p style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
              {calls.length} {plural(calls.length, 'звонок', 'звонка', 'звонков')} ·{' '}
              {meetings.length} {plural(meetings.length, 'встреча', 'встречи', 'встреч')}
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
              {calls.map(c => (
                <div key={c.id} style={{ fontSize: 'var(--font-size-sm)' }}>
                  📞 {c.title} <span style={{ color: 'var(--color-text-muted)' }}>· {c.datetime}</span>
                </div>
              ))}
              {meetings.map(m => (
                <div key={m.id} style={{ fontSize: 'var(--font-size-sm)' }}>
                  📅 {m.topic || m.title || 'Встреча'}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  )
}
