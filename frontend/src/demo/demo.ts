// Demo mode: a presentation layer layered on top of the admin's "view as user"
// preview. When ON, the API client serves a curated demo dataset instead of real
// data, the "Расширение" nav is hidden, and (later) mock not-yet-built features
// are shown with demo data. The flag lives in localStorage and is only meaningful
// inside preview mode (it is cleared when leaving preview — see App.tsx).
import type { Meeting } from '../types'

const KEY = 'demo'

export function isDemoOn(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function setDemo(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, '1')
    else localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}

export function clearDemo(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}

// ── Demo dataset ──────────────────────────────────────────────────────────────

function daysAgo(d: number, hour = 11): string {
  const dt = new Date()
  dt.setDate(dt.getDate() - d)
  dt.setHours(hour, 0, 0, 0)
  return dt.toISOString()
}
function plusMinutes(iso: string, minutes: number): string {
  return new Date(new Date(iso).getTime() + minutes * 60000).toISOString()
}

const DEMO_TRANSCRIPT = `[00:00] Анна Соколова: Коллеги, добрый день. Давайте начнём с короткого статуса по пилоту.
[00:12] Дмитрий Орлов: По нашей стороне всё готово, развернули тестовый контур на прошлой неделе.
[00:34] Анна Соколова: Отлично. Какие сроки по интеграции API?
[00:41] Дмитрий Орлов: Базовые методы закроем за две недели, дальше — обратная связь и доработки.
[01:05] Мария Кузнецова: Со стороны заказчика важно, чтобы выгрузка отчётов работала из коробки.
[01:20] Анна Соколова: Принято. Зафиксируем как приоритет к следующему созвону.`

function mk(
  id: string,
  title: string,
  meeting_type: string,
  topic: string,
  tags: string[],
  summary: string,
  dayOffset: number,
  durationMin: number,
  participants: string[],
): Meeting {
  const start = daysAgo(dayOffset)
  return {
    id,
    title,
    start_time: start,
    end_time: plusMinutes(start, durationMin),
    status: 'done',
    summary,
    tags,
    topic,
    meeting_type,
    audio_path: `demo/${id}.mp3`,
    audio_size: 4_200_000,
    error_message: null,
    created_at: start,
    participants: participants.map(name => ({ name, email: null, user_id: null })),
    transcript: DEMO_TRANSCRIPT,
  }
}

export const DEMO_MEETINGS: Meeting[] = [
  mk(
    'demo-1', 'Демострой — пилот платформы', 'partner',
    'Обсуждение пилота платформы записи встреч с заказчиком Демострой',
    ['демострой', 'пилот', 'платформа'],
    `## Участники
Анна Соколова, Дмитрий Орлов, Мария Кузнецова

## Договорённости
- Развернуть тестовый контур до конца недели
- Интеграция базовых методов API — 2 недели
- Приоритет: выгрузка отчётов «из коробки»

## Следующие шаги
- Демо результатов на следующем созвоне`,
    1, 32, ['Анна Соколова', 'Дмитрий Орлов', 'Мария Кузнецова'],
  ),
  mk(
    'demo-2', 'АльфаТрейд — интеграция API', 'sales',
    'Подключение АльфаТрейд к API и условия пилота',
    ['альфатрейд', 'api', 'интеграция'],
    `## Контекст и цели
Подключение АльфаТрейд к платформе по API.

## Ключевые темы
- Авторизация и лимиты запросов
- Сроки пилота — 1 месяц

## Договорённости
- Прислать доступы до среды
- Назначить технического куратора`,
    2, 41, ['Анна Соколова', 'Игорь Лебедев'],
  ),
  mk(
    'demo-3', 'Внутренний синк по продукту', 'internal',
    'Планирование задач продуктовой команды на спринт',
    ['продукт', 'планирование'],
    `## Повестка
Планирование спринта.

## Принятые решения
- Берём в работу демо-режим и фильтр по тегам

## Задачи
- Орлов: каркас демо-режима
- Кузнецова: дизайн пустых состояний`,
    3, 28, ['Дмитрий Орлов', 'Мария Кузнецова'],
  ),
  mk(
    'demo-4', 'Ретро спринта 14', 'review',
    'Ретроспектива по итогам спринта 14',
    ['ретро', 'спринт'],
    `## Что разбирали
Итоги спринта 14.

## Что улучшить
- Раньше согласовывать требования с заказчиком

## Действия по итогам
- Ввести чек-лист готовности задачи`,
    5, 25, ['Анна Соколова', 'Дмитрий Орлов', 'Мария Кузнецова', 'Игорь Лебедев'],
  ),
]

const DEMO_UPCOMING: Meeting[] = [
  {
    ...mk('demo-up-1', 'Демострой — демо результатов', 'partner', 'Демонстрация результатов пилота', ['демострой', 'демо'], '', -1, 30, ['Анна Соколова', 'Мария Кузнецова']),
    status: 'pending', summary: null, transcript: undefined, audio_path: null, audio_size: null,
  },
  {
    ...mk('demo-up-2', 'Синк команды', 'internal', 'Еженедельный синк', ['команда'], '', -3, 30, ['Дмитрий Орлов', 'Мария Кузнецова']),
    status: 'pending', summary: null, transcript: undefined, audio_path: null, audio_size: null,
  },
]

export const DEMO_WEEK_SUMMARY =
  'За неделю — 4 встречи: продвинулся пилот с Демострой и переговоры с АльфаТрейд, ' +
  'внутри команды спланировали спринт и провели ретро.\n' +
  '• Демострой — пилот: развёрнут тестовый контур, интеграция API в работе (2 недели).\n' +
  '• АльфаТрейд — интеграция: согласованы условия пилота на месяц, ждём доступы.\n' +
  '• Продукт: в спринт взяты демо-режим и фильтр по тегам.\n' +
  '• Ретро спринта 14: договорились раньше согласовывать требования с заказчиком.'

export function demoMeetingById(id: string): Meeting | undefined {
  return [...DEMO_MEETINGS, ...DEMO_UPCOMING].find(m => m.id === id)
}

export const demo = {
  list: () => ({ meetings: DEMO_MEETINGS }),
  upcoming: () => ({ meetings: DEMO_UPCOMING }),
  week: () => ({ meetings: DEMO_MEETINGS }),
  weekSummary: () => ({ summary: DEMO_WEEK_SUMMARY, count: DEMO_MEETINGS.length }),
  get: (id: string) => demoMeetingById(id),
  transcript: () => ({ transcript: DEMO_TRANSCRIPT }),
}
