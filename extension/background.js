// Service worker: lifecycle + tabCapture stream id. Actual capture/upload runs
// in the offscreen document (service workers have no MediaRecorder/getUserMedia).

const OFFSCREEN_PATH = 'offscreen.html'
const RECORDING_HEALTH_ALARM = 'recording-health'

// Recording state is persisted in chrome.storage — the MV3 service worker is
// killed after ~30s idle, so an in-memory flag would be lost mid-meeting (then
// the popup couldn't show "Stop"). The offscreen document keeps recording
// independently of the SW; storage lets us recover the state on wake-up.
async function setRecording(on) {
  await chrome.storage.local.set({
    recording: on,
    recordingInterrupted: false,
    interruptionRecoverable: false,
  })
  chrome.action.setBadgeText({ text: on ? 'REC' : '' })
  if (on) {
    chrome.action.setBadgeBackgroundColor({ color: '#dc2626' })
    await chrome.alarms.create(RECORDING_HEALTH_ALARM, {
      delayInMinutes: 0.5,
      periodInMinutes: 0.5,
    })
  } else {
    await chrome.alarms.clear(RECORDING_HEALTH_ALARM)
  }
}

async function offscreenExists() {
  if (chrome.offscreen.hasDocument) return await chrome.offscreen.hasDocument()
  return true // older Chrome — assume yes
}

async function markRecordingInterrupted(recoverable) {
  await chrome.storage.local.set({
    recording: false,
    recordingInterrupted: true,
    interruptionRecoverable: recoverable,
  })
  await chrome.alarms.clear(RECORDING_HEALTH_ALARM)
  chrome.action.setBadgeText({ text: '!' })
  chrome.action.setBadgeBackgroundColor({ color: '#d97706' })
}

async function probeRecorder() {
  if (!(await offscreenExists())) return false
  try {
    const health = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'health' })
    return !!(health && health.ok && health.active)
  } catch (e) {
    return false
  }
}

async function getRecordingState() {
  const state = await chrome.storage.local.get([
    'recording',
    'recordingInterrupted',
    'interruptionRecoverable',
    'pendingUpload',
    'capturedTab',
  ])

  if (state.recording) {
    const active = await probeRecorder()
    if (!active) {
      const recoverable = !!(state.pendingUpload && state.pendingUpload.meetingId)
      await markRecordingInterrupted(recoverable)
      return {
        recording: false,
        interrupted: true,
        recoverable,
        tab: state.capturedTab || null,
      }
    }
  }

  return {
    recording: !!state.recording,
    interrupted: !!state.recordingInterrupted,
    recoverable: !!state.interruptionRecoverable,
    tab: state.capturedTab || null,
  }
}

async function ensureOffscreen() {
  // hasDocument() may be undefined on older Chrome — guard it.
  if (chrome.offscreen.hasDocument) {
    const has = await chrome.offscreen.hasDocument()
    if (has) return
  }
  try {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_PATH,
      reasons: ['USER_MEDIA'],
      justification: 'Запись звука вкладки и микрофона для протокола встречи.',
    })
  } catch (e) {
    // Already exists race — ignore "single offscreen document" error.
    if (!String(e).includes('Only a single offscreen')) throw e
  }
}

// Read the user's Sifox web-login session cookie (set by Google OAuth).
async function getSessionToken(baseUrl) {
  try {
    const c = await chrome.cookies.get({ url: baseUrl, name: 'session' })
    return c && c.value ? c.value : null
  } catch (e) {
    return null
  }
}

// Stop the active recording: tell the offscreen doc to stop + upload, then
// clear state. Used by the popup "Stop" and by the captured-tab-closed handler.
async function stopRecording() {
  if (!(await offscreenExists())) {
    // Recreate the offscreen document: it can resume acknowledged state and
    // unacknowledged chunks from chrome.storage + IndexedDB.
    await ensureOffscreen()
  }
  const { capturedBaseUrl } = await chrome.storage.local.get('capturedBaseUrl')
  const sessionToken = capturedBaseUrl ? await getSessionToken(capturedBaseUrl) : null
  const res = await chrome.runtime.sendMessage({
    target: 'offscreen',
    type: 'stop',
    sessionToken,
  })
  if (res && res.ok) {
    await setRecording(false)
    await chrome.storage.local.remove(['capturedTabId', 'capturedTab', 'capturedBaseUrl'])
  }
  return res || { ok: false, error: 'no response from offscreen' }
}

// If the tab being recorded is closed, auto-stop and upload what we have.
// tabs.onRemoved reliably wakes the MV3 service worker (unlike the audio
// track's 'ended' event, which Chrome doesn't always fire for tab capture).
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { recording, capturedTabId } = await chrome.storage.local.get(['recording', 'capturedTabId'])
  if (recording && capturedTabId === tabId) {
    await stopRecording()
  }
})

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === RECORDING_HEALTH_ALARM) {
    getRecordingState().catch(error => console.error('Recording health check failed:', error))
  }
})

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // Ignore messages addressed to the offscreen document.
  if (msg && msg.target === 'offscreen') return

  ;(async () => {
    try {
      // Offscreen documents only have access to chrome.runtime. Keep all
      // chrome.storage calls in the service worker and expose the three small
      // operations the recorder needs through extension-internal messages.
      if (msg.target === 'background' && msg.type === 'storage-get') {
        sendResponse({ ok: true, value: await chrome.storage.local.get(msg.keys) })
        return
      }

      if (msg.target === 'background' && msg.type === 'storage-set') {
        await chrome.storage.local.set(msg.values)
        sendResponse({ ok: true })
        return
      }

      if (msg.target === 'background' && msg.type === 'storage-remove') {
        await chrome.storage.local.remove(msg.keys)
        sendResponse({ ok: true })
        return
      }

      if (msg.type === 'getState') {
        sendResponse(await getRecordingState())
        return
      }

      if (msg.type === 'dismissInterrupted') {
        await setRecording(false)
        await chrome.storage.local.remove(['capturedTabId', 'capturedTab', 'capturedBaseUrl'])
        sendResponse({ ok: true })
        return
      }

      if (msg.type === 'recording-ended') {
        // Offscreen auto-stopped (e.g. captured tab was closed) and uploaded.
        if (msg.result && msg.result.ok) {
          await setRecording(false)
          await chrome.storage.local.remove(['capturedTabId', 'capturedTab', 'capturedBaseUrl'])
        }
        sendResponse(msg.result || { ok: false })
        return
      }

      if (msg.type === 'start') {
        const { baseUrl, title, sourceUrl, tabId, favIconUrl } = msg
        const sessionToken = await getSessionToken(baseUrl)
        if (!sessionToken) {
          sendResponse({ ok: false, error: 'Войдите в Sifox в этом браузере' })
          return
        }
        const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId })
        await ensureOffscreen()
        const res = await chrome.runtime.sendMessage({
          target: 'offscreen', type: 'start',
          streamId, sessionToken, baseUrl, title, sourceUrl,
        })
        if (res && res.ok) {
          await setRecording(true)
          await chrome.storage.local.set({
            capturedTabId: tabId,
            capturedTab: { title, url: sourceUrl, icon: favIconUrl },
            capturedBaseUrl: baseUrl,
          })
        }
        sendResponse(res || { ok: false, error: 'no response from offscreen' })
        return
      }

      if (msg.type === 'stop') {
        sendResponse(await stopRecording())
        return
      }
    } catch (e) {
      if (msg.type !== 'stop') await setRecording(false)
      sendResponse({ ok: false, error: String(e && e.message || e) })
    }
  })()

  return true // async sendResponse
})
