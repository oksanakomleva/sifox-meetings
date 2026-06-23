import { useState } from 'react'
import { api } from '../../api/client'
import MeetingsView from '../../components/MeetingsView'
import UploadRecordingModal from '../../components/UploadRecordingModal'

export default function AdminMeetings() {
  const [showUpload, setShowUpload] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: 'var(--space-5) var(--space-5) 0' }}>
        <button className="btn btn-primary" onClick={() => setShowUpload(true)}>⬆ Загрузить запись</button>
      </div>
      <MeetingsView
        key={reloadKey}
        title="Все встречи"
        subtitle="Все встречи в системе"
        admin
        fetchDone={() => api.admin.allMeetings(200).then(r => r.meetings)}
        fetchUpcoming={() => api.admin.upcoming().then(r => r.meetings)}
        onReanalyze={(id) => api.admin.reanalyzeMeeting(id)}
        onRetranscribe={(id) => api.admin.retranscribeMeeting(id)}
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
