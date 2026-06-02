import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import StatusBadge from '../components/StatusBadge'
import type { Meeting } from '../types'

// ── helpers ───────────────────────────────────────────────────────────────────

const fmtDateTime = (iso: string | null) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ru-RU', {
    day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
  })
}

const fmtTime = (iso: string | null) => {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

const isoDay = (iso: string | null) => {
  if (!iso) return ''
  return new Date(iso).toISOString().slice(0, 10)  // "YYYY-MM-DD"
}

// ── Mini Calendar ─────────────────────────────────────────────────────────────

interface CalendarProps {
  meetings: Meeting[]
  onSelectDay: (day: string | null) => void
  selectedDay: string | null
}

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const MONTHS_RU = [
  'Январь','Февраль','Март','Апрель','Май','Июнь',
  'Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь',
]

function MiniCalendar({ meetings, onSelectDay, selectedDay }: CalendarProps) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth()) // 0-based

  const meetingDays = useMemo(() => {
    const days = new Set<string>()
    meetings.forEach(m => { if (m.start_time) days.add(isoDay(m.start_time)) })
    return days
  }, [meetings])

  const prevMonth = () => {
    if (month === 0) { setYear(y => y - 1); setMonth(11) }
    else setMonth(m => m - 1)
  }
  const nextMonth = () => {
    if (month === 11) { setYear(y => y + 1); setMonth(0) }
    else setMonth(m => m + 1)
  }

  // Build grid: Monday-based weeks
  const firstDay = new Date(year, month, 1)
  const lastDay = new Date(year, month + 1, 0)
  // Monday=0 … Sunday=6
  const startOffset = (firstDay.getDay() + 6) % 7
  const totalDays = lastDay.getDate()

  const cells: (number | null)[] = [
    ...Array(startOffset).fill(null),
    ...Array.from({ length: totalDays }, (_, i) => i + 1),
  ]
  // Pad to full weeks
  while (cells.length % 7 !== 0) cells.push(null)

  const todayStr = today.toISOString().slice(0, 10)

  return (
    <div className="card" style={{ padding: 'var(--space-5)' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-4)' }}>
        <button
          onClick={prevMonth}
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 'var(--space-1)', color: 'var(--color-text-secondary)', lineHeight: 1 }}
        >
          ‹
        </button>
        <span style={{ fontWeight: 'var(--font-weight-semibold)', fontSize: 'var(--font-size-base)' }}>
          {MONTHS_RU[month]} {year}
        </span>
        <button
          onClick={nextMonth}
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 'var(--space-1)', color: 'var(--color-text-secondary)', lineHeight: 1 }}
        >
          ›
        </button>
      </div>

      {/* Weekday headers */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2, marginBottom: 4 }}>
        {WEEKDAYS.map(d => (
          <div key={d} style={{
            textAlign: 'center', fontSize: 'var(--font-size-xs)',
            color: 'var(--color-text-muted)', fontWeight: 600, padding: '2px 0',
          }}>{d}</div>
        ))}
      </div>

      {/* Day cells */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2 }}>
        {cells.map((day, i) => {
          if (!day) return <div key={`e-${i}`} />
          const dayStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
          const hasMeeting = meetingDays.has(dayStr)
          const isToday = dayStr === todayStr
          const isSelected = dayStr === selectedDay

          return (
            <button
              key={dayStr}
              onClick={() => onSelectDay(isSelected ? null : dayStr)}
              style={{
                position: 'relative',
                textAlign: 'center',
                padding: '6px 2px',
                borderRadius: 'var(--radius-sm)',
                border: isSelected ? '2px solid var(--color-accent)' : '2px solid transparent',
                background: isSelected
                  ? 'var(--color-accent-6)'
                  : isToday
                  ? 'var(--color-surface-2)'
                  : 'transparent',
                fontWeight: isToday ? 700 : 400,
                color: isSelected
                  ? 'var(--color-accent)'
                  : isToday
                  ? 'var(--color-text)'
                  : 'var(--color-text)',
                fontSize: 'var(--font-size-sm)',
                cursor: hasMeeting ? 'pointer' : 'default',
                opacity: hasMeeting ? 1 : 0.45,
              }}
            >
              {day}
              {hasMeeting && (
                <span style={{
                  position: 'absolute',
                  bottom: 2, left: '50%', transform: 'translateX(-50%)',
                  width: 4, height: 4, borderRadius: '50%',
                  background: isSelected ? 'var(--color-accent)' : 'var(--color-primary)',
                }} />
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Meetings page ─────────────────────────────────────────────────────────────

export default function Meetings() {
  const [tab, setTab] = useState<'done' | 'upcoming'>('done')
  const [doneMeetings, setDoneMeetings] = useState<Meeting[]>([])
  const [upcomingMeetings, setUpcomingMeetings] = useState<Meeting[]>([])
  const [search, setSearch] = useState('')
  const [selectedTags, setSelectedTags] = useState<string[]>([])
  const [selectedDay, setSelectedDay] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api.meetings.list(100),
      api.meetings.upcoming(),
    ])
      .then(([doneRes, upRes]) => {
        // Only show truly finished meetings in "Завершённые" tab
        setDoneMeetings(doneRes.meetings.filter((m: import('../types').Meeting) => m.status === 'done'))
        setUpcomingMeetings(upRes.meetings)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Tags present in the loaded meetings, most frequent first — for the filter bar.
  const availableTags = useMemo(() => {
    const counts = new Map<string, number>()
    for (const m of doneMeetings) {
      for (const t of m.tags ?? []) counts.set(t, (counts.get(t) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(e => e[0])
  }, [doneMeetings])

  const toggleTag = (t: string) =>
    setSelectedTags(prev => (prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]))

  // Filtered done meetings: text search AND (any selected tag matches)
  const filteredDone = useMemo(() => {
    const q = search.trim().toLowerCase()
    return doneMeetings.filter(m => {
      const matchesSearch = !q || (
        (m.topic ?? '').toLowerCase().includes(q) ||
        (m.title ?? '').toLowerCase().includes(q) ||
        (m.tags ?? []).some(t => t.toLowerCase().includes(q)) ||
        (m.summary ?? '').toLowerCase().includes(q)
      )
      const matchesTags = selectedTags.length === 0 ||
        (m.tags ?? []).some(t => selectedTags.includes(t))
      return matchesSearch && matchesTags
    })
  }, [doneMeetings, search, selectedTags])

  // Upcoming meetings filtered by selected calendar day
  const filteredUpcoming = useMemo(() => {
    if (!selectedDay) return upcomingMeetings
    return upcomingMeetings.filter(m => isoDay(m.start_time) === selectedDay)
  }, [upcomingMeetings, selectedDay])

  return (
    <div className="main-content">
      <div className="page-header">
        <h1 className="page-title">Встречи</h1>
        <p className="page-subtitle">Записи встреч, в которых вы участвовали</p>
      </div>

      <div className="page-body">
        {/* Tabs */}
        <div style={{
          display: 'flex', gap: 0, marginBottom: 'var(--space-5)',
          borderBottom: '2px solid var(--color-border)',
        }}>
          {(['done', 'upcoming'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                padding: 'var(--space-3) var(--space-5)',
                fontSize: 'var(--font-size-sm)',
                fontWeight: tab === t ? 'var(--font-weight-semibold)' : 400,
                color: tab === t ? 'var(--color-accent)' : 'var(--color-text-secondary)',
                borderBottom: tab === t ? '2px solid var(--color-accent)' : '2px solid transparent',
                marginBottom: -2,
                transition: 'all 0.15s',
              }}
            >
              {t === 'done' ? 'Завершённые' : 'Запланированные'}
              {t === 'upcoming' && upcomingMeetings.length > 0 && (
                <span style={{
                  marginLeft: 6, background: 'var(--color-accent)', color: '#fff',
                  borderRadius: 'var(--radius-full)', padding: '1px 6px',
                  fontSize: 'var(--font-size-xs)', fontWeight: 700,
                }}>
                  {upcomingMeetings.length}
                </span>
              )}
            </button>
          ))}
        </div>

        {loading && (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 'var(--space-12)' }}>
            <span className="spinner" style={{ width: 32, height: 32, borderWidth: 3 }} />
          </div>
        )}

        {!loading && error && (
          <div style={{ color: 'var(--color-error)', fontSize: 'var(--font-size-sm)' }}>{error}</div>
        )}

        {/* ── Завершённые tab ── */}
        {!loading && tab === 'done' && (
          <>
            {/* Search */}
            <div style={{ marginBottom: 'var(--space-4)', position: 'relative' }}>
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
                style={{
                  width: 16, height: 16, position: 'absolute', left: 12,
                  top: '50%', transform: 'translateY(-50%)',
                  color: 'var(--color-text-muted)', pointerEvents: 'none',
                }}>
                <circle cx="8.5" cy="8.5" r="5.5"/>
                <path d="M13.5 13.5l3 3"/>
              </svg>
              <input
                className="input"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Поиск по названию, тегам, содержанию…"
                style={{ paddingLeft: 36 }}
              />
            </div>

            {/* Tag filter */}
            {availableTags.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginBottom: 'var(--space-4)', alignItems: 'center' }}>
                {availableTags.map(t => {
                  const active = selectedTags.includes(t)
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => toggleTag(t)}
                      style={{
                        fontSize: 'var(--font-size-xs)',
                        padding: '2px 10px',
                        borderRadius: 'var(--radius-full)',
                        cursor: 'pointer',
                        fontWeight: 500,
                        border: active ? '1px solid var(--color-accent)' : '1px solid var(--color-border)',
                        background: active ? 'var(--color-accent)' : 'transparent',
                        color: active ? '#fff' : 'var(--color-text-secondary)',
                      }}
                    >#{t}</button>
                  )
                })}
                {selectedTags.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setSelectedTags([])}
                    style={{
                      fontSize: 'var(--font-size-xs)', background: 'none', border: 'none',
                      color: 'var(--color-accent)', cursor: 'pointer',
                    }}
                  >сбросить</button>
                )}
              </div>
            )}

            {filteredDone.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">{search ? '🔍' : '📅'}</div>
                <div className="empty-state-title">
                  {search ? 'Ничего не найдено' : 'Нет записей встреч'}
                </div>
                <p className="empty-state-text">
                  {search
                    ? 'Попробуйте другой запрос'
                    : 'Когда запись будет готова, она появится здесь автоматически.'}
                </p>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                {filteredDone.map(m => (
                  <MeetingCard key={m.id} meeting={m} onClick={() => navigate(`/meetings/${m.id}`)} />
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Запланированные tab ── */}
        {!loading && tab === 'upcoming' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
            <MiniCalendar
              meetings={upcomingMeetings}
              selectedDay={selectedDay}
              onSelectDay={setSelectedDay}
            />

            {/* Meeting list for selected day / all upcoming */}
            <div>
              {selectedDay && (
                <div style={{
                  fontSize: 'var(--font-size-sm)',
                  color: 'var(--color-text-secondary)',
                  marginBottom: 'var(--space-3)',
                }}>
                  {new Date(selectedDay + 'T12:00:00').toLocaleDateString('ru-RU', {
                    weekday: 'long', day: 'numeric', month: 'long',
                  })}
                  <button
                    onClick={() => setSelectedDay(null)}
                    style={{ marginLeft: 8, color: 'var(--color-accent)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 'var(--font-size-xs)' }}
                  >
                    сбросить
                  </button>
                </div>
              )}

              {filteredUpcoming.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-state-icon">📆</div>
                  <div className="empty-state-title">
                    {selectedDay ? 'Нет встреч в этот день' : 'Нет запланированных встреч'}
                  </div>
                  <p className="empty-state-text">
                    Встречи из вашего Google Календаря появятся здесь автоматически.
                  </p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                  {filteredUpcoming.map(m => (
                    <div key={m.id} className="card">
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 'var(--font-weight-semibold)', marginBottom: 'var(--space-1)' }}>
                            {m.title || m.topic || 'Без названия'}
                          </div>
                          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
                            {fmtDateTime(m.start_time)}
                            {m.end_time && <> — {fmtTime(m.end_time)}</>}
                          </div>
                        </div>
                        <StatusBadge status={m.status} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Meeting card (завершённые) ─────────────────────────────────────────────────

function MeetingCard({ meeting: m, onClick }: { meeting: Meeting; onClick: () => void }) {
  return (
    <div className="card card-hover" onClick={onClick}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-4)' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontWeight: 'var(--font-weight-semibold)',
            fontSize: 'var(--font-size-base)',
            marginBottom: 'var(--space-1)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {m.title || m.topic || 'Без названия'}
          </div>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-secondary)' }}>
            {fmtDateTime(m.start_time)}
            {m.end_time && (
              <> — {new Date(m.end_time).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</>
            )}
          </div>
          {m.tags && m.tags.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-1)', marginTop: 'var(--space-2)' }}>
              {m.tags.map(tag => (
                <span key={tag} style={{
                  fontSize: 'var(--font-size-xs)',
                  background: 'var(--color-accent-6)',
                  color: 'var(--color-accent)',
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-full)',
                  fontWeight: 'var(--font-weight-medium)',
                }}>
                  #{tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <StatusBadge status={m.status} />
      </div>
    </div>
  )
}
