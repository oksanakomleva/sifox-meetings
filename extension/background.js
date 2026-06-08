// Service worker: lifecycle + tabCapture stream id. Actual capture/upload runs
// in the offscreen document (service workers have no MediaRecorder/getUserMedia).

const OFFSCREEN_PATH = 'offscreen.html'

// Recording state is persisted in chrome.storage — the MV3 service worker is
// killed after ~30s idle, so an in-memory flag would be lost mid-meeting (then
// the popup couldn't show "Stop"). The offscreen document keeps recording
// independently of the SW; storage lets us recover the state on wake-up.
async function setRecording(on) {
  await chrome.storage.local.set({ recording: on })
  chrome.action.setBadgeText({ text: on ? 'REC' : '' })
  if (on) chrome.action.setBadgeBackgroundColor({ color: '#dc2626' })
}

async function isRecording() {
  const { recording } = await chrome.storage.local.get('recording')
  return !!recording
}

async function offscreenExists() {
  if (chrome.offscreen.hasDocument) return await chrome.offscreen.hasDocument()
  return true // older Chrome — assume yes
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
    await setRecording(false)
    await chrome.storage.local.remove(['capturedTabId', 'capturedTab'])
    return { ok: false, error: 'Запись потеряна (расширение перезапускалось). Аудио не сохранено.' }
  }
  const res = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'stop' })
  await setRecording(false)
  await chrome.storage.local.remove(['capturedTabId', 'capturedTab'])
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

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // Ignore messages addressed to the offscreen document.
  if (msg && msg.target === 'offscreen') return

  ;(async () => {
    try {
      if (msg.type === 'getState') {
        const { capturedTab } = await chrome.storage.local.get('capturedTab')
        sendResponse({ recording: await isRecording(), tab: capturedTab || null })
        return
      }

      if (msg.type === 'recording-ended') {
        // Offscreen auto-stopped (e.g. captured tab was closed) and uploaded.
        await setRecording(false)
        sendResponse({ ok: true })
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
      await setRecording(false)
      sendResponse({ ok: false, error: String(e && e.message || e) })
    }
  })()

  return true // async sendResponse
})
