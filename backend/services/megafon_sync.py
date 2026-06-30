"""rec.megafon.ru call import.

Interactive Keycloak login (phone → OTP, OTP entered by an admin via our UI),
then the call list + recordings are pulled straight from the JSON API with the
captured Bearer token (no DOM scraping). See docs/backlog/megafon-calls.md.

OTP is required for EVERY sync (no session persisted). Viewing already-imported
calls never touches MegaFon — they live in our `calls` table.
"""
import asyncio
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config import config
from database import models

logger = logging.getLogger(__name__)


class MegafonError(Exception):
    pass


# Interactive login sessions held between the /start and /otp requests.
# One import at a time (admin-only); short TTL so a dangling browser is reaped.
_sessions: dict[str, dict] = {}
_SESSION_TTL = 300  # seconds
_RECORDS_PAGE_SIZE = 50
_MAX_PAGES = 400  # safety backstop (~20k calls)


def get_status(job_id: str) -> dict | None:
    s = _sessions.get(job_id)
    if not s:
        return None
    return {"status": s.get("status"), "stats": s.get("stats"), "error": s.get("error")}


def _national_digits(phone: str) -> str:
    """Reduce any phone form to the 10 national digits the imask field expects
    (it renders the +7 prefix itself). '+7 925 005 87 80' / '89250058780' →
    '9250058780'."""
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d[0] in ("7", "8"):
        d = d[1:]
    return d


def _direction(raw: str | None) -> str | None:
    v = (raw or "").strip().lower()
    if v in ("mt", "in", "incoming", "input", "incomming"):
        return "in"
    if v in ("mo", "out", "outgoing", "output"):
        return "out"
    return v or None


def _parse_call_date(raw) -> "datetime | None":
    if not raw:
        return None
    if isinstance(raw, (int, float)):  # epoch (s or ms)
        ts = raw / 1000 if raw > 1e12 else raw
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _gc_sessions() -> None:
    now = time.time()
    for jid in [k for k, v in _sessions.items() if now - v.get("started", now) > _SESSION_TTL]:
        await _close_browser(jid)
        # keep the entry if an import is still running so status survives
        if _sessions.get(jid, {}).get("status") not in ("importing",):
            _sessions.pop(jid, None)


async def _close_browser(job_id: str) -> None:
    s = _sessions.get(job_id)
    if not s:
        return
    for key in ("browser", "pw"):
        obj = s.pop(key, None)
        try:
            if key == "browser" and obj:
                await obj.close()
            elif key == "pw" and obj:
                await obj.stop()
        except Exception:  # noqa: BLE001
            pass
    s.pop("page", None)
    s.pop("ctx", None)


async def _launch_browser():
    from playwright.async_api import async_playwright

    display = os.environ.get("DISPLAY", ":99")
    pw = await async_playwright().start()
    browser = await asyncio.wait_for(
        pw.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            env={**os.environ, "DISPLAY": display},
        ),
        timeout=30,
    )
    return pw, browser


async def start_login(phone: str) -> str:
    """Open the MegaFon login, submit the phone, wait for the OTP form.
    Returns a job_id; the browser stays open until submit_otp()."""
    if not phone:
        raise MegafonError("Не указан номер телефона")
    await _gc_sessions()
    # Only one interactive login at a time.
    for jid, s in list(_sessions.items()):
        if s.get("status") in ("otp_required", "importing"):
            raise MegafonError("Уже идёт импорт. Дождитесь завершения.")

    job_id = uuid.uuid4().hex[:12]
    pw, browser = await _launch_browser()
    try:
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(config.MEGAFON_REDIRECT_URI, wait_until="domcontentloaded", timeout=30_000)

        inp = page.locator("input[type='tel']").first
        await inp.wait_for(timeout=20_000)
        await inp.click()
        # Feed only the 10 national digits — the imask field renders +7 itself,
        # so typing a literal "+7…" collides with the mask and the form won't
        # advance (was the cause of the "Код из SMS" timeout).
        await inp.press("Control+a")
        await inp.press("Delete")
        await inp.type(_national_digits(phone), delay=45)

        btn = page.get_by_role("button", name="Продолжить")
        try:
            await btn.click(timeout=6_000)
        except Exception:  # noqa: BLE001 — button may be styled/disabled; submit via Enter
            await inp.press("Enter")

        otp = page.get_by_placeholder("Код из SMS")
        try:
            await otp.wait_for(timeout=30_000)
        except Exception:  # noqa: BLE001 — surface what the page is actually showing
            info = ""
            try:
                body = (await page.locator("body").inner_text())[:200].replace("\n", " ")
                info = f" | страница: {body.strip()}"
            except Exception:  # noqa: BLE001
                pass
            raise MegafonError(f"Экран ввода кода не появился — проверьте номер.{info}")
    except MegafonError:
        try:
            await browser.close()
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as e:  # noqa: BLE001
        try:
            await browser.close()
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass
        raise MegafonError(f"Не удалось дойти до ввода кода: {e}")

    _sessions[job_id] = {
        "pw": pw, "browser": browser, "ctx": ctx, "page": page,
        "status": "otp_required", "started": time.time(), "stats": None, "error": None,
    }
    logger.info("MegaFon login %s: OTP requested", job_id)
    return job_id


async def submit_otp(job_id: str, code: str) -> None:
    """Submit the SMS code, capture the access token, kick off the import."""
    s = _sessions.get(job_id)
    if not s or s.get("status") != "otp_required":
        raise MegafonError("Сессия не найдена или истекла — начните заново")
    page = s["page"]
    try:
        otp = page.get_by_placeholder("Код из SMS")
        await otp.click()
        await otp.type(code.strip(), delay=25)
        async with page.expect_response(
            lambda r: "openid-connect/token" in r.url and r.request.method == "POST",
            timeout=30_000,
        ) as info:
            await page.get_by_role("button", name="Войти").click()
        resp = await info.value
        data = await resp.json()
        access_token = data.get("access_token")
        if not access_token:
            raise MegafonError("Токен не получен — возможно, неверный код")
    except MegafonError:
        raise
    except Exception as e:  # noqa: BLE001
        raise MegafonError(f"Не удалось войти по коду: {e}")
    finally:
        # The browser is no longer needed — the JSON API is called with the token.
        await _close_browser(job_id)

    s["status"] = "importing"
    s["started"] = time.time()
    asyncio.create_task(_run_import(job_id, access_token), name=f"megafon-import-{job_id}")
    logger.info("MegaFon login %s: token captured, import started", job_id)


async def _import_one(client: httpx.AsyncClient, item: dict) -> bool:
    """Create the call row, download the recording, queue processing. Returns
    True if a new call was imported."""
    rid = item.get("record_id")
    if not rid:
        return False
    call = await models.create_call(
        rid,
        phone=item.get("party_number"),
        direction=_direction(item.get("direction")),
        started_at=_parse_call_date(item.get("call_date")),
        duration_sec=int(item.get("duration") or 0),
    )
    call_id = str(call["id"])
    # Already processed on a previous run? (idempotent create returned existing row)
    if call.get("status") not in (None, "pending"):
        return False

    dest = Path(config.AUDIO_DIR) / f"call_{call_id}.audio"
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    async with client.stream("GET", f"/record/{rid}/file") as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            async for chunk in r.aiter_bytes():
                f.write(chunk)

    # Transcribe+analyze in the background (shares the whisper queue with meetings).
    from services.call_processing import process_call
    asyncio.create_task(process_call(call_id, dest), name=f"call-proc-{call_id[:8]}")
    return True


async def _run_import(job_id: str, access_token: str) -> None:
    s = _sessions.get(job_id) or {}
    imported = 0
    try:
        async with httpx.AsyncClient(
            base_url=config.MEGAFON_API_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=120,
        ) as client:
            for page in range(_MAX_PAGES):
                r = await client.get("/records", params={
                    "order": "DESC", "direction": "ALL", "only_favorite": "false",
                    "date_offset": 0, "page": page, "size": _RECORDS_PAGE_SIZE,
                })
                r.raise_for_status()
                body = r.json()
                items = body.get("items") if isinstance(body, dict) else body
                if not items:
                    break
                ids = [it["record_id"] for it in items if it.get("record_id")]
                existing = await models.call_external_ids_existing(ids)
                fresh = [it for it in items if it.get("record_id") not in existing]
                for it in fresh:
                    try:
                        if await _import_one(client, it):
                            imported += 1
                    except Exception as e:  # noqa: BLE001
                        logger.error("MegaFon import: record %s failed: %s", it.get("record_id"), e)
                # DESC order: once a page holds known records, everything older is known.
                if len(fresh) < len(items) or len(items) < _RECORDS_PAGE_SIZE:
                    break
        s["status"] = "done"
        s["stats"] = {"imported": imported}
        logger.info("MegaFon import %s done: %d new calls", job_id, imported)
    except Exception as e:  # noqa: BLE001
        s["status"] = "error"
        s["error"] = str(e)[:400]
        s["stats"] = {"imported": imported}
        logger.error("MegaFon import %s failed: %s", job_id, e, exc_info=True)
