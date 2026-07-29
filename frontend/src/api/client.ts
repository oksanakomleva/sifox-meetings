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
    // Don't bounce to /login on the public, password-gated share page — an
    // anonymous visitor's /auth/me 401 must not hijack /share/:token.
    const p = window.location.pathname
    if (!p.startsWith('/login') && !p.startsWith('/share/')) {
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
    gmailSendStatus: () => request<{ connected: boolean }>('/auth/gmail-send-status'),
    connectGmailSendUrl: (next: string) => `/api/auth/connect-gmail-send?next=${encodeURIComponent(next)}`,
  },

  // ── Public share (no auth; raw fetch so a 401 doesn't redirect to /login) ──
  share: {
    unlock: async (token: string, password: string) => {
      const r = await fetch(`/api/share/${token}/unlock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail
        throw new Error(detail || (r.status === 401 ? 'Неверный пароль' : 'Ошибка'))
      }
      return r.json() as Promise<{
        title: string | null; start_time: string | null; end_time: string | null
        summary: string | null; transcript: string | null
        has_audio: boolean; audio_url: string | null
      }>
    },
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
    protocolRecipients: (id: string) =>
      request<{ recipients: string[] }>(`/meetings/${id}/protocol-recipients`),
    sendProtocol: (id: string, payload: { subject: string; recipients: string[]; body_markdown: string }) =>
      request<{ ok: boolean; sent_to: number }>(`/meetings/${id}/send-protocol`, {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    audioUrl: (id: string) => `/api/meetings/${id}/audio`,
    audioDownloadUrl: (id: string) => `/api/meetings/${id}/audio?download=1`,
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
    setMeetingAssistantEnabled: (meetingId: string, enabled: boolean) =>
      request<{ ok: boolean; meeting_id: string; assistant_enabled: boolean }>(
        `/admin/meetings/${meetingId}/assistant`,
        {
          method: 'PATCH',
          body: JSON.stringify({ assistant_enabled: enabled }),
        },
      ),
    setMeetingPublicInfoEnabled: (meetingId: string, enabled: boolean) =>
      request<{ ok: boolean; meeting_id: string; public_info_enabled: boolean }>(
        `/admin/meetings/${meetingId}/assistant-public-info`,
        {
          method: 'PATCH',
          body: JSON.stringify({ public_info_enabled: enabled }),
        },
      ),
    liveQa: (meetingId: string) =>
      request<{
        live_assistant_enabled: boolean
        live_assistant_speak: boolean
        live_assistant_all_meetings: boolean
        live_public_info_enabled: boolean
        meeting_assistant_enabled: boolean
        meeting_public_info_enabled: boolean
        items: import('../types').LiveQaItem[]
        notes: import('../types').LiveNote[]
      }>(`/admin/meetings/${meetingId}/live-qa`),
    reanalyzeMeeting: (meetingId: string) =>
      request<void>(`/admin/meetings/${meetingId}/reanalyze`, { method: 'POST' }),
    retranscribeMeeting: (meetingId: string) =>
      request<void>(`/admin/meetings/${meetingId}/retranscribe`, { method: 'POST' }),
    grantAccess: (userId: number, meetingId: string) =>
      request<void>('/admin/grant-access', {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, meeting_id: meetingId }),
      }),
    setVisibleToAll: (meetingId: string, value: boolean) =>
      request<{ ok: boolean; visible_to_all: boolean }>(`/admin/meetings/${meetingId}/visible-to-all`, {
        method: 'POST',
        body: JSON.stringify({ value }),
      }),
    createShare: (meetingId: string, password: string, expires_at?: string) =>
      request<{ token: string; url: string }>(`/admin/meetings/${meetingId}/share`, {
        method: 'POST',
        body: JSON.stringify({ password, expires_at }),
      }),
    listShares: (meetingId: string) =>
      request<{ shares: { token: string; url: string; created_at: string; expires_at: string | null }[] }>(`/admin/meetings/${meetingId}/shares`),
    revokeShare: (token: string) =>
      request<void>(`/admin/meetings/share/${token}`, { method: 'DELETE' }),
    // Upload an external recording (mp4/mp3/…) — multipart, not JSON.
    uploadRecording: (file: File, title: string) => {
      const fd = new FormData()
      fd.append('file', file)
      if (title) fd.append('title', title)
      return fetch('/api/admin/recordings/upload', { method: 'POST', credentials: 'include', body: fd })
        .then(async r => {
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Ошибка загрузки')
          return r.json() as Promise<{ meeting_id: string; status: string }>
        })
    },
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

    // ── MegaFon call import (interactive: start → OTP → poll status) ──
    megafonStart: (phone?: string) =>
      request<{ job_id: string; status: string }>('/admin/megafon/start', {
        method: 'POST', body: JSON.stringify({ phone }),
      }),
    megafonOtp: (job_id: string, code: string) =>
      request<{ job_id: string; status: string }>('/admin/megafon/otp', {
        method: 'POST', body: JSON.stringify({ job_id, code }),
      }),
    megafonStatus: (job_id: string) =>
      request<{ status: string; stats: { imported?: number } | null; error: string | null }>(
        `/admin/megafon/status/${job_id}`
      ),
  },

  // ── Calls (demo "Звонки" — imported from rec.megafon.ru) ───────────────────────
  calls: {
    list: () => request<{ calls: import('../types').Call[] }>('/calls'),
    get: (id: string) => request<import('../types').Call>(`/calls/${id}`),
    audioUrl: (id: string) => `/api/calls/${id}/audio`,
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
