export interface User {
  id: number
  email: string
  name: string
  avatar_url: string | null
  is_admin: boolean
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
