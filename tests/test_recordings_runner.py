import asyncio
import json

import pytest

from ku_lms_cli.cli import run
from ku_lms_cli.live import LiveCommandError, _public_playback
from ku_lms_cli.recordings import RecordingRunner
from .recording_fakes import QueueSession


def test_stream_duration_and_pause_do_not_prove_completion():
    result = _public_playback(
        {"title": "lecture"},
        {"video_mp4_partial_content_seen": True,
         "observed_duration_seconds": 1.0,
         "media_events": {"pause": True}, "completed": False},
        until_end=True, seconds=None,
    )
    assert result["completed"] is False
    assert result["completion_basis"] == "not_observed"


def test_recordings_status_needs_no_config_or_browser(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    code = run(["--env-file", str(tmp_path / "missing"), "--json", "recordings", "status"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "video": None, "position_seconds": None, "paused": True,
        "remaining": 0, "error": None,
    }


def test_queue_discovered_once_and_trusted_ended_advances_once():
    async def scenario():
        session = QueueSession()
        runner = RecordingRunner(session.provider(), "fixture")
        playback = asyncio.create_task(runner.play())
        try:
            assert await asyncio.wait_for(session.client.navigations.get(), 2) == "one"
            old_generation = session.client.generation
            session.client.emit("ended", ended=True)
            session.client.emit("ended", ended=True)
            assert await asyncio.wait_for(session.client.navigations.get(), 2) == "two"
            session.client.emit("ended", generation=old_generation, ended=True)
            session.client.emit("pause")
            assert runner.status["remaining"] == 1
            assert not runner.finished.is_set()
            session.client.emit("ended", ended=True)
            await asyncio.wait_for(playback, 2)
            assert runner.status["remaining"] == 0
            assert runner.terminal == "queue_complete"
            assert (session.entries, session.logins, session.discoveries) == (1, 1, 1)
            assert session.closed.is_set()
            assert session.client.navigations.empty()
        finally:
            playback.cancel()
            await asyncio.gather(playback, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("event, trusted, ended", [("pause", True, False), ("ended", False, True), ("ended", True, False)])
def test_pause_or_untrusted_completion_does_not_advance(event, trusted, ended, tmp_path):
    async def scenario():
        session = QueueSession()
        runner = RecordingRunner(session.provider(), "fixture")
        ready = asyncio.Event()
        service = asyncio.create_task(runner.serve(tmp_path, ready.set))
        try:
            await asyncio.wait_for(ready.wait(), 2)
            await asyncio.wait_for(session.client.navigations.get(), 2)
            reader, writer = await asyncio.open_unix_connection(str(tmp_path / "control.sock"))
            session.client.emit(event, trusted=trusted, ended=ended)
            writer.write(b"status\n")
            await writer.drain()
            status = json.loads(await asyncio.wait_for(reader.readline(), 2))
            writer.close()
            await writer.wait_closed()
            assert status["remaining"] == 2
            assert session.client.navigations.empty()
            await runner.stop()
            assert runner.terminal is None
            assert session.closed.is_set()
        finally:
            runner.shutdown.set()
            await asyncio.wait_for(service, 2)
    asyncio.run(scenario())


@pytest.mark.parametrize("event, terminal", [("error", "playback_error"), ("login_expired", "login_expired")])
def test_failures_close_browser_and_emit_terminal(event, terminal):
    async def scenario():
        session = QueueSession()
        runner = RecordingRunner(session.provider(), "fixture")
        task = asyncio.create_task(runner.play())
        await asyncio.wait_for(session.client.navigations.get(), 2)
        session.client.emit(event)
        await asyncio.wait_for(task, 2)
        assert runner.terminal == terminal
        assert runner.status["remaining"] == 2
        assert runner.status["paused"] is True
        assert session.closed.is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["status", "stop"])
def test_controls_bypass_login_and_return_compact_json(tmp_path, monkeypatch, capsys, action):
    async def scenario():
        session = QueueSession()
        runner = RecordingRunner(session.provider(), "fixture")
        from ku_lms_cli.recordings import state_directory
        ready = asyncio.Event()
        server = asyncio.create_task(runner.serve(state_directory(), ready.set))
        try:
            await asyncio.wait_for(ready.wait(), 2)
            await asyncio.wait_for(session.client.navigations.get(), 2)
            code = await asyncio.to_thread(run, ["--env-file", "/missing", "--json", "recordings", action])
            payload = json.loads(capsys.readouterr().out)
            assert code == 0
            assert set(payload) == {"video", "position_seconds", "paused", "remaining", "error"}
            assert payload["video"] == "one"
            assert payload["remaining"] == 2
            assert session.logins == 1
            if action == "stop":
                assert payload["paused"] is True
                await asyncio.wait_for(server, 2)
                assert session.closed.is_set()
        finally:
            runner.shutdown.set()
            await asyncio.wait_for(server, 2)
    # Short per-test socket path avoids POSIX sockaddr_un's 108-byte limit.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ku-control-") as directory:
        monkeypatch.setenv("XDG_STATE_HOME", directory)
        asyncio.run(scenario())


def test_duplicate_runner_cannot_replace_control_socket(tmp_path):
    async def scenario():
        first = RecordingRunner(QueueSession().provider(), "fixture")
        second_session = QueueSession()
        second = RecordingRunner(second_session.provider(), "fixture")
        ready = asyncio.Event()
        server = asyncio.create_task(first.serve(tmp_path, ready.set))
        try:
            await asyncio.wait_for(ready.wait(), 2)
            inode = (tmp_path / "control.sock").stat().st_ino
            with pytest.raises(LiveCommandError):
                await second.serve(tmp_path, lambda: None)
            assert (tmp_path / "control.sock").stat().st_ino == inode
            assert second_session.entries == 0
        finally:
            first.shutdown.set()
            await asyncio.wait_for(server, 2)
    asyncio.run(scenario())


def test_control_failure_keeps_five_field_status(tmp_path, monkeypatch, capsys):
    import ku_lms_cli.recording_process as process_module

    def unavailable(command):
        raise OSError("fixture unavailable")

    monkeypatch.setattr(process_module, "control_query", unavailable)
    code = run(["--env-file", str(tmp_path / "missing"), "--json", "recordings", "status"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert set(payload) == {"video", "position_seconds", "paused", "remaining", "error"}
    assert payload["error"]


@pytest.mark.parametrize("arguments", [
    ["recordings", "play", "--all", "--course", "fixture"],
    ["--live", "recordings", "play", "--all"],
    ["--live", "recordings", "list", "--all", "--course", "fixture"],
    ["--live", "recordings", "play", "--all", "--course", "fixture", "--title", "one"],
    ["--live", "recordings", "play", "--all", "--course", "fixture", "--id=one"],
    ["--live", "recordings", "play", "--all", "--course", "fixture", "--seconds", "1"],
])
def test_all_rejects_conflicting_or_missing_selection(arguments, capsys):
    assert run(["--env-file", "/missing", "--json", *arguments]) == 1
    assert json.loads(capsys.readouterr().out)["error"]


def test_browser_disconnect_is_a_terminal_playback_error():
    async def scenario():
        session = QueueSession()
        runner = RecordingRunner(session.provider(), "fixture")
        task = asyncio.create_task(runner.play())
        await asyncio.wait_for(session.client.navigations.get(), 2)
        assert session.client.event_callback is not None
        session.client.event_callback({"method": "CDP.disconnected", "params": {}})
        await asyncio.wait_for(task, 2)
        assert runner.terminal == "playback_error"
        assert runner.status["remaining"] == 2
        assert session.closed.is_set()
    asyncio.run(scenario())
