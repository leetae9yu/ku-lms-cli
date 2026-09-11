"""Detached recordings entrypoint and one-query local CLI clients (POSIX)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import select
import signal
import socket
import subprocess
import sys
from pathlib import Path

from .config import load_config
from .live import LiveCommandError, LiveLmsProvider, LiveOptions
from .recordings import PlaybackStatus, RecordingRunner, idle_status, state_directory


def control_query(command: str) -> PlaybackStatus | dict[str, str | float | bool | None]:
    """One socket connection, with no config, browser, or LMS discovery."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(15)
        try:
            client.connect(str(state_directory() / "control.sock"))
        except (FileNotFoundError, ConnectionRefusedError):
            if command == "events":
                raise LiveCommandError("no recordings runner") from None
            return idle_status()
        client.sendall((command + "\n").encode())
        if command == "events":
            client.settimeout(None)
        with client.makefile("rb") as stream:
            raw = stream.readline(16384)
            if command == "events":
                if json.loads(raw) != {"subscribed": True}:
                    raise LiveCommandError("invalid recordings event subscription")
                raw = stream.readline(16384)
        if not raw:
            if command == "events":
                return {}  # Explicit stop closes subscriptions without a terminal event.
            raise LiveCommandError("recordings control connection closed")
        return json.loads(raw)


def start_runner(args: argparse.Namespace) -> dict[str, str | float | bool | None]:
    """Return after an inherited pipe signals readiness, not after a polling delay."""
    read_fd, write_fd = os.pipe()
    command = [sys.executable, "-m", "ku_lms_cli.recording_process", "--ready-fd", str(write_fd),
               "--env-file", args.env_file,
               "--course", args.course, "--timeout", str(args.timeout)]
    if args.headful:
        command.append("--headful")
    # Use the same source/install as the invoking CLI, even from another working directory.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True,
                                   pass_fds=(write_fd,), env=env)
    except OSError as exc:
        os.close(read_fd)
        raise LiveCommandError("could not launch recordings runner") from exc
    finally:
        os.close(write_fd)
    try:
        with os.fdopen(read_fd, "rb") as ready:
            readable, _, _ = select.select([ready], [], [], 15)
            if not readable:
                process.terminate()
                process.wait(timeout=10)
                raise LiveCommandError("recordings runner startup timed out")
            raw = ready.readline(16384)
        if not raw:
            process.wait(timeout=10)
            raise LiveCommandError("recordings runner exited before readiness")
        payload = json.loads(raw)
        if payload.get("error"):
            process.wait(timeout=10)
            raise LiveCommandError(payload["error"])
        return payload
    except OSError as exc:
        raise LiveCommandError("recordings runner startup failed") from exc


async def _serve(args: argparse.Namespace, ready) -> None:
    provider = LiveLmsProvider(load_config(args.env_file), LiveOptions(headless=not args.headful, timeout_seconds=args.timeout))
    runner = RecordingRunner(provider, args.course)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, runner.shutdown.set)

    def announce() -> None:
        ready.write(json.dumps(runner.status) + "\n")
        ready.flush()

    await runner.serve(state_directory(), announce)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-fd", type=int, required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--course", required=True)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    os.umask(0o077)
    with os.fdopen(args.ready_fd, "w") as ready:
        try:
            asyncio.run(_serve(args, ready))
        except (ValueError, OSError, LiveCommandError) as exc:
            from .redaction import redact_text
            try:
                ready.write(json.dumps({"error": redact_text(str(exc))}) + "\n")
                ready.flush()
            except BrokenPipeError:
                print("recordings runner failed: " + type(exc).__name__, file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
