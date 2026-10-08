import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { api } from '../api/client'

interface Props {
  meetingId: string
  tags: string[]
  /** Notify parent so its local meeting state stays in sync. */
  onChange?: (tags: string[]) => void
}

const chipStyle: CSSProperties = {
  fontSize: 'var(--font-size-xs)',
  background: 'var(--color-accent-6)',
  color: 'var(--color-accent)',
  padding: '2px 8px',
  borderRadius: 'var(--radius-full)',
  fontWeight: 500,
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
}

/** Normalize the same way the backend does, so the UI matches what gets saved. */
function normalize(tag: string): string {
  return tag.trim().replace(/^#+/, '').trim().toLowerCase().replace(/\s+/g, ' ')
}

export default function TagEditor({ meetingId, tags, onChange }: Props) {
  const [current, setCurrent] = useState<string[]>(tags ?? [])
  const [known, setKnown] = useState<string[]>([])
  const [adding, setAdding] = useState(false)
  const [input, setInput] = useState('')
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { setCurrent(tags ?? []) }, [tags])

  useEffect(() => {
    api.meetings.knownTags().then(r => setKnown(r.tags)).catch(() => {})
  }, [])

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  const save = async (next: string[]) => {
    const prev = current
    setCurrent(next)            // optimistic (local only)
    setSaving(true)
    try {
      const r = await api.meetings.updateTags(meetingId, next)
      setCurrent(r.tags)
      onChange?.(r.tags)        // sync parent only after the server confirms
      // a freshly created tag should show up in suggestions immediately
      setKnown(k => Array.from(new Set([...k, ...r.tags])))
    } catch {
      setCurrent(prev)         // revert to the real previous state
    } finally {
      setSaving(false)
    }
  }

  const addTag = (raw: string) => {
    const t = normalize(raw)
    setInput('')
    if (!t || current.includes(t)) return
    save([...current, t])
  }

  const removeTag = (t: string) => save(current.filter(x => x !== t))

  const suggestions = known.filter(t => !current.includes(t))

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', alignItems: 'center' }}>
      {current.map(t => (
        <span key={t} style={chipStyle}>
          #{t}
          <button
            type="button"
            onClick={() => removeTag(t)}
            aria-label={`Удалить тег ${t}`}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: 0,
              color: 'var(--color-accent)', lineHeight: 1, fontSize: 14,
            }}
          >×</button>
        </span>
      ))}

      {adding ? (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <input
            ref={inputRef}
            list="known-tags-list"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { e.preventDefault(); addTag(input) }
              if (e.key === 'Escape') { setInput(''); setAdding(false) }
            }}
            onBlur={() => { if (input.trim()) addTag(input); setAdding(false) }}
            placeholder="тег…"
            style={{
              fontSize: 'var(--font-size-xs)', padding: '2px 8px',
              borderRadius: 'var(--radius-full)', border: '1px solid var(--color-border)',
              minWidth: 120,
            }}
          />
          <datalist id="known-tags-list">
            {suggestions.map(t => <option key={t} value={t} />)}
          </datalist>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          disabled={saving}
          style={{
            ...chipStyle,
            background: 'transparent',
            border: '1px dashed var(--color-border)',
            cursor: 'pointer',
          }}
        >+ тег</button>
      )}
    </div>
  )
}
