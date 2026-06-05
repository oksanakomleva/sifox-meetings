import { type ReactNode } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
import { isDemoOn } from '../../demo/demo'
import { DEMO_CALLS } from '../../demo/calls'

export default function CallsFeed() {
  const navigate = useNavigate()

  // Demo-only section.
  if (!isDemoOn()) return <Navigate to="/" replace />

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Звонки</h1>
        <p className="page-subtitle">Записи звонков, расшифровки и AI-анализ</p>
      </div>

      <div className="page-body">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {DEMO_CALLS.map(c => (
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
                  <span style={{
                    width: 26, height: 26, borderRadius: 8, display: 'flex', alignItems: 'center',
                    justifyContent: 'center', background: 'var(--color-accent-6)', color: 'var(--color-accent)',
                  }} title="AI-анализ">✨</span>
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
