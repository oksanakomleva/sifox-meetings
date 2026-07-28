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
import json
import logging
import os
import sys
from contextlib import suppress
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_AUDIO = Path(__file__).parent / "test_audio.wav"
LIVE_ASSISTANT_AUDIO = Path(__file__).parent / "live_assistant_test_audio.wav"
DISPLAY = os.environ.get("DISPLAY", ":99")
PULSE_SERVER = os.environ.get("PULSE_SERVER", "unix:/tmp/pulse.sock")


async def _dismiss_modals(page) -> None:
    """Dismiss Telemost informational overlays that can cover media/join buttons."""
    selectors = [
        "button:has-text('Понятно')",
        "button:has-text('Хорошо')",
        "button:has-text('Продолжить')",
        "button:has-text('Закрыть')",
        "button:has-text('OK')",
        "button:has-text('Got it')",
        "button:has-text('Continue')",
        "button[aria-label*='Закрыть' i]",
        "button[aria-label*='Close' i]",
    ]
    for _ in range(3):
        dismissed = False
        for selector in selectors:
            try:
                button = page.locator(selector).last
                if await button.count() and await button.is_visible():
                    await button.click(force=True, timeout=2_000)
                    logger.info("Dismissed Telemost modal via %s", selector)
                    await page.wait_for_timeout(500)
                    dismissed = True
                    break
            except Exception:
                continue
        if not dismissed:
            return


async def _capture_debug(page, filename: str) -> None:
    try:
        dialogs = await page.locator(
            "[role='dialog'], div[class*='Modal'], div[class*='Overlay']"
        ).all_inner_texts()
        logger.error("Visible Telemost dialogs/overlays: %s", dialogs[-10:])
    except Exception:
        pass
    try:
        debug_dir = Path("/tmp/recorder-debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(debug_dir / filename), full_page=True)
    except Exception as exc:
        logger.warning("Could not save E2E speaker screenshot: %s", exc)


async def _log_controls(page) -> list[dict]:
    try:
        controls = await page.evaluate(
            "() => [...document.querySelectorAll(\"button,[role='button']\")]"
            ".map(e => ({"
            " label: e.getAttribute('aria-label'),"
            " title: e.getAttribute('title'),"
            " testid: e.getAttribute('data-testid'),"
            " state: e.getAttribute('data-state'),"
            " pressed: e.getAttribute('aria-pressed'),"
            " cls: String(e.className || '').slice(0, 160)"
            "})).filter(x => x.label || x.title || x.testid || x.state).slice(0, 60)"
        )
        logger.info("Telemost controls: %s", controls)
        return controls
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
    clicked_unknown = False
    for _ in range(3):
        await _dismiss_modals(page)
        unknown_control = None
        clicked = False
        for index in range(await controls.count()):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                attributes = [
                    await control.get_attribute("aria-label"),
                    await control.get_attribute("title"),
                    await control.get_attribute("data-testid"),
                    await control.get_attribute("data-state"),
                    await control.get_attribute("data-status"),
                    await control.get_attribute("class"),
                ]
                label = " ".join(filter(None, attributes)).strip()
                normalized = f" {label.lower()}"
                if not any(term in normalized for term in mic_terms):
                    continue

                # The label describes the action. "Выключить микрофон" means
                # it is already on; "Включить микрофон" means it is muted.
                if any(term in normalized for term in enable_terms):
                    logger.info("Microphone is muted; clicking %r", label)
                    await control.click(timeout=3_000)
                    await page.wait_for_timeout(1_500)
                    clicked = True
                    break
                if any(term in normalized for term in disable_terms):
                    logger.info("Microphone is ON (%r)", label)
                    return True
                if any(
                    token in normalized
                    for token in (" muted", " off", " disabled", " inactive")
                ):
                    logger.info("Microphone state is OFF; clicking %r", label)
                    await control.click(timeout=3_000)
                    await page.wait_for_timeout(1_500)
                    clicked = True
                    break
                if any(
                    token in normalized
                    for token in (" unmuted", " on", " enabled", " active")
                ):
                    logger.info("Microphone state is ON (%r)", label)
                    return True
                unknown_control = control
            except Exception:
                continue

        if clicked:
            continue
        if unknown_control is not None and not clicked_unknown:
            # Telemost sometimes exposes only data-testid="mic-button" with no
            # state. Guests start muted, so click exactly once; never click it
            # again on a subsequent probe and accidentally toggle back to mute.
            logger.warning("Microphone state is opaque; performing one fallback click")
            await unknown_control.click(timeout=3_000)
            await page.wait_for_timeout(1_500)
            clicked_unknown = True
            continue
        if clicked_unknown:
            logger.warning("Microphone control remains opaque after one click; treating it as ON")
            return True
        if unknown_control is None:
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


async def _create_listener_sink(name: str) -> int:
    proc = await asyncio.create_subprocess_exec(
        "pactl",
        "load-module",
        "module-null-sink",
        f"sink_name={name}",
        f"sink_properties=device.description={name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"could not create listener sink: {stderr.decode(errors='replace')[-500:]}"
        )
    return int(stdout.decode().strip())


async def _pin_listener_audio(sink_name: str) -> int:
    """Move this speaker browser's output onto its private listener sink."""
    try:
        needle = f"PULSE_SINK={sink_name}".encode()
        pids: set[str] = set()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/environ", "rb") as source:
                    if needle in source.read():
                        pids.add(pid)
            except OSError:
                continue
        proc = await asyncio.create_subprocess_exec(
            "pactl",
            "list",
            "sink-inputs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        current = None
        moves: list[str] = []
        for raw in stdout.decode(errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("Sink Input #"):
                current = line.split("#", 1)[1]
            elif current and "application.process.id" in line and '"' in line:
                if line.split('"')[1] in pids:
                    moves.append(current)
                current = None
        pinned = 0
        for sink_input in moves:
            move = await asyncio.create_subprocess_exec(
                "pactl",
                "move-sink-input",
                sink_input,
                sink_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await move.wait()
            if move.returncode == 0:
                pinned += 1
        return pinned
    except Exception as exc:
        logger.debug("Could not pin listener audio: %s", exc)
        return 0


async def _listener_pin_loop(sink_name: str, state: dict[str, int | bool]) -> None:
    try:
        while True:
            pinned = await _pin_listener_audio(sink_name)
            if pinned:
                if not state["pinned"]:
                    logger.info(
                        "Listener browser playback stream found (%d stream(s))",
                        pinned,
                    )
                state["pinned"] = True
                state["pin_count"] = pinned
            await asyncio.sleep(3)
    except asyncio.CancelledError:
        pass


async def _compact_listener_audio(path: Path) -> Path:
    """Remove long silence before asking STT what a remote participant heard.

    The listener records roughly two minutes for a 1–3 second bot answer.
    Whisper hallucinates stock subtitle credits on an almost-silent file and can
    completely miss the real short phrase. Compacting only audible regions makes
    this an honest check: silence now fails explicitly instead of producing a
    plausible-looking transcript.
    """
    compact = path.with_name(f"{path.stem}-speech.wav")
    try:
        compact.unlink()
    except FileNotFoundError:
        pass
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-af",
        (
            "silenceremove="
            "start_periods=1:start_duration=0.05:start_threshold=-50dB:"
            "stop_periods=-1:stop_duration=0.35:stop_threshold=-50dB"
        ),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(compact),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        with suppress(OSError):
            compact.unlink()
        raise RuntimeError("listener silence compaction timed out")
    if proc.returncode != 0:
        with suppress(OSError):
            compact.unlink()
        raise RuntimeError(
            "listener silence compaction failed: "
            f"{stderr.decode(errors='replace')[-500:]}"
        )
    size = compact.stat().st_size if compact.exists() else 0
    logger.info(
        "Listener audio compacted: %d bytes → %d bytes",
        path.stat().st_size,
        size,
    )
    if size < 10_000:
        try:
            compact.unlink()
        except OSError:
            pass
        raise RuntimeError("Remote listener captured no audible speech")
    return compact


async def _transcribe_listener_audio(path: Path) -> str:
    speech_path: Path | None = None
    client = None
    try:
        speech_path = await _compact_listener_audio(path)
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
            with open(speech_path, "rb") as audio:
                response = await asyncio.wait_for(
                    client.audio.transcriptions.create(
                        model=os.environ.get(
                            "LIVE_QUESTION_STT_MODEL",
                            "whisper-1",
                        ),
                        file=audio,
                        language="ru",
                        response_format="text",
                    ),
                    timeout=60,
                )
            if isinstance(response, str):
                return response.strip()
            text = str(getattr(response, "text", response) or "").strip()
            if text:
                return text
        except Exception as exc:
            logger.warning("Cloud listener transcription failed; using local: %s", exc)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "services.transcribe_worker",
            str(speech_path),
            "small",
            "ru",
            "3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=240)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("listener transcription timed out")
        if proc.returncode != 0:
            raise RuntimeError(
                f"listener transcription failed: {stderr.decode(errors='replace')[-800:]}"
            )
        payload = json.loads(stdout.decode(errors="replace").strip().splitlines()[-1])
        return " ".join(
            segment.get("text", "") for segment in payload.get("segments", [])
        ).strip()
    finally:
        if client is not None:
            with suppress(Exception):
                await client.close()
        if speech_path:
            try:
                speech_path.unlink()
            except OSError:
                pass


async def _cleanup_listener(capture, module_id: int | None) -> None:
    if capture and capture.returncode is None:
        capture.terminate()
        try:
            _, stderr = await asyncio.wait_for(capture.communicate(), timeout=10)
        except asyncio.TimeoutError:
            capture.kill()
            _, stderr = await capture.communicate()
        if capture.returncode not in (0, -15):
            logger.warning(
                "Listener capture exited %s: %s",
                capture.returncode,
                stderr.decode(errors="replace")[-500:],
            )
    if module_id is not None:
        unload = await asyncio.create_subprocess_exec(
            "pactl",
            "unload-module",
            str(module_id),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await unload.wait()


async def speak_in_meeting(
    meeting_url: str,
    duration_minutes: int = 5,
    audio_profile: str = "standard",
) -> bool:
    from playwright.async_api import async_playwright

    audio_file = (
        LIVE_ASSISTANT_AUDIO if audio_profile == "live_assistant" else TEST_AUDIO
    )
    if not audio_file.exists():
        logger.error(
            "%s not found — generate it first via Dockerfile",
            audio_file,
        )
        return False

    logger.info(
        "Test Speaker starting: url=%s duration=%dmin profile=%s",
        meeting_url,
        duration_minutes,
        audio_profile,
    )

    listener_sink = None
    listener_module = None
    listener_capture = None
    listener_pin_task = None
    listener_state: dict[str, int | bool] = {"pinned": False, "pin_count": 0}
    listener_path = Path(f"/tmp/e2e-listener-{os.getpid()}.wav")
    try:
        listener_path.unlink()
    except FileNotFoundError:
        pass
    success = True
    async with async_playwright() as p:
        # Inherit all env vars from the process (DISPLAY, PULSE_SERVER already set by Railway),
        # but ensure HOME=/tmp so Chromium doesn't try to write to /root
        launch_env = {**os.environ, "HOME": "/tmp"}
        if audio_profile == "live_assistant":
            listener_sink = f"e2e_listener_{os.getpid()}"
            listener_module = await _create_listener_sink(listener_sink)
            launch_env["PULSE_SINK"] = listener_sink
            listener_capture = await asyncio.create_subprocess_exec(
                "parec",
                f"--device={listener_sink}.monitor",
                "--file-format=wav",
                str(listener_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )

        browser_args = [
            # Fake microphone — loops test_audio.wav as mic input
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={audio_file}",
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--allow-file-access-from-files",
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
        if audio_profile != "live_assistant":
            # The normal recorder E2E only needs to speak. The live-assistant E2E
            # must also capture what the remote Protocaller sends back.
            browser_args.append("--mute-audio")
        if os.environ.get("CHROMIUM_DISABLE_SANDBOX", "").lower() in ("1", "true", "yes"):
            browser_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])

        try:
            browser = await p.chromium.launch(
                # headless=True saves ~300MB RAM vs headless=False.
                # Two Chromium instances (recorder + speaker) running simultaneously would OOM.
                # Fake mic (--use-file-for-fake-audio-capture) works fine in headless mode.
                headless=True,
                args=browser_args,
                env=launch_env,
            )
        except Exception:
            await _cleanup_listener(listener_capture, listener_module)
            listener_capture = None
            listener_module = None
            raise
        if listener_sink:
            listener_pin_task = asyncio.create_task(
                _listener_pin_loop(listener_sink, listener_state),
                name="e2e-listener-pin",
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

            # Do not enable the microphone on the pre-join screen. Telemost may
            # open a permission/info modal there; that overlay then intercepts
            # the Join click. Join muted first, enable mic in the in-call toolbar.
            await _dismiss_modals(page)

            # Join the meeting — mic ON (we want to speak)
            join_btn = page.locator(
                "button:has-text('Подключиться'), "
                "button:has-text('Войти'), "
                "button:has-text('Присоединиться'), "
                "button:has-text('Join')"
            ).first
            await join_btn.wait_for(state="visible", timeout=20_000)
            await join_btn.click(force=True)
            await page.wait_for_timeout(5_000)

            # The in-call toolbar may be a different DOM tree from pre-join.
            # Verify once more and fail loudly instead of reporting a false pass.
            if not await _ensure_microphone_on(page):
                await _capture_debug(page, "e2e-speaker-mic-failed.png")
                raise RuntimeError("Test Speaker joined, but its microphone is still muted")

            debug_dir = Path("/tmp/recorder-debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            await page.screenshot(
                path=str(debug_dir / "e2e-speaker-ready.png"),
                full_page=True,
            )
            logger.info("E2E_SPEAKER_READY — joined with microphone ON")
            print("E2E_SPEAKER_READY", flush=True)
            logger.info("✅ Test Speaker joined — streaming test_audio.wav for %d min", duration_minutes)
            await asyncio.sleep(duration_minutes * 60)

        except asyncio.CancelledError:
            logger.info("Test Speaker cancelled — leaving meeting")
        except Exception as e:
            logger.error("Test Speaker error: %s", e)
            await _log_controls(page)
            await _capture_debug(page, "e2e-speaker-error.png")
            success = False
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    if listener_pin_task:
        listener_pin_task.cancel()
        await asyncio.gather(listener_pin_task, return_exceptions=True)
    await _cleanup_listener(listener_capture, listener_module)
    if not success:
        return False
    if audio_profile == "live_assistant":
        if not listener_state["pinned"]:
            logger.error("Listener browser playback stream was not found")
            return False
        if not listener_path.exists() or listener_path.stat().st_size < 10_000:
            logger.error("Listener capture is empty: %s", listener_path)
            return False
        try:
            listener_text = await _transcribe_listener_audio(listener_path)
            logger.info("Remote listener transcript: %r", listener_text)
            print(f"E2E_LISTENER_TRANSCRIPT={listener_text}", flush=True)
        finally:
            try:
                listener_path.unlink()
            except OSError:
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
    ap.add_argument(
        "--audio-profile",
        choices=("standard", "live_assistant"),
        default="standard",
    )
    args = ap.parse_args()

    ok = asyncio.run(
        speak_in_meeting(args.url, args.duration, args.audio_profile)
    )
    sys.exit(0 if ok else 1)
