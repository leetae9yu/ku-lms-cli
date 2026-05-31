import json
from pathlib import Path

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
