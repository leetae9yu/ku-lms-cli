#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${KU_LMS_CLI_REPO_URL:-https://github.com/leetae9yu/ku-lms-cli.git}"
REPO_BRANCH="${KU_LMS_CLI_BRANCH:-main}"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
CONFIG_HOME_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
ENV_DIR="$CONFIG_HOME_DIR/ku-lms-cli"
ENV_FILE="$ENV_DIR/KU_LMS.env"
SKILL_NAME="ku-lms"

say() { printf '[ku-lms-cli] %s\n' "$*"; }
fail() { printf '[ku-lms-cli] ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

find_python() {
  if [ -n "${PYTHON_BIN:-}" ]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "PYTHON_BIN not found: $PYTHON_BIN"
    printf '%s\n' "$PYTHON_BIN"
    return
  fi
  if have python3; then printf '%s\n' python3; return; fi
  if have python; then printf '%s\n' python; return; fi
  fail "Python 3.9+ is required"
}

is_repo_root() {
  [ -f "$1/pyproject.toml" ] && [ -d "$1/src/ku_lms_cli" ] && [ -f "$1/codex/skills/ku-lms/SKILL.md" ]
}

resolve_local_root() {
  local source_path="${BASH_SOURCE[0]:-$0}"
  if [ -n "$source_path" ] && [ -f "$source_path" ]; then
    local script_dir
    script_dir="$(cd "$(dirname "$source_path")" && pwd)"
    local candidate
    candidate="$(cd "$script_dir/.." && pwd)"
    if is_repo_root "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi
  if is_repo_root "$PWD"; then
    printf '%s\n' "$PWD"
    return 0
  fi
  return 1
}

fetch_repo() {
  local dest="$1"
  if have git; then
    git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$dest" >/dev/null
    return
  fi
  if have curl && have tar; then
    local archive="$dest.tar.gz"
    curl -fsSL "https://github.com/leetae9yu/ku-lms-cli/archive/refs/heads/$REPO_BRANCH.tar.gz" -o "$archive"
    mkdir -p "$dest"
    tar -xzf "$archive" --strip-components=1 -C "$dest"
    rm -f "$archive"
    return
  fi
  fail "Need git, or curl+tar, to install from GitHub"
}

python_bin="$(find_python)"
"$python_bin" - <<'PY' || fail "Python 3.9+ is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY

tmp_dir=""
cleanup() {
  if [ -n "$tmp_dir" ] && [ -d "$tmp_dir" ]; then
    rm -rf "$tmp_dir"
  fi
}
trap cleanup EXIT

repo_root=""
if repo_root="$(resolve_local_root 2>/dev/null)"; then
  say "using local checkout: $repo_root"
else
  tmp_dir="$(mktemp -d)"
  repo_root="$tmp_dir/ku-lms-cli"
  say "fetching $REPO_URL ($REPO_BRANCH)"
  fetch_repo "$repo_root"
fi

is_repo_root "$repo_root" || fail "source tree is missing required files"

pip_scope=(--user)
if [ "${KU_LMS_CLI_NO_USER_INSTALL:-}" = "1" ]; then
  pip_scope=()
elif ! "$python_bin" - <<'PY'
import site
raise SystemExit(0 if site.ENABLE_USER_SITE else 1)
PY
then
  pip_scope=()
fi

install_target=("$repo_root")
if [ "${KU_LMS_INSTALL_EDITABLE:-}" = "1" ]; then
  install_target=(-e "$repo_root")
fi

say "installing CLI with $python_bin -m pip"
"$python_bin" -m pip install "${pip_scope[@]}" "${install_target[@]}"

skill_src="$repo_root/codex/skills/$SKILL_NAME"
skill_dest="$CODEX_HOME_DIR/skills/$SKILL_NAME"
[ -f "$skill_src/SKILL.md" ] || fail "bundled Codex skill not found: $skill_src"
mkdir -p "$(dirname "$skill_dest")"
rm -rf "$skill_dest.tmp"
cp -R "$skill_src" "$skill_dest.tmp"
rm -rf "$skill_dest"
mv "$skill_dest.tmp" "$skill_dest"
say "registered Codex skill: $skill_dest"

mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR" 2>/dev/null || true
if [ ! -f "$ENV_FILE" ]; then
  cp "$repo_root/KU_LMS.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  say "created env template: $ENV_FILE"
  say "edit it with your own KU_LMS_ID and KU_LMS_PWD before live use"
else
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  say "kept existing env file: $ENV_FILE"
fi

scripts_dir="$($python_bin - <<'PY'
import os, site, sysconfig
if site.ENABLE_USER_SITE:
    print(sysconfig.get_path('scripts', scheme='posix_user'))
else:
    print(sysconfig.get_path('scripts'))
PY
)"
cli_path="$(command -v ku-lms || true)"
if [ -z "$cli_path" ] && [ -x "$scripts_dir/ku-lms" ]; then
  cli_path="$scripts_dir/ku-lms"
fi
[ -n "$cli_path" ] || fail "ku-lms was installed, but is not on PATH. Add $scripts_dir to PATH."

say "smoke testing CLI"
"$cli_path" --json status >/dev/null
say "done. Try: ku-lms --json status"
say "if ku-lms is not found in a new shell, add this to PATH: $scripts_dir"
