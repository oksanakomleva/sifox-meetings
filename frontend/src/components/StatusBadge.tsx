import type { MeetingStatus } from '../types'

const STATUS_LABELS: Record<MeetingStatus, string> = {
  pending:     'В ожидании',
  recording:   '● Запись',
  transcribing:'Транскрипция...',
  analyzing:   'Анализ...',
  done:        'Готово',
  error:       'Ошибка',
  no_show:     'Не состоялась',
}

const STATUS_CLASSES: Record<MeetingStatus, string> = {
  pending:     'badge-pending',
  recording:   'badge-recording',
  transcribing:'badge-pending',
  analyzing:   'badge-pending',
  done:        'badge-done',
  error:       'badge-error',
  no_show:     'badge-other',
}

export default function StatusBadge({ status }: { status: MeetingStatus }) {
  return (
    <span className={`badge ${STATUS_CLASSES[status] ?? 'badge-other'}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  )
}
