import { useState } from 'react'
import { ANALYSIS_TYPES, ANALYSIS_RESULTS, type AnalysisType, type DemoCall, type AnalysisObservation } from '../../demo/calls'

const TONE: Record<'good' | 'medium' | 'bad', { fg: string; bg: string; border: string }> = {
  good:   { fg: '#0f8a4f', bg: 'rgba(34,197,94,0.10)',  border: 'rgba(34,197,94,0.35)' },
  medium: { fg: '#c2680a', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.40)' },
  bad:    { fg: '#c0392b', bg: 'rgba(239,68,68,0.10)',  border: 'rgba(239,68,68,0.35)' },
}

const OBS: Record<AnalysisObservation['type'], { label: string; icon: string; color: string }> = {
  strength: { label: 'Сильная сторона', icon: '✓', color: '#0f8a4f' },
  growth:   { label: 'Точка роста',     icon: '↗', color: '#c2680a' },
  note:     { label: 'Наблюдение',      icon: 'ⓘ', color: 'var(--color-text-secondary)' },
}

export default function CallAnalysisPanel({ call }: { call: DemoCall }) {
  const [picked, setPicked] = useState<AnalysisType['key'] | null>(null)
  const [chat, setChat] = useState<{ role: 'user' | 'assistant'; text: string }[]>([])
  const [input, setInput] = useState('')

  const send = () => {
    const t = input.trim()
    if (!t) return
    setInput('')
    setChat(prev => [
      ...prev,
      { role: 'user', text: t },
      { role: 'assistant', text: `Это демонстрационный ответ AI по звонку «${call.title}». В рабочей версии ассистент отвечает по реальной расшифровке разговора.` },
    ])
  }

  if (!picked) return <Picker onPick={setPicked} />

  if (picked === 'chat') {
    return (
      <div>
        <Back onClick={() => setPicked(null)} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', marginTop: 'var(--space-3)', maxHeight: 320, overflowY: 'auto' }}>
          {chat.length === 0 && <div style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-sm)' }}>Задайте вопрос по этому звонку.</div>}
          {chat.map((m, i) => (
            <div key={i} style={{
              alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '85%',
              padding: '6px 10px', borderRadius: 'var(--radius-md)', fontSize: 'var(--font-size-sm)',
              background: m.role === 'user' ? 'var(--color-accent)' : 'var(--color-surface-2)',
              color: m.role === 'user' ? '#fff' : 'var(--color-text)',
            }}>{m.text}</div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
          <input className="input" value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && send()} placeholder="Ваш вопрос…" style={{ flex: 1 }} />
          <button className="btn btn-primary" onClick={send}>→</button>
        </div>
      </div>
    )
  }

  const r = ANALYSIS_RESULTS[picked]
  const tone = TONE[r.status.tone]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <Back onClick={() => setPicked(null)} />

      {/* Score header */}
      <div style={{
        textAlign: 'center', padding: 'var(--space-5)', borderRadius: 'var(--radius-lg)',
        background: tone.bg, border: `1px solid ${tone.border}`,
      }}>
        <div style={{ fontSize: 34, fontWeight: 800, color: tone.fg, lineHeight: 1 }}>{r.score}/100</div>
        <div style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-sm)', margin: '6px 0' }}>{r.title}</div>
        <span style={{
          display: 'inline-block', fontSize: 'var(--font-size-xs)', fontWeight: 700,
          padding: '3px 12px', borderRadius: 'var(--radius-full)',
          background: 'var(--color-surface)', color: tone.fg, border: `1px solid ${tone.border}`,
        }}>{r.status.label}</span>
      </div>

      {/* Key observations */}
      <div>
        <div style={{ fontWeight: 600, marginBottom: 'var(--space-3)' }}>Ключевые наблюдения</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {r.observations.map((o, i) => {
            const meta = OBS[o.type]
            return (
              <div key={i} style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: 'var(--space-3)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <span style={{ fontSize: 'var(--font-size-xs)', fontWeight: 700, color: meta.color }}>
                    {meta.icon} {meta.label}
                  </span>
                  <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)' }}>⏱ {o.time}</span>
                </div>
                <div style={{ fontSize: 'var(--font-size-sm)' }}>{o.text}</div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Recommendations */}
      <div>
        <div style={{ fontWeight: 600, marginBottom: 'var(--space-3)' }}>💡 Рекомендации</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {r.recommendations.map((rec, i) => (
            <div key={i} style={{
              background: 'var(--color-accent-6)', borderRadius: 'var(--radius-md)',
              padding: 'var(--space-3)', fontSize: 'var(--font-size-sm)',
            }}>💡 {rec}</div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Picker({ onPick }: { onPick: (k: AnalysisType['key']) => void }) {
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

function Back({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-accent)',
      fontSize: 'var(--font-size-sm)', padding: 0, alignSelf: 'flex-start',
    }}>← Все типы анализа</button>
  )
}
