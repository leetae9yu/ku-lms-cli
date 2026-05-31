"""Command-line entrypoint for KU LMS CLI scaffold."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import DEFAULT_ENV_PATH, load_config
from .discovery import DEFAULT_ENTRY_URL, run_discovery
from .domain import to_dicts
from .paths import PathPolicy
from .provider import FixtureProvider
from .live import LiveCommandError, LiveLmsProvider, LiveOptions
from .redaction import redact_data
from .session import SessionState, write_session_marker

FORBIDDEN_COMMANDS = {"submit", "upload", "post", "comment", "delete", "edit", "write", "mark", "enroll"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ku-lms", description="Safe discovery-first KU LMS CLI")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Path to KU_LMS.env")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON where supported")
    parser.add_argument("--live", dest="global_live", action="store_true", help="Use live KU LMS browser/API provider for supported read-only commands")
    parser.add_argument("--headful", action="store_true", help="Show browser window in live mode instead of headless mode")
    parser.add_argument("--timeout", type=float, default=60.0, help="Live browser timeout in seconds")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="Show redacted local config/session status")
    sub.add_parser("login", help="Validate credentials and prepare local session cache (discovery implementation pending)")
    discover = sub.add_parser("discover", help="Run redacted LMS discovery")
    discover.add_argument("--entry-url", default=DEFAULT_ENTRY_URL, help="KU LMS entry URL to start from")
    discover.add_argument("--live", action="store_true", help="Attempt live browser discovery; dry-run schema is the default")
    discover.add_argument(
        "--devtools-observation",
        help="Consume a redacted shape-only DevTools/CDP observation JSON and normalize it into discovery artifacts",
    )
    sub.add_parser("courses", help="List courses (implementation pending)")
    materials = sub.add_parser("materials", help="List or download lecture materials")
    materials.add_argument("action", nargs="?", choices=["list", "download"], default="list")
    materials.add_argument("--id", dest="item_id", default="sample-material", help="Material id for download")
    assignments = sub.add_parser("assignments", help="List assignments/deadlines and downloadable attachments")
    assignments.add_argument("action", nargs="?", choices=["list", "deadlines", "download"], default="list")
    assignments.add_argument("--id", dest="item_id", default="sample-assignment-file", help="Attachment id for download")
    assignments.add_argument("--course", default="", help="Course name substring for live mode")
    recordings = sub.add_parser("recordings", help="List/play/keepalive recorded lectures")
    recordings.add_argument("action", nargs="?", choices=["list", "play", "keepalive"], default="list")
    recordings.add_argument("--id", dest="item_id", default="sample-recording", help="Recording id for play/keepalive")
    recordings.add_argument("--course", default="", help="Course name substring for live mode")
    recordings.add_argument("--title", default="", help="Recording title/module substring for live playback")
    recordings.add_argument("--until-end", action="store_true", help="Play a recording until completion is observed in live mode")
    recordings.add_argument("--seconds", type=float, default=None, help="Playback/keepalive duration in seconds for live mode")
    return parser


def _emit(payload: dict[str, Any], as_json: bool) -> int:
    safe = redact_data(payload)
    if as_json:
        print(json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key, value in safe.items():
            print(f"{key}: {value}")
    return int(payload.get("exit_code", 0))


def _first_command(argv: list[str]) -> str | None:
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token in {"--env-file", "--timeout"}:
            skip_next = True
            continue
        if token.startswith("--env-file=") or token.startswith("--timeout=") or token in {"--json", "--live", "--headful"}:
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def run(argv: list[str] | None = None, live_provider_factory: Any | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv or [])
    first_command = _first_command(raw_argv)
    if first_command in FORBIDDEN_COMMANDS:
        as_json = "--json" in raw_argv
        return _emit({"ok": False, "error": "not supported by design", "command": first_command, "exit_code": 2}, as_json)
    args = parser.parse_args(raw_argv)
    if not args.command:
        parser.print_help()
        return 0
    policy = PathPolicy()
    if args.command in {"login", "discover"} or getattr(args, "global_live", False):
        policy.ensure()
    try:
        config = load_config(args.env_file)
    except ValueError as exc:
        return _emit({"ok": False, "error": str(exc), "exit_code": 1}, args.json)
    if args.command == "status":
        return _emit({"ok": True, "config": config.redacted(), "session_cache": str(policy.cache_dir), "implementation": "scaffold"}, args.json)
    if args.command == "login":
        marker = write_session_marker(policy.resolve(policy.cache_dir), SessionState.new(str(config.env_path)))
        return _emit({"ok": True, "message": "credentials present; local session cache prepared", "config": config.redacted(), "session_marker": str(marker)}, args.json)
    if args.command == "discover":
        result = run_discovery(config, policy, entry_url=args.entry_url, live=args.live, observation_path=args.devtools_observation)
        return _emit(result, args.json)
    live_mode = bool(getattr(args, "global_live", False))
    if live_mode:
        options = LiveOptions(headless=not args.headful, timeout_seconds=args.timeout)
        provider = live_provider_factory(config, options) if live_provider_factory else LiveLmsProvider(config, options)
    else:
        provider = FixtureProvider()
    if args.command == "courses":
        try:
            courses = provider.courses()
        except LiveCommandError as exc:
            return _emit({"ok": False, "error": str(exc), "exit_code": 1}, args.json)
        return _emit({"ok": True, "courses": courses if live_mode else to_dicts(courses)}, args.json)
    if args.command == "materials":
        if live_mode:
            return _emit({"ok": False, "error": "live materials support is not implemented in this read-only build", "exit_code": 1}, args.json)
        if args.action == "download":
            try:
                path = provider.download_material(args.item_id, policy)
            except KeyError:
                return _emit({"ok": False, "error": "material not found", "id": args.item_id, "exit_code": 1}, args.json)
            return _emit({"ok": True, "downloaded": str(path), "id": args.item_id}, args.json)
        return _emit({"ok": True, "materials": to_dicts(provider.materials())}, args.json)
    if args.command == "assignments":
        if live_mode:
            try:
                if args.action == "deadlines":
                    return _emit({"ok": True, "deadlines": provider.deadlines(args.course)}, args.json)
                if args.action == "download":
                    return _emit({"ok": False, "error": "assignment attachment download is fixture-only in this build", "exit_code": 1}, args.json)
                return _emit({"ok": True, "assignments": provider.assignments(args.course)}, args.json)
            except LiveCommandError as exc:
                return _emit({"ok": False, "error": str(exc), "exit_code": 1}, args.json)
        assignments = to_dicts(provider.assignments())
        if args.action == "download":
            try:
                path = provider.download_assignment_attachment(args.item_id, policy)
            except KeyError:
                return _emit({"ok": False, "error": "assignment attachment not found", "id": args.item_id, "exit_code": 1}, args.json)
            return _emit({"ok": True, "downloaded": str(path), "id": args.item_id}, args.json)
        if args.action == "deadlines":
            return _emit({"ok": True, "deadlines": [{"id": item["id"], "course_id": item["course_id"], "title": item["title"], "due_at": item.get("due_at", "")} for item in assignments]}, args.json)
        return _emit({"ok": True, "assignments": assignments}, args.json)
    if args.command == "recordings":
        if live_mode:
            try:
                if args.action == "list":
                    return _emit({"ok": True, "recordings": provider.recordings(args.course)}, args.json)
                seconds = args.seconds if args.seconds is not None else (30.0 if args.action == "keepalive" and not args.until_end else None)
                playback = provider.play_recording(args.course, args.title or args.item_id, until_end=args.until_end, seconds=seconds)
                return _emit({"ok": True, "playback": playback}, args.json)
            except LiveCommandError as exc:
                return _emit({"ok": False, "error": str(exc), "exit_code": 1}, args.json)
        if args.action in {"play", "keepalive"}:
            try:
                plan = provider.playback_plan(args.item_id, keepalive=args.action == "keepalive")
            except KeyError:
                return _emit({"ok": False, "error": "recording not found", "id": args.item_id, "exit_code": 1}, args.json)
            return _emit({"ok": True, "playback": plan}, args.json)
        return _emit({"ok": True, "recordings": to_dicts(provider.recordings())}, args.json)
    return _emit({"ok": False, "command": args.command, "error": "unknown command", "exit_code": 3}, args.json)


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
