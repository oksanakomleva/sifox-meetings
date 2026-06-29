import { useState, useEffect, type ReactNode } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import { isDemoOn } from '../../demo/demo'
import { DEMO_CALLS, apiCallToDemoCall, type DemoCall } from '../../demo/calls'
import { api } from '../../api/client'
import CallAnalysisPanel from '../../components/demo/CallAnalysisPanel'

export default function CallsFeed() {
  const navigate = useNavigate()
  const [analysisCall, setAnalysisCall] = useState<DemoCall | null>(null)
  const [calls, setCalls] = useState<DemoCall[]>([])
  const [loading, setLoading] = useState(true)

  // Real imported calls; fall back to the illustrative demo set if none yet.
  useEffect(() => {
    let alive = true
    api.calls.list()
      .then(r => { if (alive) setCalls(r.calls.length ? r.calls.map(apiCallToDemoCall) : DEMO_CALLS) })
      .catch(() => { if (alive) setCalls(DEMO_CALLS) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  // Demo-only section.
  if (!isDemoOn()) return <Navigate to="/" replace />

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Звонки</h1>
        <p className="page-subtitle">Записи звонков, расшифровки и AI-анализ</p>
      </div>

      <div className="page-body">
        {loading && <div style={{ color: 'var(--color-text-muted)' }}>Загрузка…</div>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {calls.map(c => (
            <div key={c.id} className="card" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 'var(--font-weight-semibold)', marginBottom: 2 }}>{c.title}</div>
                  <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>
                    📞 {c.phone} · {c.duration} · {c.datetime}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                  {c.tasksCount ? <Badge>✔ {c.tasksCount}</Badge> : null}
                  {c.remindersCount ? <Badge>📅 {c.remindersCount}</Badge> : null}
                  <button
                    onClick={() => setAnalysisCall(c)}
                    title="AI-анализ"
                    style={{
                      width: 28, height: 28, borderRadius: 8, display: 'flex', alignItems: 'center',
                      justifyContent: 'center', background: 'var(--color-accent-6)', color: 'var(--color-accent)',
                      border: 'none', cursor: 'pointer', fontSize: 15,
                    }}
                  >✨</button>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
                <div style={{
                  flex: 1, display: 'flex', alignItems: 'center', gap: 8,
                  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                  padding: '8px 12px', color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-sm)',
                }}>▶ Воспроизвести</div>
                <button className="btn btn-primary" style={{ flex: 1 }} onClick={() => navigate(`/calls/${c.id}`)}>
                  Подробнее
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Right-side AI analysis drawer */}
      {analysisCall && (
        <>
          <div onClick={() => setAnalysisCall(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 100 }} />
          <div style={{
            position: 'fixed', top: 0, right: 0, bottom: 0, width: 'min(460px, 92vw)', zIndex: 101,
            background: 'var(--color-surface)', boxShadow: '-8px 0 30px rgba(0,0,0,0.25)',
            display: 'flex', flexDirection: 'column',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 'var(--space-4)', borderBottom: '1px solid var(--color-border)' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700 }}>AI Анализ звонка</div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {analysisCall.title}
                </div>
              </div>
              <button onClick={() => setAnalysisCall(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 20, color: 'var(--color-text-muted)' }}>×</button>
            </div>
            <div style={{ padding: 'var(--space-4)', overflowY: 'auto' }}>
              <CallAnalysisPanel call={analysisCall} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function Badge({ children }: { children: ReactNode }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, color: 'var(--color-accent)',
      background: 'var(--color-accent-6)', borderRadius: 'var(--radius-full)', padding: '2px 8px',
    }}>{children}</span>
  )
}
