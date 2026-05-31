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
- Browser profiles are temporary local-only directories and are cleaned up after each live command.

## Browser/runtime notes

Live mode uses a small CDP abstraction in `ku_lms_cli.live` and a bounded `websockets` runtime dependency. If Chrome cannot be found automatically, set:

```bash
export KU_LMS_CHROME=/path/to/chrome-or-headless_shell
```

Use `--headful` for debugging the login flow locally. Do not persist raw screenshots, HAR files, cookies, headers, or local/session storage dumps.
