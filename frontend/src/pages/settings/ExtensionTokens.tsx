import { useEffect, useState } from 'react'
import { api, ExtensionToken } from '../../api/client'

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export default function ExtensionTokens() {
  const [tokens, setTokens] = useState<ExtensionToken[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [freshToken, setFreshToken] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => { load() }, [])

  const load = () => {
    setLoading(true)
    setError(null)
    api.extension.listTokens()
      .then(r => setTokens(r.tokens))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  const handleCreate = async () => {
    setCreating(true)
    try {
      const r = await api.extension.createToken(newName.trim() || undefined)
      setFreshToken(r.token)
      setNewName('')
      setCopied(false)
      await load()
    } catch (e: any) {
      alert('Ошибка создания токена: ' + e.message)
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async (id: number) => {
    if (!confirm('Отозвать токен? Расширение с ним перестанет работать.')) return
    try {
      await api.extension.revokeToken(id)
      await load()
    } catch (e: any) {
      alert('Ошибка отзыва: ' + e.message)
    }
  }

  const copyToken = () => {
    if (!freshToken) return
    navigator.clipboard.writeText(freshToken).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <div>
          <h1 className="page-title">Браузерное расширение</h1>
          <p className="page-subtitle">
            Запись любой встречи в браузере (звук вкладки + микрофон) прямо в ваш аккаунт
          </p>
        </div>
      </div>

      <div className="page-body">
        {/* Instructions */}
        <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
          <h3 style={{ marginTop: 0 }}>Как подключить</h3>
          <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7 }}>
            <li>Скачайте расширение кнопкой ниже и распакуйте архив.</li>
            <li>Откройте <code>chrome://extensions</code>, включите «Режим разработчика».</li>
            <li>«Загрузить распакованное расширение» → выберите распакованную папку.</li>
            <li>Создайте токен ниже и вставьте его в попапе расширения.</li>
            <li>На вкладке со встречей нажмите «Запись» → по окончании запись появится в разделе «Встречи».</li>
          </ol>
          <a
            className="btn btn-primary"
            href={api.extension.downloadUrl()}
            style={{ marginTop: 'var(--space-3)', display: 'inline-block', textDecoration: 'none' }}
          >
            ⬇ Скачать расширение (.zip)
          </a>
        </div>

        {/* Fresh token banner */}
        {freshToken && (
          <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)', borderLeft: '3px solid var(--color-success, #16a34a)' }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>
              ✅ Токен создан — скопируйте сейчас, позже он не будет показан полностью
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <code style={{ padding: '8px 12px', background: 'var(--color-surface-2)', borderRadius: 6, wordBreak: 'break-all', flex: 1, minWidth: 240 }}>
                {freshToken}
              </code>
              <button className="btn btn-secondary" onClick={copyToken}>
                {copied ? '✓ Скопировано' : 'Копировать'}
              </button>
              <button className="btn btn-secondary" onClick={() => setFreshToken(null)}>Скрыть</button>
            </div>
          </div>
        )}

        {/* Create */}
        <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="Название (напр. «Рабочий ноутбук»)"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            style={{ flex: 1, minWidth: 200, padding: '8px 12px', borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-surface)' }}
          />
          <button className="btn btn-primary" onClick={handleCreate} disabled={creating}>
            {creating ? <span className="spinner" style={{ width: 14, height: 14 }} /> : '+ Создать токен'}
          </button>
        </div>

        {error && (
          <div className="card" style={{ borderLeft: '3px solid var(--color-error)', padding: 'var(--space-4)', marginBottom: 'var(--space-4)', color: 'var(--color-error)' }}>
            ❌ {error}
          </div>
        )}

        {/* List */}
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}>
            <span className="spinner" style={{ width: 28, height: 28 }} />
          </div>
        ) : tokens.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">🔑</div>
            <div className="empty-state-title">Токенов пока нет</div>
            <p className="empty-state-text">Создайте токен, чтобы подключить расширение</p>
          </div>
        ) : (
          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                  {['Название', 'Токен', 'Создан', 'Использован', ''].map(h => (
                    <th key={h} style={{
                      padding: 'var(--space-3) var(--space-4)', textAlign: 'left',
                      fontSize: 'var(--font-size-xs)', fontWeight: 600,
                      color: 'var(--color-text-secondary)', textTransform: 'uppercase',
                      letterSpacing: '0.06em', background: 'var(--color-surface-2)',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tokens.map((t, i) => (
                  <tr key={t.id} style={{
                    borderBottom: i < tokens.length - 1 ? '1px solid var(--color-border)' : 'none',
                    opacity: t.revoked ? 0.5 : 1,
                  }}>
                    <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                      {t.name || <span style={{ color: 'var(--color-text-secondary)', fontStyle: 'italic' }}>Без названия</span>}
                      {t.revoked && <span style={{ marginLeft: 8, fontSize: 'var(--font-size-xs)', color: 'var(--color-error)' }}>отозван</span>}
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)' }}>
                      <code>{t.token_preview}</code>
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-sm)', whiteSpace: 'nowrap' }}>
                      {fmtDate(t.created_at)}
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-sm)', whiteSpace: 'nowrap' }}>
                      {fmtDate(t.last_used)}
                    </td>
                    <td style={{ padding: 'var(--space-3) var(--space-4)', textAlign: 'right' }}>
                      {!t.revoked && (
                        <button
                          className="btn btn-secondary"
                          style={{ padding: '4px 12px', fontSize: 'var(--font-size-sm)', color: 'var(--color-error)' }}
                          onClick={() => handleRevoke(t.id)}
                        >
                          Отозвать
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
