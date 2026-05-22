const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
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

// ── Auth ──────────────────────────────────────────────────────────────────────
export const api = {
  auth: {
    me: () => request<{ id: number; email: string; name: string; avatar_url: string | null; is_admin: boolean }>('/auth/me'),
    logout: () => request<void>('/auth/logout', { method: 'POST' }),
    loginUrl: () => '/api/auth/login',
    connectCalendarUrl: () => '/api/auth/connect-calendar',
  },

  // ── Meetings ────────────────────────────────────────────────────────────────
  meetings: {
    list: (limit = 20, offset = 0) =>
      request<{ meetings: import('../types').Meeting[] }>(`/meetings?limit=${limit}&offset=${offset}`),
    get: (id: string) =>
      request<import('../types').Meeting>(`/meetings/${id}`),
    transcript: (id: string) =>
      request<{ transcript: string }>(`/meetings/${id}/transcript`),
    audioUrl: (id: string) => `/api/meetings/${id}/audio`,
  },

  // ── Admin ────────────────────────────────────────────────────────────────────
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
    grantAccess: (userId: number, meetingId: string) =>
      request<void>('/admin/grant-access', {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, meeting_id: meetingId }),
      }),
  },

  // ── Chat ─────────────────────────────────────────────────────────────────────
  chat: {
    history: (meetingId?: string) =>
      request<{ messages: import('../types').ChatMessage[] }>(
        `/chat/history${meetingId ? `?meeting_id=${meetingId}` : ''}`
      ),
  },
}
