import { api } from '../api/client'
import MeetingsView from '../components/MeetingsView'

export default function Meetings() {
  return (
    <MeetingsView
      title="Мои встречи"
      subtitle="Записи встреч, в которых вы участвовали"
      fetchDone={() => api.meetings.list(100).then(r => r.meetings)}
      fetchUpcoming={() => api.meetings.upcoming().then(r => r.meetings)}
    />
  )
}
