import { api } from '../../api/client'
import MeetingsView from '../../components/MeetingsView'

export default function AdminMeetings() {
  return (
    <MeetingsView
      title="Все встречи"
      subtitle="Все встречи в системе"
      admin
      fetchDone={() => api.admin.allMeetings(200).then(r => r.meetings)}
      fetchUpcoming={() => api.admin.upcoming().then(r => r.meetings)}
      onReanalyze={(id) => api.admin.reanalyzeMeeting(id)}
      onRetranscribe={(id) => api.admin.retranscribeMeeting(id)}
    />
  )
}
