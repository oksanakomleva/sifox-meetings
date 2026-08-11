const DEFAULT_BASE = 'https://sifox-meetings.up.railway.app'
const $ = id => document.getElementById(id)

let activeTab = null
let stateCheckInProgress = false
let actionInProgress = false
let lastStateKey = null

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

function hostOf(url) {
  try { return new URL(url).hostname } catch (e) { return '' }
}

function originPattern(baseUrl) {
  try {
    const url = new URL(baseUrl)
    if (!['http:', 'https:'].includes(url.protocol)) return null
    return `${url.origin}/*`
  } catch (e) {
    return null
  }
}

async function ensureOriginPermission(baseUrl, requestIfMissing = false) {
  const pattern = originPattern(baseUrl)
  if (!pattern) return false
  if (await chrome.permissions.contains({ origins: [pattern] })) return true
  if (!requestIfMissing) return false
  return await chrome.permissions.request({ origins: [pattern] })
}

// Show which tab is (or will be) captured: favicon + title + host.
function fillTabCard(tab) {
  const wrap = $('tabIconWrap')
  wrap.innerHTML = ''
  const icon = tab && (tab.icon || tab.favIconUrl)
  if (icon) {
    const img = document.createElement('img')
    img.src = icon
    img.onerror = () => { const s = document.createElement('span'); s.className = 'ico-fallback'; img.replaceWith(s) }
    wrap.appendChild(img)
  } else {
    const s = document.createElement('span'); s.className = 'ico-fallback'; wrap.appendChild(s)
  }
  $('tabTitle').textContent = (tab && tab.title) || 'Без названия'
  $('tabHost').textContent = hostOf(tab && tab.url)
}

function isNewer(remote, local) {
  const a = String(remote).split('.').map(n => parseInt(n) || 0)
  const b = String(local).split('.').map(n => parseInt(n) || 0)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] || 0, y = b[i] || 0
    if (x !== y) return x > y
  }
  return false
}

async function checkUpdate(baseUrl) {
  try {
    const res = await fetch(`${baseUrl}/api/extension/version`)
    if (!res.ok) return
    const { version } = await res.json()
    if (isNewer(version, chrome.runtime.getManifest().version)) {
      $('updateBanner').classList.remove('hidden')
      $('dlUpdate').addEventListener('click', () =>
        chrome.tabs.create({ url: `${baseUrl}/api/extension/download` }))
    }
  } catch (e) { /* offline / old server — ignore */ }
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
  fillTabCard({ title: activeTab?.title, url: activeTab?.url, icon: activeTab?.favIconUrl })
  updateMicUI(await micGranted())
  checkUpdate(baseUrl)
  await refreshRecorderState(true)
  show('recorder')
  setInterval(() => refreshRecorderState(), 3_000)
}

function stateKey(st) {
  return [!!st.recording, !!st.interrupted, !!st.recoverable].join(':')
}

function reflectAndRemember(st = {}) {
  reflect(st)
  lastStateKey = stateKey(st)
}

async function refreshRecorderState(force = false) {
  if (stateCheckInProgress || (actionInProgress && !force)) return null
  stateCheckInProgress = true
  try {
    const st = await chrome.runtime.sendMessage({ type: 'getState' }) || {}
    const key = stateKey(st)
    if (force || key !== lastStateKey) {
      reflectAndRemember(st)
    }
    return st
  } finally {
    stateCheckInProgress = false
  }
}

function reflect(st = {}) {
  const btn = $('toggle')
  if ((st.recording || st.interrupted) && st.tab) {
    // Show the tab actually being recorded (may differ from the current active tab).
    fillTabCard(st.tab)
  }

  if (st.interrupted) {
    btn.className = 'warning'
    if (st.recoverable) {
      btn.textContent = 'Отправить сохранённую запись'
      $('recStatus').innerHTML = '<strong>Запись неожиданно прервалась</strong><br>Chrome остановил запись. Всё аудио, которое удалось сохранить до сбоя, осталось на устройстве. Нажмите «Отправить сохранённую запись», чтобы загрузить его в Sifox.'
    } else {
      btn.textContent = 'Сбросить ошибку'
      $('recStatus').textContent = '⚠ Запись прервана до сохранения аудио. Начните новую запись.'
    }
    $('tabHint').textContent = 'Запись этой вкладки неожиданно остановилась.'
  } else if (st.recording) {
    btn.textContent = '■ Остановить запись'
    btn.className = 'danger'
    $('recStatus').innerHTML = '<span class="dot"></span> Идёт запись…'
    $('tabHint').textContent = 'Идёт запись этой вкладки.'
  } else {
    if (activeTab) {
      fillTabCard({ title: activeTab.title, url: activeTab.url, icon: activeTab.favIconUrl })
    }
    btn.textContent = '● Начать запись'
    btn.className = 'primary'
    $('recStatus').textContent = ''
    $('tabHint').innerHTML = 'Записывается звук <b>этой вкладки</b> и ваш микрофон. Если звонок в другой вкладке или в приложении — откройте его и запустите запись именно на той вкладке.'
  }
}

// ── Login view ──────────────────────────────────────────────────────────────
$('openLogin').addEventListener('click', async () => {
  const baseUrl = ($('baseUrl').value.trim() || DEFAULT_BASE).replace(/\/+$/, '')
  if (!(await ensureOriginPermission(baseUrl, true))) {
    $('loginStatus').textContent = 'Разрешите расширению доступ к выбранному адресу Sifox.'
    return
  }
  await chrome.storage.local.set({ baseUrl })
  chrome.tabs.create({ url: baseUrl })
})

$('baseUrl').addEventListener('change', async () => {
  const baseUrl = $('baseUrl').value.trim().replace(/\/+$/, '')
  if (await ensureOriginPermission(baseUrl, true)) {
    await chrome.storage.local.set({ baseUrl })
  } else {
    $('loginStatus').textContent = 'Адрес не сохранён: требуется разрешение на доступ.'
  }
})

// ── Mic permission ─────────────────────────────────────────────────────────────
$('grantMicBig').addEventListener('click', openMicPermission)

// ── Recorder view ─────────────────────────────────────────────────────────────
$('toggle').addEventListener('click', async () => {
  if (actionInProgress) return
  actionInProgress = true
  try {
  const baseUrl = await getBaseUrl()
  if (!(await ensureOriginPermission(baseUrl, true))) {
    $('recStatus').textContent = 'Разрешите расширению доступ к адресу Sifox.'
    return
  }
  const st = await chrome.runtime.sendMessage({ type: 'getState' })

  if (st && st.interrupted && !st.recoverable) {
    await chrome.runtime.sendMessage({ type: 'dismissInterrupted' })
    reflectAndRemember({})
    $('recStatus').textContent = 'Ошибка сброшена. Можно начать новую запись.'
    return
  }

  if (st && (st.recording || (st.interrupted && st.recoverable))) {
    const recovering = !!st.interrupted
    $('recStatus').textContent = recovering
      ? 'Отправляю сохранённую запись…'
      : 'Останавливаю и загружаю…'
    const res = await chrome.runtime.sendMessage({ type: 'stop' })
    if (res && res.ok) {
      reflectAndRemember({})
      $('recStatus').textContent = recovering
        ? '✓ Сохранённая запись отправлена. Обработка идёт на сервере.'
        : '✓ Загружено. Обработка идёт на сервере.'
    } else {
      $('recStatus').textContent = res && res.retryable
        ? 'Запись сохранена на устройстве и ожидает отправки. Ошибка: ' + (res.error || '') + ' Нажмите ещё раз, чтобы повторить.'
        : 'Ошибка загрузки: ' + (res && res.error || '')
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
    favIconUrl: activeTab.favIconUrl,
  })
  if (res && res.ok) {
    lastStateKey = null
    await refreshRecorderState(true)
  }
  else $('recStatus').textContent = 'Не удалось начать: ' + (res && res.error || '')
  } finally {
    actionInProgress = false
  }
})

$('openApp').addEventListener('click', async () => {
  const baseUrl = await getBaseUrl()
  chrome.tabs.create({ url: `${baseUrl}/meetings` })
})

init()
