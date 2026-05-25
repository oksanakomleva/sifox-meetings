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


async def speak_in_meeting(meeting_url: str, duration_minutes: int = 5) -> bool:
    from playwright.async_api import async_playwright

    if not TEST_AUDIO.exists():
        logger.error("test_audio.wav not found at %s — generate it first via Dockerfile or generate_test_audio.py", TEST_AUDIO)
        return False

    logger.info("Test Speaker starting: url=%s duration=%dmin", meeting_url, duration_minutes)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                # Fake microphone — loops test_audio.wav as mic input
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={TEST_AUDIO}",
                "--allow-file-access-from-files",
                # Don't play meeting audio locally (we're the speaker, not the listener)
                "--mute-audio",
                # Standard flags for headless-ish environment
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
            env={
                "DISPLAY": DISPLAY,
                "HOME": "/tmp",
                "PULSE_SERVER": PULSE_SERVER,
            },
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

            # Turn off camera if button is visible
            for cam_sel in [
                "button[aria-label*='камер']",
                "button[aria-label*='camera']",
                "button[data-testid*='cam']",
            ]:
                try:
                    btn = page.locator(cam_sel).first
                    if await btn.is_visible(timeout=2_000):
                        await btn.click()
                        await page.wait_for_timeout(300)
                        break
                except Exception:
                    pass

            # Join the meeting — mic ON (we want to speak)
            join_btn = page.locator(
                "button:has-text('Подключиться'), "
                "button:has-text('Войти'), "
                "button:has-text('Присоединиться'), "
                "button:has-text('Join')"
            ).first
            await join_btn.wait_for(state="visible", timeout=20_000)
            await join_btn.click()
            await page.wait_for_timeout(3_000)

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
