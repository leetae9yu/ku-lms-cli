# KU LMS CLI

[한국어 README](README_ko.md)

Discovery-first, secret-safe CLI for Korea University LMS automation. Each user provides their own KU LMS/KUPID credentials locally via `KU_LMS.env`.


## Install

One-command install from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/leetae9yu/ku-lms-cli/main/scripts/install.sh | bash
```

This installs the `ku-lms` CLI, registers the bundled Codex skill at `~/.codex/skills/ku-lms`, and creates `~/.config/ku-lms-cli/KU_LMS.env` from the example if it does not already exist. Edit that env file with your own KU LMS/KUPID credentials before live use.

From a cloned repository, run the same installer locally:

```bash
bash scripts/install.sh
```

Development editable install alternative:

```bash
python -m pip install -e .
```

The CLI entrypoint is:

```bash
ku-lms --help
```

## Per-user credential setup

Each user must create their own local `KU_LMS.env` file. Do **not** share or commit it. If you used `scripts/install.sh`, the global template already exists here:

```bash
$EDITOR ~/.config/ku-lms-cli/KU_LMS.env
```

For a repository-local env file instead:

```bash
cp KU_LMS.env.example KU_LMS.env
$EDITOR KU_LMS.env
```

`KU_LMS.env` format:

```env
KU_LMS_ID=your-kupid-id
KU_LMS_PWD=your-kupid-password
```

The real `KU_LMS.env` file is gitignored. The CLI redacts these values from command output and the safety scan checks that known local credentials were not copied into tracked files.

Global default lookup also supports a per-user env file, so `--env-file` is not required outside the repository:

```bash
mkdir -p ~/.config/ku-lms-cli
cp KU_LMS.env.example ~/.config/ku-lms-cli/KU_LMS.env
$EDITOR ~/.config/ku-lms-cli/KU_LMS.env
ku-lms --json --live courses
```

Lookup order when `--env-file` is omitted:

1. `KU_LMS_ENV_FILE` environment variable, if set
2. `./KU_LMS.env` in the current working directory
3. `~/.config/ku-lms-cli/KU_LMS.env`


## Codex skill

The repository bundles a Codex skill in `codex/skills/ku-lms`. The one-command installer copies it to `~/.codex/skills/ku-lms` so Codex can translate natural-language LMS requests into safe `ku-lms` commands. The skill preserves the same safety boundary as the CLI: read-only queries, downloads, and recording playback only; no submissions or LMS-mutating actions.

## Safety model

- Reads each user's local credentials from `KU_LMS.env` (`KU_LMS_ID`, `KU_LMS_PWD`) but must never print or commit values.
- Assignment submission and all LMS-mutating commands are out of scope by design.
- Recording playback/keepalive may create progress, attendance, or viewing-history side effects; this is accepted for the requested v1 and must remain documented.
- Sessions, downloads, raw discovery data, traces, videos, screenshots, and private artifacts are local-only and gitignored.
- Discovery artifacts must be redacted before retention. Raw screenshots of LMS content are not allowed.

## Current status

The scaffold includes package metadata, CLI skeleton, config loading, redaction, local path policy, fixture-backed read-only commands, discovery artifacts, and an explicit `--live` mode for supported read-only LMS queries plus recording playback. DevTools/CDP live discovery has confirmed the authenticated Canvas main page, read-only Canvas API shapes, and browser/LTI recording playback; retained artifacts are redacted and local-only.

## Usage

```bash
ku-lms --json status
ku-lms --json --live courses

# Development checkout alternative:
PYTHONPATH=src python -m ku_lms_cli.cli --json status
PYTHONPATH=src python -m ku_lms_cli.cli login
PYTHONPATH=src python -m ku_lms_cli.cli discover
PYTHONPATH=src python -m ku_lms_cli.cli discover --devtools-observation discovery/redacted/devtools-auth-*/fixtures/devtools-auth-shapes.json
PYTHONPATH=src python -m ku_lms_cli.cli --json --live courses
PYTHONPATH=src python -m ku_lms_cli.cli --json --live assignments deadlines --course "국제법"
PYTHONPATH=src python -m ku_lms_cli.cli --json --live recordings list --course "국제법"
PYTHONPATH=src python -m ku_lms_cli.cli --json --live recordings play --course "국제법" --title "1차시" --until-end
```

Installed script after package installation:

```bash
ku-lms --json status
```

## Required v1 capabilities

- `status` / `login`
- `courses`
- `materials list` / `materials download`
- `assignments list` / `assignments deadlines` / attachment download
- `recordings list` / `recordings play` / `recordings keepalive`

## Forbidden by design

Commands such as `submit`, `upload`, `post`, `comment`, `delete`, `edit`, and other LMS-mutating actions must be absent or fail closed.

## Live mode

`--live` uses a temporary local Chrome/CDP session and Canvas read-only endpoints where available. Fixture mode remains the default. Live outputs are intentionally shape-limited: course names, assignment titles/deadlines, recording titles/modules, and playback status may be printed; raw IDs, launch URLs, cookies, headers, OAuth/SAML/LTI params, emails, credentials, and tokens must not be printed or persisted. See `docs/live.md`.

## DevTools discovery

`discover --devtools-observation <json>` consumes a **redacted, shape-only** DevTools/CDP observation and normalizes it into the standard route map, network inventory, selector map, feasibility matrix, and fixtures. The importer rejects raw private artifacts such as cookies, HAR data, headers, token dumps, local/session storage, and screenshots.

Observed so far:

- Portal SSO can reach the Canvas `마이페이지` through a browser session.
- `courses` and assignment deadline metadata are API-feasible through Canvas JSON endpoints.
- Materials are mixed: modules API is useful, while direct files/pages APIs may be course-dependent and need browser fallback.
- Recordings remain browser/LTI playback work; playback/keepalive is allowed, but submission/upload/mutation is not.

## Verification

```bash
pytest -q
python scripts/safety_scan.py
python -m compileall -q src tests scripts
PYTHONPATH=src python -m ku_lms_cli.cli --json submit
```

The `submit` smoke must fail closed with `not supported by design`.
