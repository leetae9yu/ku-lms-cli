"""Local session state helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from .redaction import redact_data


@dataclass(frozen=True)
class SessionState:
    created_at: str
    env_path: str
    status: str = "credentials-present"
    note: str = "Live browser session is created by discovery/login implementation."

    @classmethod
    def new(cls, env_path: str) -> "SessionState":
        return cls(created_at=datetime.now(timezone.utc).isoformat(), env_path=env_path)


def write_session_marker(cache_dir: Path, state: SessionState) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "session-state.json"
    path.write_text(json.dumps(redact_data(asdict(state)), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path
