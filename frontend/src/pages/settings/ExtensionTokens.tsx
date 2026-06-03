import { useState } from 'react'
import { api } from '../../api/client'

function CopyChip({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1800)
        })
      }}
      title="Скопировать"
      style={{
        width: 'auto', margin: 0, padding: '2px 10px',
        background: 'var(--color-surface-2)', border: '1px solid var(--color-border)',
        borderRadius: 6, fontFamily: 'monospace', fontSize: '13px', cursor: 'pointer',
        color: 'var(--color-text)', fontWeight: 500, display: 'inline-flex', gap: 6, alignItems: 'center',
      }}
    >
      {text} <span style={{ fontSize: 11, color: copied ? 'var(--color-success, #16a34a)' : 'var(--color-text-secondary)' }}>
        {copied ? '✓ скопировано' : '⧉ копировать'}
      </span>
    </button>
  )
}

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

      <div className="page-body" style={{ maxWidth: 760 }}>
        {/* Installation */}
        <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
          <h3 style={{ marginTop: 0 }}>Установка</h3>
          <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.9 }}>
            <li>
              Скачайте архив и распакуйте его в постоянную папку (не удаляйте её потом —
              расширение работает прямо из неё).
              <div style={{ marginTop: 8 }}>
                <a
                  className="btn btn-primary"
                  href={api.extension.downloadUrl()}
                  style={{ display: 'inline-block', textDecoration: 'none' }}
                >
                  ⬇ Скачать расширение (.zip)
                </a>
              </div>
            </li>
            <li>
              Откройте страницу расширений Chrome. Скопируйте адрес и вставьте его в
              адресную строку браузера (Chrome не позволяет открыть её по ссылке):
              <div style={{ marginTop: 6 }}><CopyChip text="chrome://extensions" /></div>
            </li>
            <li>
              Включите <b>«Режим разработчика»</b> — переключатель в <b>правом верхнем углу</b>
              этой страницы.
            </li>
            <li>
              Нажмите <b>«Загрузить распакованное расширение»</b> (кнопка слева вверху) и
              выберите <b>распакованную папку</b> из шага 1.
            </li>
            <li>
              Закрепите иконку на панели: значок-пазл <b>🧩</b> справа от адресной строки →
              «булавка» напротив «Sifox Meetings Recorder».
            </li>
          </ol>
        </div>

        {/* First run */}
        <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
          <h3 style={{ marginTop: 0 }}>Первый запуск</h3>
          <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.9 }}>
            <li>Войдите в Sifox в этом браузере (как обычно, через Google) — расширение
              подхватит ваш вход автоматически, отдельный токен не нужен.</li>
            <li>Откройте попап расширения (иконка на панели) и нажмите
              <b> «Разрешить доступ к микрофону»</b> → в открывшейся вкладке подтвердите доступ.
              Это нужно один раз; без него в запись попадёт только звук вкладки, без вашего голоса.</li>
          </ol>
        </div>

        {/* Usage */}
        <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
          <h3 style={{ marginTop: 0 }}>Как пользоваться</h3>
          <p style={{ margin: '0 0 6px', fontWeight: 600 }}>Когда вы на встрече:</p>
          <ul style={{ margin: '0 0 12px', paddingLeft: 20, lineHeight: 1.8 }}>
            <li>Откройте вкладку со встречей (Google Meet, Zoom-web, любой звонок в браузере).</li>
            <li>Кликните иконку расширения → при необходимости поправьте название →
              <b> «● Начать запись»</b>.</li>
            <li>Идёт запись — на иконке появляется метка <b>REC</b>. Попап можно закрыть,
              запись продолжится в фоне. Держать его открытым не нужно.</li>
          </ul>
          <p style={{ margin: '0 0 6px', fontWeight: 600 }}>Когда встреча закончилась:</p>
          <ul style={{ margin: '0 0 12px', paddingLeft: 20, lineHeight: 1.8 }}>
            <li>Откройте попап и нажмите <b>«■ Остановить запись»</b>.</li>
            <li>Загрузка и обработка (расшифровка + протокол) происходят <b>только после
              «Стоп»</b>. Через 1–2 минуты встреча появится в разделе «Встречи».</li>
          </ul>
          <div className="banner" style={{
            background: 'var(--color-surface-2)', borderRadius: 8, padding: '10px 12px',
            fontSize: 13, lineHeight: 1.6,
          }}>
            <b>Важно:</b> запись существует, пока вы её не остановили.
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              <li>Закроете <b>вкладку встречи</b> — запись автоматически остановится и загрузится.</li>
              <li>Закроете <b>весь браузер</b> до «Стоп» — запись потеряется. Сначала нажмите «Стоп».</li>
            </ul>
          </div>
        </div>

        {/* Auth */}
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
