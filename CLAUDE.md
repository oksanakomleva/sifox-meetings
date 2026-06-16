# Protocaller (sifox-meetings) — контекст проекта

FastAPI + React/TS сервис: бот записывает встречи Яндекс Телемост, делает транскрипт, протокол и AI-аналитику; плюс сбор/анализ коммуникаций (Mattermost + Gmail).

- **Прод:** https://sifox-meetings.up.railway.app (Railway)
- **Репо:** github.com/oksanakomleva/sifox-meetings, ветка **main** (деплой по push в main)
- **Локально:** `C:\Users\Oksana Komleva\telemost-web` (Windows, PowerShell)

## Архитектура

**Стек:** Python 3 / FastAPI, asyncpg, PostgreSQL; фронт React + TypeScript (Vite), в проде раздаётся бэкендом из `frontend/dist`. AI — OpenAI. Запись — Playwright/Chromium + Xvfb + PulseAudio + faster-whisper + ffmpeg.

**Основной поток записи:**
`calendar_sync` (каждые 5 мин) тянет события Google Calendar (`singleEvents=True`) → `meetings` в PostgreSQL → планировщик в `recorder.py` (каждые 30с) поднимает Chromium и заходит гостем «Protocaller» на Телемост → WAV → транскрипция (faster-whisper) → WAV→MP3 (WAV удаляется) → анализ OpenAI (тип/тема/теги + протокол).

**Фоновые циклы** (запускаются в `backend/main.py` lifespan через `asyncio.create_task`):
- `run_sync_loop` (calendar, 5 мин), `run_recording_scheduler` (30с)
- `run_mm_sync_loop` (Mattermost, 15 мин), `run_gmail_sync_loop` (Gmail, 30 мин)

**Auth:** Google OAuth — отдельные потоки логина и подключения календаря (scope `calendar.readonly`). Токены пользователей шифруются Fernet (`ENCRYPTION_KEY`). Админы — по `ADMIN_EMAILS`. Сессии в таблице `sessions`; превью-сессии помечаются `is_preview`.

**AI-модели (`config.py`):**
- `OPENAI_MODEL=gpt-4o` — шаг тегирования в анализаторе.
- `CHAT_MODEL=gpt-4.1` (окно ~1M) — чат, итоги недели, протокол, демо-сводки, AI-аналитик коммуникаций (нагрузка полными транскриптами).
- `CHAT_MAX_CONTEXT_CHARS` — бюджет контекста; `CHAT_CONTEXT_DAYS=90` — окно общего чата.

**Браузерное расширение** (`extension/`, раздел «Запись в браузере»): пишет звук активной вкладки + микрофон (`chrome.tabCapture` + offscreen), грузит на бэкенд, авторизуется кукой веб-сессии. Версия/баннер обновления — `GET /api/extension/version`.

**Коммуникации:** Mattermost (каналы, где состоит бот) + Gmail (Service Account + Domain-Wide Delegation, импersonation пользователей из `users`) → таблицы `mm_messages`/`email_messages`/`sync_state`. Админ-страница `/admin/communications`: просмотр + AI-аналитик (релевантный контекст через Postgres FTS).

## Ключевые директории
- `backend/api/` — роутеры: `auth, meetings, chat, admin, extension, communications`.
- `backend/services/` — `recorder.py`, `calendar_sync.py`, `analyzer.py`, `transcriber.py`, `mattermost_sync.py`, `gmail_sync.py`.
- `backend/database/` — `schema.sql` (применяется на старте), `models.py` (все запросы asyncpg), `connection.py`.
- `frontend/src/pages/` (+ `pages/admin/`, `pages/demo/`), `frontend/src/components/`, `frontend/src/api/client.ts`.

## Соглашения
**Бэкенд:** доступ к БД через `get_pool()` в `models.py`; роутеры тонкие. Таблицы — `TIMESTAMPTZ`, через `CREATE TABLE IF NOT EXISTS` + идемпотентные миграции (`ALTER … IF NOT EXISTS`) в `schema.sql` (отдельной системы миграций нет). Новые env — Optional в `config.py` (pydantic BaseSettings, `.env`), задокументировать в `.env.example`. Прямого доступа к проду-БД нет — диагностика через админ-эндпоинты с заголовком `X-Test-Api-Key` (см. `.env.test`).

**Фронт:** новый JSX-трансформ — **не** импортировать `React`; для типов использовать `import { type CSSProperties, type ReactNode } from 'react'`. API — через `api` из `client.ts`. Админ-страницы — в `pages/admin/`, ссылки в `Sidebar.tsx`.

**Проверка перед пушем:**
- `python -m py_compile <изменённые .py>`
- `python -m pytest backend/tests --ignore=backend/tests/e2e` (юнит-тесты чистых функций)
- фронт: `npx tsc --noEmit` во `frontend` (Node поставлен через winget; в сессии PowerShell он на PATH только если добавить `$env:ProgramFiles\nodejs`)
- коммит с `Co-Authored-By: Claude …`, push в `main` → Railway сам деплоит.

**Деплой/Railway:** переменные — в Variables сервиса; изменение переменной триггерит редеплой (иногда нужен ручной Redeploy). **Graceful-shutdown ждёт активные записи** (до ~55 мин), поэтому новый деплой может «висеть», пока идёт запись. Имена переменных без хвостовых пробелов (был баг с `MM_TOKEN `).

## Статус (на 2026-06-16)
Рабочее дерево чистое, всё в `main` задеплоено. Подробности — в `docs/progress.md`; важные решения и причины — в `docs/decisions.md`.
