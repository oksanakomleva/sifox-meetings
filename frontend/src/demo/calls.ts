// Fake "Calls" dataset — DEMO ONLY. Powers the demo-only Звонки section
// (call feed → call detail → AI analysis). No backend, no real data.

export interface CallLine {
  time: string
  speaker: 'Вы' | 'Собеседник'
  text: string
}

export interface CallTaskGroup {
  assignee: string
  role: string
  items: string[]
}

export interface DemoCall {
  id: string
  title: string
  phone: string
  duration: string          // mm:ss
  datetime: string          // human label, e.g. "17 окт., 16:57"
  tasksCount?: number
  remindersCount?: number
  transcript: CallLine[]
  summary: string
  tasks: CallTaskGroup[]
  reminders: string[]
  note?: string
}

export interface AnalysisType {
  key: 'negotiation' | 'communication' | 'interlocutor' | 'chat'
  icon: string
  title: string
  desc: string
}

export const ANALYSIS_TYPES: AnalysisType[] = [
  { key: 'negotiation', icon: '🎯', title: 'Качество переговоров', desc: 'Покажет, как звучать увереннее и достигать результата быстрее.' },
  { key: 'communication', icon: '💬', title: 'Качество коммуникации', desc: 'Оценит конструктивность, дружелюбие и структуру диалога.' },
  { key: 'interlocutor', icon: '👥', title: 'Собеседник', desc: 'Поможет понять настроение и вовлечённость.' },
  { key: 'chat', icon: '❓', title: 'Чат с AI', desc: 'Задайте вопрос вручную и получите точный ответ.' },
]

const PHONE = '+7 (XXX) XXX-XX-XX'

export const DEMO_CALLS: DemoCall[] = [
  {
    id: 'call-1',
    title: 'Продажа квартиры Айгуль: встреча в 20:00',
    phone: PHONE,
    duration: '3:14',
    datetime: '17 окт., 16:57',
    tasksCount: 5,
    remindersCount: 1,
    transcript: [
      { time: '0:00', speaker: 'Вы', text: 'Алло. Здравствуйте.' },
      { time: '0:01', speaker: 'Собеседник', text: 'Здрасте.' },
      { time: '0:01', speaker: 'Вы', text: 'Квартира продаётся у вас, да?' },
      { time: '0:02', speaker: 'Собеседник', text: 'Да.' },
      { time: '0:03', speaker: 'Вы', text: 'Вы сами собственница?' },
      { time: '0:04', speaker: 'Собеседник', text: 'Квартира оформлена на дочь, я мать.' },
      { time: '0:05', speaker: 'Вы', text: 'Понял. По цене какие у вас ожидания?' },
      { time: '0:07', speaker: 'Собеседник', text: 'Хотим как договаривались, но можем уступить.' },
      { time: '0:12', speaker: 'Вы', text: 'Давайте встретимся сегодня в 20:00, посмотрим квартиру и обсудим условия.' },
      { time: '0:18', speaker: 'Собеседник', text: 'Хорошо, дочь тоже будет.' },
    ],
    summary:
      'Разговор с Айгуль о продаже квартиры. Айгуль является собственницей, но квартира оформлена на дочь. ' +
      'Обсуждалась окончательная цена и возможность уступки в 500 тысяч рублей, а при быстрой сделке — до миллиона. ' +
      'Договорились о встрече с риелтором сегодня в 20:00 для осмотра квартиры и обсуждения условий продажи, ' +
      'а также подписания риелторского договора. Дочь Айгуль, на которую оформлена квартира, будет присутствовать на встрече.',
    tasks: [
      { assignee: 'Юно', role: 'Менеджер', items: ['Организовать визит риелтора к Айгуль и её дочери сегодня в 20:00.', 'Созвониться с Айгуль в 17:00 для подтверждения встречи.'] },
      { assignee: 'Айгуль', role: 'Клиент', items: ['Присутствовать на встрече с риелтором сегодня в 20:00.', 'Обеспечить присутствие дочери на встрече.'] },
      { assignee: 'Дочь Айгуль', role: 'Собственница квартиры', items: ['Присутствовать на встрече с риелтором сегодня в 20:00.'] },
    ],
    reminders: ['Созвониться в 17:00 для подтверждения встречи.'],
    note: 'Встреча для осмотра квартиры, обсуждения условий продажи и подписания риелторского договора.',
  },
  {
    id: 'call-2',
    title: 'Документы для Edo и Egoist',
    phone: PHONE,
    duration: '0:59',
    datetime: '27 янв., 21:02',
    tasksCount: 3,
    transcript: [
      { time: '0:00', speaker: 'Вы', text: 'Привет, по документам для Edo и Egoist что готово?' },
      { time: '0:04', speaker: 'Собеседник', text: 'Шаблоны готовы, осталось подставить реквизиты.' },
      { time: '0:21', speaker: 'Вы', text: 'Ок, пришли мне на проверку до конца дня.' },
      { time: '0:44', speaker: 'Собеседник', text: 'Договорились.' },
    ],
    summary:
      'Короткий разговор по подготовке документов для контрагентов Edo и Egoist. Шаблоны готовы, ' +
      'осталось подставить реквизиты и отправить на проверку до конца дня.',
    tasks: [
      { assignee: 'Вы', role: 'Менеджер', items: ['Проверить документы до конца дня.'] },
      { assignee: 'Собеседник', role: 'Исполнитель', items: ['Подставить реквизиты в шаблоны.', 'Отправить документы на проверку.'] },
    ],
    reminders: [],
  },
  {
    id: 'call-3',
    title: 'Договоры на Арцуловской, подписание',
    phone: PHONE,
    duration: '0:49',
    datetime: '27 янв., 20:42',
    tasksCount: 3,
    remindersCount: 1,
    transcript: [
      { time: '0:00', speaker: 'Вы', text: 'По Арцуловской — когда подписываем договоры?' },
      { time: '0:05', speaker: 'Собеседник', text: 'Можем завтра в первой половине дня.' },
      { time: '0:20', speaker: 'Вы', text: 'Давайте в 11:00, я подготовлю экземпляры.' },
    ],
    summary:
      'Согласование времени подписания договоров по объекту на Арцуловской. Договорились подписать ' +
      'завтра в 11:00, экземпляры готовит менеджер.',
    tasks: [
      { assignee: 'Вы', role: 'Менеджер', items: ['Подготовить экземпляры договоров к 11:00.'] },
      { assignee: 'Собеседник', role: 'Клиент', items: ['Прийти на подписание завтра в 11:00.'] },
    ],
    reminders: ['Напомнить о подписании договоров завтра в 10:30.'],
  },
  {
    id: 'call-4',
    title: 'Обсуждение планов и проблем с iPhone',
    phone: PHONE,
    duration: '3:36',
    datetime: '12 дек., 19:45',
    transcript: [
      { time: '0:00', speaker: 'Вы', text: 'Что с поставкой iPhone, решили вопрос?' },
      { time: '0:08', speaker: 'Собеседник', text: 'Часть партии задерживается на неделю.' },
      { time: '0:30', speaker: 'Вы', text: 'Тогда давай перепланируем продажи и предупредим клиентов.' },
    ],
    summary:
      'Обсуждение задержки поставки партии iPhone и пересмотра планов продаж. Решили предупредить ' +
      'клиентов и сдвинуть сроки на неделю.',
    tasks: [
      { assignee: 'Вы', role: 'Менеджер', items: ['Предупредить клиентов о сдвиге сроков.', 'Перепланировать продажи на неделю.'] },
    ],
    reminders: [],
  },
]

export function demoCallById(id: string): DemoCall | undefined {
  return DEMO_CALLS.find(c => c.id === id)
}

// ── Structured AI analysis (static demo content) ──────────────────────────────

export interface AnalysisObservation {
  type: 'strength' | 'growth' | 'note'
  time: string
  text: string
}

export interface AnalysisResult {
  title: string
  score: number                 // 0..100
  status: { label: string; tone: 'good' | 'medium' | 'bad' }
  observations: AnalysisObservation[]
  recommendations: string[]
}

export const ANALYSIS_RESULTS: Record<Exclude<AnalysisType['key'], 'chat'>, AnalysisResult> = {
  negotiation: {
    title: 'Анализ качества переговоров',
    score: 72,
    status: { label: 'Уверенные переговоры', tone: 'good' },
    observations: [
      { type: 'strength', time: '0:12', text: 'Быстро вышли на ЛПР и зафиксировали следующий шаг — встречу с конкретным временем.' },
      { type: 'growth', time: '0:30', text: 'Цену обсудили поверхностно, без конкретных цифр.' },
      { type: 'note', time: '1:18', text: 'Договорённость о встрече закреплена с конкретным временем.' },
    ],
    recommendations: [
      'Проговаривайте условия вслух и резюмируйте их в конце звонка',
      'Фиксируйте цену цифрами, а не общими словами',
      'Подтвердите договорённости письмом сразу после звонка',
    ],
  },
  communication: {
    title: 'Анализ качества коммуникации',
    score: 67,
    status: { label: 'Средний, требует улучшений', tone: 'medium' },
    observations: [
      { type: 'growth', time: '0:00', text: 'Дисбаланс говорения: вы говорили 65% времени.' },
      { type: 'growth', time: '1:30', text: 'Задан только 1 уточняющий вопрос при сложном запросе.' },
      { type: 'growth', time: '2:20', text: 'Собеседник перебил вас — возможно, слишком длинная реплика.' },
      { type: 'strength', time: '1:00', text: 'Нейтральный и профессиональный тон на протяжении разговора.' },
    ],
    recommendations: [
      'Давайте собеседнику больше времени для ответа — целитесь на 50/50',
      'Используйте паузы после важных моментов',
      'Задавайте больше уточняющих вопросов в начале беседы',
    ],
  },
  interlocutor: {
    title: 'Анализ собеседника',
    score: 79,
    status: { label: 'Конструктивный разговор', tone: 'good' },
    observations: [
      { type: 'strength', time: '0:45', text: 'Собеседник задал 2 детализирующих вопроса — высокий интерес.' },
      { type: 'growth', time: '2:30', text: 'Появились признаки раздражения: повышенный тон.' },
      { type: 'note', time: '3:20', text: 'Собеседник 3 раза упомянул важность сроков.' },
    ],
    recommendations: [
      'Дайте собеседнику больше времени на обдумывание в напряжённых моментах',
      'Фокусируйтесь на фактах и конкретике — это ценится',
      'Отправьте детали по срокам и гарантиям в течение 24 часов',
    ],
  },
}

// ── Day / week roll-ups across all demo calls (for the demo Dashboard) ────────

export const DEMO_CALLS_DAY_SUMMARY =
  'Сегодня 2 звонка. По квартире Айгуль договорились о встрече в 20:00 — нужно подтвердить в 17:00. ' +
  'По документам для Edo и Egoist: подставить реквизиты и отправить на проверку до конца дня.'

export const DEMO_CALLS_WEEK_SUMMARY =
  'За неделю 4 звонка.\n' +
  '• Айгуль — продажа квартиры: вышли на встречу с осмотром, цена обсуждается.\n' +
  '• Арцуловская — подписание договоров согласовано на завтра 11:00.\n' +
  '• Edo / Egoist — документы готовятся, проверка сегодня.\n' +
  '• iPhone — задержка поставки на неделю, клиентов предупреждаем.'
