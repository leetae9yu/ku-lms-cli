import json
from pathlib import Path

from ku_lms_cli.config import KuLmsConfig
from ku_lms_cli.discovery import ArtifactWriter, build_devtools_observation_artifacts, build_dry_run_artifacts, run_discovery
from ku_lms_cli.paths import PathPolicy


def test_dry_run_artifact_writer_redacts_secrets(tmp_path):
    artifacts = build_dry_run_artifacts("https://example.test/?token=abc123456789")
    paths = ArtifactWriter(tmp_path, extra_secret_values=["secret-pwd", "student-id"]).write(artifacts)
    assert Path(paths["route_map"]).exists()
    combined = "\n".join(Path(path).read_text(encoding="utf-8") for path in paths.values() if Path(path).is_file())
    assert "secret-pwd" not in combined
    assert "student-id" not in combined
    assert "screenshots" in combined


def test_run_discovery_creates_required_artifacts(tmp_path):
    config = KuLmsConfig(user_id="student-id", password="secret-pwd", env_path=tmp_path / "KU_LMS.env")
    policy = PathPolicy(root=tmp_path)
    result = run_discovery(config, policy, live=False)
    assert result["ok"] is True
    for key in ["route_map", "network_api_inventory", "selector_map", "command_feasibility_matrix", "summary", "fixtures"]:
        assert key in result["artifacts"]
        assert Path(result["artifacts"][key]).exists()
    fixture = Path(result["artifacts"]["fixtures"]) / "discovery-artifacts.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["mode"] == "dry-run-schema"
    assert "secret-pwd" not in fixture.read_text(encoding="utf-8")


def test_live_discovery_fails_safely_without_browser_tooling(tmp_path):
    config = KuLmsConfig(user_id="student-id", password="secret-pwd", env_path=tmp_path / "KU_LMS.env")
    result = run_discovery(config, PathPolicy(root=tmp_path), live=True)
    assert result["ok"] is False
    assert "secret-pwd" not in str(result)


def test_devtools_observation_artifacts_filter_sensitive_shape_keys():
    observation = {
        "page": {"url": "https://mylms.korea.ac.kr/accounts/1/external_tools/9?launch_type=global_navigation"},
        "probes": [
            {
                "url": ":///api/v1/courses?enrollment_state=active&per_page=100",
                "status": 200,
                "ok": True,
                "shape": {"kind": "array", "length": 1, "firstKeys": ["id", "name", "course_code", "oauth_signature", "lis_person_contact_email_primary"]},
            },
            {
                "url": ":///api/v1/courses/123456789/assignments?per_page=10",
                "status": 200,
                "ok": True,
                "shape": {"kind": "array", "length": 1, "firstKeys": ["due_at", "html_url", "secure_params"]},
            },
        ],
    }
    artifacts = build_devtools_observation_artifacts(observation)
    rendered = json.dumps(artifacts.to_redacted_dict(), ensure_ascii=False)
    assert "oauth_signature" not in rendered
    assert "lis_person_contact_email_primary" not in rendered
    assert "secure_params" not in rendered
    assert "123456789" not in rendered
    assert "/courses/{course_id}/assignments" in rendered
    assert any(item.command == "courses" and item.classification == "api-feasible" for item in artifacts.feasibility)


def test_run_discovery_consumes_redacted_devtools_observation(tmp_path):
    observation_path = tmp_path / "observation.json"
    observation_path.write_text(
        json.dumps(
            {
                "page": {"url": "https://mylms.korea.ac.kr/accounts/1/external_tools/9?launch_type=global_navigation"},
                "probes": [{"url": ":///api/v1/courses?enrollment_state=active&per_page=100", "status": 200, "ok": True, "shape": {"kind": "array", "length": 1, "firstKeys": ["name", "email"]}}],
                "observed_nav_controls": [{"tag": "A", "text": "읽지 않은 메시지 18 개 user@example.edu"}],
            }
        ),
        encoding="utf-8",
    )
    config = KuLmsConfig(user_id="student-id", password="secret-pwd", env_path=tmp_path / "KU_LMS.env")
    result = run_discovery(config, PathPolicy(root=tmp_path), observation_path=observation_path)
    assert result["ok"] is True
    fixture = Path(result["artifacts"]["fixtures"]) / "devtools-observation.json"
    text = fixture.read_text(encoding="utf-8")
    assert "user@example.edu" not in text
    assert "secret-pwd" not in text
    assert "email" not in text


def test_run_discovery_rejects_raw_private_devtools_observation(tmp_path):
    observation_path = tmp_path / "observation.json"
    observation_path.write_text(json.dumps({"probes": [{"secure_params": "raw-token-value"}]}), encoding="utf-8")
    config = KuLmsConfig(user_id="student-id", password="secret-pwd", env_path=tmp_path / "KU_LMS.env")
    result = run_discovery(config, PathPolicy(root=tmp_path), observation_path=observation_path)
    assert result["ok"] is False
    assert "raw/private" in result["detail"]
