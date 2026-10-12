import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '../api/client'

interface Props {
  onClose: () => void
  onStarted: () => void
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 200,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
}
const panel: CSSProperties = {
  background: 'var(--color-surface)', border: '1px solid var(--color-border)',
  borderRadius: 'var(--radius-lg)', boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
  width: 'min(520px, 96vw)', maxHeight: '90vh', overflowY: 'auto',
  padding: 'var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)',
}

export default function ManualRecordingModal({ onClose, onStarted }: Props) {
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [meetingId, setMeetingId] = useState<string | null>(null)
  const [startedStatus, setStartedStatus] = useState('')
  const [startFailed, setStartFailed] = useState(false)
  const [mm, setMm] = useState<{ enabled: boolean; linked: boolean; bot_username: string | null } | null>(null)
  const [code, setCode] = useState('')
  const [codeBusy, setCodeBusy] = useState(false)

  useEffect(() => {
    api.meetings.mattermostRecorder().then(setMm).catch(() => setMm(null))
  }, [])

  useEffect(() => {
    if (!meetingId) return
    let stopped = false
    const check = async () => {
      try {
        const meeting = await api.meetings.get(meetingId)
        if (stopped) return
        if (meeting.status === 'recording') setStartedStatus('Протоколлер вошёл во встречу, запись идёт.')
        if (meeting.status === 'joining') setStartedStatus('Протоколлер открывает встречу. Это может занять до двух минут.')
        if (meeting.status === 'error') {
          setStartFailed(true)
          setStartedStatus(`Не удалось подключиться: ${meeting.error_message || 'неизвестная ошибка'}`)
        }
        if (meeting.status === 'no_show') {
          setStartFailed(true)
          setStartedStatus('Запись завершена: других участников во встрече не было.')
        }
        if (['transcribing', 'analyzing', 'done'].includes(meeting.status)) {
          setStartedStatus('Запись завершена, встреча обрабатывается или уже готова.')
        }
      } catch {
        // A temporary read failure must not turn a successfully queued request
        // into a false connection error. The next poll retries.
      }
    }
    void check()
    const timer = window.setInterval(() => { void check() }, 4_000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [meetingId])

  const start = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const result = await api.meetings.startManual(url, title)
      setMeetingId(result.meeting_id)
      setStartFailed(false)
      setStartedStatus(result.existing && ['done', 'transcribing', 'analyzing'].includes(result.status)
        ? 'Эта встреча недавно уже записывалась — повторно не подключаемся.'
        : 'Протоколлер подключается.')
      onStarted()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось запустить запись')
    } finally {
      setBusy(false)
    }
  }

  const getCode = async () => {
    setCodeBusy(true); setError('')
    try {
      const result = await api.meetings.mattermostLinkCode()
      setCode(result.code)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось получить код')
    } finally {
      setCodeBusy(false)
    }
  }

  return (
    <div style={overlay} onClick={onClose}>
      <div className="modal-panel" style={panel} onClick={event => event.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--font-size-lg)' }}>Отправить Протоколлер на встречу</h2>
          <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Закрыть" style={{ padding: 4 }}>✕</button>
        </div>

        {meetingId ? (
          <div role={startFailed ? 'alert' : 'status'} style={{ color: startFailed ? 'var(--color-error)' : 'var(--color-success)' }}>
            {startedStatus} <a href={`/meetings/${meetingId}`}>Открыть встречу</a>
          </div>
        ) : (
          <form onSubmit={start} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            <label htmlFor="manual-telemost-url">Ссылка Телемоста или Zoom</label>
            <input id="manual-telemost-url" className="input" type="url" required
              placeholder="https://telemost.yandex.ru/j/... или https://zoom.us/j/..." value={url}
              onChange={event => setUrl(event.target.value)} />
            <label htmlFor="manual-meeting-title">Название встречи (необязательно)</label>
            <input id="manual-meeting-title" className="input" maxLength={120}
              placeholder="Встреча по ссылке" value={title}
              onChange={event => setTitle(event.target.value)} />
            <button className="btn btn-primary" type="submit" disabled={busy || !url.trim()}>
              {busy ? 'Отправляем…' : 'Отправить Протоколлер'}
            </button>
          </form>
        )}

        <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: 'var(--space-4)' }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Через личное сообщение в Mattermost</div>
          {!mm?.enabled ? (
            <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
              Этот способ пока не включён. Отправить Протоколлер по ссылке можно выше.
            </p>
          ) : mm.linked ? (
            <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
              Аккаунт привязан. Пришлите {mm.bot_username ? <>@{mm.bot_username}</> : 'протоколлеру'}
              {' '}ссылку Телемоста или Zoom отдельным личным сообщением.
            </p>
          ) : (
            <div style={{ fontSize: 'var(--font-size-sm)', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span>Один раз привяжите вашу учётную запись Mattermost к Sifox.</span>
              <button className="btn btn-secondary" type="button" onClick={getCode} disabled={codeBusy}>
                {codeBusy ? 'Получаем код…' : 'Получить код привязки'}
              </button>
              {code && (
                <p style={{ margin: 0 }}>
                  Отправьте {mm.bot_username ? <>@{mm.bot_username}</> : 'протоколлеру'} в личку:
                  <br /><code>привязать {code}</code><br />
                  Код действует 15 минут. После подтверждения можно присылать просто ссылку.
                </p>
              )}
            </div>
          )}
        </div>
        {error && <div role="alert" style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>}
      </div>
    </div>
  )
}
