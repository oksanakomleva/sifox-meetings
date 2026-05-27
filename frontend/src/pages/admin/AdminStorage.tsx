import { useEffect, useState } from 'react'
import { api, StorageFile } from '../../api/client'

function fmtSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} ГБ`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} МБ`
  return `${(bytes / 1024).toFixed(0)} КБ`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export default function AdminStorage() {
  const [files, setFiles] = useState<StorageFile[]>([])
  const [totalBytes, setTotalBytes] = useState(0)
  const [audioDir, setAudioDir] = useState('')
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { load() }, [])

  const load = () => {
    setLoading(true)
    setError(null)
    api.admin.storage()
      .then(r => {
        setFiles(r.files)
        setTotalBytes(r.total_bytes)
        setAudioDir(r.audio_dir)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  const handleDelete = async (meetingId: string) => {
    setDeleting(meetingId)
    setConfirmId(null)
    try {
      await api.admin.deleteAudioFile(meetingId)
      await load()
    } catch (e: any) {
      alert('Ошибка удаления: ' + e.message)
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <div>
          <h1 className="page-title">Хранилище аудио</h1>
          <p className="page-subtitle">
            {audioDir} · {files.length} файлов · {fmtSize(totalBytes)}
          </p>
        </div>
        <button
          className="btn btn-secondary"
          onClick={load}
          disabled={loading}
        >
          {loading ? <span className="spinner" style={{ width: 14, height: 14 }} /> : '↻'} Обновить
        </button>
      </div>

      <div className="page-body">
        {error && (
          <div className="card" style={{ borderLeft: '3px solid var(--color-error)', padding: 'var(--space-4)', marginBottom: 'var(--space-4)', color: 'var(--color-error)' }}>
            ❌ {error}
          </div>
        )}

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}>
            <span className="spinner" style={{ width: 28, height: 28 }} />
          </div>
        ) : files.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📂</div>
            <div className="empty-state-title">Аудиофайлов нет</div>
            <p className="empty-state-text">Директория {audioDir} пуста</p>
          </div>
        ) : (
          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                  {['Встреча', 'Пользователь', 'Размер', 'Изменён', ''].map(h => (
                    <th key={h} style={{
                      padding: 'var(--space-3) var(--space-4)',
                      textAlign: 'left',
                      fontSize: 'var(--font-size-xs)',
                      fontWeight: 600,
                      color: 'var(--color-text-secondary)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.06em',
                      background: 'var(--color-surface-2)',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {files.map((f, i) => {
                  const ext = f.filename.endsWith('.mp3') ? '.mp3' : '.wav'
                  const short = f.meeting_id.slice(0, 8) + '…' + f.meeting_id.slice(-8) + ext
                  const isConfirming = confirmId === f.meeting_id
                  const isDeleting = deleting === f.meeting_id
                  // .wav > 1 ГБ — подозрительная зависшая запись; mp3 такого размера не бывает
                  const isLarge = ext === '.wav' && f.size_bytes > 1024 ** 3

                  return (
                    <tr
                      key={f.meeting_id}
                      style={{
                        borderBottom: i < files.length - 1 ? '1px solid var(--color-border)' : 'none',
                        background: isLarge ? 'rgba(239,68,68,0.04)' : undefined,
                      }}
                    >
                      <td style={{ padding: 'var(--space-3) var(--space-4)', maxWidth: 320 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {isLarge && (
                            <span title="Подозрительно большой файл — возможно зависшая запись" style={{ color: 'var(--color-error)', fontSize: 16, flexShrink: 0 }}>⚠️</span>
                          )}
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {f.title || <span style={{ color: 'var(--color-text-secondary)', fontStyle: 'italic' }}>Без названия</span>}
                            </div>
                            <code style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)' }}>
                              {short}
                            </code>
                          </div>
                        </div>
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-4)', maxWidth: 220 }}>
                        {f.user_name ? (
                          <div style={{ minWidth: 0 }}>
                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {f.user_name}
                            </div>
                            {f.user_email && (
                              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {f.user_email}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span style={{ color: 'var(--color-text-secondary)', fontStyle: 'italic', fontSize: 'var(--font-size-sm)' }}>—</span>
                        )}
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-4)', fontWeight: isLarge ? 700 : 400, color: isLarge ? 'var(--color-error)' : undefined, whiteSpace: 'nowrap' }}>
                        {fmtSize(f.size_bytes)}
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-4)', color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-sm)', whiteSpace: 'nowrap' }}>
                        {fmtDate(f.modified_at)}
                      </td>
                      <td style={{ padding: 'var(--space-3) var(--space-4)', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {isDeleting ? (
                          <span className="spinner" style={{ width: 16, height: 16 }} />
                        ) : isConfirming ? (
                          <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                            <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>Удалить?</span>
                            <button
                              className="btn btn-danger"
                              style={{ padding: '4px 12px', fontSize: 'var(--font-size-sm)' }}
                              onClick={() => handleDelete(f.meeting_id)}
                            >
                              Да
                            </button>
                            <button
                              className="btn btn-secondary"
                              style={{ padding: '4px 12px', fontSize: 'var(--font-size-sm)' }}
                              onClick={() => setConfirmId(null)}
                            >
                              Отмена
                            </button>
                          </span>
                        ) : (
                          <button
                            className="btn btn-secondary"
                            style={{ padding: '4px 12px', fontSize: 'var(--font-size-sm)', color: 'var(--color-error)' }}
                            onClick={() => setConfirmId(f.meeting_id)}
                          >
                            🗑 Удалить
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
