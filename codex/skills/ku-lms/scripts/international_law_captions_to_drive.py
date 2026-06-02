#!/usr/bin/env python3
"""Download KU LMS Korean captions and upload them to a Google Drive folder.

Local helper for the ku-lms Codex skill. It intentionally prints only filenames
and redacted ID tails; it never prints caption text, LMS URLs, Drive URLs, or OAuth
material.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_COURSE = "국제법"


@dataclass(frozen=True)
class DriveFolder:
    id: str
    name: str
    parent_tail: str = ""


def run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if Path("src/ku_lms_cli").exists():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = "src" + ((os.pathsep + existing) if existing else "")
    elif Path("/home/opc/projects/ku-lms-cli/src/ku_lms_cli").exists():
        existing = env.get("PYTHONPATH", "")
        source = "/home/opc/projects/ku-lms-cli/src"
        env["PYTHONPATH"] = source + ((os.pathsep + existing) if existing else "")
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, env=env)


def ku_lms_cmd() -> list[str]:
    raw = os.environ.get("KU_LMS_CMD", "").strip()
    if raw:
        return shlex.split(raw)
    repo_cli = Path("src/ku_lms_cli/cli.py")
    if repo_cli.exists():
        env_python = os.environ.get("PYTHON", sys.executable)
        return [env_python, "-m", "ku_lms_cli.cli"]
    installed_source_cli = Path("/home/opc/projects/ku-lms-cli/src/ku_lms_cli/cli.py")
    if installed_source_cli.exists():
        env_python = os.environ.get("PYTHON", sys.executable)
        return [env_python, "-m", "ku_lms_cli.cli"]
    return ["ku-lms"]


def run_json(cmd: list[str], *, timeout: int | None = None) -> dict[str, Any]:
    proc = run(cmd, timeout=timeout)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd[:4])}... {stderr or stdout}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command returned non-JSON: {' '.join(cmd[:4])}...") from exc


def drive_list(q: str) -> list[dict[str, Any]]:
    params = {
        "q": q,
        "pageSize": 100,
        "fields": "files(id,name,mimeType,parents),nextPageToken",
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    data = run_json(["gws", "drive", "files", "list", "--params", json.dumps(params, ensure_ascii=False)])
    return list(data.get("files") or [])


def resolve_drive_path(path: str) -> DriveFolder:
    parts = [part.strip() for part in path.split("/") if part.strip()]
    if not parts:
        raise RuntimeError("Drive destination path is empty")

    candidates: list[DriveFolder] = [DriveFolder(id="", name="")]
    for part in parts:
        next_candidates: list[DriveFolder] = []
        for parent in candidates:
            q = f"name = '{escape_drive_q(part)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
            if parent.id:
                q += f" and '{parent.id}' in parents"
            for folder in drive_list(q):
                folder_id = str(folder.get("id") or "")
                if folder_id:
                    next_candidates.append(
                        DriveFolder(id=folder_id, name=str(folder.get("name") or part), parent_tail=parent.id[-6:] if parent.id else "")
                    )
        candidates = next_candidates
        if not candidates:
            raise RuntimeError(f"Drive folder not found: {path}")

    if len(candidates) > 1:
        tails = ", ".join(
            f"...{folder.id[-6:]}" + (f" under ...{folder.parent_tail}" if folder.parent_tail else "")
            for folder in candidates
        )
        raise RuntimeError(f"Drive folder is ambiguous: {path} ({tails})")
    return candidates[0]


def resolve_caption_folder(parent_name: str, folder_name: str) -> DriveFolder:
    return resolve_drive_path(f"{parent_name}/{folder_name}")


def drive_destination(args: argparse.Namespace) -> str | None:
    if args.drive_path:
        return args.drive_path.strip()
    if args.drive_parent or args.drive_folder:
        if not args.drive_parent or not args.drive_folder:
            raise RuntimeError("Both --drive-parent and --drive-folder are required when either is provided")
        return f"{args.drive_parent.strip()}/{args.drive_folder.strip()}"
    return None


def escape_drive_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_recordings(course: str, timeout: int) -> list[dict[str, Any]]:
    data = run_json(ku_lms_cmd() + [
        "--json",
        "--live",
        "--timeout",
        str(timeout),
        "recordings",
        "list",
        "--course",
        course,
    ], timeout=timeout + 30)
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "recordings list failed"))
    return list(data.get("recordings") or [])


def recording_text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k) or "") for k in ("module", "title", "type"))


def select_recordings(recordings: list[dict[str, Any]], week: int, session: int | None) -> list[dict[str, Any]]:
    # Avoid substring false positives such as "4주차" matching "14주차".
    week_re = re.compile(rf"(?<!\d){week}\s*주차")
    session_re = re.compile(rf"(?<!\d){session}\s*차시") if session is not None else None
    selected = []
    for item in recordings:
        text = recording_text(item)
        if not week_re.search(text):
            continue
        if session_re is not None and not session_re.search(text):
            continue
        selected.append(item)
    return selected


def infer_session(item: dict[str, Any], explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    text = recording_text(item)
    match = re.search(r"(\d+)\s*차시", text)
    if not match:
        raise RuntimeError(f"cannot infer class session from recording title/module: {text}")
    return int(match.group(1))


def caption_title(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("module") or "").strip()


def timestamp() -> str:
    # Matches ku-lms CLI's requested p-q-yyyymmdd-hhmmdd.txt convention.
    return datetime.now().strftime("%Y%m%d-%H%M%d")


def download_caption(course: str, title: str, out_path: Path, timeout: int, *, headful: bool = False) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ku_lms_cmd() + [
        "--json",
        "--live",
        "--timeout",
        str(timeout),
    ]
    if headful:
        cmd.append("--headful")
    data = run_json(cmd + [
        "recordings",
        "captions",
        "--course",
        course,
        "--title",
        title,
        "--output",
        str(out_path),
    ], timeout=timeout + 60)
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or f"caption download failed: {title}"))
    if not out_path.exists() or out_path.stat().st_size <= 0:
        raise RuntimeError(f"caption file was not created or is empty: {out_path}")
    return dict(data.get("captions") or {})


def upload_to_drive(folder: DriveFolder, file_path: Path) -> dict[str, Any]:
    metadata = {"name": file_path.name, "parents": [folder.id], "mimeType": "text/plain"}
    params = {"fields": "id,name,mimeType,size", "supportsAllDrives": True}
    data = run_json([
        "gws",
        "drive",
        "files",
        "create",
        "--params",
        json.dumps(params, ensure_ascii=False),
        "--json",
        json.dumps(metadata, ensure_ascii=False),
        "--upload",
        str(file_path),
        "--upload-content-type",
        "text/plain; charset=utf-8",
    ])
    return {"name": data.get("name") or file_path.name, "id_tail": str(data.get("id") or "")[-6:], "size": data.get("size")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download 국제법 Korean captions and upload txt files to Google Drive when gws is available.")
    parser.add_argument("--week", type=int, required=True, help="p in p주차")
    parser.add_argument("--session", type=int, help="q in q차시. Omit to process every recording in the week.")
    parser.add_argument("--course", default=DEFAULT_COURSE)
    parser.add_argument("--drive-path", help='Google Drive folder path to upload into, e.g. "국제법/자막 모음". Required for upload when gws is available.')
    parser.add_argument("--drive-parent", help="Backward-compatible two-level Drive parent folder name; use with --drive-folder.")
    parser.add_argument("--drive-folder", help="Backward-compatible two-level Drive child folder name; use with --drive-parent.")
    parser.add_argument("--downloads-dir", default="downloads")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--headful", action="store_true", help="Use a visible Chrome window for players that fail in headless mode.")
    parser.add_argument("--dry-run", action="store_true", help="List selected recordings and Drive availability, but do not download/upload.")
    parser.add_argument("--check-drive", action="store_true", help="Only verify Drive availability/folder mapping; succeeds with upload skipped when gws is absent.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd0 = ku_lms_cmd()[0]
    if cmd0 == "ku-lms" and shutil.which("ku-lms") is None:
        raise SystemExit("ku-lms is not installed or not on PATH")

    gws_available = shutil.which("gws") is not None
    destination = drive_destination(args)
    folder = resolve_drive_path(destination) if gws_available and destination else None
    missing_destination_reason = "Drive destination not provided; ask the user where to save and pass --drive-path"
    if args.check_drive:
        print(json.dumps({
            "ok": True,
            "drive_available": gws_available,
            "drive_folder": destination or "",
            "needs_drive_destination": bool(gws_available and not destination),
            "folder_id_tail": folder.id[-6:] if folder else "",
            "upload_skipped": None if folder else ("gws is not installed or not on PATH" if not gws_available else missing_destination_reason),
        }, ensure_ascii=False))
        return 0
    if gws_available and not destination:
        raise SystemExit(missing_destination_reason)

    recordings = list_recordings(args.course, args.timeout)
    selected = select_recordings(recordings, args.week, args.session)
    if not selected:
        raise SystemExit(f"no recordings matched: {args.course} {args.week}주차" + (f" {args.session}차시" if args.session else ""))

    stamp = timestamp()
    planned = []
    for item in selected:
        q = infer_session(item, args.session)
        title = caption_title(item)
        if not title:
            raise RuntimeError(f"recording has no title/module: {item}")
        file_path = Path(args.downloads_dir) / f"{args.week}-{q}-{stamp}.txt"
        planned.append((item, q, title, file_path))

    if args.dry_run:
        print(json.dumps({
            "ok": True,
            "dry_run": True,
            "drive_available": gws_available,
            "drive_folder": destination or "",
            "needs_drive_destination": bool(gws_available and not destination),
            "folder_id_tail": folder.id[-6:] if folder else "",
            "upload_skipped": None if folder else ("gws is not installed or not on PATH" if not gws_available else missing_destination_reason),
            "selected": [{"week": args.week, "session": q, "title": title, "output": str(path)} for _, q, title, path in planned],
        }, ensure_ascii=False, indent=2))
        return 0

    results = []
    for _, q, title, file_path in planned:
        captions = download_caption(args.course, title, file_path, args.timeout, headful=args.headful)
        upload = upload_to_drive(folder, file_path) if folder else None
        results.append({
            "week": args.week,
            "session": q,
            "title": title,
            "local_file": str(file_path),
            "drive_file": upload,
            "upload_skipped": None if upload else ("gws is not installed or not on PATH" if not gws_available else missing_destination_reason),
            "caption_language": captions.get("caption_language"),
            "track_count": captions.get("track_count"),
        })

    print(json.dumps({
        "ok": True,
        "course": args.course,
        "saved_count": len(results),
        "drive_available": gws_available,
        "drive_folder": destination or "",
        "uploaded_count": sum(1 for item in results if item.get("drive_file")),
        "uploads": results,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
