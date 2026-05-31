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
        raise AssertionError(path_or_url)

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
