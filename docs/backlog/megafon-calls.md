# Импорт звонков из rec.megafon.ru — карта API (Фаза 0, разобрано вживую)

Личный кабинет «Запись разговоров» МегаФон. Вход — **Keycloak OIDC**, данные — **чистый JSON-API**
на `openapi.megafon.ru` с Bearer-токеном. DOM скрейпить НЕ нужно.

## Конфиг SPA
`GET https://rec.megafon.ru/content/config/config.json`:
```
KEYCLOAK_URL       = https://account.megafon.ru/auth
KEYCLOAK_REALM     = Subscribers
KEYCLOAK_CLIENT_ID = rec
SERVER_API         = https://openapi.megafon.ru/api/product/rec/v1
```

## Логин (Keycloak, хостед-страницы)
1. Authorize: `https://account.megafon.ru/auth/realms/Subscribers/protocol/openid-connect/auth`
   `?client_id=rec&redirect_uri=https://rec.megafon.ru&response_mode=fragment&response_type=code&scope=openid&state=…&nonce=…`
2. Страница телефона: `input[type=tel]` placeholder **«Номер телефона»** → кнопка **«Продолжить»**
   (POST `…/login-actions/authenticate?...&execution=…&client_id=rec`).
3. Страница OTP: `input[type=tel]` placeholder **«Код из SMS»** → кнопка **«Войти»**
   (POST на тот же `login-actions/authenticate` с новым `execution`).
4. Успех → редирект на `https://rec.megafon.ru/#code=…` → SPA меняет код на токены:
   `POST https://account.megafon.ru/auth/realms/Subscribers/protocol/openid-connect/token`.
   Токен SPA кладёт в **sessionStorage**.

## API звонков (Bearer access_token)
- **Список:** `GET {SERVER_API}/records`
  query: `order=DESC` · `direction=ALL` (ALL/IN/OUT?) · `only_favorite=false` · `date_offset=-240` (минуты TZ) · `page=0` · `size=50`
  Элемент: `{ record_id, call_date, direction, duration, party_number, isFavorite }`
  Пагинация `page`/`size`; сортировка DESC (новые первыми) → инкремент: листать, пока не встретим уже известные `record_id`.
- **Скачать запись:** `GET {SERVER_API}/record/{record_id}/file` → аудиофайл.
- Прочее (на старте грузится): `GET {SERVER_API}/metadata`, `GET {SERVER_API}/account`.

## План автоматизации
1. Playwright (headed, Xvfb) проходит Keycloak-логин: подставляем номер, **OTP вводит админ** через нашу UI
   (интерактивная сессия: start → otp). Решение: OTP при КАЖДОМ синке (сессию не храним).
2. Перехватить `access_token` из ответа token-эндпоинта (`page.on("response")`) ИЛИ из sessionStorage после входа.
3. Дальше — напрямую `httpx` с `Authorization: Bearer …`:
   - листаем `/records` (page=0,1,… size=50), берём только новые `record_id` (`call_external_ids_existing`);
   - качаем `/record/{id}/file` на том `AUDIO_DIR`.
4. Обработка: формат/стерео определить через ffprobe на этапе обработки; стерео → каналы = стороны (Вы/Собеседник).

## Заметки
- Токен короткоживущий — нужен только на время одного импорта.
- `date_offset=-240` — смещение таймзоны в минутах (UTC+4); при запросах можно передавать своё/0 и нормализовать `call_date`.
- Капчи/антибота на пути логина не замечено.
