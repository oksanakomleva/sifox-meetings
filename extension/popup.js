const DEFAULT_BASE = 'https://sifox-meetings.up.railway.app'
const $ = id => document.getElementById(id)

let activeTab = null

async function getBaseUrl() {
  const { baseUrl } = await chrome.storage.local.get(['baseUrl'])
  return (baseUrl || DEFAULT_BASE).replace(/\/+$/, '')
}

async function getSessionToken(baseUrl) {
  try {
    const c = await chrome.cookies.get({ url: baseUrl, name: 'session' })
    return c && c.value ? c.value : null
  } catch (e) {
    return null
  }
}

// Whether the extension already has microphone access (granted earlier via the
// permission tab). Used to hide the request UI once granted.
async function micGranted() {
  try {
    const s = await navigator.permissions.query({ name: 'microphone' })
    return s.state === 'granted'
  } catch (e) {
    return false // can't tell — show the prompt to be safe
  }
}

function show(view) {
  $('login').classList.toggle('hidden', view !== 'login')
  $('recorder').classList.toggle('hidden', view !== 'recorder')
}

function updateMicUI(granted) {
  $('micBanner').classList.toggle('hidden', granted)
  $('micOk').classList.toggle('hidden', !granted)
}

// Request mic from a dedicated tab — a transient popup can't hold the prompt
// (it closes on focus loss → "Permission dismissed").
function openMicPermission() {
  chrome.tabs.create({ url: chrome.runtime.getURL('permission.html') })
}

async function init() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true })
  activeTab = tabs[0]
  const baseUrl = await getBaseUrl()
  $('baseUrl').value = baseUrl

  const token = await getSessionToken(baseUrl)
  if (!token) { show('login'); return }

  try {
    const res = await fetch(`${baseUrl}/api/extension/me`, {
      headers: { 'X-Session-Token': token },
    })
    if (!res.ok) throw new Error('not logged in')
    const me = await res.json()
    $('account').textContent = `Аккаунт: ${me.email}`
  } catch (e) {
    $('loginStatus').textContent = 'Сессия не найдена — войдите в Sifox.'
    show('login')
    return
  }

  $('title').value = activeTab?.title || 'Запись из браузера'
  updateMicUI(await micGranted())
  const st = await chrome.runtime.sendMessage({ type: 'getState' })
  reflect(st && st.recording)
  show('recorder')
}

function reflect(isRecording) {
  const btn = $('toggle')
  if (isRecording) {
    btn.textContent = '■ Остановить запись'
    btn.className = 'danger'
    $('recStatus').innerHTML = '<span class="dot"></span> Идёт запись…'
  } else {
    btn.textContent = '● Начать запись'
    btn.className = 'primary'
    $('recStatus').textContent = ''
  }
}

// ── Login view ──────────────────────────────────────────────────────────────
$('openLogin').addEventListener('click', async () => {
  const baseUrl = ($('baseUrl').value.trim() || DEFAULT_BASE).replace(/\/+$/, '')
  await chrome.storage.local.set({ baseUrl })
  chrome.tabs.create({ url: baseUrl })
})

$('baseUrl').addEventListener('change', async () => {
  await chrome.storage.local.set({ baseUrl: $('baseUrl').value.trim() })
})

// ── Mic permission ─────────────────────────────────────────────────────────────
$('grantMicBig').addEventListener('click', openMicPermission)

// ── Recorder view ─────────────────────────────────────────────────────────────
$('toggle').addEventListener('click', async () => {
  const baseUrl = await getBaseUrl()
  const st = await chrome.runtime.sendMessage({ type: 'getState' })

  if (st && st.recording) {
    $('recStatus').textContent = 'Останавливаю и загружаю…'
    const res = await chrome.runtime.sendMessage({ type: 'stop' })
    if (res && res.ok) {
      $('recStatus').textContent = '✓ Загружено. Обработка идёт на сервере.'
      reflect(false)
    } else {
      $('recStatus').textContent = 'Ошибка загрузки: ' + (res && res.error || '')
    }
    return
  }

  // Require mic before the first recording — request it explicitly if missing.
  if (!(await micGranted())) {
    updateMicUI(false)
    openMicPermission()
    $('recStatus').textContent = 'Разрешите доступ к микрофону в открывшейся вкладке, затем вернитесь и нажмите «Начать запись».'
    return
  }

  $('recStatus').textContent = 'Запускаю…'
  const res = await chrome.runtime.sendMessage({
    type: 'start',
    tabId: activeTab.id,
    baseUrl,
    title: $('title').value.trim(),
    sourceUrl: activeTab.url,
  })
  if (res && res.ok) reflect(true)
  else $('recStatus').textContent = 'Не удалось начать: ' + (res && res.error || '')
})

$('openApp').addEventListener('click', async () => {
  const baseUrl = await getBaseUrl()
  chrome.tabs.create({ url: `${baseUrl}/meetings` })
})

init()
