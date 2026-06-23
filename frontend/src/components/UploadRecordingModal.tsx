import { useState, type CSSProperties } from 'react'
import { api } from '../api/client'

interface Props {
  onClose: () => void
  onUploaded: () => void
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 200,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
}
const panel: CSSProperties = {
  background: 'var(--color-surface)', border: '1px solid var(--color-border)',
  borderRadius: 'var(--radius-lg)', boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
  width: 'min(480px, 96vw)', padding: 'var(--space-6)',
  display: 'flex', flexDirection: 'column', gap: 'var(--space-4)',
}
const field: CSSProperties = {
  padding: '8px 10px', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
  background: 'var(--color-surface-2)', color: 'var(--color-text)', fontSize: 'var(--font-size-sm)',
}

export default function UploadRecordingModal({ onClose, onUploaded }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  const upload = async () => {
    if (!file) return
    setBusy(true); setError('')
    try {
      await api.admin.uploadRecording(file, title || file.name)
      setDone(true)
      onUploaded()
      setTimeout(onClose, 1500)
    } catch (e: any) {
      setError(e.message || 'Ошибка загрузки')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={overlay} onClick={onClose}>
      <div style={panel} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, margin: 0 }}>Загрузить запись</h2>
          <button className="btn btn-ghost" onClick={onClose} style={{ padding: 4, height: 'auto' }}>✕</button>
        </div>

        {done ? (
          <div style={{ color: 'var(--color-success, #16a34a)' }}>
            ✓ Загружено. Идёт транскрибация и анализ — встреча появится в списке.
          </div>
        ) : (
          <>
            <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)', margin: 0 }}>
              Видео/аудио (mp4, m4a, mp3, wav…). Звук сохранится как mp3, пройдёт транскрибацию и саммаризацию.
              Лимит 500 МБ — для крупного видео загрузите извлечённое аудио.
            </p>
            <input type="file" accept="video/*,audio/*" onChange={e => setFile(e.target.files?.[0] || null)} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>Название</label>
              <input style={field} value={title} onChange={e => setTitle(e.target.value)} placeholder={file?.name || 'Название встречи'} />
            </div>
            {error && <div style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)' }}>
              <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Отмена</button>
              <button className="btn btn-primary" onClick={upload} disabled={busy || !file}>
                {busy ? 'Загрузка…' : 'Загрузить'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
