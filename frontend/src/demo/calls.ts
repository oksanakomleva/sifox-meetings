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

// Pre-written demo "AI analysis" per type (static, plausible — no real model).
export function callAnalysis(call: DemoCall, type: AnalysisType['key']): string {
  switch (type) {
    case 'negotiation':
      return (
        'Оценка переговоров: 7 / 10.\n\n' +
        '• Сильное: быстро вышли на ЛПР и зафиксировали следующий шаг — встречу с конкретным временем.\n' +
        '• Зона роста: цену обсудили поверхностно, не закрепили договорённость цифрами.\n' +
        '• Рекомендация: проговаривайте условия вслух и резюмируйте их в конце звонка, чтобы не было разночтений.'
      )
    case 'communication':
      return (
        'Качество коммуникации: конструктивно, дружелюбный тон.\n\n' +
        '• Структура диалога логичная: приветствие → уточнение → договорённость.\n' +
        '• Вы вели разговор и удерживали инициативу.\n' +
        '• Совет: добавляйте короткие подтверждающие реплики («верно понимаю, что…»), это снижает риск недопонимания.'
      )
    case 'interlocutor':
      return (
        'Собеседник: настрой положительный, вовлечённость высокая.\n\n' +
        '• Готов(а) к встрече и к компромиссу по цене.\n' +
        '• Решение принимается не единолично (есть второй собственник) — учитывайте это в следующих шагах.'
      )
    case 'chat':
      return ''  // handled by the inline mini-chat
  }
}
