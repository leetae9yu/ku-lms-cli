"""Configuration loading for KU LMS CLI."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .redaction import REDACTION

DEFAULT_ENV_PATH = Path("KU_LMS.env")
ENV_FILE_VAR = "KU_LMS_ENV_FILE"
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


def user_config_env_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the per-user global env-file location."""
    values = os.environ if env is None else env
    config_home = values.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "ku-lms-cli" / "KU_LMS.env"


def candidate_env_paths(env_path: str | Path = DEFAULT_ENV_PATH, env: Mapping[str, str] | None = None) -> list[Path]:
    """Return env-file candidates in precedence order.

    Explicit ``--env-file`` values are respected exactly. The default lookup checks an
    optional ``KU_LMS_ENV_FILE`` override, then the current directory, then the per-user
    config file at ``~/.config/ku-lms-cli/KU_LMS.env``.
    """
    values = os.environ if env is None else env
    requested = Path(env_path).expanduser()
    if requested != DEFAULT_ENV_PATH:
        return [requested]
    paths: list[Path] = []
    override = values.get(ENV_FILE_VAR)
    if override:
        paths.append(Path(override).expanduser())
    paths.append(DEFAULT_ENV_PATH)
    paths.append(user_config_env_path(values))
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


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


def _select_env_path(candidates: list[Path]) -> Path:
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def load_config(env_path: str | Path = DEFAULT_ENV_PATH, env: Mapping[str, str] | None = None) -> KuLmsConfig:
    runtime_env = {**os.environ, **(dict(env) if env else {})}
    candidates = candidate_env_paths(env_path, runtime_env)
    path = _select_env_path(candidates)
    file_values = parse_env_file(path)
    merged = {**file_values, **{key: runtime_env[key] for key in REQUIRED_KEYS if runtime_env.get(key)}}
    missing = [key for key in REQUIRED_KEYS if not merged.get(key)]
    if missing:
        searched = ", ".join(str(candidate) for candidate in candidates)
        raise ValueError(f"Missing required KU LMS environment keys: {', '.join(missing)}; searched: {searched}")
    return KuLmsConfig(user_id=merged["KU_LMS_ID"], password=merged["KU_LMS_PWD"], env_path=path)
