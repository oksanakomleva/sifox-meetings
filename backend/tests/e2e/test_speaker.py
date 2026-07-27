"""
E2E Test Speaker — joins a Telemost meeting with a fake microphone
that streams test_audio.wav. Runs on Railway where Playwright + Xvfb are set up.

The WAV file is looped automatically by Chromium's fake audio device.

Usage (on Railway via API, or locally for debugging):
    python backend/tests/e2e/test_speaker.py \
        --url https://telemost.yandex.ru/j/XXXXX \
        --duration 5
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_AUDIO = Path(__file__).parent / "test_audio.wav"
DISPLAY = os.environ.get("DISPLAY", ":99")
PULSE_SERVER = os.environ.get("PULSE_SERVER", "unix:/tmp/pulse.sock")


async def _log_controls(page) -> list[str]:
    try:
        labels = await page.evaluate(
            "() => [...document.querySelectorAll(\"button,[role='button']\")]"
            ".map(e => (e.getAttribute('aria-label') || e.getAttribute('title') || '').trim())"
            ".filter(Boolean).slice(0, 60)"
        )
        logger.info("Telemost controls: %s", labels)
        return labels
    except Exception as exc:
        logger.warning("Could not inspect Telemost controls: %s", exc)
        return []


async def _ensure_microphone_on(page) -> bool:
    """Turn the Telemost microphone on and verify the resulting UI state.

    Telemost normally joins guests muted. The old E2E test only assumed the mic
    was on, so Chromium could stream the WAV into a muted WebRTC track forever.
    """
    mic_terms = ("микроф", "microphone", " mic")
    enable_terms = ("включить", "unmute", "turn on", "enable")
    disable_terms = ("выключить", "mute microphone", "turn off", "disable")

    controls = page.locator("button,[role='button']")
    for _ in range(3):
        for index in range(await controls.count()):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                label = " ".join(filter(None, [
                    await control.get_attribute("aria-label"),
                    await control.get_attribute("title"),
                ])).strip()
                normalized = f" {label.lower()}"
                if not any(term in normalized for term in mic_terms):
                    continue

                # The label describes the action. "Выключить микрофон" means
                # it is already on; "Включить микрофон" means it is muted.
                if any(term in normalized for term in disable_terms):
                    logger.info("Microphone is ON (%r)", label)
                    return True
                if any(term in normalized for term in enable_terms):
                    logger.info("Microphone is muted; clicking %r", label)
                    await control.click(timeout=3_000)
                    await page.wait_for_timeout(800)
                    break
            except Exception:
                continue
        else:
            break

    await _log_controls(page)
    logger.error("Could not verify that the Telemost microphone is ON")
    return False


async def _ensure_camera_off(page) -> None:
    """Turn camera off only when the control explicitly says it is currently on."""
    controls = page.locator("button,[role='button']")
    for index in range(await controls.count()):
        control = controls.nth(index)
        try:
            if not await control.is_visible():
                continue
            label = " ".join(filter(None, [
                await control.get_attribute("aria-label"),
                await control.get_attribute("title"),
            ])).strip()
            normalized = label.lower()
            if (
                any(term in normalized for term in ("камер", "camera"))
                and any(term in normalized for term in ("выключить", "turn off", "disable"))
            ):
                await control.click(timeout=3_000)
                logger.info("Camera turned off via %r", label)
                return
        except Exception:
            continue


async def speak_in_meeting(meeting_url: str, duration_minutes: int = 5) -> bool:
    from playwright.async_api import async_playwright

    if not TEST_AUDIO.exists():
        logger.error("test_audio.wav not found at %s — generate it first via Dockerfile or generate_test_audio.py", TEST_AUDIO)
        return False

    logger.info("Test Speaker starting: url=%s duration=%dmin", meeting_url, duration_minutes)

    async with async_playwright() as p:
        # Inherit all env vars from the process (DISPLAY, PULSE_SERVER already set by Railway),
        # but ensure HOME=/tmp so Chromium doesn't try to write to /root
        launch_env = {**os.environ, "HOME": "/tmp"}

        browser_args = [
            # Fake microphone — loops test_audio.wav as mic input
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={TEST_AUDIO}",
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--allow-file-access-from-files",
            # Don't play meeting audio locally (we're the speaker, not the listener)
            "--mute-audio",
            # Memory saving flags
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--disable-translate",
            "--no-first-run",
            "--js-flags=--max-old-space-size=128",
        ]
        if os.environ.get("CHROMIUM_DISABLE_SANDBOX", "").lower() in ("1", "true", "yes"):
            browser_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])

        browser = await p.chromium.launch(
            # headless=True saves ~300MB RAM vs headless=False.
            # Two Chromium instances (recorder + speaker) running simultaneously would OOM.
            # Fake mic (--use-file-for-fake-audio-capture) works fine in headless mode.
            headless=True,
            args=browser_args,
            env=launch_env,
        )

        context = await browser.new_context(
            permissions=["microphone"],
            ignore_https_errors=True,
        )
        page = await context.new_page()

        try:
            logger.info("Opening meeting URL...")
            await page.goto(meeting_url, timeout=30_000)
            await page.wait_for_timeout(3_000)

            # Fill guest name
            name_input = page.locator(
                "input[placeholder*='имя'], input[placeholder*='name'], input[type='text']"
            ).first
            await name_input.wait_for(state="visible", timeout=20_000)
            await name_input.fill("Test Speaker")
            logger.info("Filled name: Test Speaker")

            # Keep video disabled, but don't blindly click a camera button: the
            # previous code could turn an already-disabled camera back on.
            await _ensure_camera_off(page)

            # Telemost starts guests muted. Enable the fake WAV microphone on
            # the pre-join screen when the control is available.
            await _ensure_microphone_on(page)

            # Join the meeting — mic ON (we want to speak)
            join_btn = page.locator(
                "button:has-text('Подключиться'), "
                "button:has-text('Войти'), "
                "button:has-text('Присоединиться'), "
                "button:has-text('Join')"
            ).first
            await join_btn.wait_for(state="visible", timeout=20_000)
            await join_btn.click()
            await page.wait_for_timeout(5_000)

            # The in-call toolbar may be a different DOM tree from pre-join.
            # Verify once more and fail loudly instead of reporting a false pass.
            if not await _ensure_microphone_on(page):
                raise RuntimeError("Test Speaker joined, but its microphone is still muted")

            logger.info("E2E_SPEAKER_READY — joined with microphone ON")
            print("E2E_SPEAKER_READY", flush=True)
            logger.info("✅ Test Speaker joined — streaming test_audio.wav for %d min", duration_minutes)
            await asyncio.sleep(duration_minutes * 60)

        except asyncio.CancelledError:
            logger.info("Test Speaker cancelled — leaving meeting")
        except Exception as e:
            logger.error("Test Speaker error: %s", e)
            return False
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    logger.info("✅ Test Speaker left the meeting")
    return True


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [speaker] %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Telemost meeting URL")
    ap.add_argument("--duration", type=int, default=5, help="How many minutes to stay in meeting")
    args = ap.parse_args()

    ok = asyncio.run(speak_in_meeting(args.url, args.duration))
    sys.exit(0 if ok else 1)
