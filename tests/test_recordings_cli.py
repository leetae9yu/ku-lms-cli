"""Actual CLI subprocesses talking to an independently owned browser fixture."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from ku_lms_cli.live import LiveLmsProvider
from ku_lms_cli.recordings import RecordingRunner, state_directory
from .test_recordings_browser import browser, video_page


async def cli(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "ku_lms_cli.cli", "--env-file", "/missing", "--json", "recordings", *args,
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise
    assert not stderr, stderr.decode()
    return process.returncode, json.loads(stdout)


@pytest.mark.parametrize("outcome", ["complete", "error", "login", "stop"])
def test_real_cli_with_native_browser_queue(outcome, monkeypatch):
    async def scenario():
        session = browser()
        calls = []
        entries = []
        pause_observed = asyncio.Event()
        first = video_page(video_attributes='onplaying="if(!window.didPause){window.didPause=true;this.pause()}"')
        second = video_page()

        async def login():
            entries.append("login")

        async def fetch_json(path_or_url):
            calls.append(path_or_url)
            if path_or_url.startswith("/api/v1/courses?"):
                return [{"id": 1, "name": "fixture"}]
            return [{"name": "week", "items": [
                {"title": "one", "type": "ExternalTool", "html_url": first},
                {"title": "two", "type": "ExternalTool", "html_url": second},
                {"title": "future", "type": "ExternalTool", "html_url": second, "unlock_at": "2099-01-01T00:00:00Z"},
            ]}]

        session.login = login
        session.fetch_json = fetch_json
        provider = LiveLmsProvider(session.config, session.options, session_factory=lambda: session)
        runner = RecordingRunner(provider, "fixture")
        original_update = runner.update
        playing = False

        def update(position, paused):
            nonlocal playing
            original_update(position, paused)
            playing = playing or not paused
            if playing and paused:
                pause_observed.set()

        runner.update = update
        ready = asyncio.Event()
        service = asyncio.create_task(runner.serve(state_directory(), ready.set))
        writer = None
        try:
            await asyncio.wait_for(ready.wait(), 10)
            await asyncio.wait_for(pause_observed.wait(), 15)
            code, status = await cli("status")
            assert code == 0
            assert set(status) == {"video", "position_seconds", "paused", "remaining", "error"}
            assert status["video"] == "one" and status["paused"] is True and status["remaining"] == 2
            # The protocol acknowledges registration before the action is triggered.
            reader, writer = await asyncio.open_unix_connection(str(state_directory() / "control.sock"))
            writer.write(b"events\n")
            await writer.drain()
            assert json.loads(await asyncio.wait_for(reader.readline(), 2)) == {"subscribed": True}
            terminal = asyncio.create_task(reader.readline())
            if outcome == "stop":
                code, status = await cli("stop")
                assert code == 0 and status["paused"] is True
                assert await asyncio.wait_for(terminal, 10) == b""
                await asyncio.wait_for(service, 10)
            else:
                if outcome == "complete":
                    await session.evaluate("document.querySelector('video').play()")
                elif outcome == "error":
                    await session.evaluate("(() => {const v=document.querySelector('video');v.src='data:video/mp4;base64,bm90LXZpZGVv';v.load();})()")
                else:
                    await session.evaluate("document.body.insertAdjacentHTML('beforeend','<input type=\"password\">')")
                event = json.loads(await asyncio.wait_for(terminal, 15))
                expected = {"complete": "queue_complete", "error": "playback_error", "login": "login_expired"}[outcome]
                assert event["event"] == expected
                assert event["remaining"] == (0 if outcome == "complete" else 2)
                # A supervisor arriving after completion gets the same terminal event.
                code, replay = await cli("events")
                assert code == (0 if outcome == "complete" else 1)
                assert replay == event
                assert await cli("stop") == (0, {k: v for k, v in event.items() if k != "event"})
                await asyncio.wait_for(service, 10)
            assert entries == ["login"]
            assert len(calls) == 2  # One course query and one queue discovery for both videos.
            assert session._proc is not None
            assert session._proc.poll() is not None
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            runner.shutdown.set()
            await asyncio.wait_for(service, 10)

    with tempfile.TemporaryDirectory(prefix="ku-cli-") as directory:
        monkeypatch.setenv("XDG_STATE_HOME", directory)
        asyncio.run(scenario())



def test_detached_play_all_prevents_duplicate_and_reports_startup_error(tmp_path, monkeypatch):
    config = tmp_path / "fixture.env"
    config.write_text("KU_LMS_ID=fixture\nKU_LMS_PWD=fixture\n")

    async def start():
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "ku_lms_cli.cli", "--env-file", str(config), "--json", "--live",
            "recordings", "play", "--all", "--course", "fixture", env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        assert not stderr
        return process.returncode, json.loads(stdout)

    async def scenario():
        code, status = await start()
        try:
            assert code == 0 and status["remaining"] == 0
            # The invoking CLI has exited; its detached runner still serves events.
            code, event = await cli("events")
            assert code == 1 and event["event"] == "playback_error"
            code, duplicate = await start()
            assert code == 1 and duplicate["error"]
            code, status = await cli("status")
            assert code == 1 and status["error"] == event["error"]
        finally:
            code, _ = await cli("stop")
            assert code == 0

    with tempfile.TemporaryDirectory(prefix="ku-detach-") as directory:
        monkeypatch.setenv("XDG_STATE_HOME", directory)
        monkeypatch.setenv("KU_LMS_CHROME", str(tmp_path / "absent-browser"))
        asyncio.run(scenario())
