---
name: ku-lms
description: "Use when Codex should use the installed `ku-lms` CLI for Korea University LMS tasks: listing courses, checking assignments/deadlines, summarizing remaining work, listing calendar events/todos, safely copying/opening the calendar feed, listing recorded lectures, or playing/keeping alive recorded lectures. Also use when a user asks natural-language LMS questions such as 공학수학 과제 확인, 국제법 영상 목록, 남은 과제 뭐 있어, or 녹화 강의 틀어줘. Do not use for assignment submission, uploads, comments, edits, deletes, enrollment changes, or any LMS-mutating action."
---

# KU LMS CLI

Use the installed `ku-lms` command as the execution surface for KU LMS read-only queries and recording playback. Keep the CLI as source of truth; this skill only maps natural-language requests to safe CLI usage.

## Safety contract

- Use live mode for real LMS data: `ku-lms --json --live ...`.
- Never print credentials, cookies, tokens, headers, raw launch URLs, raw course IDs, SSO/SAML/OAuth/LTI params, or emails.
- Do not automate assignment submission, uploads, comments, posts, edits, deletes, marks, enrollments, or other mutating LMS actions.
- If the user asks for a forbidden action, refuse briefly and offer read-only alternatives such as checking deadline/status or downloading/viewing materials.
- Recording `play`/`keepalive` can update viewing progress, attendance, or watch history. Only run playback commands when the user directly asks to play/keep alive/complete a recording; otherwise list recordings or describe capability.
- Prefer concise Korean summaries when the user writes Korean.

## Environment

`ku-lms` should already be installed globally. It finds credentials in this order when `--env-file` is omitted:

1. `KU_LMS_ENV_FILE`
2. `./KU_LMS.env`
3. `~/.config/ku-lms-cli/KU_LMS.env`

Quick checks:

```bash
ku-lms --json status
ku-lms --help
```

If config is missing, tell the user to create `~/.config/ku-lms-cli/KU_LMS.env` from `KU_LMS.env.example`; do not ask them to paste credentials into chat.

## Workflow

1. Identify intent: courses, assignments/deadlines, calendar upcoming/todo/feed, recordings list, playback/keepalive, or status.
2. Choose the narrowest CLI command.
3. Run with `--json --live` for real LMS data unless the user explicitly wants fixture/sample mode.
4. Read the JSON and summarize the relevant fields; do not dump raw JSON unless requested.
5. For assignment checks, highlight `remaining_candidate`, `unsubmitted`, `missing`, lock status, and due time.
6. For recording checks, show module/title only. For playback, report `video_mp4_partial_content_seen`, `observed_duration_seconds`, `completed`, and `completion_basis` if present.

## Command patterns

Courses:

```bash
ku-lms --json --live courses
```

Assignments for a course:

```bash
ku-lms --json --live assignments list --course "공학수학"
ku-lms --json --live assignments deadlines --course "공학수학"
```

Calendar:

```bash
ku-lms --json --live calendar upcoming
ku-lms --json --live calendar list --from 2026-05-31 --to 2026-06-30 --course "국제법"
ku-lms --json --live calendar todo
ku-lms --json --live calendar feed --copy
ku-lms --json --live calendar feed --open-google
```

Never print the raw calendar `.ics` feed URL. Use `--copy`, `--open`, or `--open-google` only when the user explicitly asks to connect/copy/open the feed.

Recordings for a course:

```bash
ku-lms --json --live recordings list --course "국제법"
```

Play a recording:

```bash
ku-lms --json --live recordings play --course "국제법" --title "1주차 4차시" --until-end
```

Keep a recording open for a bounded duration:

```bash
ku-lms --json --live recordings keepalive --course "국제법" --title "1주차 4차시" --seconds 30
```

Use `--timeout 120` when live browser operations need more time. Use `--headful` only for local debugging when a visible browser is useful.

## Natural-language mapping examples

- "과목 조회" → run courses and print course names.
- "공학수학 과제 확인" → run assignments list for `공학수학`; summarize remaining/missing/unsubmitted first.
- "국제법 과제 남은 거 있음?" → run assignments list for `국제법`; answer whether any `remaining_candidate` or missing/unsubmitted work exists.
- "캘린더 일정 보여줘" → run calendar upcoming/list and summarize title/date/course.
- "남은 할 일 보여줘" → run calendar todo.
- "구글 캘린더 연동" or "캘린더 피드 복사" → run calendar feed with `--copy` or `--open-google`; do not print the raw feed URL.
- "국제법 영상 목록" → run recordings list for `국제법`; print module/title list.
- "국제법 1주차 4차시 끝까지 재생" → run recordings play with `--until-end` and summarize playback evidence.

## Output style

For assignment summaries, prefer:

```text
남은 과제:
- <title>: <due time>, 상태 <submitted/unsubmitted/missing/locked>, 배점 <points>

완료/채점됨:
- ...
```

For no remaining work:

```text
현재 제출 가능 + 마감 전 + 미제출 과제는 없어 보임.
다만 과거 미제출/잠금/채점 상태는 아래와 같음: ...
```

## Troubleshooting

- `Missing required KU LMS environment keys`: create or fix `~/.config/ku-lms-cli/KU_LMS.env`.
- Browser/Chrome not found: set `KU_LMS_CHROME=/path/to/chrome-or-headless_shell`.
- Course query ambiguous: run `ku-lms --json --live courses`, then retry with a more exact course name substring.
- Live timeout: retry once with `--timeout 180`; if it still fails, report the redacted error.
