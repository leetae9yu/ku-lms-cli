"""Caption helpers shared by CLI and live LMS extraction."""
from __future__ import annotations

from typing import Any


def is_korean_caption_track(track: dict[str, Any]) -> bool:
    """Return whether a caption track is Korean by language code or LMS label."""
    language = str(track.get("language") or "").strip().casefold()
    label = str(track.get("label") or "").strip().casefold()
    normalized_language = language.replace("_", "-")
    return (
        normalized_language in {"ko", "kor", "kr", "ko-kr"}
        or normalized_language.startswith("ko-")
        or any(token in f"{language} {label}" for token in ("korean", "한국", "한글", "국문"))
    )
