import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '../api/client'

interface Props {
  meetingId: string
  title: string | null
  summary: string
  onClose: () => void
  onSent: () => void
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 200,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
}
const panel: CSSProperties = {
  background: 'var(--color-surface)', border: '1px solid var(--color-border)',
  borderRadius: 'var(--radius-lg)', boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
  width: 'min(640px, 96vw)', maxHeight: '90vh', overflow: 'auto', padding: 'var(--space-6)',
  display: 'flex', flexDirection: 'column', gap: 'var(--space-4)',
}
const label: CSSProperties = {
  fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--color-text-secondary)',
  textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4, display: 'block',
}
const field: CSSProperties = {
  width: '100%', padding: '8px 10px', border: '1px solid var(--color-border)',
  borderRadius: 'var(--radius-md)', background: 'var(--color-surface-2)',
  color: 'var(--color-text)', fontSize: 'var(--font-size-sm)', fontFamily: 'inherit',
}

export default function SendProtocolModal({ meetingId, title, summary, onClose, onSent }: Props) {
  const [checking, setChecking] = useState(true)
  const [connected, setConnected] = useState(false)
  const [subject, setSubject] = useState(`Протокол встречи: ${title || ''}`.trim())
  const [recipientsText, setRecipientsText] = useState('')
  const [body, setBody] = useState(summary)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    api.auth.gmailSendStatus()
      .then(s => {
        setConnected(s.connected)
        if (s.connected) {
          api.meetings.protocolRecipients(meetingId)
            .then(r => setRecipientsText(r.recipients.join('\n')))
            .catch(() => {})
        }
      })
      .catch(e => setError(e.message))
      .finally(() => setChecking(false))
  }, [meetingId])

  const connectUrl = api.auth.connectGmailSendUrl(`/meetings/${meetingId}`)

  const send = async () => {
    setError('')
    const recipients = recipientsText.split('\n').map(s => s.trim()).filter(Boolean)
    if (recipients.length === 0) { setError('Укажите хотя бы одного получателя'); return }
    setSending(true)
    try {
      const r = await api.meetings.sendProtocol(meetingId, { subject, recipients, body_markdown: body })
      setDone(true)
      onSent()
      setTimeout(onClose, 1200)
      void r
    } catch (e: any) {
      // Token missing / revoked → offer to (re)connect.
      if (String(e.message).includes('gmail_send_not_connected')) {
        setConnected(false)
      } else {
        setError(e.message || 'Не удалось отправить')
      }
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={overlay} onClick={onClose}>
      <div style={panel} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, margin: 0 }}>Отправить протокол</h2>
          <button className="btn btn-ghost" onClick={onClose} style={{ padding: 4, height: 'auto' }}>✕</button>
        </div>

        {checking ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}>
            <span className="spinner" style={{ width: 24, height: 24 }} />
          </div>
        ) : !connected ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)', margin: 0 }}>
              Чтобы отправлять протоколы от вашего имени, один раз разрешите приложению отправку писем
              через ваш Google-аккаунт.
            </p>
            <a href={connectUrl}>
              <button className="btn btn-primary">Подключить отправку писем</button>
            </a>
          </div>
        ) : done ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--color-success, #16a34a)' }}>
            ✓ Протокол отправлен
          </div>
        ) : (
          <>
            <div>
              <label style={label}>Тема</label>
              <input style={field} value={subject} onChange={e => setSubject(e.target.value)} />
            </div>
            <div>
              <label style={label}>Получатели (по одному в строке)</label>
              <textarea
                style={{ ...field, minHeight: 90, resize: 'vertical' }}
                value={recipientsText}
                onChange={e => setRecipientsText(e.target.value)}
                placeholder="ivan@example.com&#10;external@partner.com"
              />
            </div>
            <div>
              <label style={label}>Текст протокола (Markdown)</label>
              <textarea
                style={{ ...field, minHeight: 240, resize: 'vertical', lineHeight: 1.5 }}
                value={body}
                onChange={e => setBody(e.target.value)}
              />
            </div>
            {error && <div style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)' }}>
              <button className="btn btn-secondary" onClick={onClose} disabled={sending}>Отмена</button>
              <button className="btn btn-primary" onClick={send} disabled={sending}>
                {sending ? 'Отправка…' : 'Отправить'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
