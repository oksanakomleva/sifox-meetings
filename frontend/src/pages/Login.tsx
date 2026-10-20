import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { api } from '../api/client'

export default function Login() {
  const { user, loading } = useAuth()
  const navigate = useNavigate()
  const [gitlabEnabled, setGitlabEnabled] = useState(false)
  const [gitlabDirectLogin, setGitlabDirectLogin] = useState(false)
  const [gitlabAllUsers, setGitlabAllUsers] = useState(false)
  const query = new URLSearchParams(window.location.search)
  const error = query.get('error')
  const invited = query.get('invite') === '1'

  useEffect(() => {
    if (!loading && user && !invited) navigate('/', { replace: true })
  }, [user, loading, navigate, invited])

  useEffect(() => {
    api.auth.gitlabStatus().then(status => {
      setGitlabEnabled(status.enabled)
      setGitlabDirectLogin(status.direct_login)
      setGitlabAllUsers(status.all_users)
    }).catch(() => {})
  }, [])

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--gradient-brand)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 'var(--space-8)',
    }}>
      <div style={{
        background: 'var(--color-surface)',
        borderRadius: 'var(--radius-2xl)',
        padding: 'var(--space-12) var(--space-10)',
        width: '100%',
        maxWidth: 420,
        textAlign: 'center',
        boxShadow: 'var(--shadow-xl)',
      }}>
        {/* Logo */}
        <div style={{ marginBottom: 'var(--space-8)', display: 'flex', justifyContent: 'center' }}>
          <img src="/logo-dark.svg" alt="Sifox" style={{ height: 36 }} />
        </div>

        <h1 style={{
          fontFamily: 'var(--font-title)',
          fontSize: 'var(--font-size-3xl)',
          fontWeight: 'var(--font-weight-extrabold)',
          letterSpacing: 'var(--letter-spacing-title)',
          textTransform: 'uppercase',
          marginBottom: 'var(--space-2)',
          color: 'var(--color-text)',
        }}>
          Sifox Meetings
        </h1>
        <p style={{
          fontSize: 'var(--font-size-sm)',
          color: 'var(--color-text-secondary)',
          marginBottom: 'var(--space-8)',
        }}>
          Записи встреч и протоколы команды
        </p>

        {invited && (
          <p style={{ fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-6)' }}>
            Вы получили приглашение. Войдите через GitLab с адресом, на который пришло письмо.
          </p>
        )}

        {error && (
          <div style={{
            background: 'var(--color-error-bg)',
            color: 'var(--color-error)',
            borderRadius: 'var(--radius-md)',
            padding: 'var(--space-3) var(--space-4)',
            fontSize: 'var(--font-size-sm)',
            marginBottom: 'var(--space-6)',
          }}>
            {error === 'domain_not_allowed' && 'Для входа через GitLab нужен подтверждённый адрес @sifox.com или @sifox.ru. Вход через Google также доступен по приглашению.'}
            {error === 'invite_invalid' && 'Ссылка приглашения недействительна или истекла'}
            {error === 'invite_used' && 'Это приглашение уже было использовано'}
            {error === 'invite_email_mismatch' && 'Адрес выбранного аккаунта GitLab не совпадает с адресом приглашения. Выберите другой аккаунт.'}
            {error === 'oauth_failed' && 'Ошибка авторизации Google. Попробуйте ещё раз.'}
            {error === 'email_unverified' && 'Google не подтвердил адрес этого аккаунта. Выберите другой аккаунт или обратитесь к администратору.'}
            {error === 'account_conflict' && 'Этот адрес уже связан с другой учётной записью Google. Обратитесь к администратору.'}
            {error === 'state_expired' && 'Сессия авторизации устарела. Попробуйте ещё раз.'}
            {error === 'gitlab_failed' && 'Не удалось подтвердить вход через GitLab. Попробуйте ещё раз.'}
            {error === 'gitlab_state_expired' && 'Время входа через GitLab истекло. Попробуйте ещё раз.'}
            {error === 'gitlab_link_failed' && 'Не удалось связать аккаунты. Войдите через Google и повторите попытку.'}
            {error === 'gitlab_link_conflict' && 'Этот аккаунт GitLab уже связан с другой учётной записью.'}
            {error === 'gitlab_not_linked' && 'Сначала свяжите GitLab с вашей учётной записью Sifox после входа через Google.'}
            {error === 'gitlab_pilot_mismatch' && 'Эта учётная запись GitLab не входит в пилот. Проверьте выбранный аккаунт.'}
            {error === 'gitlab_pilot_id_mismatch' && 'GitLab вернул другой User ID. Проверьте выбранный аккаунт и значение GITLAB_PILOT_SUB в staging.'}
            {error === 'gitlab_identity_unverified' && 'GitLab не передал подтверждённый email. Проверьте выбранный аккаунт и настройку Public email в профиле GitLab.'}
            {error === 'gitlab_account_conflict' && 'Этот GitLab-аккаунт связан с другим адресом Sifox или адрес уже занят. Обратитесь к администратору.'}
            {error === 'gitlab_pilot_access_denied' && 'Не удалось подтвердить доступ пилотной учётной записи GitLab. Обратитесь к администратору тестовой среды.'}
          </div>
        )}

        <a
          href={gitlabEnabled ? api.auth.gitlabLoginUrl() : undefined}
          style={{ textDecoration: 'none', display: 'block' }}
        >
          <button className="btn btn-primary btn-lg" style={{ width: '100%' }} disabled={!gitlabEnabled}>
            Войти через GitLab
          </button>
        </a>

        <a
          href={api.auth.loginUrl()}
          style={{ textDecoration: 'none', display: 'block', marginTop: 'var(--space-3)' }}
        >
          <button className="btn btn-secondary btn-lg" style={{ width: '100%', gap: 'var(--space-3)' }}>
            <GoogleIcon />
            Войти через Google
          </button>
        </a>

        <p style={{
          marginTop: 'var(--space-6)',
          fontSize: 'var(--font-size-xs)',
          color: 'var(--color-text-muted)',
        }}>
          Вход через Google — только для сотрудников sifox.com.
          {!gitlabAllUsers && <> GitLab пока доступен только в пилоте{gitlabDirectLogin ? ' для одной учётной записи.' : ' для привязанной учётной записи.'}</>}
          {!gitlabEnabled && ' Вход через GitLab ещё не настроен на сервере.'}
        </p>
      </div>
    </div>
  )
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18">
      <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
      <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 009 18z" fill="#34A853"/>
      <path d="M3.964 10.71A5.41 5.41 0 013.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 000 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
      <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 00.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
    </svg>
  )
}
