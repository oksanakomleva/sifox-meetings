import { useState } from 'react'
import { useParams, useNavigate, Navigate } from 'react-router-dom'
import { isDemoOn } from '../../demo/demo'
import { demoCallById } from '../../demo/calls'
import CallAnalysisPanel from '../../components/demo/CallAnalysisPanel'

type RightTab = 'summary' | 'ai'

export default function CallDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [rightTab, setRightTab] = useState<RightTab>('summary')

  if (!isDemoOn()) return <Navigate to="/" replace />
  const call = id ? demoCallById(id) : undefined
  if (!call) return <Navigate to="/calls" replace />

  return (
    <div className="main-content">
      {/* Header */}
      <div className="page-header">
        <button className="btn btn-ghost" onClick={() => navigate('/calls')}
          style={{ marginBottom: 'var(--space-3)', padding: 0, height: 'auto', gap: 6 }}>
          ← Назад
        </button>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1 className="page-title">{call.title}</h1>
            <p className="page-subtitle">📞 {call.phone} · {call.datetime}</p>
          </div>
          <button className="btn btn-secondary" onClick={() => setRightTab('ai')}>✨ AI-анализ</button>
        </div>
      </div>

      <div className="page-body" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 420px)', gap: 'var(--space-5)', alignItems: 'start' }}>
        {/* Left: audio + transcript */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 'var(--space-3)' }}>
              <span style={{ fontWeight: 600 }}>Аудиозапись</span>
              <span style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-sm)' }}>{call.duration}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span style={{
                width: 40, height: 40, borderRadius: '50%', background: 'var(--color-accent)', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              }}>▶</span>
              <div style={{ flex: 1, height: 4, background: 'var(--color-border)', borderRadius: 2 }} />
              <span style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-xs)' }}>{call.duration}</span>
            </div>
          </div>

          <div className="card">
            <div style={{ fontWeight: 600, marginBottom: 'var(--space-4)' }}>Расшифровка</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              {call.transcript.map((l, i) => {
                const you = l.speaker === 'Вы'
                return (
                  <div key={i} style={{
                    display: 'flex', gap: 'var(--space-3)', padding: 'var(--space-2)',
                    borderRadius: 'var(--radius-md)', background: you ? 'var(--color-accent-6)' : 'transparent',
                  }}>
                    <span style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-xs)', width: 34, flexShrink: 0 }}>{l.time}</span>
                    <div>
                      <span style={{
                        fontSize: 11, fontWeight: 700, padding: '1px 8px', borderRadius: 'var(--radius-full)',
                        background: you ? 'var(--color-accent)' : 'var(--color-surface-2)',
                        color: you ? '#fff' : 'var(--color-text-secondary)',
                      }}>{l.speaker}</span>
                      <div style={{ marginTop: 4, fontSize: 'var(--font-size-sm)' }}>{l.text}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {/* Right: results / AI */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            {([['summary', 'Итоги звонка'], ['ai', '✦ Разбор с AI']] as [RightTab, string][]).map(([k, label]) => {
              const active = rightTab === k
              return (
                <button key={k} onClick={() => setRightTab(k)} style={{
                  flex: 1, fontSize: 'var(--font-size-sm)', fontWeight: 600, padding: '8px 12px',
                  borderRadius: 'var(--radius-md)', cursor: 'pointer',
                  border: '1px solid var(--color-border)',
                  background: active ? 'var(--color-surface-2)' : 'transparent',
                  color: active ? 'var(--color-text)' : 'var(--color-text-secondary)',
                }}>{label}</button>
              )
            })}
          </div>

          {rightTab === 'summary' ? (
            <>
              <div className="card">
                <div style={{ fontWeight: 600, marginBottom: 'var(--space-2)' }}>Краткое содержание</div>
                <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', lineHeight: 'var(--line-height-relaxed)' }}>{call.summary}</p>
              </div>

              {call.tasks.length > 0 && (
                <div className="card">
                  <div style={{ fontWeight: 600, marginBottom: 'var(--space-3)' }}>Задачи</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                    {call.tasks.map((g, i) => (
                      <div key={i}>
                        <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
                          👤 {g.assignee} <span style={{ color: 'var(--color-text-muted)', fontWeight: 400 }}>({g.role})</span>
                        </div>
                        <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                          {g.items.map((it, j) => <li key={j}>{it}</li>)}
                        </ul>
                      </div>
                    ))}
                  </div>
                  {call.note && (
                    <div style={{ marginTop: 'var(--space-3)', fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)' }}>
                      📌 {call.note}
                    </div>
                  )}
                </div>
              )}

              {call.reminders.length > 0 && (
                <div className="card">
                  <div style={{ fontWeight: 600, marginBottom: 'var(--space-2)' }}>📅 Напоминания</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                    {call.reminders.map((r, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                        <span style={{ flex: 1, fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>{r}</span>
                        <button
                          type="button"
                          title="Добавить в календарь"
                          onClick={() => window.open(
                            `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(r)}`,
                            '_blank', 'noopener',
                          )}
                          style={{
                            flexShrink: 0, width: 28, height: 28, borderRadius: 8, cursor: 'pointer',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            border: '1px solid var(--color-border)', background: 'transparent', color: 'var(--color-accent)',
                          }}
                        >
                          <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
                            <rect x="3" y="4" width="14" height="13" rx="2" />
                            <path d="M3 8h14M7 2v4M13 2v4M10 11v4M8 13h4" />
                          </svg>
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="card">
              <CallAnalysisPanel call={call} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
