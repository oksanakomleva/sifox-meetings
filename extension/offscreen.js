// Captures tab audio (chrome tabCapture stream id) + microphone, mixes them with
// Web Audio, records to webm/opus, and uploads to the backend on stop.

let recorder = null
let chunks = []
let audioCtx = null
let streams = []
let uploadCtx = {}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'offscreen') return

  if (msg.type === 'start') {
    startCapture(msg)
      .then(() => sendResponse({ ok: true }))
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }))
    return true
  }

  if (msg.type === 'stop') {
    stopAndUpload()
      .then(r => sendResponse(r))
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }))
    return true
  }
})

async function startCapture({ streamId, sessionToken, baseUrl, title, sourceUrl }) {
  uploadCtx = { sessionToken, baseUrl, title, sourceUrl, startedAt: new Date().toISOString() }
  chunks = []
  streams = []

  // Tab audio via the stream id minted by the service worker.
  const tabStream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    video: false,
  })
  streams.push(tabStream)

  // If the captured tab is closed, its audio track ends — auto-stop and upload
  // what we have so the recording isn't silently lost.
  const tabTrack = tabStream.getAudioTracks()[0]
  if (tabTrack) tabTrack.addEventListener('ended', onTabEnded)

  // Microphone (best-effort — requires a prior permission grant from the popup).
  let micStream = null
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    streams.push(micStream)
  } catch (e) {
    console.warn('Mic unavailable, recording tab audio only:', e)
  }

  audioCtx = new AudioContext()
  const dest = audioCtx.createMediaStreamDestination()
  audioCtx.createMediaStreamSource(tabStream).connect(dest)
  if (micStream) audioCtx.createMediaStreamSource(micStream).connect(dest)

  // tabCapture mutes the tab's own playback — pipe it back so the user still hears it.
  audioCtx.createMediaStreamSource(tabStream).connect(audioCtx.destination)

  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus' : 'audio/webm'
  recorder = new MediaRecorder(dest.stream, { mimeType: mime })
  recorder.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data) }
  recorder.start(1000) // gather data every second
}

async function onTabEnded() {
  // Guard against racing a manual Stop (recorder already cleared).
  if (!recorder || recorder.state === 'inactive') return
  let result
  try {
    result = await stopAndUpload()
  } catch (e) {
    result = { ok: false, error: String(e && e.message || e) }
  }
  // Tell the service worker to clear the recording state / badge.
  chrome.runtime.sendMessage({ type: 'recording-ended', result })
}

function stopAndUpload() {
  return new Promise((resolve, reject) => {
    if (!recorder) return resolve({ ok: false, error: 'not recording' })
    recorder.onstop = async () => {
      try {
        streams.forEach(s => s.getTracks().forEach(t => t.stop()))
        if (audioCtx) { try { await audioCtx.close() } catch (_) {} }
        const blob = new Blob(chunks, { type: 'audio/webm' })
        const r = await upload(blob)
        recorder = null
        resolve(r)
      } catch (e) {
        reject(e)
      }
    }
    recorder.stop()
  })
}

async function upload(blob) {
  const { sessionToken, baseUrl, title, sourceUrl, startedAt } = uploadCtx
  const fd = new FormData()
  fd.append('file', blob, 'recording.webm')
  if (title) fd.append('title', title)
  if (sourceUrl) fd.append('source_url', sourceUrl)
  if (startedAt) fd.append('started_at', startedAt)

  const res = await fetch(`${baseUrl}/api/extension/upload`, {
    method: 'POST',
    headers: { 'X-Session-Token': sessionToken },
    body: fd,
  })
  if (!res.ok) {
    const t = await res.text().catch(() => '')
    return { ok: false, error: `HTTP ${res.status}: ${t.slice(0, 200)}` }
  }
  const data = await res.json()
  return { ok: true, meetingId: data.meeting_id }
}
