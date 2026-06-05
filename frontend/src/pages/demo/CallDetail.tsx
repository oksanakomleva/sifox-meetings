import { useState } from 'react'
import { useParams, useNavigate, Navigate } from 'react-router-dom'
import { isDemoOn } from '../../demo/demo'
import { demoCallById, ANALYSIS_TYPES, callAnalysis, type AnalysisType } from '../../demo/calls'

type RightTab = 'summary' | 'ai'

export default function CallDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [rightTab, setRightTab] = useState<RightTab>('summary')
  const [analysis, setAnalysis] = useState<AnalysisType['key'] | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [chat, setChat] = useState<{ role: 'user' | 'assistant'; text: string }[]>([])
  const [chatInput, setChatInput] = useState('')

  if (!isDemoOn()) return <Navigate to="/" replace />
  const call = id ? demoCallById(id) : undefined
  if (!call) return <Navigate to="/calls" replace />

  const pickAnalysis = (key: AnalysisType['key']) => {
    setAnalysis(key)
    setRightTab('ai')
    setModalOpen(false)
  }

  const sendChat = () => {
    const t = chatInput.trim()
    if (!t) return
    setChatInput('')
    setChat(prev => [
      ...prev,
      { role: 'user', text: t },
      { role: 'assistant', text: `Это демонстрационный ответ AI по звонку «${call.title}». В рабочей версии ассистент отвечает по реальной расшифровке разговора.` },
    ])
  }

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
          <button className="btn btn-secondary" onClick={() => setModalOpen(true)}>✨ AI-анализ</button>
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
                  {call.reminders.map((r, i) => (
                    <div key={i} style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>{r}</div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="card">
              {!analysis ? (
                <AnalysisPicker onPick={pickAnalysis} />
              ) : analysis === 'chat' ? (
                <div>
                  <BackToTypes onBack={() => setAnalysis(null)} />
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', marginTop: 'var(--space-3)', maxHeight: 280, overflowY: 'auto' }}>
                    {chat.length === 0 && (
                      <div style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-sm)' }}>Задайте вопрос по этому звонку.</div>
                    )}
                    {chat.map((m, i) => (
                      <div key={i} style={{
                        alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                        maxWidth: '85%', padding: '6px 10px', borderRadius: 'var(--radius-md)',
                        fontSize: 'var(--font-size-sm)',
                        background: m.role === 'user' ? 'var(--color-accent)' : 'var(--color-surface-2)',
                        color: m.role === 'user' ? '#fff' : 'var(--color-text)',
                      }}>{m.text}</div>
                    ))}
                  </div>
                  <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
                    <input className="input" value={chatInput} onChange={e => setChatInput(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && sendChat()} placeholder="Ваш вопрос…" style={{ flex: 1 }} />
                    <button className="btn btn-primary" onClick={sendChat}>→</button>
                  </div>
                </div>
              ) : (
                <div>
                  <BackToTypes onBack={() => setAnalysis(null)} />
                  <div style={{ fontWeight: 600, margin: 'var(--space-3) 0 var(--space-2)' }}>
                    {ANALYSIS_TYPES.find(t => t.key === analysis)?.title}
                  </div>
                  <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', lineHeight: 'var(--line-height-relaxed)', whiteSpace: 'pre-wrap' }}>
                    {callAnalysis(call, analysis)}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* AI analysis modal */}
      {modalOpen && (
        <div onClick={() => setModalOpen(false)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 100,
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 'var(--space-4)',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: 'var(--color-surface)', borderRadius: 'var(--radius-lg)', maxWidth: 460, width: '100%',
            padding: 'var(--space-6)', boxShadow: '0 12px 40px rgba(0,0,0,0.3)',
          }}>
            <div style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, marginBottom: 4 }}>AI Анализ звонка</div>
            <div style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-4)' }}>
              Что вы хотите узнать из этого разговора?
            </div>
            <AnalysisPicker onPick={pickAnalysis} />
          </div>
        </div>
      )}
    </div>
  )
}

function AnalysisPicker({ onPick }: { onPick: (k: AnalysisType['key']) => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      {ANALYSIS_TYPES.map(t => (
        <button key={t.key} onClick={() => onPick(t.key)} style={{
          display: 'flex', alignItems: 'flex-start', gap: 'var(--space-3)', textAlign: 'left',
          padding: 'var(--space-3)', borderRadius: 'var(--radius-md)', cursor: 'pointer',
          border: '1px solid var(--color-border)', background: 'transparent',
        }}>
          <span style={{ fontSize: 20, lineHeight: 1 }}>{t.icon}</span>
          <span>
            <span style={{ display: 'block', fontWeight: 600, fontSize: 'var(--font-size-sm)' }}>{t.title}</span>
            <span style={{ display: 'block', color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-xs)' }}>{t.desc}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

function BackToTypes({ onBack }: { onBack: () => void }) {
  return (
    <button onClick={onBack} style={{
      background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-accent)',
      fontSize: 'var(--font-size-sm)', padding: 0,
    }}>← Все типы анализа</button>
  )
}
