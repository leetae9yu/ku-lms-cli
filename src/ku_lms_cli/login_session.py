"""Detached logged-in browser shared by live commands over a user-local Unix socket (POSIX)."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import load_config
from .errors import LiveCommandError
from .live import CdpBrowserSession, LiveOptions
from .redaction import redact_text

DEFAULT_IDLE_SECONDS = 3 * 60 * 60


def session_directory() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "ku-lms-cli" / "session"


def session_query(command: str) -> dict[str, Any] | None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        try:
            client.connect(str(session_directory() / "control.sock"))
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        client.sendall((command + "\n").encode())
        with client.makefile("rb") as stream:
            raw = stream.readline(4096)
    if not raw:
        raise LiveCommandError("login session control connection closed")
    return json.loads(raw)


def session_port() -> int | None:
    reply = session_query("endpoint")
    return int(reply["port"]) if reply else None


def start_session(env_file: str, timeout: float, headful: bool, idle_seconds: float) -> dict[str, Any]:
    read_fd, write_fd = os.pipe()
    command = [sys.executable, "-m", "ku_lms_cli.login_session", "--ready-fd", str(write_fd),
               "--env-file", env_file, "--timeout", str(timeout), "--idle-seconds", str(idle_seconds)]
    if headful:
        command.append("--headful")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True,
                                   pass_fds=(write_fd,), env=env)
    except OSError as exc:
        os.close(read_fd)
        raise LiveCommandError("could not launch login session") from exc
    finally:
        os.close(write_fd)
    with os.fdopen(read_fd, "rb") as ready:
        # Browser startup plus a full SSO login, each bounded by --timeout.
        readable, _, _ = select.select([ready], [], [], timeout * 3 + 15)
        if not readable:
            process.terminate()
            process.wait(timeout=10)
            raise LiveCommandError("login session startup timed out")
        raw = ready.readline(4096)
    if not raw:
        process.wait(timeout=10)
        raise LiveCommandError("login session exited before login completed")
    payload = json.loads(raw)
    if payload.get("error"):
        process.wait(timeout=10)
        raise LiveCommandError(payload["error"])
    return payload


async def _serve(args: argparse.Namespace, ready) -> None:
    directory = session_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    socket_path = directory / "control.sock"
    with (directory / "session.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LiveCommandError("login session already running; use session status or stop") from exc
        options = LiveOptions(headless=not args.headful, timeout_seconds=args.timeout)
        async with CdpBrowserSession(load_config(args.env_file), options) as browser:
            await browser.login()
            stop = asyncio.Event()
            used = asyncio.Event()
            state = {"running": True, "started_at": time.time(), "last_used_at": time.time(),
                     "idle_timeout_seconds": args.idle_seconds}

            async def control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                try:
                    command = await asyncio.wait_for(reader.readline(), 5)
                    if command == b"endpoint\n":
                        state["last_used_at"] = time.time()
                        used.set()
                        payload: dict[str, Any] = {"port": browser.port}
                    elif command == b"status\n":
                        payload = dict(state)
                    elif command == b"stop\n":
                        stop.set()
                        payload = {**state, "running": False}
                    else:
                        payload = {"error": "unknown control command"}
                    writer.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
                    await writer.drain()
                except (ConnectionError, asyncio.TimeoutError):
                    pass
                finally:
                    writer.close()

            # Only the lock owner may remove a stale socket, never a live session's socket.
            socket_path.unlink(missing_ok=True)
            server = await asyncio.start_unix_server(control, str(socket_path), limit=1024)
            socket_path.chmod(0o600)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
            try:
                ready.write(json.dumps(state) + "\n")
                ready.flush()
                while not stop.is_set():
                    used.clear()
                    waiters = {asyncio.ensure_future(stop.wait()), asyncio.ensure_future(used.wait())}
                    done, pending = await asyncio.wait(waiters, timeout=args.idle_seconds,
                                                       return_when=asyncio.FIRST_COMPLETED)
                    for waiter in pending:
                        waiter.cancel()
                    if not done:
                        break
            finally:
                server.close()
                await server.wait_closed()
                socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-fd", type=int, required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    args = parser.parse_args()
    os.umask(0o077)
    with os.fdopen(args.ready_fd, "w") as ready:
        try:
            asyncio.run(_serve(args, ready))
        except (ValueError, OSError, LiveCommandError) as exc:
            try:
                ready.write(json.dumps({"error": redact_text(str(exc))}) + "\n")
                ready.flush()
            except (BrokenPipeError, ValueError):
                print("login session failed: " + type(exc).__name__, file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
