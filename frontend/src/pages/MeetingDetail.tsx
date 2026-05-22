import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import StatusBadge from '../components/StatusBadge'
import AudioPlayer from '../components/AudioPlayer'
import ChatWidget from '../components/ChatWidget'
import type { Meeting, ChatMessage } from '../types'

type Tab = 'protocol' | 'transcript' | 'audio' | 'chat'

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [transcript, setTranscript] = useState<string | null>(null)
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('protocol')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!id) return
    api.meetings.get(id)
      .then(m => {
        setMeeting(m)
        setLoading(false)
      })
      .catch(e => {
        setError(e.message)
        setLoading(false)
      })

    api.chat.history(id).then(r => setChatHistory(r.messages)).catch(() => {})
  }, [id])

  const loadTranscript = async () => {
    if (!id || transcript !== null) return
    try {
      const r = await api.meetings.transcript(id)
      setTranscript(r.transcript)
    } catch (e: any) {
      setTranscript('Транскрипция недоступна')
    }
  }

  const handleTabChange = (t: Tab) => {
    setTab(t)
    if (t === 'transcript') loadTranscript()
  }

  const fmt = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('ru-RU', {
      day: 'numeric', month: 'long', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  }

  if (loading) {
    return (
      <div className="main-content">
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
          <span className="spinner" style={{ width: 32, height: 32, borderWidth: 3 }} />
        </div>
      </div>
    )
  }

  if (error || !meeting) {
    return (
      <div className="main-content">
        <div className="page-body">
          <p style={{ color: 'var(--color-error)' }}>{error || 'Встреча не найдена'}</p>
          <button className="btn btn-secondary" style={{ marginTop: 16 }} onClick={() => navigate(-1)}>
            ← Назад
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="main-content">
      {/* Header */}
      <div className="page-header">
        <button
          className="btn btn-ghost"
          onClick={() => navigate(-1)}
          style={{ marginBottom: 'var(--space-3)', padding: 0, height: 'auto', gap: 6 }}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M10 3L5 8l5 5"/>
          </svg>
          Назад
        </button>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <div style={{ flex: 1 }}>
            <h1 className="page-title">{meeting.topic || meeting.title || 'Встреча'}</h1>
            <p className="page-subtitle">
              {fmt(meeting.start_time)}
              {meeting.end_time && (
                <> — {new Date(meeting.end_time).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</>
              )}
            </p>
          </div>
          <StatusBadge status={meeting.status} />
        </div>

        {/* Meta row */}
        <div style={{ marginTop: 'var(--space-3)', display: 'flex', flexWrap: 'wrap', gap: 'var(--space-4)', alignItems: 'center' }}>
          {meeting.meeting_type && (
            <span style={{
              fontSize: 'var(--font-size-xs)', fontWeight: 600,
              color: 'var(--color-text-secondary)',
              textTransform: 'uppercase', letterSpacing: '0.06em',
            }}>
              {meeting.meeting_type}
            </span>
          )}
          {meeting.tags?.map(t => (
            <span key={t} style={{
              fontSize: 'var(--font-size-xs)',
              background: 'var(--color-accent-6)',
              color: 'var(--color-accent)',
              padding: '2px 8px',
              borderRadius: 'var(--radius-full)',
              fontWeight: 500,
            }}>#{t}</span>
          ))}
          {meeting.participants?.filter(p => p.name !== 'Protocaller').map(p => (
            <span key={p.name} style={{
              fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)',
              display: 'flex', alignItems: 'center', gap: 4,
            }}>
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6">
                <circle cx="6" cy="4" r="2.5"/>
                <path d="M1 11c0-2.2 2.24-4 5-4s5 1.8 5 4"/>
              </svg>
              {p.name}
            </span>
          ))}
        </div>
      </div>

      <div className="page-body" style={{ paddingTop: 'var(--space-6)' }}>
        {/* Tabs */}
        <div className="tabs">
          {(['protocol', 'transcript', 'audio', 'chat'] as Tab[]).map(t => {
            const labels: Record<Tab, string> = {
              protocol: '📋 Протокол',
              transcript: '📝 Транскрипт',
              audio: '🎵 Аудио',
              chat: '💬 AI-чат',
            }
            const disabled: Record<Tab, boolean> = {
              protocol: meeting.status !== 'done' || !meeting.summary,
              transcript: meeting.status !== 'done',
              audio: !meeting.audio_path,
              chat: meeting.status !== 'done',
            }
            return (
              <button
                key={t}
                className={`tab ${tab === t ? 'active' : ''}`}
                onClick={() => !disabled[t] && handleTabChange(t)}
                disabled={disabled[t]}
                style={{ opacity: disabled[t] ? 0.4 : 1 }}
              >
                {labels[t]}
              </button>
            )
          })}
        </div>

        {/* Content */}
        {tab === 'protocol' && (
          <div style={{ maxWidth: 760 }}>
            {meeting.summary ? (
              <div style={{
                fontSize: 'var(--font-size-sm)',
                lineHeight: 'var(--line-height-relaxed)',
                color: 'var(--color-text)',
                whiteSpace: 'pre-wrap',
              }}>
                <MarkdownRenderer text={meeting.summary} />
              </div>
            ) : (
              <div className="empty-state">
                <div className="empty-state-icon">⏳</div>
                <div className="empty-state-title">Протокол ещё не готов</div>
              </div>
            )}
          </div>
        )}

        {tab === 'transcript' && (
          <div style={{ maxWidth: 760 }}>
            {transcript === null ? (
              <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}>
                <span className="spinner" style={{ width: 24, height: 24 }} />
              </div>
            ) : (
              <pre style={{
                fontFamily: 'var(--font-family)',
                fontSize: 'var(--font-size-sm)',
                lineHeight: 'var(--line-height-relaxed)',
                whiteSpace: 'pre-wrap',
                color: 'var(--color-text)',
              }}>
                {transcript}
              </pre>
            )}
          </div>
        )}

        {tab === 'audio' && meeting.audio_path && (
          <div style={{ maxWidth: 560 }}>
            <AudioPlayer
              src={api.meetings.audioUrl(meeting.id)}
              title={meeting.topic || meeting.title || undefined}
            />
            {meeting.audio_size && (
              <p style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)', marginTop: 8 }}>
                Размер: {(meeting.audio_size / 1_000_000).toFixed(1)} МБ
              </p>
            )}
          </div>
        )}

        {tab === 'chat' && (
          <div className="card" style={{ maxWidth: 760, padding: 0, overflow: 'hidden' }}>
            <ChatWidget meetingId={id} initialHistory={chatHistory} />
          </div>
        )}
      </div>
    </div>
  )
}

/** Very minimal Markdown renderer for headings, bold, lists */
function MarkdownRenderer({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <>
      {lines.map((line, i) => {
        if (line.startsWith('## ')) return (
          <h2 key={i} style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, margin: '16px 0 8px' }}>
            {line.slice(3)}
          </h2>
        )
        if (line.startsWith('### ')) return (
          <h3 key={i} style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, margin: '12px 0 4px' }}>
            {line.slice(4)}
          </h3>
        )
        if (line.startsWith('- ') || line.startsWith('* ')) return (
          <div key={i} style={{ display: 'flex', gap: 8, margin: '2px 0' }}>
            <span style={{ flexShrink: 0, color: 'var(--color-primary)' }}>•</span>
            <span dangerouslySetInnerHTML={{ __html: boldify(line.slice(2)) }} />
          </div>
        )
        if (/^\d+\. /.test(line)) return (
          <div key={i} style={{ display: 'flex', gap: 8, margin: '2px 0' }}>
            <span style={{ flexShrink: 0, minWidth: 20, color: 'var(--color-text-secondary)' }}>
              {line.match(/^(\d+)\./)?.[1]}.
            </span>
            <span dangerouslySetInnerHTML={{ __html: boldify(line.replace(/^\d+\. /, '')) }} />
          </div>
        )
        if (line === '') return <div key={i} style={{ height: 8 }} />
        return <p key={i} style={{ margin: '2px 0' }} dangerouslySetInnerHTML={{ __html: boldify(line) }} />
      })}
    </>
  )
}

function boldify(s: string) {
  return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}
