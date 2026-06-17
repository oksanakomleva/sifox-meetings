export interface StorageFile {
  filename: string
  meeting_id: string
  size_bytes: number
  modified_at: string
  title: string | null
  status: string | null
  meeting_start_time: string | null
  user_name: string | null
  user_email: string | null
}

import { isDemoOn, filterDemoMeetings, stripDemoTag, stripDemoFromTags } from '../demo/demo'

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { headers: extraHeaders, ...restOptions } = options ?? {}
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    ...restOptions,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  })
  if (res.status === 401) {
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login'
    }
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

function qs(p: Record<string, string | number | undefined | null>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(p)) {
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  }
  return u.toString()
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const api = {
  auth: {
    me: () => request<{ id: number; email: string; name: string; avatar_url: string | null; is_admin: boolean }>('/auth/me'),
    logout: () => request<void>('/auth/logout', { method: 'POST' }),
    loginUrl: () => '/api/auth/login',
    connectCalendarUrl: () => '/api/auth/connect-calendar',
  },

  // ── Meetings ──────────────────────────────────────────────────────────────────
  meetings: {
    // In demo mode: real meetings, but only those tagged "демо", with the "демо"
    // tag hidden. Other tags shown as usual.
    list: (limit = 20, offset = 0) =>
      isDemoOn()
        // Demo: the full curated "демо" set (preview-gated, not limited by the
        // preview user's per-meeting access). Hide the "демо" tag itself.
        ? request<{ meetings: import('../types').Meeting[] }>('/meetings/demo-list')
            .then(r => ({ meetings: r.meetings.map(stripDemoTag) }))
        : request<{ meetings: import('../types').Meeting[] }>(`/meetings?limit=${limit}&offset=${offset}`),
    get: (id: string) => {
      const p = request<import('../types').Meeting>(`/meetings/${id}`)
      return isDemoOn() ? p.then(stripDemoTag) : p
    },
    transcript: (id: string) =>
      request<{ transcript: string }>(`/meetings/${id}/transcript`),
    audioUrl: (id: string) => `/api/meetings/${id}/audio`,
    calendarStatus: () =>
      request<{ connected: boolean; has_enabled_calendar: boolean; calendar_count: number }>('/meetings/calendar-status'),
    week: () => {
      const p = request<{ meetings: import('../types').Meeting[] }>('/meetings/week')
      return isDemoOn() ? p.then(r => ({ meetings: filterDemoMeetings(r.meetings) })) : p
    },
    weekSummary: () =>
      request<{ summary: string | null; count: number }>('/meetings/week-summary'),
    // Demo-only: day/week summary over "демо" meetings + the fake calls (sent in).
    demoSummary: (period: 'day' | 'week', calls: { title: string; datetime: string; transcript: string }[]) =>
      request<{ summary: string | null }>('/meetings/demo-summary', {
        method: 'POST',
        body: JSON.stringify({ period, calls }),
      }),
    // Upcoming meetings are pending (no tags yet); in demo we show the real
    // planned meetings from the calendar as-is.
    upcoming: () =>
      request<{ meetings: import('../types').Meeting[] }>('/meetings/upcoming'),
    knownTags: () => {
      const p = request<{ tags: string[] }>('/meetings/tags')
      return isDemoOn() ? p.then(r => ({ tags: stripDemoFromTags(r.tags) })) : p
    },
    updateTags: (id: string, tags: string[]) =>
      request<{ tags: string[] }>(`/meetings/${id}/tags`, {
        method: 'PUT',
        body: JSON.stringify({ tags }),
      }),
  },

  // ── Admin ──────────────────────────────────────────────────────────────────────
  admin: {
    users: () => request<{ users: import('../types').User[] }>('/admin/users'),
    setAdmin: (userId: number, isAdmin: boolean) =>
      request<void>(`/admin/users/${userId}/admin`, {
        method: 'PATCH',
        body: JSON.stringify({ is_admin: isAdmin }),
      }),
    calendars: () => request<{ calendars: import('../types').Calendar[] }>('/admin/calendars'),
    setCalendarEnabled: (calId: number, enabled: boolean) =>
      request<void>(`/admin/calendars/${calId}`, {
        method: 'PATCH',
        body: JSON.stringify({ record_enabled: enabled }),
      }),
    syncCalendars: () => request<void>('/admin/calendars/sync', { method: 'POST' }),
    allMeetings: (limit = 100, offset = 0) =>
      request<{ meetings: import('../types').Meeting[] }>(`/admin/meetings?limit=${limit}&offset=${offset}`),
    upcoming: () =>
      request<{ meetings: import('../types').Meeting[] }>('/admin/upcoming'),
    reanalyzeMeeting: (meetingId: string) =>
      request<void>(`/admin/meetings/${meetingId}/reanalyze`, { method: 'POST' }),
    retranscribeMeeting: (meetingId: string) =>
      request<void>(`/admin/meetings/${meetingId}/retranscribe`, { method: 'POST' }),
    grantAccess: (userId: number, meetingId: string) =>
      request<void>('/admin/grant-access', {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, meeting_id: meetingId }),
      }),
    // Invitations
    listInvitations: () =>
      request<{ invitations: import('../types').Invitation[] }>('/admin/invitations'),
    createInvitation: (email: string) =>
      request<{ ok: boolean; invitation: import('../types').Invitation }>('/admin/invitations', {
        method: 'POST',
        body: JSON.stringify({ email }),
      }),
    deleteInvitation: (id: number) =>
      request<void>(`/admin/invitations/${id}`, { method: 'DELETE' }),
    previewSelf: () =>
      request<{ ok: boolean; url: string; expires_in: string }>('/admin/preview-self', { method: 'POST' }),
    storage: () =>
      request<{ files: StorageFile[]; total_bytes: number; audio_dir: string }>('/admin/storage'),
    deleteAudioFile: (meetingId: string) =>
      request<{ ok: boolean; freed_bytes: number }>(`/admin/storage/${meetingId}`, { method: 'DELETE' }),

    // ── Communications (Mattermost / Gmail) ──
    commsSync: () => request<{ ok: boolean; message: string }>('/admin/comms/sync', { method: 'POST' }),
    mmChannels: () => request<{ channels: import('../types').MmChannel[] }>('/admin/mm/channels'),
    emailUsers: () => request<{ users: string[] }>('/admin/email/users'),
    mmMessages: (p: Record<string, string | number | undefined>) =>
      request<{ messages: import('../types').MmMessage[] }>(`/admin/mm/messages?${qs(p)}`),
    emailMessages: (p: Record<string, string | number | undefined>) =>
      request<{ messages: import('../types').EmailMessage[] }>(`/admin/email/messages?${qs(p)}`),
    commsAiChat: (body: {
      question: string
      context_filters: Record<string, unknown>
      conversation_history: import('../types').CommsChatMessage[]
    }) => request<{ answer: string | null; error?: string }>('/admin/ai/chat', {
      method: 'POST', body: JSON.stringify(body),
    }),
  },

  // ── Extension (browser recorder) ───────────────────────────────────────────────
  extension: {
    downloadUrl: () => '/api/extension/download',
  },

  // ── Chat ──────────────────────────────────────────────────────────────────────
  chat: {
    // Demo chat starts empty and is not persisted — never load past history.
    history: (meetingId?: string) =>
      isDemoOn()
        ? Promise.resolve({ messages: [] as import('../types').ChatMessage[] })
        : request<{ messages: import('../types').ChatMessage[] }>(
            `/chat/history${meetingId ? `?meeting_id=${meetingId}` : ''}`
          ),
  },
}
