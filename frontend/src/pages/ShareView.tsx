import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import MarkdownRenderer from '../components/MarkdownRenderer'
import AudioPlayer from '../components/AudioPlayer'

type Unlocked = Awaited<ReturnType<typeof api.share.unlock>>

export default function ShareView() {
  const { token } = useParams<{ token: string }>()
  const [password, setPassword] = useState('')
  const [data, setData] = useState<Unlocked | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<'protocol' | 'transcript'>('protocol')

  const unlock = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!token) return
    setLoading(true)
    setError('')
    try {
      setData(await api.share.unlock(token, password))
    } catch (err: any) {
      setError(err.message || 'Не удалось открыть')
    } finally {
      setLoading(false)
    }
  }

  const fmt = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''

  // ── Password gate ──
  if (!data) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--color-bg)', padding: 16 }}>
        <form onSubmit={unlock} className="card" style={{ width: 'min(380px, 100%)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <div>
            <h1 style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, margin: 0 }}>Запись встречи</h1>
            <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)', margin: '4px 0 0' }}>
              Введите пароль для просмотра.
            </p>
          </div>
          <input
            type="password" autoFocus value={password} onChange={e => setPassword(e.target.value)}
            placeholder="Пароль"
            style={{ padding: '10px 12px', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', background: 'var(--color-surface-2)', color: 'var(--color-text)' }}
          />
          {error && <div style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>}
          <button className="btn btn-primary" type="submit" disabled={loading || !password}>
            {loading ? 'Открываем…' : 'Открыть'}
          </button>
        </form>
      </div>
    )
  }

  // ── Unlocked view ──
  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-bg)' }}>
      <div style={{ maxWidth: 820, margin: '0 auto', padding: 'var(--space-8) var(--space-5)' }}>
        <h1 className="page-title">{data.title || 'Запись встречи'}</h1>
        <p className="page-subtitle">{fmt(data.start_time)}</p>

        {data.has_audio && data.audio_url && (
          <div style={{ maxWidth: 560, margin: 'var(--space-5) 0' }}>
            <AudioPlayer src={data.audio_url} title="Аудиозапись" />
          </div>
        )}

        <div className="tabs" style={{ marginTop: 'var(--space-4)' }}>
          {data.summary && (
            <button className={`tab ${tab === 'protocol' ? 'active' : ''}`} onClick={() => setTab('protocol')}>📋 Протокол</button>
          )}
          {data.transcript && (
            <button className={`tab ${tab === 'transcript' ? 'active' : ''}`} onClick={() => setTab('transcript')}>📝 Транскрипт</button>
          )}
        </div>

        {tab === 'protocol' && data.summary && (
          <div style={{ maxWidth: 760, fontSize: 'var(--font-size-sm)', lineHeight: 'var(--line-height-relaxed)', color: 'var(--color-text)' }}>
            <MarkdownRenderer text={data.summary} />
          </div>
        )}
        {tab === 'transcript' && data.transcript && (
          <pre style={{ maxWidth: 760, fontFamily: 'var(--font-family)', fontSize: 'var(--font-size-sm)', lineHeight: 'var(--line-height-relaxed)', whiteSpace: 'pre-wrap', color: 'var(--color-text)' }}>
            {data.transcript}
          </pre>
        )}
      </div>
    </div>
  )
}
