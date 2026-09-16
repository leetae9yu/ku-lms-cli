import pytest
from datetime import datetime

from ku_lms_cli.config import KuLmsConfig
from ku_lms_cli.live import LiveCommandError, LiveOptions
from ku_lms_cli.workload import LiveWorkloadProvider, parse_workload_request


class WorkloadSession:
    def __init__(self):
        self.enter_count = 0
        self.login_count = 0

    async def __aenter__(self):
        self.enter_count += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def login(self):
        self.login_count += 1

    async def fetch_json(self, path_or_url):
        if path_or_url.startswith("/api/v1/courses?"):
            return [
                {"id": 101, "name": "262R (서울-학부)국제법(영강)(INTERNATIONAL LAW)-00분반"},
                {"id": 202, "name": "262R (서울-학부)운영체제(OPERATING SYSTEMS)-02분반"},
            ]
        if path_or_url.startswith("/api/v1/courses/101/assignments"):
            return [
                {
                    "name": "완료 과제",
                    "due_at": "2026-09-09T14:59:00Z",
                    "submission": {"workflow_state": "graded", "submitted_at": "2026-09-09T12:00:00Z"},
                },
                {
                    "name": "밀린 과제",
                    "due_at": "2026-09-12T14:59:00Z",
                    "submission": {"workflow_state": "unsubmitted", "missing": True},
                },
                {
                    "name": "오늘 과제",
                    "due_at": "2026-09-13T03:00:00Z",
                    "submission": {"workflow_state": "unsubmitted"},
                },
                {
                    "name": " 다가오는 과제",
                    "due_at": "2026-09-15T14:59:00Z",
                    "submission": {"workflow_state": "unsubmitted"},
                },
                {
                    "name": "범위 밖 과제",
                    "due_at": "2026-09-21T00:00:00Z",
                    "submission": {"workflow_state": "unsubmitted"},
                },
            ]
        if path_or_url.startswith("/api/v1/courses/202/assignments"):
            return []
        if path_or_url.startswith("/api/v1/planner/items?"):
            return [
                {
                    "context_name": "262R (서울-학부)국제법(영강)(INTERNATIONAL LAW)-00분반",
                    "plannable_date": "2026-09-15T14:59:00Z",
                    "plannable_type": "assignment",
                    "plannable": {"title": " 다가오는 과제"},
                    "submissions": {"submitted": False},
                },
                {
                    "context_name": "262R (서울-학부)운영체제(OPERATING SYSTEMS)-02분반",
                    "plannable_date": "2026-09-16T14:59:00Z",
                    "plannable_type": "quiz",
                    "plannable": {"title": "보충 퀴즈"},
                    "submissions": {"submitted": False},
                },
            ]
        if path_or_url.startswith("/api/v1/users/self/todo?"):
            return [
                {
                    "type": "submitting",
                    "context_name": "262R (서울-학부)운영체제(OPERATING SYSTEMS)-02분반",
                    "assignment": {"name": "보충 퀴즈", "due_at": "2026-09-16T14:59:00Z"},
                }
            ]
        raise AssertionError(path_or_url)


def test_workload_uses_one_login_and_returns_compact_kst_buckets():
    # Given
    fake = WorkloadSession()
    provider = LiveWorkloadProvider(
        KuLmsConfig(user_id="student-id", password="secret-pwd"),
        LiveOptions(),
        session_factory=lambda: fake,
    )
    request = parse_workload_request("2026-09-13", 7, "Asia/Seoul")

    # When
    result = provider.workload(request)

    # Then
    assert fake.enter_count == 1
    assert fake.login_count == 1
    assert result == {
        "as_of": "2026-09-13",
        "timezone": "Asia/Seoul",
        "overdue": [
            {"course": "국제법", "title": "밀린 과제", "due": "2026-09-12 23:59"},
        ],
        "due_today": [
            {"course": "국제법", "title": "오늘 과제", "due": "2026-09-13 12:00"},
        ],
        "completed_before_cutoff": 1,
        "upcoming": [
            {"course": "국제법", "title": "다가오는 과제", "due": "2026-09-15 23:59"},
            {"course": "운영체제", "title": "보충 퀴즈", "due": "2026-09-16 23:59"},
        ],
    }


@pytest.mark.parametrize(
    ("as_of", "lookahead", "timezone"),
    [
        ("09-13-2026", 7, "Asia/Seoul"),
        ("2026-09-13", -1, "Asia/Seoul"),
        ("2026-09-13", 7, "Not/AZone"),
    ],
)
def test_workload_request_rejects_invalid_boundaries(as_of, lookahead, timezone):
    # Given / When / Then
    with pytest.raises(LiveCommandError):
        parse_workload_request(as_of, lookahead, timezone)


def test_workload_request_uses_timezone_today_when_date_is_omitted(monkeypatch):
    # Given
    import ku_lms_cli.workload as workload_module

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 14, 0, 30, tzinfo=tz)

    monkeypatch.setattr(workload_module, "datetime", FixedDatetime)

    # When
    request = parse_workload_request("", 7, "Asia/Seoul")

    # Then
    assert request.as_of.isoformat() == "2026-09-14"
