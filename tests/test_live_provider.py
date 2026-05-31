import pytest

from ku_lms_cli.config import KuLmsConfig
from ku_lms_cli.live import LiveCommandError, LiveLmsProvider, LiveOptions, _remaining_candidate


class FakeSession:
    def __init__(self):
        self.logged_in = False
        self.played_urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def login(self):
        self.logged_in = True

    async def fetch_json(self, path_or_url):
        if path_or_url.startswith("/api/v1/courses?"):
            return [
                {"id": 101, "name": "국제법", "workflow_state": "available", "default_view": "modules"},
                {"id": 202, "name": "운영체제", "workflow_state": "available", "default_view": "modules"},
                {"id": 303, "name": "국제법연습", "workflow_state": "available", "default_view": "modules"},
            ]
        if path_or_url.startswith("/api/v1/courses/101/assignments"):
            return [
                {
                    "id": 999,
                    "name": "기말 리포트",
                    "due_at": "2099-06-01T00:00:00Z",
                    "unlock_at": None,
                    "lock_at": None,
                    "points_possible": 10,
                    "published": True,
                    "locked_for_user": False,
                    "submission_types": ["online_upload"],
                    "submission": {"workflow_state": "unsubmitted", "submitted_at": None, "missing": False, "late": False},
                }
            ]
        if path_or_url.startswith("/api/v1/courses/101/modules"):
            return [
                {
                    "name": "1주차",
                    "items": [
                        {"title": "1주차 1차시", "type": "ExternalTool", "html_url": "https://lti.example.invalid/launch?user_id=12345678"},
                        {"title": "[강의 교안] 1주차", "type": "ExternalTool", "html_url": "https://files.example.invalid/raw"},
                    ],
                }
            ]
        if path_or_url.startswith("/api/v1/calendar_events?"):
            assert "context_codes%5B%5D=course_101" in path_or_url
            return [
                {
                    "id": 11111111,
                    "title": "퀴즈9차",
                    "start_at": "2026-06-01T09:00:00Z",
                    "end_at": "2026-06-01T10:00:00Z",
                    "type": "assignment",
                    "context_name": "국제법",
                    "html_url": "https://mylms.korea.ac.kr/courses/101/assignments/11111111",
                }
            ]
        if path_or_url.startswith("/api/v1/planner/items?"):
            return [
                {
                    "context_name": "국제법",
                    "plannable_date": "2026-06-01T09:00:00Z",
                    "plannable_type": "assignment",
                    "plannable": {"title": "퀴즈9차", "id": 11111111},
                    "submissions": {"submitted": False},
                }
            ]
        if path_or_url.startswith("/api/v1/users/self/todo?"):
            return [
                {
                    "type": "submitting",
                    "context_name": "국제법",
                    "assignment": {"name": "퀴즈9차", "due_at": "2026-06-01T09:00:00Z", "id": 11111111},
                }
            ]
        raise AssertionError(path_or_url)

    async def get_calendar_feed_url(self):
        return "https://mylms.korea.ac.kr/feeds/calendars/" + "user_abc123TOKEN.ics"

    async def play_url(self, url, *, until_end=False, seconds=None):
        self.played_urls.append(url)
        return {
            "video_mp4_partial_content_seen": True,
            "media_events": {"play": True, "pause": True, "duration_changed": True},
            "observed_duration_seconds": 1.0,
            "completed": until_end,
            "raw_url": url,
        }


def provider():
    config = KuLmsConfig(user_id="student-id", password="secret-pwd")
    fake = FakeSession()
    return LiveLmsProvider(config, LiveOptions(), session_factory=lambda: fake), fake


def test_live_courses_are_public_name_shape_only():
    lms, _ = provider()
    assert lms.courses() == [
        {"name": "국제법", "workflow_state": "available", "default_view": "modules"},
        {"name": "운영체제", "workflow_state": "available", "default_view": "modules"},
        {"name": "국제법연습", "workflow_state": "available", "default_view": "modules"},
    ]


def test_live_assignments_compute_remaining_without_ids_or_urls():
    lms, _ = provider()
    rows = lms.assignments("국제법")
    assert rows[0]["title"] == "기말 리포트"
    assert rows[0]["remaining_candidate"] is True
    assert "id" not in rows[0]
    assert "course_id" not in rows[0]
    assert "url" not in rows[0]


def test_live_recordings_filter_handouts_and_hide_launch_urls():
    lms, _ = provider()
    rows = lms.recordings("국제법")
    assert rows == [{"module": "1주차", "title": "1주차 1차시", "type": "ExternalTool", "playable": True}]
    assert "launch" not in str(rows)


def test_live_playback_redacts_raw_playback_data():
    lms, _ = provider()
    result = lms.play_recording("국제법", "1차시", until_end=True)
    assert result["title"] == "1주차 1차시"
    assert result["side_effects_accepted"] is True
    assert result["completed"] is True
    assert "raw_url" not in result
    assert "user_id" not in str(result)
    assert "12345678" not in str(result)


def test_live_calendar_events_hide_ids_and_urls():
    lms, _ = provider()
    rows = lms.calendar_events("2026-05-31", "2026-06-30", "국제법")
    assert rows == [
        {
            "title": "퀴즈9차",
            "start_at": "2026-06-01T09:00:00Z",
            "end_at": "2026-06-01T10:00:00Z",
            "type": "assignment",
            "context_name": "국제법",
            "all_day": False,
            "location_name": "",
        }
    ]
    assert "11111111" not in str(rows)
    assert "html_url" not in str(rows)


def test_live_calendar_upcoming_todo_and_feed_are_safe(monkeypatch):
    lms, _ = provider()
    upcoming = lms.calendar_upcoming()
    todo = lms.calendar_todo()
    assert upcoming[0]["title"] == "퀴즈9차"
    assert todo[0]["course"] == "국제법"

    import ku_lms_cli.live as live_mod

    copied = {}

    def fake_copy(text):
        copied["text"] = text
        return True, "fake"

    monkeypatch.setattr(live_mod, "_copy_to_clipboard", fake_copy)
    feed = lms.calendar_feed("copy")
    assert feed["copied"] is True
    assert feed["raw_url_printed"] is False
    assert feed["url_shape"] == "https://mylms.korea.ac.kr/feeds/calendars/[REDACTED-FEED-TOKEN].ics"
    assert copied["text"].endswith(".ics")
    assert "user_abc123TOKEN" not in str(feed)


def test_ambiguous_or_missing_course_error_is_safe():
    lms, _ = provider()
    with pytest.raises(LiveCommandError) as exc:
        lms.assignments("법")
    text = str(exc.value)
    assert "국제법" in text
    assert "101" not in text


def test_remaining_candidate_logic():
    assert _remaining_candidate("2099-01-01T00:00:00Z", False, "", "unsubmitted") is True
    assert _remaining_candidate("2000-01-01T00:00:00Z", False, "", "unsubmitted") is False
    assert _remaining_candidate("2099-01-01T00:00:00Z", True, "", "unsubmitted") is False
    assert _remaining_candidate("2099-01-01T00:00:00Z", False, "2099-01-01T00:00:00Z", "submitted") is False
