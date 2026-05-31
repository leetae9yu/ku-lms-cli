#!/usr/bin/env python3
"""Local safety scan for KU LMS CLI.

Checks that known local secret values are not copied into source/docs/planning outputs and
that forbidden LMS-mutating command names are not exposed in CLI help.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["src", "tests", "scripts", "docs", "codex/skills", ".omx/specs", ".omx/plans", "README.md", "README_ko.md", "pyproject.toml"]
SKIP_PARTS = {".git", ".pytest_cache", "__pycache__", ".cache", "private", "downloads"}
FORBIDDEN_COMMANDS = {"submit", "upload", "post", "comment", "delete", "edit", "write", "mark", "enroll"}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def iter_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCAN_DIRS:
        path = ROOT / entry
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and not (set(child.relative_to(ROOT).parts) & SKIP_PARTS):
                files.append(child)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default="KU_LMS.env")
    args = parser.parse_args(argv)
    env_values = [v for k, v in parse_env(ROOT / args.env_file).items() if k in {"KU_LMS_ID", "KU_LMS_PWD"} and len(v) >= 4]
    failures: list[str] = []
    for file in iter_files():
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for value in env_values:
            if value and value in text:
                failures.append(f"secret value leaked into {file.relative_to(ROOT)}")
    # Ensure forbidden commands fail closed and are not presented in help text.
    sys.path.insert(0, str(ROOT / "src"))
    from ku_lms_cli.cli import build_parser, FORBIDDEN_COMMANDS as CLI_FORBIDDEN  # noqa: WPS433

    if not FORBIDDEN_COMMANDS.issubset(CLI_FORBIDDEN):
        failures.append("CLI forbidden command set is missing required mutating verbs")
    help_text = build_parser().format_help()
    exposed = sorted(command for command in FORBIDDEN_COMMANDS if command in help_text)
    if exposed:
        failures.append(f"forbidden commands exposed in top-level help: {', '.join(exposed)}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: safety scan found no known secret leaks or exposed mutating commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
