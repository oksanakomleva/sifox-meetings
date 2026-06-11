export interface User {
  id: number
  email: string
  name: string
  avatar_url: string | null
  is_admin: boolean
  is_preview?: boolean
}

// ── Communications (Mattermost / Gmail) ──────────────────────────────────────
export interface MmMessage {
  id: string
  channel_id: string
  channel_name: string | null
  user_id: string | null
  username: string | null
  message: string
  created_at: string
}

export interface MmChannel {
  channel_id: string
  channel_name: string | null
  count: number
}

export interface EmailMessage {
  id: string
  user_email: string
  from_email: string | null
  to_emails: string[] | null
  subject: string | null
  body_text: string | null
  received_at: string
}

export interface CommsChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface Meeting {
  id: string
  title: string | null
  start_time: string | null
  end_time: string | null
  status: MeetingStatus
  summary: string | null
  tags: string[] | null
  topic: string | null
  meeting_type: string | null
  audio_path: string | null
  audio_size: number | null
  error_message: string | null
  created_at: string
  participants?: Participant[]
  transcript?: string
}

export type MeetingStatus =
  | 'pending'
  | 'recording'
  | 'transcribing'
  | 'analyzing'
  | 'done'
  | 'error'

export interface Participant {
  name: string
  email: string | null
  user_id: number | null
}

export interface Calendar {
  id: number
  owner_user_id: number
  owner_email: string
  owner_name: string
  google_calendar_id: string
  name: string
  is_primary: boolean
  record_enabled: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface Invitation {
  id: number
  email: string
  token: string
  expires_at: string
  accepted_at: string | null
  created_at: string
  created_by_name: string | null
  url?: string
}
