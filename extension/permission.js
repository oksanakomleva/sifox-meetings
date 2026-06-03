// Runs as a normal extension tab so the mic permission prompt persists (unlike
// the transient popup). Granting here stores the permission for the extension
// origin, so the offscreen recorder can use the mic without a prompt.

const msg = document.getElementById('msg')

async function request() {
  msg.textContent = ''
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.getTracks().forEach(t => t.stop())
    msg.className = 'ok'
    msg.textContent = '✓ Доступ к микрофону разрешён. Можно закрыть эту вкладку и вернуться к записи.'
  } catch (e) {
    msg.className = 'err'
    msg.textContent = 'Не удалось получить доступ: ' + (e && e.message || e) +
      '. Проверьте, что микрофон не заблокирован в настройках Chrome для этого расширения.'
  }
}

document.getElementById('grant').addEventListener('click', request)
