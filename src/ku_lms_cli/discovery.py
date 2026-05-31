"""Discovery artifact schemas and harness for KU LMS."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import KuLmsConfig
from .paths import PathPolicy
from .redaction import redact_data, redact_text

DEFAULT_ENTRY_URL = "https://mylms.korea.ac.kr/accounts/1/external_tools/9?launch_type=global_navigation"
FORBIDDEN_OBSERVATION_KEYS = {
    "cookie",
    "cookies",
    "headers",
    "har",
    "localStorage",
    "sessionStorage",
    "screenshot",
    "screenshots",
    "token",
    "tokens",
}
FORBIDDEN_OBSERVATION_KEYS_LOWER = {key.lower() for key in FORBIDDEN_OBSERVATION_KEYS}
SENSITIVE_SHAPE_KEY = re.compile(
    r"(?:password|pwd|secret|token|oauth|signature|nonce|saml|relay|cookie|session|email|mail|lis_person|custom_user|user_login|sourcedid|person_name|consumer_key|secure_params)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteEntry:
    name: str
    url: str
    method: str = "GET"
    notes: str = ""


@dataclass(frozen=True)
class NetworkEndpoint:
    name: str
    method: str
    url_pattern: str
    request_shape: dict[str, Any] = field(default_factory=dict)
    response_shape: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class SelectorEntry:
    name: str
    selector: str
    purpose: str
    fallback_text: str = ""


@dataclass(frozen=True)
class FeasibilityEntry:
    command: str
    classification: str
    evidence: str
    fallback: str = ""


@dataclass(frozen=True)
class DiscoveryArtifacts:
    generated_at: str
    entry_url: str
    mode: str
    routes: list[RouteEntry]
    endpoints: list[NetworkEndpoint]
    selectors: list[SelectorEntry]
    feasibility: list[FeasibilityEntry]
    notes: list[str] = field(default_factory=list)

    def to_redacted_dict(self, extra_values: list[str] | None = None) -> dict[str, Any]:
        return redact_data(asdict(self), extra_values=extra_values)


class ArtifactWriter:
    """Writes redacted discovery artifacts and refuses raw screenshots by design."""

    def __init__(self, output_dir: Path, extra_secret_values: list[str] | None = None) -> None:
        self.output_dir = output_dir
        self.extra_secret_values = extra_secret_values or []

    def write(self, artifacts: DiscoveryArtifacts, fixture_payload: dict[str, Any] | None = None) -> dict[str, str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data = artifacts.to_redacted_dict(self.extra_secret_values)
        paths = {
            "route_map": self.output_dir / "route-map.json",
            "network_api_inventory": self.output_dir / "network-api-inventory.json",
            "selector_map": self.output_dir / "selector-map.json",
            "command_feasibility_matrix": self.output_dir / "command-feasibility-matrix.json",
            "summary": self.output_dir / "summary.md",
        }
        _write_json(paths["route_map"], {"generated_at": data["generated_at"], "entry_url": data["entry_url"], "routes": data["routes"]})
        _write_json(paths["network_api_inventory"], {"generated_at": data["generated_at"], "endpoints": data["endpoints"]})
        _write_json(paths["selector_map"], {"generated_at": data["generated_at"], "selectors": data["selectors"]})
        _write_json(paths["command_feasibility_matrix"], {"generated_at": data["generated_at"], "feasibility": data["feasibility"]})
        paths["summary"].write_text(_summary_markdown(data), encoding="utf-8")
        fixture_dir = self.output_dir / "fixtures"
        fixture_dir.mkdir(exist_ok=True)
        _write_json(fixture_dir / "discovery-artifacts.json", data)
        if fixture_payload is not None:
            _write_json(fixture_dir / "devtools-observation.json", redact_data(_sanitize_observation(fixture_payload), self.extra_secret_values))
        return {key: str(path) for key, path in paths.items()} | {"fixtures": str(fixture_dir)}


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _summary_markdown(data: dict[str, Any]) -> str:
    lines = ["# KU LMS Discovery Summary", "", f"- Generated: {data['generated_at']}", f"- Mode: {data['mode']}", f"- Entry URL: {data['entry_url']}", "", "## Feasibility"]
    for item in data["feasibility"]:
        lines.append(f"- `{item['command']}`: {item['classification']} — {item['evidence']}")
    lines.extend(["", "## Safety", "- Raw screenshots are forbidden and were not written.", "- Artifacts are redacted through the shared redaction layer."])
    return "\n".join(lines) + "\n"


def build_dry_run_artifacts(entry_url: str = DEFAULT_ENTRY_URL) -> DiscoveryArtifacts:
    generated_at = datetime.now(timezone.utc).isoformat()
    return DiscoveryArtifacts(
        generated_at=generated_at,
        entry_url=entry_url,
        mode="dry-run-schema",
        routes=[
            RouteEntry("entry", entry_url, notes="User-provided KU LMS global navigation external tool URL"),
            RouteEntry("login", "https://lms.korea.ac.kr/", notes="Exact SSO/login flow must be filled by live discovery"),
            RouteEntry("courses", "<discovered>", notes="Pending live discovery"),
            RouteEntry("materials", "<discovered>", notes="Pending live discovery"),
            RouteEntry("assignments", "<discovered>", notes="Pending live discovery"),
            RouteEntry("recordings", "<discovered>", notes="Pending live discovery"),
        ],
        endpoints=[
            NetworkEndpoint("login/session", "UNKNOWN", "<discovered>", notes="Capture only redacted request/response shapes"),
            NetworkEndpoint("courses", "UNKNOWN", "<discovered>"),
            NetworkEndpoint("materials", "UNKNOWN", "<discovered>"),
            NetworkEndpoint("assignments", "UNKNOWN", "<discovered>"),
            NetworkEndpoint("recordings", "UNKNOWN", "<discovered>"),
        ],
        selectors=[
            SelectorEntry("login_username", "<discovered>", "Username field"),
            SelectorEntry("login_password", "<discovered>", "Password field"),
            SelectorEntry("course_links", "<discovered>", "Course navigation"),
            SelectorEntry("recording_play", "<discovered>", "Recording playback trigger"),
        ],
        feasibility=[
            FeasibilityEntry("status", "browser-required-until-live-discovery", "Session validity cannot be known from static scaffold"),
            FeasibilityEntry("courses", "unknown", "Needs route/API/selector discovery"),
            FeasibilityEntry("materials list/download", "unknown", "Needs route/API/selector discovery"),
            FeasibilityEntry("assignments/deadlines", "unknown", "Needs route/API/selector discovery"),
            FeasibilityEntry("recordings list/play/keepalive", "browser-likely", "Playback likely needs browser automation and may create accepted progress side effects"),
        ],
        notes=["Dry-run schema artifact; replace placeholders with live discovery results."],
    )


def build_devtools_observation_artifacts(observation: dict[str, Any], entry_url: str = DEFAULT_ENTRY_URL) -> DiscoveryArtifacts:
    """Build standard discovery artifacts from a shape-only DevTools/CDP observation."""
    _reject_raw_observation(observation)
    sanitized = _sanitize_observation(observation)
    generated_at = datetime.now(timezone.utc).isoformat()
    probes = _observation_probes(sanitized)
    page = sanitized.get("page") or (sanitized.get("result") or {}).get("page") or {}
    endpoints = [
        NetworkEndpoint(
            name=_endpoint_name(probe.get("url", "")),
            method="GET",
            url_pattern=_normalize_observed_url(probe.get("url", "")),
            request_shape={"credentials": "same-origin", "accept": "application/json"},
            response_shape=probe.get("shape", {}),
            notes=f"DevTools/CDP observed status={probe.get('status', 'unknown')} ok={probe.get('ok', 'unknown')}; values omitted.",
        )
        for probe in probes
    ]
    return DiscoveryArtifacts(
        generated_at=generated_at,
        entry_url=_normalize_observed_url(page.get("url") or entry_url),
        mode="devtools-cdp-observation",
        routes=[
            RouteEntry("entry", _normalize_observed_url(entry_url), notes="Original KU LMS global navigation entry URL"),
            RouteEntry("sso-wrapper", "https://lms.korea.ac.kr/xn-sso/login.php?[query:redacted]", notes="Unauthenticated entry redirects through KU LMS SSO wrapper"),
            RouteEntry("portal-sso", "https://ksso.korea.ac.kr/svc/tk/Auth.do?[query:redacted]", notes="Portal SSO presents KUPID login controls"),
            RouteEntry("authenticated-main", _normalize_observed_url(page.get("url") or entry_url), notes="Authenticated Canvas external-tool landing page"),
            RouteEntry("courses-api", "https://mylms.korea.ac.kr/api/v1/courses?[query_keys:enrollment_state,per_page]", notes="Canvas courses JSON shape observed"),
            RouteEntry("assignments-api", "https://mylms.korea.ac.kr/api/v1/courses/{course_id}/assignments?[query_keys:per_page]", notes="Canvas assignments/deadline JSON shape observed"),
            RouteEntry("modules-api", "https://mylms.korea.ac.kr/api/v1/courses/{course_id}/modules?[query_keys:per_page]", notes="Canvas modules JSON shape observed for materials traversal"),
        ],
        endpoints=endpoints,
        selectors=[
            SelectorEntry("portal_login_button", 'text contains "포털 계정 로그인"', "Choose KU Portal SSO from LMS login wrapper", "포털 계정 로그인"),
            SelectorEntry("kupid_username", '#one_id, input[name="one_id"]', "KUPID username field", "KUPID Single ID"),
            SelectorEntry("kupid_password", '#password, input[name="user_password"]', "KUPID password field", "Password"),
            SelectorEntry("kupid_submit", 'button/input value or text equals "Login"', "Submit portal login form", "Login"),
            SelectorEntry("canvas_navigation", "authenticated Canvas a/button controls", "Find courses/calendar/dashboard/browser fallbacks", "대시보드 / 과목 / 캘린더"),
        ],
        feasibility=_feasibility_from_probes(probes),
        notes=[
            "Built from shape-only DevTools/CDP observation.",
            "No screenshots, HAR, cookies, hidden form values, or token-bearing payloads are accepted.",
        ],
    )


def run_discovery(
    config: KuLmsConfig,
    policy: PathPolicy,
    entry_url: str = DEFAULT_ENTRY_URL,
    live: bool = False,
    observation_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run dry-run schema discovery or fail safely when live browser tooling is unavailable."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = policy.resolve(policy.discovery_dir / "redacted" / timestamp)
    if observation_path:
        try:
            observation = json.loads(Path(observation_path).read_text(encoding="utf-8"))
            artifacts = build_devtools_observation_artifacts(observation, entry_url=entry_url)
        except Exception as exc:
            return {
                "ok": False,
                "error": "invalid redacted DevTools observation artifact",
                "detail": redact_text(str(exc), [config.user_id, config.password]),
                "exit_code": 4,
            }
        paths = ArtifactWriter(output_dir, [config.user_id, config.password]).write(artifacts, fixture_payload=observation)
        return {"ok": True, "mode": artifacts.mode, "output_dir": str(output_dir), "artifacts": paths}
    if live:
        try:
            import playwright.sync_api  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover - environment dependent
            return {
                "ok": False,
                "error": "live discovery requires optional Playwright browser tooling",
                "detail": redact_text(str(exc), [config.user_id, config.password]),
                "suggestion": "Install the browser extra and run the documented live discovery command; do not bypass SSO/MFA controls.",
                "exit_code": 4,
            }
        return {
            "ok": False,
            "error": "live discovery browser flow is intentionally gated for a later implementation step",
            "exit_code": 4,
        }
    artifacts = build_dry_run_artifacts(entry_url)
    paths = ArtifactWriter(output_dir, [config.user_id, config.password]).write(artifacts)
    return {"ok": True, "mode": artifacts.mode, "output_dir": str(output_dir), "artifacts": paths}


def _reject_raw_observation(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_s = str(key)
            if key_s.lower() in FORBIDDEN_OBSERVATION_KEYS_LOWER or (
                key_s not in {"firstKeys", "keys"} and SENSITIVE_SHAPE_KEY.search(key_s)
            ):
                raise ValueError(f"raw/private observation key is forbidden at {path}.{key_s}")
            _reject_raw_observation(child, f"{path}.{key_s}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            _reject_raw_observation(child, f"{path}[{idx}]")


def _sanitize_observation(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            key_s = str(key)
            if key_s.lower() in FORBIDDEN_OBSERVATION_KEYS_LOWER:
                continue
            if key_s in {"firstKeys", "keys"} and isinstance(child, list):
                clean[key_s] = [item for item in child if not SENSITIVE_SHAPE_KEY.search(str(item))]
            elif key_s in {"url", "urlPattern"} and isinstance(child, str):
                clean[key_s] = _normalize_observed_url(child)
            else:
                clean[key] = _sanitize_observation(child)
        return clean
    if isinstance(value, list):
        return [_sanitize_observation(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _normalize_observed_url(url: str) -> str:
    if not url:
        return "<unknown>"
    normalized = url if not url.startswith(":///") else "https://mylms.korea.ac.kr/" + url[4:]
    normalized = re.sub(r"/courses/\d+", "/courses/{course_id}", normalized)
    normalized = re.sub(r"/accounts/\d+", "/accounts/{account_id}", normalized)
    normalized = re.sub(r"/external_tools/\d+", "/external_tools/{tool_id}", normalized)
    normalized = re.sub(r"/assignments/\d+", "/assignments/{assignment_id}", normalized)
    normalized = re.sub(r"/files/\d+", "/files/{file_id}", normalized)
    normalized = re.sub(r"/modules/\d+", "/modules/{module_id}", normalized)
    return redact_text(normalized)


def _observation_probes(observation: dict[str, Any]) -> list[dict[str, Any]]:
    probes = observation.get("probes")
    if probes is None:
        probes = (observation.get("result") or {}).get("probes")
    return [probe for probe in probes or [] if isinstance(probe, dict)]


def _endpoint_name(url: str) -> str:
    normalized = _normalize_observed_url(url)
    if "/api/v1/" in normalized:
        return "canvas/" + normalized.split("/api/v1/", 1)[1].split("?", 1)[0]
    return normalized.split("?", 1)[0]


def _probe(probes: list[dict[str, Any]], fragment: str) -> dict[str, Any]:
    return next((probe for probe in probes if fragment in _normalize_observed_url(probe.get("url", ""))), {})


def _status(probes: list[dict[str, Any]], fragment: str) -> Any:
    return _probe(probes, fragment).get("status", "unknown")


def _feasibility_from_probes(probes: list[dict[str, Any]]) -> list[FeasibilityEntry]:
    courses_status = _status(probes, "/courses?")
    assignments_status = _status(probes, "/assignments")
    modules_status = _status(probes, "/modules")
    files_status = _status(probes, "/files")
    pages_status = _status(probes, "/pages")
    return [
        FeasibilityEntry("status/login", "browser-session-required", "Portal SSO login requires browser automation; store only local session markers."),
        FeasibilityEntry("courses", "api-feasible" if courses_status == 200 else "browser-fallback-required", f"Canvas courses API observed status {courses_status}."),
        FeasibilityEntry("assignments list/deadlines/download", "api-feasible-for-metadata-browser-fallback-for-files" if assignments_status == 200 else "browser-fallback-required", f"Canvas assignments API observed status {assignments_status}."),
        FeasibilityEntry("materials list/download", "modules-api-feasible-files-api-course-dependent" if modules_status == 200 else "browser-fallback-required", f"Modules/files/pages statuses: {modules_status}/{files_status}/{pages_status}."),
        FeasibilityEntry("recordings list/play/keepalive", "browser-lti-required", "No stable recordings JSON endpoint was confirmed from the main-page observation."),
        FeasibilityEntry("submit/upload/post/edit/delete", "forbidden", "Mutating LMS actions are forbidden by user policy and must fail closed."),
    ]
