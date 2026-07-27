// Captures tab audio + microphone and incrementally uploads webm/opus chunks.
// Unacknowledged chunks live in IndexedDB, so an offscreen/service-worker
// restart does not destroy the only copy of the recording.

let recorder = null
let flushPromise = null
let stopPromise = null
let persistChain = Promise.resolve()
let volatileChunks = new Map()
let nextChunkSeq = 0
let audioCtx = null
let streams = []
let uploadCtx = {}

const DB_NAME = 'sifox-recorder'
const DB_VERSION = 1
const CHUNK_STORE = 'chunks'

function openChunkDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(CHUNK_STORE)) {
        const store = db.createObjectStore(CHUNK_STORE, { keyPath: 'key' })
        store.createIndex('meetingId', 'meetingId', { unique: false })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function runStore(mode, operation) {
  const db = await openChunkDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CHUNK_STORE, mode)
    const store = tx.objectStore(CHUNK_STORE)
    let result
    try {
      result = operation(store)
    } catch (e) {
      db.close()
      reject(e)
      return
    }
    tx.oncomplete = () => { db.close(); resolve(result) }
    tx.onerror = () => { db.close(); reject(tx.error) }
    tx.onabort = () => { db.close(); reject(tx.error || new Error('IndexedDB transaction aborted')) }
  })
}

function persistChunk(meetingId, seq, blob) {
  const key = `${meetingId}:${String(seq).padStart(10, '0')}`
  return runStore('readwrite', store => store.put({ key, meetingId, seq, blob }))
}

async function listChunks(meetingId) {
  const db = await openChunkDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CHUNK_STORE, 'readonly')
    const index = tx.objectStore(CHUNK_STORE).index('meetingId')
    const req = index.getAll(IDBKeyRange.only(meetingId))
    req.onsuccess = () => resolve(req.result.sort((a, b) => a.seq - b.seq))
    req.onerror = () => reject(req.error)
    tx.oncomplete = () => db.close()
    tx.onerror = () => { db.close(); reject(tx.error) }
  })
}

function deleteChunk(key) {
  return runStore('readwrite', store => store.delete(key))
}

async function saveUploadContext() {
  const { sessionToken, freshSessionToken, ...safeContext } = uploadCtx
  await chrome.storage.local.set({ pendingUpload: safeContext })
}

async function restoreUploadContext(freshSessionToken) {
  const { pendingUpload } = await chrome.storage.local.get('pendingUpload')
  if (!pendingUpload || !pendingUpload.meetingId) return false
  uploadCtx = {
    ...pendingUpload,
    sessionToken: freshSessionToken || pendingUpload.sessionToken,
  }
  return true
}

async function fetchWithTimeout(url, options, timeoutMs = 30_000) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'offscreen') return

  if (msg.type === 'start') {
    startCapture(msg)
      .then(() => sendResponse({ ok: true }))
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }))
    return true
  }

  if (msg.type === 'stop') {
    if (msg.sessionToken) {
      uploadCtx.sessionToken = msg.sessionToken
      uploadCtx.freshSessionToken = msg.sessionToken
    }
    stopAndUpload()
      .then(r => sendResponse(r))
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }))
    return true
  }
})

async function startCapture({ streamId, sessionToken, baseUrl, title, sourceUrl }) {
  const { pendingUpload } = await chrome.storage.local.get('pendingUpload')
  if (pendingUpload && pendingUpload.meetingId) {
    throw new Error('Сначала завершите предыдущую загрузку кнопкой «Остановить запись».')
  }
  uploadCtx = { sessionToken, baseUrl, title, sourceUrl, startedAt: new Date().toISOString() }
  flushPromise = null
  stopPromise = null
  persistChain = Promise.resolve()
  volatileChunks = new Map()
  nextChunkSeq = 0
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

  try {
    await startUploadSession()
  } catch (e) {
    recorder = null
    streams.forEach(s => s.getTracks().forEach(t => t.stop()))
    if (audioCtx) { try { await audioCtx.close() } catch (_) {} }
    throw e
  }

  recorder.ondataavailable = e => {
    if (e.data && e.data.size) {
      const seq = nextChunkSeq++
      volatileChunks.set(seq, e.data)
      // Keep a failed blob in memory and retry it before later blobs. This
      // prevents a transient IndexedDB failure from silently creating a hole.
      persistChain = persistChain.catch(error => {
        uploadCtx.lastError = error
      })
        .then(() => persistVolatileChunks())
        .then(() => queueFlush())
    }
  }
  try {
    recorder.start(10_000) // upload approximately every ten seconds
  } catch (e) {
    await cancelUploadSession()
    recorder = null
    streams.forEach(s => s.getTracks().forEach(t => t.stop()))
    if (audioCtx) { try { await audioCtx.close() } catch (_) {} }
    throw e
  }
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
  if (stopPromise) return stopPromise
  stopPromise = performStopAndUpload().finally(() => { stopPromise = null })
  return stopPromise
}

function performStopAndUpload() {
  if (recorder && recorder.state === 'inactive') return finishUpload()
  return new Promise((resolve, reject) => {
    if (!recorder) {
      restoreUploadContext(uploadCtx.freshSessionToken)
        .then(found => found ? finishUpload() : { ok: false, error: 'not recording' })
        .then(resolve)
        .catch(reject)
      return
    }
    recorder.onstop = async () => {
      try {
        streams.forEach(s => s.getTracks().forEach(t => t.stop()))
        if (audioCtx) { try { await audioCtx.close() } catch (_) {} }
        const r = await finishUpload()
        if (r.ok) recorder = null
        resolve(r)
      } catch (e) {
        resolve({
          ok: false,
          retryable: true,
          error: String(e && e.message || e),
        })
      }
    }
    recorder.stop()
  })
}

async function startUploadSession() {
  const { sessionToken, baseUrl, title, sourceUrl, startedAt } = uploadCtx
  const res = await fetchWithTimeout(`${baseUrl}/api/extension/upload/start`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Session-Token': sessionToken,
    },
    body: JSON.stringify({ title, source_url: sourceUrl, started_at: startedAt }),
  })
  if (!res.ok) {
    const t = await res.text().catch(() => '')
    throw new Error(`Не удалось начать загрузку: HTTP ${res.status}: ${t.slice(0, 200)}`)
  }
  const data = await res.json()
  uploadCtx.meetingId = data.meeting_id
  uploadCtx.offset = data.offset || 0
  uploadCtx.lastError = null
  await saveUploadContext()
}

async function cancelUploadSession() {
  const { sessionToken, baseUrl, meetingId } = uploadCtx
  if (!meetingId) return
  try {
    await fetchWithTimeout(`${baseUrl}/api/extension/upload/${meetingId}/cancel`, {
      method: 'POST',
      headers: { 'X-Session-Token': sessionToken },
    })
  } finally {
    await chrome.storage.local.remove('pendingUpload')
  }
}

async function persistVolatileChunks() {
  const pending = [...volatileChunks.entries()].sort((a, b) => a[0] - b[0])
  for (const [seq, blob] of pending) {
    await persistChunk(uploadCtx.meetingId, seq, blob)
    volatileChunks.delete(seq)
  }
}

function queueFlush() {
  if (!flushPromise) {
    flushPromise = flushPending()
      .then(() => true)
      .catch(e => {
        uploadCtx.lastError = e
        return false
      })
      .finally(() => { flushPromise = null })
  }
  return flushPromise
}

async function flushPending() {
  while (true) {
    const stored = await listChunks(uploadCtx.meetingId)
    if (!stored.length) return
    const currentChunk = stored[0]
    const body = await currentChunk.blob.arrayBuffer()
    const { sessionToken, baseUrl, meetingId, offset } = uploadCtx
    const res = await fetchWithTimeout(
      `${baseUrl}/api/extension/upload/${meetingId}/chunk?offset=${offset}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Session-Token': sessionToken,
        },
        body,
      },
    )
    if (!res.ok) {
      const t = await res.text().catch(() => '')
      throw new Error(`Загрузка части записи: HTTP ${res.status}: ${t.slice(0, 200)}`)
    }
    const data = await res.json()
    uploadCtx.offset = data.offset
    uploadCtx.lastError = null
    await deleteChunk(currentChunk.key)
    await saveUploadContext()
  }
}

async function finishUpload() {
  persistChain = persistChain.catch(error => {
    uploadCtx.lastError = error
  }).then(() => persistVolatileChunks())
  await persistChain
  await queueFlush()
  const remaining = await listChunks(uploadCtx.meetingId)
  if (remaining.length) {
    return {
      ok: false,
      retryable: true,
      error: String(uploadCtx.lastError && uploadCtx.lastError.message || 'часть записи ещё не загружена'),
    }
  }

  const { sessionToken, baseUrl, meetingId, offset } = uploadCtx
  let res = await fetchWithTimeout(`${baseUrl}/api/extension/upload/${meetingId}/finish`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Session-Token': sessionToken,
    },
    body: JSON.stringify({ total_bytes: offset }),
  })
  if (res.status === 409) {
    const data = await res.json().catch(() => null)
    const expected = data && data.detail && data.detail.expected_offset
    if (Number.isInteger(expected) && expected > offset) {
      uploadCtx.offset = expected
      await saveUploadContext()
      res = await fetchWithTimeout(`${baseUrl}/api/extension/upload/${meetingId}/finish`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-Token': sessionToken,
        },
        body: JSON.stringify({ total_bytes: expected }),
      })
    }
  }
  if (!res.ok) {
    const t = await res.text().catch(() => '')
    return {
      ok: false,
      retryable: true,
      error: `Завершение загрузки: HTTP ${res.status}: ${t.slice(0, 200)}`,
    }
  }
  await chrome.storage.local.remove('pendingUpload')
  return { ok: true, meetingId }
}
