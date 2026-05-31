# KU LMS CLI command reference

Use these command shapes from Codex. Always keep `--json --live` for real LMS data.

```bash
ku-lms --json status
ku-lms --json --live courses
ku-lms --json --live assignments list --course "<course>"
ku-lms --json --live assignments deadlines --course "<course>"
ku-lms --json --live recordings list --course "<course>"
ku-lms --json --live recordings play --course "<course>" --title "<title>" --until-end
ku-lms --json --live recordings keepalive --course "<course>" --title "<title>" --seconds 30
```

Forbidden commands must remain unsupported: `submit`, `upload`, `post`, `comment`, `delete`, `edit`, `write`, `mark`, `enroll`.
