# Live KU LMS CLI mode

`--live` switches supported read-only commands from deterministic fixtures to a local Chrome DevTools Protocol browser session.

## Supported live commands

```bash
PYTHONPATH=src python -m ku_lms_cli.cli --json --live courses
PYTHONPATH=src python -m ku_lms_cli.cli --json --live assignments list --course "국제법"
PYTHONPATH=src python -m ku_lms_cli.cli --json --live assignments deadlines --course "국제법"
PYTHONPATH=src python -m ku_lms_cli.cli --json --live recordings list --course "국제법"
PYTHONPATH=src python -m ku_lms_cli.cli --json --live recordings play --course "국제법" --title "1차시" --until-end
PYTHONPATH=src python -m ku_lms_cli.cli --json --live recordings keepalive --course "국제법" --title "1차시" --seconds 30
PYTHONPATH=src python -m ku_lms_cli.cli --json --live calendar upcoming
PYTHONPATH=src python -m ku_lms_cli.cli --json --live calendar list --from 2026-05-31 --to 2026-06-30 --course "국제법"
PYTHONPATH=src python -m ku_lms_cli.cli --json --live calendar todo
PYTHONPATH=src python -m ku_lms_cli.cli --json --live calendar feed --copy
PYTHONPATH=src python -m ku_lms_cli.cli --json --live calendar feed --open-google
```

## Safety boundaries

- Fixture mode remains the default; live mode must be explicitly requested with `--live`.
- Live output includes course names, assignment titles/deadlines, calendar event titles/dates, recording module/title, and playback status only.
- Live output must not include raw course IDs, raw launch URLs, raw calendar `.ics` feed URLs, cookies, headers, OAuth/SAML/LTI parameters, email addresses, credentials, or tokens.
- Assignment submission, upload, post/comment, edit/delete, enrollment, and other LMS-mutating actions remain forbidden and fail closed.
- Recording playback/keepalive may update LMS viewing progress, attendance, or watch history; this side effect is explicitly accepted for this build.
- Calendar feed URLs are secret-like iCalendar subscription tokens. `calendar feed --copy`, `--open`, and `--open-google` pass the URL only to the local clipboard/browser and report a redacted URL shape.
- Browser profiles are temporary local-only directories. A queue keeps its profile until completion, error, or stop; other live commands clean up on return.

## Browser/runtime notes

Live mode uses a small CDP abstraction in `ku_lms_cli.live` and a bounded `websockets` runtime dependency. If Chrome cannot be found automatically, set:

```bash
export KU_LMS_CHROME=/path/to/chrome-or-headless_shell
```

Use `--headful` for debugging the login flow locally. Do not persist raw screenshots, HAR files, cookies, headers, or local/session storage dumps.


## Shared login browser (POSIX)

```bash
ku-lms --json --live session start [--idle-minutes 180]
ku-lms --json session status
ku-lms --json session stop
```

`session start` detaches one process that launches a temporary-profile Chrome, logs in
once, and replies after login succeeds (or with the login error). While it runs, every
other `--live` command opens its own tab in that browser, checks the Canvas session,
and closes only its tab on exit; if the LMS session expired, the normal SSO login runs
again in that tab. Without a running session, commands launch their own browser as
before. The detached `recordings play --all` runner still owns a separate browser.

- Control is a Unix socket at `${XDG_STATE_HOME:-$HOME/.local/state}/ku-lms-cli/session/control.sock`
  (directory 0700, socket 0600) with an exclusive lock against duplicates. It answers
  only `endpoint`, `status`, and `stop`; no cookies, tokens, or profile data are written
  outside the temporary profile.
- The browser's DevTools port listens on 127.0.0.1 for the session's lifetime. Any
  local process of the same machine can reach it, so use sessions only on single-user
  machines and stop them when done.
- The session closes the browser and removes its profile on `stop`, SIGTERM, or after
  `--idle-minutes` without a command using it. Output uses the `browser` key with
  `running`, `started_at`, `last_used_at`, and `idle_timeout_seconds`.

## Durable recordings runner (POSIX)

`ku-lms --json --live recordings play --all --course "<course>"` detaches a local
Python process with its own temporary Chrome profile. Startup acknowledges a
listening control socket, before login/discovery/playback. It does not attach to an
existing browser, persist launch URLs, invoke an agent, or write LMS completion or
attendance directly. Ordinary playback can still update the LMS's own watch history.

- One login and one queue discovery per run, including API pagination when needed.
  API order is preserved. Handouts, unpublished/locked items and modules, future
  unlock dates, and expired availability windows are excluded. Availability changes
  after the snapshot stop playback on the resulting access error; no rediscovery or
  automatic login retry occurs between videos.
- One browser/page is reused; each recording navigates through its LTI wrapper.
  Native trusted `ended` advances exactly once. Pause never advances, synthetic ended
  is ignored, and a non-normal playback rate is an error. No seek or fast-forward is
  performed. Normal player resume prompts may be accepted.
- `--all` requires `--live` and `--course`; it cannot combine with `--title`, `--id`, or
  `--seconds`. Single-video play/keepalive stays foreground and bounded; `--until-end`
  now requires a native end instead of inferred progress. `--timeout` limits startup
  and navigation, not the total duration of a queue or an intentional pause.
- `recordings status` always prints one compact JSON object containing exactly
  `video`, `position_seconds`, `paused`, `remaining`, `error`. `remaining` includes the
  current unfinished video. Position is the last native media-event observation,
  not a fresh browser query. During startup, video/position are null and remaining
  is zero. With no runner, the same idle shape is returned. Top-level `status` still
  reports configuration; it is a different command.
- `recordings stop` cancels playback, closes only the owned browser/profile, and
  releases the runner. It is idempotent when absent and returns the five-field
  snapshot. Successful stop exits zero even if the snapshot preserves a prior error.
- Completion or error closes the browser but leaves the lightweight control process
  serving its final status and terminal event. Stop it before another `play --all`.
  An exclusive file lock prevents duplicate runners before opening a browser. Only
  the lock owner can remove a stale socket after an unclean exit.

The default directory is `${XDG_STATE_HOME:-$HOME/.local/state}/ku-lms-cli/recordings`,
mode 0700, containing `control.sock` (0600) and `runner.lock`. It is independent of
other browser sessions or per-agent state directories. All related CLI invocations
must use the same `XDG_STATE_HOME`. This is process durability across CLI/agent exits,
not automatic recovery across machine reboot, SIGKILL, or browser crash. A missing
runner's status is idle, not proof that its old queue completed.

### Terminal notifications

```bash
ku-lms recordings events
```

This blocks without polling, then prints a single NDJSON object with `event` plus
the five status fields. Allowed event values are `queue_complete`, `login_expired`,
and `playback_error`. Completion exits zero; error events exit one. A late subscriber
receives the retained terminal event. Explicit stop closes a pending subscription
without an event (exit zero); per-video transitions and pauses are not notifications.
There is no chat message, monitor tool, external messaging, or agent-wake integration.
An external supervisor can consume this command and choose what to notify.

For direct local consumers, send `status\n`, `stop\n`, or `events\n` to the Unix
socket. Status/stop return one JSON line. Events first acknowledges subscription with
`{"subscribed":true}`, then returns one terminal JSON line and EOF; the CLI consumes
that acknowledgement silently. Subscribe and await this acknowledgement before
triggering an action when coordinating a supervisor. Status/stop do not load config,
launch a browser, or discover LMS content. No event history is written to disk.

### Offline verification

The suite uses independent Chromium profiles and local generated media, never LMS
credentials or an existing browser. Set `KU_LMS_CHROME` to an available compatible
Chromium binary if necessary. The browser/CLI tests cover real native playback,
pause/resume, synthetic-event rejection, inactive sources, playback error, login
expiry, terminal replay, and stop. API fixtures cover queue discovery/filtering; a
separate CLI test verifies detachment and duplicate exclusion with a deliberately
missing browser binary (no LMS request).
