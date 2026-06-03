import { api } from '../../api/client'

export default function ExtensionTokens() {
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
        <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
          <h3 style={{ marginTop: 0 }}>Как подключить</h3>
          <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7 }}>
            <li>Скачайте расширение кнопкой ниже и распакуйте архив.</li>
            <li>Откройте <code>chrome://extensions</code>, включите «Режим разработчика».</li>
            <li>«Загрузить распакованное расширение» → выберите распакованную папку.</li>
            <li>Войдите в Sifox в этом же браузере (как обычно, через Google) — расширение само подхватит ваш вход, отдельный токен не нужен.</li>
            <li>В попапе расширения нажмите «Разрешить микрофон» (разово).</li>
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

        <div className="card" style={{ padding: 'var(--space-4)' }}>
          <h3 style={{ marginTop: 0 }}>Авторизация</h3>
          <p style={{ margin: 0, lineHeight: 1.6, color: 'var(--color-text-secondary)' }}>
            Расширение использует ваш вход в Sifox (Google). Пока вы залогинены в веб-приложении
            в этом браузере — расширение авторизовано автоматически. Если вы выйдете из аккаунта,
            расширение перестанет загружать записи, пока вы не войдёте снова.
          </p>
        </div>
      </div>
    </div>
  )
}
