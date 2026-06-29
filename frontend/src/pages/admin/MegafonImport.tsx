import { useState, useRef, useEffect } from 'react'
import { api } from '../../api/client'

type Step = 'phone' | 'otp' | 'importing' | 'done' | 'error'

export default function MegafonImport() {
  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [jobId, setJobId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [imported, setImported] = useState<number | null>(null)
  const pollRef = useRef<number | null>(null)

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current) }, [])

  const start = async () => {
    setBusy(true); setError('')
    try {
      const r = await api.admin.megafonStart(phone.trim() || undefined)
      setJobId(r.job_id)
      setStep('otp')
    } catch (e: any) {
      setError(e.message || 'Не удалось начать вход')
    } finally { setBusy(false) }
  }

  const submitOtp = async () => {
    setBusy(true); setError('')
    try {
      await api.admin.megafonOtp(jobId, code.trim())
      setStep('importing')
      poll()
    } catch (e: any) {
      setError(e.message || 'Не удалось войти по коду')
    } finally { setBusy(false) }
  }

  const poll = () => {
    if (pollRef.current) window.clearInterval(pollRef.current)
    pollRef.current = window.setInterval(async () => {
      try {
        const st = await api.admin.megafonStatus(jobId)
        if (st.status === 'done') {
          window.clearInterval(pollRef.current!)
          setImported(st.stats?.imported ?? 0)
          setStep('done')
        } else if (st.status === 'error') {
          window.clearInterval(pollRef.current!)
          setError(st.error || 'Ошибка импорта')
          setStep('error')
        }
      } catch { /* keep polling */ }
    }, 3000)
  }

  const reset = () => {
    if (pollRef.current) window.clearInterval(pollRef.current)
    setStep('phone'); setCode(''); setJobId(''); setError(''); setImported(null)
  }

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Импорт звонков (МегаФон)</h1>
        <p className="page-subtitle">Подтягивает записи из rec.megafon.ru в раздел «Звонки». OTP нужен только для синхронизации новых.</p>
      </div>
      <div className="page-body">
        <div className="card" style={{ maxWidth: 460, display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          {error && <div style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>}

          {step === 'phone' && (
            <>
              <label style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>Номер телефона</label>
              <input className="input" value={phone} onChange={e => setPhone(e.target.value)} placeholder="+7 XXX XXX XX XX" />
              <button className="btn btn-primary" onClick={start} disabled={busy || !phone.trim()}>
                {busy ? 'Открываем…' : 'Получить код'}
              </button>
            </>
          )}

          {step === 'otp' && (
            <>
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                Код отправлен в SMS. Введите его, чтобы начать импорт.
              </div>
              <input className="input" value={code} onChange={e => setCode(e.target.value)} placeholder="Код из SMS" autoFocus />
              <button className="btn btn-primary" onClick={submitOtp} disabled={busy || !code.trim()}>
                {busy ? 'Входим…' : 'Импортировать'}
              </button>
              <button className="btn btn-ghost" onClick={reset}>Отмена</button>
            </>
          )}

          {step === 'importing' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span className="spinner" style={{ width: 20, height: 20 }} />
              <span>Импортируем новые звонки… Это может занять время (транскрибация в очереди).</span>
            </div>
          )}

          {step === 'done' && (
            <>
              <div style={{ color: 'var(--color-success, #0f8a4f)', fontWeight: 600 }}>
                Готово. Новых звонков: {imported}.
              </div>
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                Транскрибация и анализ идут в фоне — звонки появятся в разделе «Звонки» по мере обработки.
              </div>
              <button className="btn btn-secondary" onClick={reset}>Импортировать ещё</button>
            </>
          )}

          {step === 'error' && (
            <button className="btn btn-secondary" onClick={reset}>Попробовать снова</button>
          )}
        </div>
      </div>
    </div>
  )
}
