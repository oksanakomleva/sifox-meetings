import { useEffect, useState, type CSSProperties } from 'react'
import { api } from '../../api/client'
import type { MmMessage, MmChannel, EmailMessage, CommsChatMessage } from '../../types'

type Tab = 'mm' | 'email'

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}
function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}
function fmt(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function Expandable({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > 200
  return (
    <span style={{ fontSize: 'var(--font-size-sm)' }}>
      {open || !long ? text : text.slice(0, 200) + '… '}
      {long && (
        <button onClick={() => setOpen(o => !o)} style={{ background: 'none', border: 'none', color: 'var(--color-accent)', cursor: 'pointer', padding: 0, fontSize: 'var(--font-size-xs)' }}>
          {open ? 'свернуть' : 'ещё'}
        </button>
      )}
    </span>
  )
}

export default function Communications() {
  const [tab, setTab] = useState<Tab>('mm')
  const [syncing, setSyncing] = useState(false)

  // Left panel — filters + data
  const [q, setQ] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [channelId, setChannelId] = useState('')
  const [userEmail, setUserEmail] = useState('')
  const [channels, setChannels] = useState<MmChannel[]>([])
  const [emailUsers, setEmailUsers] = useState<string[]>([])
  const [mmRows, setMmRows] = useState<MmMessage[]>([])
  const [emailRows, setEmailRows] = useState<EmailMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [offset, setOffset] = useState(0)
  const LIMIT = 50

  // Right panel — AI chat
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState<CommsChatMessage[]>([])
  const [asking, setAsking] = useState(false)
  const [srcMm, setSrcMm] = useState(true)
  const [srcGmail, setSrcGmail] = useState(true)
  const [ctxFrom, setCtxFrom] = useState(isoDaysAgo(7))
  const [ctxTo, setCtxTo] = useState(todayIso())

  useEffect(() => {
    api.admin.mmChannels().then(r => setChannels(r.channels)).catch(() => {})
    api.admin.emailUsers().then(r => setEmailUsers(r.users)).catch(() => {})
  }, [])

  const loadData = async (reset: boolean) => {
    setLoading(true)
    const off = reset ? 0 : offset
    try {
      if (tab === 'mm') {
        const r = await api.admin.mmMessages({ q, channel_id: channelId, date_from: dateFrom, date_to: dateTo, limit: LIMIT, offset: off })
        setMmRows(reset ? r.messages : [...mmRows, ...r.messages])
      } else {
        const r = await api.admin.emailMessages({ q, user_email: userEmail, date_from: dateFrom, date_to: dateTo, limit: LIMIT, offset: off })
        setEmailRows(reset ? r.messages : [...emailRows, ...r.messages])
      }
      setOffset(off + LIMIT)
    } catch { /* ignore */ }
    setLoading(false)
  }

  // Reload when tab changes; reset filters' offset.
  useEffect(() => { setOffset(0); loadData(true) /* eslint-disable-line */ }, [tab])

  const applyFilters = () => { setOffset(0); loadData(true) }

  const sync = async () => {
    setSyncing(true)
    try { await api.admin.commsSync() } catch { /* ignore */ }
    setTimeout(() => setSyncing(false), 1500)
  }

  const ask = async () => {
    const text = question.trim()
    if (!text || asking) return
    setQuestion('')
    const newHist: CommsChatMessage[] = [...history, { role: 'user', content: text }]
    setHistory(newHist)
    setAsking(true)
    try {
      const sources = [srcMm ? 'mattermost' : '', srcGmail ? 'gmail' : ''].filter(Boolean)
      const r = await api.admin.commsAiChat({
        question: text,
        context_filters: { sources, date_from: ctxFrom, date_to: ctxTo },
        conversation_history: history,
      })
      setHistory([...newHist, { role: 'assistant', content: r.answer || r.error || 'Ошибка' }])
    } catch (e: any) {
      setHistory([...newHist, { role: 'assistant', content: 'Ошибка: ' + (e.message || '') }])
    }
    setAsking(false)
  }

  return (
    <div className="main-content">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
        <div>
          <h1 className="page-title">Коммуникации</h1>
          <p className="page-subtitle">Сообщения из Mattermost и почты + AI-аналитик по ним</p>
        </div>
        <button className="btn btn-secondary" onClick={sync} disabled={syncing}>
          {syncing ? 'Синхронизация…' : '↻ Синхронизировать'}
        </button>
      </div>

      <div className="page-body" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 420px)', gap: 'var(--space-5)', alignItems: 'start' }}>
        {/* ── Left: data ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            {([['mm', 'Mattermost'], ['email', 'Почта']] as [Tab, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setTab(k)} style={{
                fontSize: 'var(--font-size-sm)', fontWeight: 600, padding: '6px 14px',
                borderRadius: 'var(--radius-full)', cursor: 'pointer',
                border: tab === k ? '1px solid var(--color-accent)' : '1px solid var(--color-border)',
                background: tab === k ? 'var(--color-accent)' : 'transparent',
                color: tab === k ? '#fff' : 'var(--color-text-secondary)',
              }}>{label}</button>
            ))}
          </div>

          {/* Filters */}
          <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' }}>
            <input className="input" placeholder="Поиск по тексту…" value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applyFilters()} style={{ flex: 1, minWidth: 160 }} />
            <input className="input" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ width: 150 }} />
            <input className="input" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ width: 150 }} />
            {tab === 'mm' ? (
              <select className="input" value={channelId} onChange={e => setChannelId(e.target.value)} style={{ width: 200 }}>
                <option value="">Все каналы</option>
                {channels.map(c => <option key={c.channel_id} value={c.channel_id}>{c.channel_name || c.channel_id}</option>)}
              </select>
            ) : (
              <select className="input" value={userEmail} onChange={e => setUserEmail(e.target.value)} style={{ width: 200 }}>
                <option value="">Все пользователи</option>
                {emailUsers.map(u => <option key={u} value={u}>{u}</option>)}
              </select>
            )}
            <button className="btn btn-primary" onClick={applyFilters}>Применить</button>
          </div>

          {/* List */}
          <div className="card" style={{ padding: 0 }}>
            {loading && (mmRows.length === 0 && emailRows.length === 0) ? (
              <div style={{ padding: 'var(--space-6)', textAlign: 'center' }}><span className="spinner" style={{ width: 24, height: 24 }} /></div>
            ) : tab === 'mm' ? (
              mmRows.length === 0 ? <Empty /> : mmRows.map(m => (
                <div key={m.id} style={rowStyle}>
                  <div style={metaStyle}>{fmt(m.created_at)} · @{m.username || '—'} · #{m.channel_name || m.channel_id}</div>
                  <Expandable text={m.message} />
                </div>
              ))
            ) : (
              emailRows.length === 0 ? <Empty /> : emailRows.map(e => (
                <div key={e.id} style={rowStyle}>
                  <div style={metaStyle}>{fmt(e.received_at)} · от {e.from_email || '—'} · кому {(e.to_emails || []).join(', ')}</div>
                  <div style={{ fontWeight: 600, fontSize: 'var(--font-size-sm)', margin: '2px 0' }}>{e.subject || '(без темы)'}</div>
                  <Expandable text={e.body_text || ''} />
                </div>
              ))
            )}
          </div>
          <button className="btn btn-secondary" onClick={() => loadData(false)} disabled={loading}>Показать ещё</button>
        </div>

        {/* ── Right: AI chat ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ fontSize: 'var(--font-size-lg)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>AI-аналитик</h2>
            <button onClick={() => setHistory([])} style={{ background: 'none', border: 'none', color: 'var(--color-accent)', cursor: 'pointer', fontSize: 'var(--font-size-sm)' }}>Новый диалог</button>
          </div>

          {/* Context controls */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', fontSize: 'var(--font-size-sm)' }}>
            <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
              <label><input type="checkbox" checked={srcMm} onChange={e => setSrcMm(e.target.checked)} /> Mattermost</label>
              <label><input type="checkbox" checked={srcGmail} onChange={e => setSrcGmail(e.target.checked)} /> Почта</label>
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
              <span style={{ color: 'var(--color-text-secondary)' }}>Период:</span>
              <input className="input" type="date" value={ctxFrom} onChange={e => setCtxFrom(e.target.value)} style={{ flex: 1 }} />
              <input className="input" type="date" value={ctxTo} onChange={e => setCtxTo(e.target.value)} style={{ flex: 1 }} />
            </div>
          </div>

          {/* History */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', minHeight: 200, maxHeight: 420, overflowY: 'auto' }}>
            {history.length === 0 && <div style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-sm)' }}>Задайте вопрос по данным за выбранный период.</div>}
            {history.map((m, i) => (
              <div key={i} style={{
                alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '90%',
                padding: '6px 10px', borderRadius: 'var(--radius-md)', fontSize: 'var(--font-size-sm)',
                whiteSpace: 'pre-wrap',
                background: m.role === 'user' ? 'var(--color-accent)' : 'var(--color-surface-2)',
                color: m.role === 'user' ? '#fff' : 'var(--color-text)',
              }}>{m.content}</div>
            ))}
            {asking && <div style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-sm)' }}><span className="spinner" style={{ width: 14, height: 14 }} /> Думаю…</div>}
          </div>

          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <input className="input" value={question} onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && ask()} placeholder="Ваш вопрос…" style={{ flex: 1 }} />
            <button className="btn btn-primary" onClick={ask} disabled={asking}>Спросить</button>
          </div>
        </div>
      </div>
    </div>
  )
}

const rowStyle: CSSProperties = {
  padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)',
}
const metaStyle: CSSProperties = {
  fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)', marginBottom: 2,
}

function Empty() {
  return <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 'var(--font-size-sm)' }}>Нет данных</div>
}
