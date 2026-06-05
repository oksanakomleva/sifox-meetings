import { useState, useRef, useEffect } from 'react'
import { isDemoOn } from '../demo/demo'
import type { ChatMessage } from '../types'

interface Props {
  meetingId?: string
  initialHistory?: ChatMessage[]
  emptyPlaceholder?: string
}

export default function ChatWidget({ meetingId, initialHistory = [], emptyPlaceholder }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialHistory)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async () => {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')

    const userMsg: ChatMessage = { role: 'user', content: text, created_at: new Date().toISOString() }
    setMessages(prev => [...prev, userMsg])

    const assistantMsg: ChatMessage = { role: 'assistant', content: '', created_at: new Date().toISOString() }
    setMessages(prev => [...prev, assistantMsg])
    setStreaming(true)

    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, meeting_id: meetingId ?? null, demo: isDemoOn() }),
      })

      if (!res.ok) throw new Error('Failed')
      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') break
          try {
            const parsed = JSON.parse(data)
            if (parsed.delta) {
              setMessages(prev => {
                const copy = [...prev]
                copy[copy.length - 1] = {
                  ...copy[copy.length - 1],
                  content: copy[copy.length - 1].content + parsed.delta,
                }
                return copy
              })
            }
          } catch { /* ignore */ }
        }
      }
    } catch (e) {
      setMessages(prev => {
        const copy = [...prev]
        copy[copy.length - 1] = { ...copy[copy.length - 1], content: 'Ошибка при получении ответа.' }
        return copy
      })
    } finally {
      setStreaming(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Messages */}
      <div style={{
        flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column',
        gap: 'var(--space-4)', padding: 'var(--space-4)',
        minHeight: 240, maxHeight: 480,
      }}>
        {messages.length === 0 && (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 'var(--font-size-sm)', textAlign: 'center', marginTop: 'auto' }}>
            {emptyPlaceholder ?? 'Задайте вопрос о встрече'}
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{
            display: 'flex',
            justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '80%',
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: msg.role === 'user'
                ? 'var(--radius-lg) var(--radius-lg) var(--radius-sm) var(--radius-lg)'
                : 'var(--radius-lg) var(--radius-lg) var(--radius-lg) var(--radius-sm)',
              background: msg.role === 'user' ? 'var(--gradient-brand)' : 'var(--color-message-bg)',
              color: msg.role === 'user' ? '#fff' : 'var(--color-text)',
              fontSize: 'var(--font-size-sm)',
              lineHeight: 'var(--line-height-relaxed)',
              whiteSpace: 'pre-wrap',
              border: msg.role === 'assistant' ? '1px solid var(--color-accent-5)' : 'none',
            }}>
              {msg.content}
              {msg.role === 'assistant' && streaming && i === messages.length - 1 && (
                <span style={{ opacity: 0.5 }}>▌</span>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={{
        borderTop: '1px solid var(--color-border)',
        padding: 'var(--space-3)',
        display: 'flex',
        gap: 'var(--space-2)',
      }}>
        <input
          className="input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
          placeholder="Напишите вопрос..."
          disabled={streaming}
          style={{ flex: 1 }}
        />
        <button
          className="btn btn-primary"
          onClick={send}
          disabled={streaming || !input.trim()}
          style={{ flexShrink: 0 }}
        >
          {streaming ? <span className="spinner" style={{ width: 16, height: 16 }} /> : 'Отправить'}
        </button>
      </div>
    </div>
  )
}
