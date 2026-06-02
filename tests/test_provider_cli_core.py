import json
from pathlib import Path

from ku_lms_cli import cli as cli_module
from ku_lms_cli.cli import run
from ku_lms_cli.paths import PathPolicy
from ku_lms_cli.provider import FixtureProvider


def write_env(tmp_path):
    path = tmp_path / "KU_LMS.env"
    path.write_text("KU_LMS_ID=student-id\nKU_LMS_PWD=secret-pwd\n", encoding="utf-8")
    return path


def parse_json_output(capsys):
    return json.loads(capsys.readouterr().out)


def test_fixture_provider_lists_domain_objects():
    provider = FixtureProvider()
    assert provider.courses()[0].id == "sample-course"
    assert provider.materials()[0].id == "sample-material"
    assert provider.assignments()[0].attachments[0].id == "sample-assignment-file"
    assert provider.recordings()[0].id == "sample-recording"


def test_courses_cli_json(tmp_path, capsys):
    code = run(["--env-file", str(write_env(tmp_path)), "--json", "courses"])
    data = parse_json_output(capsys)
    assert code == 0
    assert data["courses"][0]["id"] == "sample-course"
    assert "secret-pwd" not in json.dumps(data)


def test_material_download_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = run(["--env-file", str(write_env(tmp_path)), "--json", "materials", "download", "--id", "sample-material"])
    data = parse_json_output(capsys)
    assert code == 0
    assert Path(data["downloaded"]).exists()


def test_assignments_deadlines_and_attachment_download(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    env = write_env(tmp_path)
    assert run(["--env-file", str(env), "--json", "assignments", "deadlines"]) == 0
    data = parse_json_output(capsys)
    assert data["deadlines"][0]["due_at"]
    assert run(["--env-file", str(env), "--json", "assignments", "download", "--id", "sample-assignment-file"]) == 0
    data = parse_json_output(capsys)
    assert Path(data["downloaded"]).exists()


def test_recording_playback_plan_documents_side_effects(tmp_path, capsys):
    code = run(["--env-file", str(write_env(tmp_path)), "--json", "recordings", "keepalive", "--id", "sample-recording"])
    data = parse_json_output(capsys)
    assert code == 0
    assert data["playback"]["side_effects_accepted"] is True
    assert data["playback"]["keepalive"] is True

class FakeLiveProvider:
    def __init__(self):
        self.options = None

    def courses(self):
        return [{"name": "국제법", "workflow_state": "available", "default_view": "modules"}]

    def assignments(self, course):
        assert course == "국제법"
        return [{"title": "기말 리포트", "due_at": "2099-06-01T00:00:00Z", "remaining_candidate": True}]

    def deadlines(self, course):
        assert course == "국제법"
        return [{"title": "기말 리포트", "due_at": "2099-06-01T00:00:00Z", "remaining_candidate": True}]

    def recordings(self, course):
        assert course == "국제법"
        return [{"module": "1주차", "title": "1주차 1차시", "type": "ExternalTool", "playable": True}]

    def calendar_events(self, start_date="", end_date="", course=""):
        assert start_date == "2026-05-31"
        assert end_date == "2026-06-30"
        assert course == "국제법"
        return [{"title": "퀴즈9차", "start_at": "2026-06-01T09:00:00Z", "type": "assignment", "context_name": "국제법"}]

    def calendar_upcoming(self, start_date="", end_date=""):
        assert start_date == ""
        assert end_date == ""
        return [{"title": "기말 리포트", "date": "2099-06-01T00:00:00Z", "type": "assignment", "course": "국제법"}]

    def calendar_todo(self):
        return [{"title": "기말 리포트", "due_at": "2099-06-01T00:00:00Z", "type": "submitting", "course": "국제법"}]

    def calendar_feed(self, delivery="inspect"):
        return {
            "delivery": delivery,
            "copied": delivery == "copy",
            "opened": delivery in {"open", "open_google"},
            "url_shape": "https://mylms.korea.ac.kr/feeds/calendars/[REDACTED-FEED-TOKEN].ics",
            "raw_url_printed": False,
        }

    def play_recording(self, course, title, *, until_end=False, seconds=None):
        assert course == "국제법"
        assert "1차시" in title
        return {
            "module": "1주차",
            "title": "1주차 1차시",
            "side_effects_accepted": True,
            "until_end": until_end,
            "keepalive_seconds": seconds,
            "video_mp4_partial_content_seen": True,
            "media_events": {"play": True, "pause": True, "duration_changed": True},
            "observed_duration_seconds": 1.0,
            "completed": until_end,
        }


def fake_live_factory(config, options):
    assert options.headless is True
    assert options.timeout_seconds == 60.0
    return FakeLiveProvider()


def test_live_courses_cli_prints_course_names_without_ids(tmp_path, capsys):
    code = run(["--env-file", str(write_env(tmp_path)), "--json", "--live", "courses"], live_provider_factory=fake_live_factory)
    data = parse_json_output(capsys)
    assert code == 0
    assert data["courses"][0]["name"] == "국제법"
    assert "course_id" not in json.dumps(data)
    assert "secret-pwd" not in json.dumps(data)


def test_live_assignments_and_deadlines_cli(tmp_path, capsys):
    env = write_env(tmp_path)
    assert run(["--env-file", str(env), "--json", "--live", "assignments", "list", "--course", "국제법"], live_provider_factory=fake_live_factory) == 0
    data = parse_json_output(capsys)
    assert data["assignments"][0]["remaining_candidate"] is True
    assert run(["--env-file", str(env), "--json", "--live", "assignments", "deadlines", "--course", "국제법"], live_provider_factory=fake_live_factory) == 0
    data = parse_json_output(capsys)
    assert data["deadlines"][0]["title"] == "기말 리포트"


def test_live_recording_list_and_play_cli(tmp_path, capsys):
    env = write_env(tmp_path)
    assert run(["--env-file", str(env), "--json", "--live", "recordings", "list", "--course", "국제법"], live_provider_factory=fake_live_factory) == 0
    data = parse_json_output(capsys)
    assert data["recordings"][0]["title"] == "1주차 1차시"
    assert run(["--env-file", str(env), "--json", "--live", "recordings", "play", "--course", "국제법", "--title", "1차시", "--until-end"], live_provider_factory=fake_live_factory) == 0
    data = parse_json_output(capsys)
    assert data["playback"]["completed"] is True
    assert data["playback"]["side_effects_accepted"] is True
    assert "url" not in json.dumps(data).lower()


def test_fixture_calendar_commands_are_available(tmp_path, capsys):
    env = write_env(tmp_path)
    assert run(["--env-file", str(env), "--json", "calendar", "upcoming"]) == 0
    data = parse_json_output(capsys)
    assert data["upcoming"][0]["title"] == "Sample Assignment"
    assert run(["--env-file", str(env), "--json", "calendar", "feed"]) == 0
    data = parse_json_output(capsys)
    assert data["feed"]["raw_url_printed"] is False
    assert "user_" not in json.dumps(data)


def test_live_calendar_cli_and_feed_do_not_print_raw_url(tmp_path, capsys):
    env = write_env(tmp_path)
    assert run(["--env-file", str(env), "--json", "--live", "calendar", "list", "--from", "2026-05-31", "--to", "2026-06-30", "--course", "국제법"], live_provider_factory=fake_live_factory) == 0
    data = parse_json_output(capsys)
    assert data["events"][0]["title"] == "퀴즈9차"
    assert run(["--env-file", str(env), "--json", "--live", "calendar", "feed", "--copy"], live_provider_factory=fake_live_factory) == 0
    data = parse_json_output(capsys)
    text = json.dumps(data)
    assert data["feed"]["copied"] is True
    assert data["feed"]["raw_url_printed"] is False
    assert "[REDACTED-FEED-TOKEN]" in text
    assert "user_" not in text


def test_fixture_recording_captions_cli_can_write_txt_output(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    env = write_env(tmp_path)
    code = run(["--env-file", str(env), "--json", "recordings", "captions", "--id", "sample-recording", "--output", "downloads/caption.vtt"])
    data = parse_json_output(capsys)
    assert code == 0
    assert data["captions"]["saved_to"].endswith("downloads/caption.txt")
    assert data["captions"]["text_format"] == "txt"
    assert Path(data["captions"]["saved_to"]).read_text(encoding="utf-8") == "Sample caption text.\n"
    assert "text" not in data["captions"]["tracks"][0]


def test_live_recording_captions_cli_uses_course_and_optional_title(tmp_path, monkeypatch, capsys):
    class CaptionLiveProvider(FakeLiveProvider):
        def recording_captions(self, course, title=""):
            assert course == "국제법"
            assert title == ""
            return {
                "module": "1주차",
                "title": "1주차 1차시",
                "track_count": 1,
                "tracks": [
                    {
                        "label": "한국어",
                        "language": "ko",
                        "format": "vtt",
                        "source": "track_element",
                        "char_count": 55,
                        "text": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n국제법 자막입니다.\n",
                    }
                ],
                "raw_urls_printed": False,
            }

    def factory(config, options):
        return CaptionLiveProvider()

    monkeypatch.chdir(tmp_path)
    env = write_env(tmp_path)
    code = run(["--env-file", str(env), "--json", "--live", "recordings", "captions", "--course", "국제법"], live_provider_factory=factory)
    data = parse_json_output(capsys)
    assert code == 0
    assert data["captions"]["title"] == "1주차 1차시"
    saved_files = list((tmp_path / "downloads").glob("1-1-*.txt"))
    assert len(saved_files) == 1
    assert data["captions"]["text_format"] == "txt"
    assert data["captions"]["caption_language"] == "ko"
    assert data["captions"]["track_count"] == 1
    assert saved_files[0].read_text(encoding="utf-8") == "국제법 자막입니다.\n"
    assert data["captions"]["raw_urls_printed"] is False
    assert "http" not in json.dumps(data).lower()


def test_recording_captions_cli_saves_only_korean_track_with_week_session_timestamp(tmp_path, monkeypatch, capsys):
    class FixedDatetime:
        @classmethod
        def now(cls):
            from datetime import datetime

            return datetime(2026, 6, 1, 4, 7, 59)

    class MultiCaptionProvider(FakeLiveProvider):
        def recording_captions(self, course, title=""):
            return {
                "module": "4주차",
                "title": "4주차 1차시 (자막 수정)",
                "track_count": 3,
                "tracks": [
                    {"label": "한글4-1", "language": "한국어", "format": "vtt", "source": "player_caption_api", "char_count": 40, "text": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n한국어 자막\n"},
                    {"label": "중문4-1", "language": "중국어", "format": "vtt", "source": "player_caption_api", "char_count": 40, "text": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n中文字幕\n"},
                    {"label": "영문4-1", "language": "영어", "format": "vtt", "source": "player_caption_api", "char_count": 40, "text": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nEnglish caption\n"},
                ],
                "raw_urls_printed": False,
            }

    def factory(config, options):
        return MultiCaptionProvider()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "datetime", FixedDatetime)
    env = write_env(tmp_path)
    code = run(["--env-file", str(env), "--json", "--live", "recordings", "captions", "--course", "국제법", "--title", "4주차 1차시"], live_provider_factory=factory)
    data = parse_json_output(capsys)
    assert code == 0
    saved_file = tmp_path / "downloads" / "4-1-20260601-040701.txt"
    assert saved_file.exists()
    assert data["captions"]["track_count"] == 1
    assert data["captions"]["tracks"][0]["label"] == "한글4-1"
    assert saved_file.read_text(encoding="utf-8") == "한국어 자막\n"


def test_korean_caption_track_accepts_kukmun_label():
    from ku_lms_cli.captions import is_korean_caption_track

    assert is_korean_caption_track({"label": "14-1 국문", "language": "14-1 국문"}) is True


def test_recording_captions_cli_requires_korean_track(tmp_path, capsys):
    class NonKoreanCaptionProvider(FakeLiveProvider):
        def recording_captions(self, course, title=""):
            return {
                "module": "1주차",
                "title": "1주차 1차시",
                "track_count": 1,
                "tracks": [{"label": "English", "language": "en", "format": "vtt", "source": "test", "char_count": 10, "text": "English only"}],
                "raw_urls_printed": False,
            }

    def factory(config, options):
        return NonKoreanCaptionProvider()

    env = write_env(tmp_path)
    code = run(["--env-file", str(env), "--json", "--live", "recordings", "captions", "--course", "국제법"], live_provider_factory=factory)
    data = parse_json_output(capsys)
    assert code == 1
    assert "no Korean caption track" in data["error"]


def test_recording_captions_cli_refuses_secret_like_saved_text(tmp_path, capsys):
    class UnsafeCaptionProvider(FakeLiveProvider):
        def recording_captions(self, course, title=""):
            return {
                "module": "1주차",
                "title": "unsafe",
                "track_count": 1,
                "tracks": [{"label": "한국어", "language": "ko", "format": "text", "source": "test", "char_count": 30, "text": "token https://example.invalid/?token=abc"}],
                "raw_urls_printed": False,
            }

    def factory(config, options):
        return UnsafeCaptionProvider()

    env = write_env(tmp_path)
    code = run(["--env-file", str(env), "--json", "--live", "recordings", "captions", "--course", "국제법", "--output", str(tmp_path / "bad.txt")], live_provider_factory=factory)
    data = parse_json_output(capsys)
    assert code == 1
    assert "refusing to save" in data["error"]
    assert not (tmp_path / "bad.txt").exists()


def test_recording_captions_cli_refuses_empty_normalized_text(tmp_path, capsys):
    class EmptyCaptionProvider(FakeLiveProvider):
        def recording_captions(self, course, title=""):
            return {"module": "1주차", "title": "empty", "track_count": 1, "tracks": [{"label": "한국어", "language": "ko", "text": "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\n"}], "raw_urls_printed": False}

    def factory(config, options):
        return EmptyCaptionProvider()

    env = write_env(tmp_path)
    code = run(["--env-file", str(env), "--json", "--live", "recordings", "captions", "--course", "국제법"], live_provider_factory=factory)
    data = parse_json_output(capsys)
    assert code == 1
    assert "did not contain extractable text" in data["error"]


def test_recording_captions_cli_refuses_redaction_policy_numeric_ids(tmp_path, capsys):
    class NumericIdCaptionProvider(FakeLiveProvider):
        def recording_captions(self, course, title=""):
            return {"module": "1주차", "title": "id", "track_count": 1, "tracks": [{"label": "한국어", "language": "ko", "text": "학생 번호 12345678"}], "raw_urls_printed": False}

    def factory(config, options):
        return NumericIdCaptionProvider()

    env = write_env(tmp_path)
    code = run(["--env-file", str(env), "--json", "--live", "recordings", "captions", "--course", "국제법", "--output", str(tmp_path / "bad.txt")], live_provider_factory=factory)
    data = parse_json_output(capsys)
    assert code == 1
    assert "refusing to save" in data["error"]
    assert not (tmp_path / "bad.txt").exists()
