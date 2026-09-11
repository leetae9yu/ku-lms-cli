"""Durable, user-local recordings queue and its terminal-only control protocol."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import Path
from collections.abc import Callable
from typing import TypedDict

from .live import LiveCommandError, LiveLmsProvider, _recording_candidates, _select_course
from .media import LoginExpired, MediaPlayer
from .redaction import redact_text


class PlaybackStatus(TypedDict):
    video: str | None
    position_seconds: float | None
    paused: bool
    remaining: int
    error: str | None


def idle_status() -> PlaybackStatus:
    return {"video": None, "position_seconds": None, "paused": True, "remaining": 0, "error": None}


def state_directory() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "ku-lms-cli" / "recordings"


class RecordingRunner:
    """Mutable queue state; a completed runner retains status until explicitly stopped."""

    def __init__(self, provider: LiveLmsProvider, course: str) -> None:
        self.provider = provider
        self.course = course
        self.status = idle_status()
        self.terminal: str | None = None
        self.finished = asyncio.Event()
        self.shutdown = asyncio.Event()
        self.playback: asyncio.Task[None] | None = None
        self._clients: set[asyncio.Task[None]] = set()
        self._stop_lock = asyncio.Lock()

    def update(self, position: float, paused: bool) -> None:
        self.status["position_seconds"] = position
        self.status["paused"] = paused

    async def play(self) -> None:
        try:
            async with self.provider._session_factory() as session:
                await session.login()
                course = await _select_course(session, self.course)
                queue = await _recording_candidates(session, course)
                self.status["remaining"] = len(queue)
                player = MediaPlayer(session)
                for recording in queue:
                    self.status["video"] = redact_text(str(recording["title"]))
                    self.status["position_seconds"] = None
                    self.status["paused"] = True
                    await player.play(recording["url"], self.update)
                    self.status["remaining"] -= 1
            self.terminal = "queue_complete"
        except LoginExpired as exc:
            self.status["error"] = str(exc)
            self.terminal = "login_expired"
        except Exception as exc:  # Runner boundary: never lose failures in a detached task.
            self.status["error"] = redact_text(str(exc)) if isinstance(exc, LiveCommandError) else "recordings runner failed: " + type(exc).__name__
            self.terminal = "playback_error"
        finally:
            self.status["paused"] = True
            self.finished.set()

    async def stop(self) -> None:
        async with self._stop_lock:
            if self.playback is not None and not self.playback.done():
                self.playback.cancel()
                try:
                    await self.playback
                except asyncio.CancelledError:
                    self.status["paused"] = True
            self.finished.set()

    async def control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        stopping = False
        try:
            command = await asyncio.wait_for(reader.readline(), 5)
            if command == b"events\n":
                writer.write(b'{"subscribed":true}\n')
                await writer.drain()
                await self.finished.wait()
                if self.terminal is None:
                    return
                payload = {"event": self.terminal, **self.status}
            elif command == b"stop\n":
                stopping = True
                await self.stop()
                payload = dict(self.status)
            elif command == b"status\n":
                payload = dict(self.status)
            else:
                payload = {"error": "unknown control command"}
            writer.write((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            await writer.drain()
        except (ConnectionError, asyncio.TimeoutError):
            # A disappeared client owns no playback state; stop still completes below.
            writer.close()
        finally:
            writer.close()
            if task is not None:
                self._clients.discard(task)
            if stopping:
                self.shutdown.set()

    async def serve(self, directory: Path, ready: Callable[[], None]) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        socket_path = directory / "control.sock"
        with (directory / "runner.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LiveCommandError("recordings runner already exists; use recordings status or stop") from exc
            # Only the lock owner may remove a stale socket, never a live runner's socket.
            socket_path.unlink(missing_ok=True)
            server = await asyncio.start_unix_server(self.control, str(socket_path), limit=1024)
            socket_path.chmod(0o600)
            try:
                self.playback = asyncio.create_task(self.play())
                ready()
                await self.shutdown.wait()
            finally:
                server.close()
                await server.wait_closed()
                await self.stop()
                clients = list(self._clients)
                for task in clients:
                    task.cancel()
                await asyncio.gather(*clients, return_exceptions=True)
                socket_path.unlink(missing_ok=True)
