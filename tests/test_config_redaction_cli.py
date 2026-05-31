from pathlib import Path

from ku_lms_cli.cli import build_parser, run
from ku_lms_cli.config import load_config
from ku_lms_cli.redaction import REDACTION, redact_data, redact_text


def test_load_config_redacts_values(tmp_path):
    env_file = tmp_path / "KU_LMS.env"
    env_file.write_text("KU_LMS_ID=student123\nKU_LMS_PWD=super-secret\n", encoding="utf-8")
    config = load_config(env_file)
    assert config.user_id == "student123"
    assert config.password == "super-secret"
    assert config.redacted()["KU_LMS_ID"] == REDACTION
    assert config.redacted()["KU_LMS_PWD"] == REDACTION


def test_missing_config_error_does_not_include_secret(tmp_path, capsys):
    env_file = tmp_path / "KU_LMS.env"
    env_file.write_text("KU_LMS_ID=student123\n", encoding="utf-8")
    code = run(["--env-file", str(env_file), "--json", "status"])
    out = capsys.readouterr().out
    assert code == 1
    assert "student123" not in out
    assert "KU_LMS_PWD" in out


def test_redact_text_and_data():
    assert "secret" not in redact_text("KU_LMS_PWD=secret")
    data = redact_data({"token": "abc123456789", "nested": {"cookie": "sid=1"}, "safe": "ok"})
    assert data["token"] == REDACTION
    assert data["nested"]["cookie"] == REDACTION
    assert data["safe"] == "ok"


def test_forbidden_commands_fail_closed(capsys):
    code = run(["--json", "submit"])
    out = capsys.readouterr().out
    assert code == 2
    assert "not supported by design" in out


def test_required_command_families_exist():
    parser = build_parser()
    help_text = parser.format_help()
    for command in ["status", "login", "discover", "courses", "materials", "assignments", "recordings", "calendar"]:
        assert command in help_text


def test_redact_query_token_values():
    redacted = redact_text("https://example.test/path?token=abc123456789&safe=ok")
    assert "abc123456789" not in redacted
    assert "token=[REDACTED]" in redacted


def test_redact_calendar_feed_url_token():
    redacted = redact_text("https://mylms.korea.ac.kr/feeds/calendars/" + "user_abc123TOKEN.ics")
    assert "user_abc123TOKEN" not in redacted
    assert "[REDACTED]" in redacted


def test_default_config_falls_back_to_user_config_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_home = tmp_path / "xdg"
    global_env = config_home / "ku-lms-cli" / "KU_LMS.env"
    global_env.parent.mkdir(parents=True)
    global_env.write_text("KU_LMS_ID=global-student\nKU_LMS_PWD=global-secret\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("KU_LMS_ID", raising=False)
    monkeypatch.delenv("KU_LMS_PWD", raising=False)

    config = load_config()

    assert config.user_id == "global-student"
    assert config.password == "global-secret"
    assert config.env_path == global_env


def test_ku_lms_env_file_override_wins_over_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    override = tmp_path / "custom.env"
    override.write_text("KU_LMS_ID=override-student\nKU_LMS_PWD=override-secret\n", encoding="utf-8")
    Path("KU_LMS.env").write_text("KU_LMS_ID=local-student\nKU_LMS_PWD=local-secret\n", encoding="utf-8")
    monkeypatch.setenv("KU_LMS_ENV_FILE", str(override))
    monkeypatch.delenv("KU_LMS_ID", raising=False)
    monkeypatch.delenv("KU_LMS_PWD", raising=False)

    config = load_config()

    assert config.user_id == "override-student"
    assert config.env_path == override
