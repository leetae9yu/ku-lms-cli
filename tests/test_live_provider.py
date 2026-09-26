import pytest

from ku_lms_cli.config import KuLmsConfig
from ku_lms_cli.discovery import DEFAULT_ENTRY_URL
from ku_lms_cli.live import CANVAS_ORIGIN, CdpBrowserSession, LiveCommandError, LiveLmsProvider, LiveOptions, _CdpClient, _remaining_candidate, _run


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
                        {"title": "1주차 2차시", "type": "ExternalTool", "html_url": "https://lti.example.invalid/launch?user_id=87654321"},
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

    async def fetch_attendance(self, course_id):
        if course_id != 101:
            return {"attendance_items": {"attendance_items": []}, "summary": {"attendance_summaries": {}}}
        video = {"item_content_type": "commons", "use_attendance": True, "lecture_period_status": "open"}
        return {
            "attendance_items": {"attendance_items": [
                {**video, "item_id": 1, "week_position": 4, "lesson_position": 2, "title": "4-2", "due_at": "2026-09-28T14:59:59Z"},
                {**video, "item_id": 2, "week_position": 4, "lesson_position": 1, "title": "4-1", "due_at": "2026-09-28T14:59:59Z"},
                {**video, "item_id": 3, "week_position": 1, "lesson_position": 1, "title": "1-1", "due_at": "2026-09-06T14:59:59Z"},
                {**video, "item_id": 4, "week_position": 2, "lesson_position": 1, "title": "2-1", "due_at": "2026-09-13T14:59:59Z"},
                {**video, "item_id": 5, "week_position": 5, "lesson_position": 1, "title": "5-1",
                 "lecture_period_status": "not_open", "due_at": "2026-10-04T14:59:59Z"},
                {**video, "item_id": 6, "week_position": None, "lesson_position": None, "title": "보충", "use_attendance": False},
                {"item_id": 7, "item_content_type": "smart_attendance", "use_attendance": True, "title": "09/09", "week_position": 2},
            ]},
            "summary": {"attendance_summaries": {
                "1": {"attendance_status": "none"},
                "3": {"attendance_status": "late"},
                "4": {"attendance_status": "attendance"},
                "7": {"attendance_status": "absent"},
            }},
        }

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


def test_current_entry_url_keeps_authenticated_canvas_api_origin():
    assert CANVAS_ORIGIN == "https://mylms.korea.ac.kr"
    assert DEFAULT_ENTRY_URL == "https://canvas.korea.ac.kr/accounts/1/external_tools/9?launch_type=global_navigation"


def test_new_portal_credentials_use_visible_fields_and_page_login_handler():
    class CapturingSession(CdpBrowserSession):
        def __init__(self):
            super().__init__(KuLmsConfig(user_id="student-id", password="secret-pwd"), LiveOptions())
            self.expression = ""

        async def evaluate(self, expression, *, timeout=None):
            self.expression = expression
            return "submitted"

    session = CapturingSession()
    _run(session._submit_credentials())

    assert "#ipt_id" in session.expression
    assert "#ipt_password" in session.expression
    assert "document.querySelector('#ipt_password') ||" in session.expression
    assert "typeof window.doLogin" in session.expression


def test_login_success_query_is_not_treated_as_a_login_page():
    class ReadySession(CdpBrowserSession):
        def __init__(self):
            super().__init__(
                KuLmsConfig(user_id="student-id", password="secret-pwd"),
                LiveOptions(timeout_seconds=0.01),
            )

        async def goto(self, url):
            return None

        async def evaluate(self, expression, *, timeout=None):
            return {
                "href": "https://mylms.korea.ac.kr/?login_success=1",
                "hasPassword": False,
            }

        async def _canvas_session_ready(self):
            return True

        async def _click_login_candidate(self):
            return None

    _run(ReadySession().login())


def test_expired_password_prompt_uses_sso_change_later(monkeypatch):
    class ExpiredPasswordSession(CdpBrowserSession):
        def __init__(self):
            super().__init__(
                KuLmsConfig(user_id="student-id", password="secret-pwd"),
                LiveOptions(timeout_seconds=5),
            )
            self.client = _CdpClient.__new__(_CdpClient)
            self.client.last_dialog_message = ""
            self._client = self.client
            self.actions = []

        async def goto(self, url):
            return None

        async def evaluate(self, expression, *, timeout=None):
            if "changePwdLater" in expression:
                self.actions.append("change_later")
                return "postponed"
            if "change_later" in self.actions:
                return {"href": "https://mylms.korea.ac.kr/", "hasPassword": False}
            return {"href": "https://sso.korea.ac.kr/Login.eps", "hasPassword": True}

        async def _submit_credentials(self):
            self.actions.append("submit")
            self.client.last_dialog_message = (
                "You have not changed your password in a long time. Please log in after resetting your password."
            )

        async def _canvas_session_ready(self):
            return True

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("ku_lms_cli.live.asyncio.sleep", no_wait)
    session = ExpiredPasswordSession()
    _run(session.login())

    assert session.actions == ["submit", "change_later"]


def test_unwatched_recordings_are_unattended_tracked_videos_by_due_date():
    lms, fake = provider()
    rows = lms.unwatched_recordings()

    assert fake.logged_in
    assert [(row["title"], row["attendance_status"], row["available"]) for row in rows] == [
        ("4-1", "none", True),
        ("4-2", "none", True),
        ("5-1", "none", False),
    ]
    assert rows[0] == {
        "course": "국제법", "week": 4, "lesson": 1, "title": "4-1",
        "attendance_status": "none", "available": True, "due_at": "2026-09-28T14:59:59Z",
    }


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
    assert rows == [
        {"module": "1주차", "title": "1주차 1차시", "type": "ExternalTool", "playable": True},
        {"module": "1주차", "title": "1주차 2차시", "type": "ExternalTool", "playable": True},
    ]
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


def test_live_recording_captions_selects_first_when_title_omitted():
    lms, fake = provider()

    async def extract_captions(url):
        assert url == "https://lti.example.invalid/launch?user_id=12345678"
        return [
            {
                "label": "한국어",
                "language": "ko",
                "format": "vtt",
                "source": "track_element",
                "text": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n안녕하세요.\n",
            }
        ]

    fake.extract_captions = extract_captions
    result = lms.recording_captions("국제법")
    assert result["title"] == "1주차 1차시"
    assert result["track_count"] == 1
    assert result["tracks"][0]["language"] == "ko"
    assert "안녕하세요" in result["tracks"][0]["text"]
    assert "lti.example" not in str(result)
    assert "12345678" not in str(result)


def test_live_recording_captions_skips_non_korean_when_title_omitted():
    lms, fake = provider()
    calls = []

    async def extract_captions(url):
        calls.append(url)
        if len(calls) == 1:
            return [
                {
                    "label": "English",
                    "language": "en",
                    "format": "vtt",
                    "source": "track_element",
                    "text": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello.\n",
                }
            ]
        return [
            {
                "label": "한국어",
                "language": "ko",
                "format": "vtt",
                "source": "track_element",
                "text": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n안녕하세요.\n",
            }
        ]

    fake.extract_captions = extract_captions
    result = lms.recording_captions("국제법")
    assert result["title"] == "1주차 2차시"
    assert len(calls) == 2
    assert result["track_count"] == 1
    assert result["tracks"][0]["language"] == "ko"


def test_live_recording_captions_reports_missing_official_captions():
    lms, fake = provider()

    async def extract_captions(url):
        return []

    fake.extract_captions = extract_captions
    with pytest.raises(LiveCommandError, match="no official Korean captions"):
        lms.recording_captions("국제법", "1차시")


def test_caption_normalization_rejects_player_javascript_and_error_html():
    import ku_lms_cli.live as live_mod

    assert live_mod._normalize_caption_item({"text": "var captionScriptList = null;\nfunction init() {}", "source": "player_caption_resource"}) is None
    assert live_mod._normalize_caption_item({"text": "<html><body>잘못된 요청입니다.</body></html>", "source": "network_caption"}) is None


def test_caption_normalization_accepts_vtt_as_plain_text():
    import ku_lms_cli.live as live_mod

    item = live_mod._normalize_caption_item({"text": "WEBVTT\n\ncue-1\n00:00:00.000 --> 00:00:02.000\n안녕하세요.\n", "format": "vtt"})
    assert item is not None
    assert item["text"] == "안녕하세요."


def test_caption_normalization_rejects_common_javascript_assignments():
    import ku_lms_cli.live as live_mod

    assert live_mod._normalize_caption_item({"text": "window.captionScriptList = [];"}) is None
    assert live_mod._normalize_caption_item({"text": "const captionScriptList = [];"}) is None


def test_caption_normalization_rejects_redaction_policy_sensitive_ids():
    import ku_lms_cli.live as live_mod

    assert live_mod._normalize_caption_item({"text": "학생 번호 12345678"}) is None
