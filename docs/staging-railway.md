# Изолированная тестовая среда Railway

Цель: проверять вход Zoom/Телемоста, запись, загрузку, расшифровку и интерфейс
на том же Docker-образе, что production, без ожидания свободного окна рабочих
встреч. В тестовую среду не переносим базу, аудиофайлы и секреты production.

## Состав и граница

Создайте в существующем Railway-проекте **постоянное пустое окружение** с
именем `staging` (не Duplicate Environment). В нём нужны только:

1. Сервис приложения из этого репозитория с веткой `codex/staging` и тем же
   `Dockerfile`/`railway.toml`.
2. Свой PostgreSQL без публичного TCP-доступа.
3. Свой Railway Volume, подключённый к приложению по пути `/audio`.
4. Отдельный публичный домен только для приложения.

В production проверьте, что сервис продолжает автоматически развёртываться
только из `main`. В staging — только из `codex/staging`. Не используйте Railway
Sync для переноса настроек из staging в production: переносим **код**, а не
переменные, базу или том.

## Переменные приложения в staging

Задайте их только в сервисе staging. Значения секретов не храните в Git.

| Переменная | Значение/правило |
| --- | --- |
| `APP_ENV` | `staging` |
| `BASE_URL` | `https://<отдельный staging-домен>` без завершающего `/` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` от **staging** PostgreSQL; адрес должен быть `*.railway.internal` |
| `AUDIO_DIR` | `/audio`, точно как путь staging Volume |
| `SECRET_KEY` | новый случайный секрет, не production |
| `ENCRYPTION_KEY` | новый Fernet-ключ, не production |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | отдельный OAuth-клиент для staging-домена |
| `OPENAI_API_KEY` | отдельный тестовый ключ/проект с лимитом расходов |
| `ADMIN_EMAILS` | только тестирующие администраторы, сначала Oksana |
| `ALLOWED_DOMAIN` | `sifox.com` (см. ограничение доступа ниже) |
| `STAGING_ALLOWED_EMAILS` | оставить пустым: доступ получат только адреса из `ADMIN_EMAILS`; для других тестировщиков задать явный список |
| `TEST_API_KEY` | отдельный staging-ключ для чтения состояния встреч; значение хранится локально в `.env.test.staging` |
| `GITLAB_PILOT_ENABLED` | `false` до настройки отдельного OAuth-приложения, затем `true` только для Oksana |
| `LIVE_ASSISTANT_ENABLED` | `false` на первом этапе; включать только для целевых тестов |
| `ALERTS_ENABLED`, `MM_RECORDER_COMMANDS_ENABLED`, `MEGAFON_ENABLED` | `false` |

Не задавайте `MM_TOKEN`, `MM_RECORDER_TOKEN`, `ALERT_MM_TOKEN`,
`GOOGLE_SERVICE_ACCOUNT_JSON` и другие production-токены. В режиме staging
приложение не запускает фоновые синхронизации календаря, Mattermost и Gmail,
отправку инцидентов и приём команд из Mattermost. Планировщик записей,
браузерный рекордер, обработка аудио и веб-интерфейс остаются рабочими.
Перед открытием базы приложение проверяет, что Railway-окружение называется
`staging`, `BASE_URL` не указывает на production, `DATABASE_URL` использует
внутренний Railway-адрес, а отдельный том смонтирован по `AUDIO_DIR`.

Для Google OAuth укажите разрешённый callback
`https://<staging-домен>/api/auth/callback`. Тестовый OAuth-клиент не должен
использовать production callback. Не подключайте рабочие календари в staging:
для проверки входа по ссылке они не нужны. Первоначальный доступ через Google
ограничен `STAGING_ALLOWED_EMAILS` (по умолчанию `ADMIN_EMAILS`) даже для
пользователей домена `sifox.com`. Не публикуйте staging-домен как обычный
сервис сотрудников; это пилотная среда без реальных данных.

## Проверка перед первым тестом

1. В Railway убедитесь, что staging-сервис подключён к `codex/staging`, а
   production-сервис — к `main`. Проверьте разные `DATABASE_URL` и разные тома.
2. Откройте staging `/health`: ожидаются `status: ok`, `database: ok`,
   `environment: staging` и SHA проверяемого коммита в `revision`.
3. Войдите через отдельный Google OAuth-клиент. Создайте только тестовую встречу
   по новой ссылке, где организатор находится в комнате и может впустить бота.
4. Подтвердите его имя в зале ожидания/среди участников. Не считайте статус
   `recording` доказательством транскрипции: после окончания встречи отдельно
   проверьте аудио, текст и протокол. Ненужные тестовые файлы удаляйте только
   из staging после проверки.

## Как переносить проверенное в production

1. Разработку фиксируйте отдельным коммитом и сначала отправляйте в
   `codex/staging`. Убедитесь, что Railway staging показывает тот же SHA.
2. Запустите серверные тесты и сборку интерфейса; затем проверьте реальный
   вход и полный цикл на staging.
3. Переносите **тот же проверенный код** в `main` через просмотренный merge/PR.
   Если при переносе добавились другие коммиты, повторите тест staging для
   итогового SHA.
4. Production-deploy выполняйте только в безопасное окно: нет `joining`,
   `recording`, `uploading`, `transcribing`, `analyzing` и встреч `pending` с
   началом в ближайшие 30 минут. После deploy проверьте `/health` и SHA.

Развёртывание staging не требует свободного окна production и не должно
перезапускать его приложение или менять его базу. За отдельные приложение,
PostgreSQL и том Railway будет начислять отдельное потребление ресурсов.

## Включить GitLab-пилот только для Oksana

Можно использовать OAuth-приложение, созданное в личной учётной записи GitLab:
администратор не обязателен, если GitLab разрешил сохранить такое приложение.
В нём должен быть **Confidential** client, redirect URI
`https://scintillating-rebirth-staging.up.railway.app/api/auth/gitlab/callback`
и только scopes `openid`, `profile`, `email`. Scope `api` не нужен.
Production callback в это приложение не добавляйте.

В Railway откройте **staging → сервис приложения → Variables** и задайте:

| Переменная | Значение |
| --- | --- |
| `GITLAB_ISSUER` | `https://gitlab.sifox.ru` |
| `GITLAB_CLIENT_ID` | Application ID этого GitLab OAuth-приложения |
| `GITLAB_CLIENT_SECRET` | Secret этого же приложения; только в Railway, не в чате и не в Git |
| `GITLAB_PILOT_EMAIL` | адрес Oksana в staging (`oksana.komleva@sifox.com`), разрешённый в `ADMIN_EMAILS`/`STAGING_ALLOWED_EMAILS` |
| `GITLAB_PILOT_USERNAME` | точный username из профиля Oksana в `gitlab.sifox.ru`, не отображаемое имя |
| `GITLAB_PILOT_SUB` | постоянный числовой GitLab User ID Oksana (`220`); нужен для прямого входа с закрытым публичным email |
| `GITLAB_PILOT_ENABLED` | `true`, только после заполнения всех переменных выше |

После применения переменных откройте страницу входа staging и сразу выберите
**Войти через GitLab** — Google и ручная привязка не требуются. Приложение
сверит подтверждённый OIDC `sub` с `GITLAB_PILOT_SUB` и username с
`GITLAB_PILOT_USERNAME`. Если пользователь с адресом `GITLAB_PILOT_EMAIL`
уже существует, вход сохранит его встречи и права; иначе создаст только эту
одну разрешённую учётную запись в пустом staging. Публичный email GitLab
может оставаться скрытым. Другие GitLab-аккаунты будут отклонены. В production
прежнее требование ручной привязки остаётся неизменным.

## Дать Codex доступ к состоянию staging

В корне локального проекта создан игнорируемый Git-файл `.env.test.staging` с
отдельным случайным `TEST_API_KEY`. Откройте его **локально**, скопируйте
только значение ключа и добавьте в **staging → сервис приложения → Variables →
TEST_API_KEY**. В production ничего не меняйте. Не присылайте ключ в чат и не
включайте этот файл в коммит. После применения переменной Codex сможет читать
`/health` и все страницы `/api/admin/meetings?limit=500&offset=...` в staging,
не используя production-ключ. Этот API-ключ технически допускает и записи:
Codex использует его только для чтения, но его следует хранить как секрет.
