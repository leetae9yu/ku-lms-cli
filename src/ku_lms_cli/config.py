"""Configuration loading for KU LMS CLI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .redaction import REDACTION

DEFAULT_ENV_PATH = Path("KU_LMS.env")
REQUIRED_KEYS = ("KU_LMS_ID", "KU_LMS_PWD")


@dataclass(frozen=True)
class KuLmsConfig:
    user_id: str
    password: str
    env_path: Path = DEFAULT_ENV_PATH

    def redacted(self) -> dict[str, str]:
        return {
            "KU_LMS_ID": REDACTION if self.user_id else "",
            "KU_LMS_PWD": REDACTION if self.password else "",
            "env_path": str(self.env_path),
        }


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def load_config(env_path: str | Path = DEFAULT_ENV_PATH, env: Mapping[str, str] | None = None) -> KuLmsConfig:
    path = Path(env_path)
    file_values = parse_env_file(path)
    merged = {**file_values, **(dict(env) if env else {})}
    missing = [key for key in REQUIRED_KEYS if not merged.get(key)]
    if missing:
        raise ValueError(f"Missing required KU LMS environment keys: {', '.join(missing)}")
    return KuLmsConfig(user_id=merged["KU_LMS_ID"], password=merged["KU_LMS_PWD"], env_path=path)
