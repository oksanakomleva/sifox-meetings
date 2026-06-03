const DEFAULT_BASE = 'https://sifox-meetings.up.railway.app'
const $ = id => document.getElementById(id)

let activeTab = null

async function getCfg() {
  const { token, baseUrl } = await chrome.storage.local.get(['token', 'baseUrl'])
  return { token: token || '', baseUrl: baseUrl || DEFAULT_BASE }
}

function show(view) {
  $('setup').classList.toggle('hidden', view !== 'setup')
  $('recorder').classList.toggle('hidden', view !== 'recorder')
}

async function init() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true })
  activeTab = tabs[0]
  const { token, baseUrl } = await getCfg()
  $('baseUrl').value = baseUrl

  if (!token) { show('setup'); return }

  // Verify token, then show recorder.
  try {
    const res = await fetch(`${baseUrl}/api/extension/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) throw new Error('invalid')
    const me = await res.json()
    $('account').textContent = `Аккаунт: ${me.email}`
  } catch (e) {
    $('setupStatus').textContent = 'Токен недействителен — введите заново.'
    show('setup')
    return
  }

  $('title').value = activeTab?.title || 'Запись из браузера'
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

// ── Setup handlers ──────────────────────────────────────────────────────────
$('saveToken').addEventListener('click', async () => {
  const token = $('token').value.trim()
  const baseUrl = ($('baseUrl').value.trim() || DEFAULT_BASE).replace(/\/+$/, '')
  if (!token) { $('setupStatus').textContent = 'Введите токен.'; return }
  $('setupStatus').textContent = 'Проверяю…'
  try {
    const res = await fetch(`${baseUrl}/api/extension/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) throw new Error('invalid')
    await chrome.storage.local.set({ token, baseUrl })
    await init()
  } catch (e) {
    $('setupStatus').textContent = 'Токен не подошёл. Проверьте адрес и токен.'
  }
})

$('grantMic').addEventListener('click', async () => {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true })
    s.getTracks().forEach(t => t.stop())
    $('setupStatus').textContent = '✓ Доступ к микрофону разрешён.'
  } catch (e) {
    $('setupStatus').textContent = 'Микрофон не разрешён: ' + e.message
  }
})

// ── Recorder handlers ─────────────────────────────────────────────────────────
$('toggle').addEventListener('click', async () => {
  const { token, baseUrl } = await getCfg()
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

  $('recStatus').textContent = 'Запускаю…'
  const res = await chrome.runtime.sendMessage({
    type: 'start',
    tabId: activeTab.id,
    token, baseUrl,
    title: $('title').value.trim(),
    sourceUrl: activeTab.url,
  })
  if (res && res.ok) reflect(true)
  else $('recStatus').textContent = 'Не удалось начать: ' + (res && res.error || '')
})

$('openApp').addEventListener('click', async (e) => {
  e.preventDefault()
  const { baseUrl } = await getCfg()
  chrome.tabs.create({ url: `${baseUrl}/meetings` })
})

$('changeToken').addEventListener('click', async (e) => {
  e.preventDefault()
  show('setup')
})

init()
