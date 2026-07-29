import { useState } from 'react'
import { api } from '../../api/client'
import MeetingsView from '../../components/MeetingsView'
import UploadRecordingModal from '../../components/UploadRecordingModal'

export default function AdminMeetings() {
  const [showUpload, setShowUpload] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  return (
    <>
      <MeetingsView
        key={reloadKey}
        title="Все встречи"
        subtitle="Все встречи в системе"
        admin
        fetchDone={() => api.admin.allMeetings(200).then(r => r.meetings)}
        fetchUpcoming={() => api.admin.upcoming().then(r => r.meetings)}
        onSetAssistantEnabled={(id, enabled) => api.admin.setMeetingAssistantEnabled(id, enabled)}
        onSetPublicInfoEnabled={(id, enabled) => api.admin.setMeetingPublicInfoEnabled(id, enabled)}
        onReanalyze={(id) => api.admin.reanalyzeMeeting(id)}
        onRetranscribe={(id) => api.admin.retranscribeMeeting(id)}
        headerAction={
          <button className="btn btn-primary" onClick={() => setShowUpload(true)}>
            ⬆ Загрузить запись
          </button>
        }
      />
      {showUpload && (
        <UploadRecordingModal
          onClose={() => setShowUpload(false)}
          onUploaded={() => setReloadKey(k => k + 1)}
        />
      )}
    </>
  )
}
