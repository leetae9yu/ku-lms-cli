# KU LMS CLI

고려대학교 LMS를 CLI에서 안전하게 조회하고, 녹화 강의를 브라우저 기반으로 재생/유지할 수 있게 만든 도구입니다.

> 기본 원칙: **읽기/다운로드/녹화 재생만 지원**합니다. 과제 제출, 업로드, 글쓰기, 댓글, 수정, 삭제 같은 LMS 변경 작업은 의도적으로 막혀 있습니다.

## 설치

GitHub에서 원커맨드로 설치:

```bash
curl -fsSL https://raw.githubusercontent.com/leetae9yu/ku-lms-cli/main/scripts/install.sh | bash
```

이 명령은 `ku-lms` CLI를 설치하고, Codex 스킬을 `~/.codex/skills/ku-lms`에 자동 등록하며, 전역 env 파일 `~/.config/ku-lms-cli/KU_LMS.env`가 없으면 예시 템플릿을 생성합니다. live 사용 전에 그 파일에 본인 LMS/KUPID 계정을 입력하세요.

저장소를 클론한 경우에는 루트 디렉터리에서 같은 설치 스크립트를 실행하면 됩니다.

```bash
bash scripts/install.sh
```

개발용 editable install 대안:

```bash
python -m pip install -e .
```

설치 후 CLI 엔트리포인트:

```bash
ku-lms --help
```

개발 체크아웃에서 바로 실행하려면:

```bash
PYTHONPATH=src python -m ku_lms_cli.cli --help
```

## 개인 계정 설정

각 사용자는 자기 고려대 LMS/KUPID 계정을 로컬 env 파일에 넣어야 합니다. 실제 값은 공유/커밋하지 마세요. 원커맨드 설치를 사용했다면 전역 env 템플릿이 이미 만들어져 있습니다.

```bash
$EDITOR ~/.config/ku-lms-cli/KU_LMS.env
```

저장소 안에서만 쓸 로컬 env 파일을 만들 수도 있습니다.

```bash
cp KU_LMS.env.example KU_LMS.env
$EDITOR KU_LMS.env
```

형식:

```env
KU_LMS_ID=your-kupid-id
KU_LMS_PWD=your-kupid-password
```

주의:

- 실제 `KU_LMS.env`는 `.gitignore`에 포함되어 있어 커밋되지 않습니다.
- 계정/비밀번호, 쿠키, 토큰, SSO/LTI 파라미터, raw URL은 출력하거나 저장하지 않는 것을 목표로 합니다.
- `KU_LMS.env.example`에는 placeholder만 넣어야 합니다.

전역 기본 env 파일도 지원하므로 저장소 밖에서 `--env-file` 없이 실행할 수 있습니다.

```bash
mkdir -p ~/.config/ku-lms-cli
cp KU_LMS.env.example ~/.config/ku-lms-cli/KU_LMS.env
$EDITOR ~/.config/ku-lms-cli/KU_LMS.env
ku-lms --json --live courses
```

`--env-file`을 생략했을 때 탐색 순서:

1. `KU_LMS_ENV_FILE` 환경변수
2. 현재 작업 디렉터리의 `./KU_LMS.env`
3. `~/.config/ku-lms-cli/KU_LMS.env`


## Codex 스킬

이 저장소는 Codex용 스킬을 `codex/skills/ku-lms`에 함께 포함합니다. 원커맨드 설치는 이 스킬을 `~/.codex/skills/ku-lms`에 복사해서, Codex가 “공학수학 과제 확인”, “국제법 영상 목록” 같은 자연어 요청을 안전한 `ku-lms` 명령으로 실행할 수 있게 합니다. 스킬도 CLI와 같은 안전 경계를 따릅니다: 읽기/다운로드/녹화 재생만 허용하고, 과제 제출·업로드·글쓰기·수정·삭제는 금지합니다.

## 빠른 사용법

상태 확인:

```bash
ku-lms --json status
```

실제 LMS 과목 조회:

```bash
ku-lms --json --live courses
```

특정 과목 과제 조회:

```bash
ku-lms --json --live assignments list --course "국제법"
ku-lms --json --live assignments deadlines --course "국제법"
```

특정 과목 녹화 강의 목록:

```bash
ku-lms --json --live recordings list --course "국제법"
```

녹화 강의 재생:

```bash
ku-lms --json --live recordings play --course "국제법" --title "1차시" --until-end
```

공식 자막/스크립트가 제공되는 녹화 강의의 자막을 txt로 저장:

```bash
ku-lms --json --live recordings captions --course "국제법" --title "4주차 1차시"
```

`--title`을 생략하면 해당 과목의 녹화 강의 중 공식 한국어 자막이 발견되는 첫 영상을 찾아 저장합니다. `--output`을 생략하면 `downloads/p-q-yyyymmdd-hhmmdd.txt`에 저장합니다. 여기서 `p`는 주차, `q`는 차시입니다. `--output`을 지정하면 지정한 경로를 사용하되 확장자는 항상 `.txt`로 맞춥니다.

일정 시간 재생/유지:

```bash
ku-lms --json --live recordings keepalive --course "국제법" --title "1차시" --seconds 30
```

캘린더/upcoming/todo 조회:

```bash
ku-lms --json --live calendar upcoming
ku-lms --json --live calendar list --from 2026-05-31 --to 2026-06-30 --course "국제법"
ku-lms --json --live calendar todo
```

캘린더 피드 연동:

```bash
ku-lms --json --live calendar feed --copy
ku-lms --json --live calendar feed --open-google
```

`calendar feed`의 실제 `.ics` URL은 구독 토큰이므로 터미널에 출력하지 않습니다. `--copy`, `--open`, `--open-google`은 로컬 클립보드/브라우저로만 전달합니다.

브라우저 창을 직접 보고 싶으면:

```bash
ku-lms --json --live --headful courses
```

## 주요 명령

### fixture 모드

`--live`를 붙이지 않으면 샘플 fixture 데이터로 동작합니다.

```bash
ku-lms --json courses
ku-lms --json materials list
ku-lms --json materials download --id sample-material
ku-lms --json assignments list
ku-lms --json assignments deadlines
ku-lms --json assignments download --id sample-assignment-file
ku-lms --json recordings list
ku-lms --json recordings play --id sample-recording
ku-lms --json recordings keepalive --id sample-recording
```

### live 모드

`--live`를 붙이면 로컬 브라우저/CDP 세션으로 로그인해 실제 LMS를 조회합니다.

지원 범위:

- 과목 목록 조회
- 과목별 과제/마감일 조회
- 과목별 녹화 강의 목록 조회
- 녹화 강의 재생/keepalive/공식 자막 txt 추출
- 캘린더 upcoming/list/todo 조회
- 캘린더 `.ics` feed를 안전하게 클립보드/브라우저/Google Calendar로 전달

옵션:

```bash
--env-file KU_LMS.env  # env 파일 위치 지정
--headful              # 브라우저 창 표시
--timeout 120          # 브라우저/CDP 타임아웃 초 단위
```

## 안전 정책

허용:

- 로그인 세션 생성
- 읽기 전용 조회
- 자료 다운로드 scaffold
- 녹화 강의 재생/keepalive/공식 자막 txt 추출
- 캘린더 upcoming/list/todo 조회
- 캘린더 `.ics` feed를 안전하게 클립보드/브라우저/Google Calendar로 전달
- 녹화 재생으로 인한 시청기록/진도/출석 체크 반영

금지:

- 과제 제출 자동화
- 파일 업로드
- 글쓰기/댓글/수정/삭제
- 수강신청/등록 변경
- LMS 상태를 직접 변경하는 명령

금지 명령은 fail-closed 됩니다.

```bash
ku-lms --json submit
# -> not supported by design
```

## 출력 정책

live 출력에는 다음 정도만 포함되도록 제한합니다.

- 과목명
- 과제명, 마감일, 제출 상태 요약
- 녹화 강의 모듈/제목
- 캘린더 일정 제목/날짜
- redacted 캘린더 feed URL shape
- 재생 상태 요약

출력하지 않아야 하는 것:

- 실제 계정/비밀번호
- 쿠키/세션/토큰
- Authorization 헤더
- OAuth/SAML/LTI 파라미터
- raw course id
- raw launch URL
- raw calendar `.ics` feed URL
- 이메일 등 민감 식별자

## Chrome / 브라우저 설정

live 모드는 Chrome 또는 `headless_shell`을 찾습니다. 자동 탐색이 실패하면 다음 환경변수를 지정하세요.

```bash
export KU_LMS_CHROME=/path/to/chrome-or-headless_shell
```

## 개발/검증

```bash
pytest -q
python scripts/safety_scan.py
python -m compileall -q src tests scripts
python -m build --sdist --wheel
```

`submit` 같은 금지 명령은 반드시 실패해야 합니다.

```bash
PYTHONPATH=src python -m ku_lms_cli.cli --json submit
```

## 제한 사항

- KU LMS/SSO/녹화 플레이어 UI가 바뀌면 live 모드 selector/CDP 로직을 업데이트해야 할 수 있습니다.
- 자료 다운로드와 과제 첨부 다운로드는 현재 안전한 scaffold/fixture 중심입니다.
- GitHub Actions workflow는 현재 저장소에 포함하지 않았습니다. 초기 push 당시 사용한 GitHub OAuth token에 `workflow` scope가 없었기 때문입니다.

## 관련 문서

- [English README](README.md)
- [Live mode details](docs/live.md)
- [Discovery details](docs/discovery.md)
