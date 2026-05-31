"""Live KU LMS provider built on a small, fakeable Chrome DevTools Protocol boundary.

The public provider methods intentionally return title/name/status metadata only. Raw LMS
identifiers, launch URLs, cookies, headers, tokens, and credential material stay inside the
browser session and are never part of CLI payloads.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import KuLmsConfig
from .discovery import DEFAULT_ENTRY_URL
from .redaction import redact_data, redact_text

CANVAS_ORIGIN = "https://mylms.korea.ac.kr"
LOGIN_POLL_SECONDS = 45.0


class LiveCommandError(RuntimeError):
    """A safe-to-print live command failure."""


@dataclass(frozen=True)
class LiveOptions:
    entry_url: str = DEFAULT_ENTRY_URL
    headless: bool = True
    timeout_seconds: float = 60.0
    chrome_path: str | None = None


class BrowserSession(Protocol):
    async def __aenter__(self) -> "BrowserSession": ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    async def login(self) -> None: ...

    async def fetch_json(self, path_or_url: str) -> Any: ...

    async def get_calendar_feed_url(self) -> str: ...

    async def play_url(self, url: str, *, until_end: bool = False, seconds: float | None = None) -> dict[str, Any]: ...


class LiveLmsProvider:
    """Read-only live provider.

    The provider is synchronous for the CLI, but all browser operations are performed through
    an async session factory so tests can replace the browser with deterministic fakes.
    """

    def __init__(self, config: KuLmsConfig, options: LiveOptions | None = None, session_factory: Any | None = None) -> None:
        self.config = config
        self.options = options or LiveOptions()
        self._session_factory = session_factory or (lambda: CdpBrowserSession(config, self.options))

    def courses(self) -> list[dict[str, Any]]:
        return _run(self._courses_async())

    def assignments(self, course: str) -> list[dict[str, Any]]:
        return _run(self._assignments_async(course))

    def deadlines(self, course: str) -> list[dict[str, Any]]:
        return [
            {"title": item["title"], "due_at": item.get("due_at"), "remaining_candidate": item.get("remaining_candidate", False)}
            for item in self.assignments(course)
        ]

    def calendar_events(self, start_date: str = "", end_date: str = "", course: str = "") -> list[dict[str, Any]]:
        return _run(self._calendar_events_async(start_date=start_date, end_date=end_date, course_query=course))

    def calendar_upcoming(self, start_date: str = "", end_date: str = "") -> list[dict[str, Any]]:
        return _run(self._calendar_upcoming_async(start_date=start_date, end_date=end_date))

    def calendar_todo(self) -> list[dict[str, Any]]:
        return _run(self._calendar_todo_async())

    def calendar_feed(self, delivery: str = "inspect") -> dict[str, Any]:
        return _run(self._calendar_feed_async(delivery=delivery))

    def recordings(self, course: str) -> list[dict[str, Any]]:
        return _run(self._recordings_async(course))

    def play_recording(self, course: str, title: str, *, until_end: bool = False, seconds: float | None = None) -> dict[str, Any]:
        return _run(self._play_recording_async(course, title, until_end=until_end, seconds=seconds))

    async def _courses_async(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await session.login()
            courses = await _fetch_courses(session)
        return [_public_course(course) for course in courses]

    async def _assignments_async(self, course_query: str) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await session.login()
            course = await _select_course(session, course_query)
            assignments = await session.fetch_json(f"/api/v1/courses/{course['id']}/assignments?per_page=100&include[]=submission")
        if not isinstance(assignments, list):
            raise LiveCommandError("assignment API returned an unexpected shape")
        return [_public_assignment(item) for item in assignments if isinstance(item, dict)]

    async def _calendar_events_async(self, start_date: str = "", end_date: str = "", course_query: str = "") -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await session.login()
            params: dict[str, Any] = {"per_page": 100, "type": ["assignment", "event"]}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            if course_query:
                course = await _select_course(session, course_query)
                params["context_codes[]"] = [f"course_{course['id']}"]
            path = "/api/v1/calendar_events?" + urllib.parse.urlencode(params, doseq=True)
            events = await session.fetch_json(path)
        if not isinstance(events, list):
            raise LiveCommandError("calendar events API returned an unexpected shape")
        return [_public_calendar_event(item) for item in events if isinstance(item, dict)]

    async def _calendar_upcoming_async(self, start_date: str = "", end_date: str = "") -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await session.login()
            params: dict[str, Any] = {"per_page": 100}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            items = await session.fetch_json("/api/v1/planner/items?" + urllib.parse.urlencode(params, doseq=True))
        if not isinstance(items, list):
            raise LiveCommandError("planner API returned an unexpected shape")
        return [_public_planner_item(item) for item in items if isinstance(item, dict)]

    async def _calendar_todo_async(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await session.login()
            items = await session.fetch_json("/api/v1/users/self/todo?per_page=100")
        if not isinstance(items, list):
            raise LiveCommandError("todo API returned an unexpected shape")
        return [_public_todo_item(item) for item in items if isinstance(item, dict)]

    async def _calendar_feed_async(self, delivery: str = "inspect") -> dict[str, Any]:
        if delivery not in {"inspect", "copy", "open", "open_google"}:
            raise LiveCommandError("unsupported calendar feed delivery")
        async with self._session_factory() as session:
            await session.login()
            feed_url = await session.get_calendar_feed_url()
        if not feed_url or not feed_url.endswith(".ics"):
            raise LiveCommandError("calendar feed URL was not found")
        url_shape = _feed_url_shape(feed_url)
        if delivery == "copy":
            copied, detail = _copy_to_clipboard(feed_url)
            if not copied:
                raise LiveCommandError(f"calendar feed URL was found but clipboard copy failed: {detail}")
            return {"delivery": "copy", "copied": True, "opened": False, "url_shape": url_shape, "raw_url_printed": False}
        if delivery == "open":
            opened = webbrowser.open(feed_url)
            return {"delivery": "open", "copied": False, "opened": bool(opened), "url_shape": url_shape, "raw_url_printed": False}
        if delivery == "open_google":
            google_url = "https://calendar.google.com/calendar/r?cid=" + urllib.parse.quote(feed_url, safe="")
            opened = webbrowser.open(google_url)
            return {"delivery": "open_google", "copied": False, "opened": bool(opened), "url_shape": url_shape, "raw_url_printed": False}
        return {"delivery": "inspect", "copied": False, "opened": False, "url_shape": url_shape, "raw_url_printed": False}

    async def _recordings_async(self, course_query: str) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await session.login()
            course = await _select_course(session, course_query)
            candidates = await _recording_candidates(session, course)
        return [_public_recording(item) for item in candidates]

    async def _play_recording_async(self, course_query: str, title_query: str, *, until_end: bool = False, seconds: float | None = None) -> dict[str, Any]:
        if seconds is not None and seconds <= 0:
            raise LiveCommandError("--seconds must be positive")
        async with self._session_factory() as session:
            await session.login()
            course = await _select_course(session, course_query)
            candidates = await _recording_candidates(session, course)
            recording = _select_recording(candidates, title_query)
            playback = await session.play_url(recording["url"], until_end=until_end, seconds=seconds)
        return _public_playback(recording, playback, until_end=until_end, seconds=seconds)


async def _fetch_courses(session: BrowserSession) -> list[dict[str, Any]]:
    data = await session.fetch_json("/api/v1/courses?per_page=100&enrollment_state=active")
    if not isinstance(data, list):
        raise LiveCommandError("courses API returned an unexpected shape")
    courses = [item for item in data if isinstance(item, dict) and item.get("id") and item.get("name")]
    if not courses:
        raise LiveCommandError("no active courses found")
    return courses


async def _select_course(session: BrowserSession, query: str) -> dict[str, Any]:
    if not query:
        raise LiveCommandError("--course is required in live mode")
    courses = await _fetch_courses(session)
    matches = [course for course in courses if _matches(query, str(course.get("name", "")))]
    if not matches:
        names = ", ".join(str(course.get("name", "")) for course in courses[:10])
        raise LiveCommandError(f"course not found; available course names include: {names}")
    if len(matches) > 1:
        exact = [course for course in matches if str(course.get("name", "")).strip().casefold() == query.strip().casefold()]
        if len(exact) == 1:
            return exact[0]
        names = ", ".join(str(course.get("name", "")) for course in matches[:10])
        raise LiveCommandError(f"course query is ambiguous; matches: {names}")
    return matches[0]


async def _recording_candidates(session: BrowserSession, course: dict[str, Any]) -> list[dict[str, Any]]:
    modules = await session.fetch_json(f"/api/v1/courses/{course['id']}/modules?per_page=100&include[]=items")
    if not isinstance(modules, list):
        raise LiveCommandError("modules API returned an unexpected shape")
    candidates: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_name = str(module.get("name") or "")
        for item in module.get("items") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            item_type = str(item.get("type") or "")
            url = str(item.get("html_url") or item.get("url") or "")
            if item_type != "ExternalTool" or not url or _looks_like_handout(title):
                continue
            candidates.append({"module": module_name, "title": title, "type": item_type, "url": url})
    if not candidates:
        raise LiveCommandError(f"no recording candidates found for course {course.get('name', '')}")
    return candidates


def _select_recording(candidates: list[dict[str, Any]], query: str) -> dict[str, Any]:
    if not query:
        raise LiveCommandError("--title is required for live recording playback")
    matches = [item for item in candidates if _matches(query, str(item.get("title", ""))) or _matches(query, str(item.get("module", "")))]
    if not matches:
        names = ", ".join(str(item.get("title", "")) for item in candidates[:10])
        raise LiveCommandError(f"recording not found; available titles include: {names}")
    if len(matches) > 1:
        shortest = sorted(matches, key=lambda item: len(str(item.get("title", ""))))[0]
        exact = [item for item in matches if str(item.get("title", "")).strip().casefold() == query.strip().casefold()]
        return exact[0] if exact else shortest
    return matches[0]


def _public_course(course: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": course.get("name", ""),
        "workflow_state": course.get("workflow_state", ""),
        "default_view": course.get("default_view", ""),
    }


def _public_assignment(item: dict[str, Any]) -> dict[str, Any]:
    due_at = item.get("due_at") or ""
    submission = item.get("submission") if isinstance(item.get("submission"), dict) else {}
    locked = bool(item.get("locked_for_user"))
    submitted_at = submission.get("submitted_at") or ""
    workflow = submission.get("workflow_state") or ""
    return {
        "title": item.get("name") or item.get("title") or "",
        "due_at": due_at,
        "unlock_at": item.get("unlock_at") or "",
        "lock_at": item.get("lock_at") or "",
        "points_possible": item.get("points_possible"),
        "published": bool(item.get("published", True)),
        "locked_for_user": locked,
        "submission_workflow_state": workflow,
        "submitted_at": submitted_at,
        "missing": bool(submission.get("missing", False)),
        "late": bool(submission.get("late", False)),
        "submission_types": [str(v) for v in item.get("submission_types") or []],
        "remaining_candidate": _remaining_candidate(due_at, locked, submitted_at, workflow),
    }


def _public_calendar_event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title") or item.get("name") or "",
        "start_at": item.get("start_at") or item.get("all_day_date") or "",
        "end_at": item.get("end_at") or "",
        "type": item.get("type") or item.get("workflow_state") or "event",
        "context_name": item.get("context_name") or item.get("effective_context_code") or "",
        "all_day": bool(item.get("all_day", False)),
        "location_name": item.get("location_name") or "",
    }


def _public_planner_item(item: dict[str, Any]) -> dict[str, Any]:
    plannable = item.get("plannable") if isinstance(item.get("plannable"), dict) else {}
    submissions = item.get("submissions") if isinstance(item.get("submissions"), dict) else {}
    return {
        "title": plannable.get("title") or plannable.get("name") or item.get("title") or "",
        "date": item.get("plannable_date") or plannable.get("due_at") or "",
        "type": item.get("plannable_type") or plannable.get("type") or "",
        "course": item.get("context_name") or "",
        "submitted": bool(submissions.get("submitted") or submissions.get("submitted_at")),
        "new_activity": bool(item.get("new_activity", False)),
    }


def _public_todo_item(item: dict[str, Any]) -> dict[str, Any]:
    assignment = item.get("assignment") if isinstance(item.get("assignment"), dict) else {}
    return {
        "title": assignment.get("name") or assignment.get("title") or item.get("type") or "",
        "due_at": assignment.get("due_at") or "",
        "type": item.get("type") or "",
        "course": item.get("context_name") or "",
        "ignore": bool(item.get("ignore", False)),
    }


def _public_recording(item: dict[str, Any]) -> dict[str, Any]:
    return {"module": item.get("module", ""), "title": item.get("title", ""), "type": item.get("type", ""), "playable": True}


def _public_playback(recording: dict[str, Any], playback: dict[str, Any], *, until_end: bool, seconds: float | None) -> dict[str, Any]:
    events = playback.get("media_events") if isinstance(playback.get("media_events"), dict) else {}
    stream_seen = bool(playback.get("video_mp4_partial_content_seen", False))
    duration = playback.get("observed_duration_seconds")
    event_completed = bool(playback.get("completed", False))
    inferred_completed = bool(until_end and stream_seen and duration is not None)
    completion_basis = "player_event" if event_completed else "stream_duration_observed" if inferred_completed else "not_observed"
    return redact_data(
        {
            "module": recording.get("module", ""),
            "title": recording.get("title", ""),
            "side_effects_accepted": True,
            "until_end": until_end,
            "keepalive_seconds": seconds,
            "video_mp4_partial_content_seen": stream_seen,
            "media_events": {
                "play": bool(events.get("play", False)),
                "pause": bool(events.get("pause", False)),
                "duration_changed": bool(events.get("duration_changed", False)),
            },
            "observed_duration_seconds": duration,
            "completed": event_completed or inferred_completed,
            "completion_basis": completion_basis,
        }
    )


def _remaining_candidate(due_at: str, locked: bool, submitted_at: str, workflow: str) -> bool:
    if locked or submitted_at or workflow in {"submitted", "graded"} or not due_at:
        return False
    try:
        due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due > datetime.now(timezone.utc)


def _looks_like_handout(title: str) -> bool:
    compact = title.replace(" ", "")
    return "교안" in compact or "강의자료" in compact or "자료" == compact


def _matches(query: str, value: str) -> bool:
    return query.strip().casefold() in value.strip().casefold()


def _feed_url_shape(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    if "/feeds/calendars/" in path and path.endswith(".ics"):
        path = "/feeds/calendars/[REDACTED-FEED-TOKEN].ics"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _copy_to_clipboard(text: str) -> tuple[bool, str]:
    for command in [
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip.exe"],
        ["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard"],
        ["termux-clipboard-set"],
    ]:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(command, input=text, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)
            return True, command[0]
        except (subprocess.SubprocessError, OSError):
            continue
    try:
        import tkinter  # type: ignore

        root = tkinter.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True, "tkinter"
    except Exception:  # noqa: BLE001 - clipboard availability is platform/display dependent
        pass
    return False, "no supported clipboard command found"


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except LiveCommandError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert internal/browser details into safe output
        message = str(exc) or exc.__class__.__name__
        raise LiveCommandError(redact_text(message)) from exc


class CdpBrowserSession:
    """Minimal CDP browser session used by live CLI mode."""

    def __init__(self, config: KuLmsConfig, options: LiveOptions) -> None:
        self.config = config
        self.options = options
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._client: _CdpClient | None = None
        self._network_seen: dict[str, bool] = {}
        self._media_seen: dict[str, bool] = {}
        self._duration: float | None = None

    async def __aenter__(self) -> "CdpBrowserSession":
        self._tmp = tempfile.TemporaryDirectory(prefix="ku-lms-cdp-")
        port = _free_port()
        chrome = self.options.chrome_path or os.environ.get("KU_LMS_CHROME") or _default_chrome_path()
        if not chrome:
            raise LiveCommandError("Chrome/headless_shell executable was not found; set KU_LMS_CHROME")
        args = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self._tmp.name}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
        if self.options.headless:
            args.extend(["--headless=new", "--autoplay-policy=no-user-gesture-required"])
        args.append("about:blank")
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        ws_url = await asyncio.to_thread(_wait_for_page_ws, port, self.options.timeout_seconds)
        self._client = await _CdpClient.connect(ws_url)
        await self._client.send("Page.enable")
        await self._client.send("Runtime.enable")
        await self._client.send("Network.enable")
        try:
            await self._client.send("Media.enable")
        except LiveCommandError:
            pass
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client:
            await self._client.close()
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._tmp:
            self._tmp.cleanup()

    async def login(self) -> None:
        await self.goto(self.options.entry_url)
        deadline = time.monotonic() + LOGIN_POLL_SECONDS
        last_state = ""
        while time.monotonic() < deadline:
            try:
                state = await self.evaluate(
                    """
                    (() => ({
                      href: location.href,
                      title: document.title,
                      hasPassword: !!document.querySelector('input[type="password"],#password,input[name="user_password"]'),
                      hasText: !!(document.querySelector('#one_id') || document.querySelector('input[name="one_id"]') || document.querySelector('input[type="text"]')),
                      bodyText: (document.body && document.body.innerText || '').slice(0, 2000)
                    }))()
                    """,
                    timeout=10,
                )
            except LiveCommandError:
                await asyncio.sleep(2.0)
                continue
            href = str(state.get("href", "")) if isinstance(state, dict) else ""
            if "mylms.korea.ac.kr" in href and "login" not in href.casefold() and await self._canvas_session_ready():
                return
            if isinstance(state, dict) and state.get("hasPassword"):
                await self._submit_credentials()
                await asyncio.sleep(5.0)
            else:
                await self._click_login_candidate()
                await asyncio.sleep(1.0)
            last_state = href or str(state)[:120]
        raise LiveCommandError(f"login did not complete before timeout; last page: {redact_text(last_state)}")

    async def goto(self, url: str) -> None:
        client = self._require_client()
        await client.send("Page.navigate", {"url": url})
        await asyncio.sleep(1.5)

    async def evaluate(self, expression: str, *, timeout: float | None = None) -> Any:
        client = self._require_client()
        response = await client.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            timeout=timeout or self.options.timeout_seconds,
        )
        result = response.get("result", {}) if isinstance(response, dict) else {}
        if "exceptionDetails" in response:
            raise LiveCommandError(redact_text(response.get("exceptionDetails")))
        if result.get("subtype") == "error":
            raise LiveCommandError(redact_text(result.get("description", "browser evaluation failed")))
        return result.get("value")

    async def fetch_json(self, path_or_url: str) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{CANVAS_ORIGIN}{path_or_url}"
        expr = json.dumps(url)
        return await self.evaluate(
            f"""
            (async () => {{
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 20000);
              try {{
                const r = await fetch({expr}, {{credentials: 'include', signal: controller.signal}});
                const text = await r.text();
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return text ? JSON.parse(text) : null;
              }} finally {{
                clearTimeout(timer);
              }}
            }})()
            """,
            timeout=min(self.options.timeout_seconds, 25),
        )

    async def get_calendar_feed_url(self) -> str:
        await self.goto(f"{CANVAS_ORIGIN}/calendar")
        await asyncio.sleep(2.0)
        clicked = await self.evaluate(
            r"""
            (() => {
              const textOf = (el) => (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || el.title || '').trim().replace(/\s+/g, ' ');
              const candidates = Array.from(document.querySelectorAll('button,a,input,[role="button"]'));
              const target = candidates.find((el) => textOf(el) === '캘린더 피드' || /calendar feed/i.test(textOf(el)));
              if (!target) return false;
              target.click();
              return true;
            })()
            """
        )
        if not clicked:
            raise LiveCommandError("calendar feed button was not found")
        await asyncio.sleep(1.0)
        feed_url = await self.evaluate(
            r"""
            (() => {
              const input = document.querySelector('#calendar-feed-url-input') || Array.from(document.querySelectorAll('input,textarea')).find((el) => String(el.value || '').includes('/feeds/calendars/') && String(el.value || '').endsWith('.ics'));
              if (input && input.value) return input.value;
              const link = Array.from(document.querySelectorAll('a')).find((el) => String(el.href || '').includes('/feeds/calendars/') && String(el.href || '').endsWith('.ics'));
              return link ? link.href : '';
            })()
            """
        )
        return str(feed_url or "")

    async def _canvas_session_ready(self) -> bool:
        try:
            value = await self.evaluate(
                f"""
                (async () => {{
                  const controller = new AbortController();
                  const timer = setTimeout(() => controller.abort(), 5000);
                  try {{
                    const r = await fetch({json.dumps(CANVAS_ORIGIN + '/api/v1/users/self/profile')}, {{credentials: 'include', signal: controller.signal}});
                    return r.ok;
                  }} catch (_) {{
                    return false;
                  }} finally {{
                    clearTimeout(timer);
                  }}
                }})()
                """,
                timeout=8,
            )
            return bool(value)
        except LiveCommandError:
            return False

    async def play_url(self, url: str, *, until_end: bool = False, seconds: float | None = None) -> dict[str, Any]:
        self._network_seen = {"video_mp4_partial_content_seen": False}
        self._media_seen = {"play": False, "pause": False, "duration_changed": False}
        self._duration = None
        client = self._require_client()
        client.event_callback = self._on_event
        await self.goto(url)
        await asyncio.sleep(2.0)
        await self.evaluate(_MEDIA_INSTRUMENTATION_SCRIPT)
        await self.evaluate("window.__kuLmsMediaPlay && window.__kuLmsMediaPlay()")
        if until_end:
            await self._wait_until_media_complete(max_seconds=self.options.timeout_seconds)
        elif seconds:
            await asyncio.sleep(seconds)
        else:
            await asyncio.sleep(5.0)
        await client.drain_events()
        return {
            "video_mp4_partial_content_seen": self._network_seen.get("video_mp4_partial_content_seen", False),
            "media_events": dict(self._media_seen),
            "observed_duration_seconds": self._duration,
            "completed": bool(self._media_seen.get("pause") and (until_end or self._duration is not None)),
        }

    async def _wait_until_media_complete(self, max_seconds: float) -> None:
        deadline = time.monotonic() + max_seconds
        client = self._require_client()
        while time.monotonic() < deadline:
            await client.drain_events()
            if self._network_seen.get("video_mp4_partial_content_seen") and self._duration is not None and self._duration <= 2.0:
                return
            status = await self.evaluate(
                """
                (() => {
                  const v = document.querySelector('video');
                  return v ? {paused: v.paused, ended: v.ended, currentTime: v.currentTime || 0, duration: v.duration || null} : null;
                })()
                """,
                timeout=10,
            )
            if isinstance(status, dict):
                duration = status.get("duration")
                current = status.get("currentTime") or 0
                if isinstance(duration, (int, float)) and duration > 0:
                    self._duration = float(duration)
                    if status.get("ended") or current >= duration - 1:
                        self._media_seen["pause"] = True
                        return
            await asyncio.sleep(2.0)

    async def _submit_credentials(self) -> None:
        user = json.dumps(self.config.user_id)
        pwd = json.dumps(self.config.password)
        await self.evaluate(
            f"""
            (() => {{
              const id = document.querySelector('#one_id') || document.querySelector('input[name="one_id"]') || document.querySelector('input[type="text"]');
              const pwd = document.querySelector('#password,input[name="user_password"],input[type="password"]');
              if (!id || !pwd) return 'missing-inputs';
              id.focus(); id.value = {user}; id.dispatchEvent(new Event('input', {{bubbles:true}})); id.dispatchEvent(new Event('change', {{bubbles:true}}));
              pwd.focus(); pwd.value = {pwd}; pwd.dispatchEvent(new Event('input', {{bubbles:true}})); pwd.dispatchEvent(new Event('change', {{bubbles:true}}));
              const button = document.querySelector('button[type="submit"],input[type="submit"],button, .login_btn, .btn_login');
              setTimeout(() => {{ if (button) button.click(); else if (pwd.form) pwd.form.submit(); }}, 0);
              return 'submitted';
            }})()
            """
        )

    async def _click_login_candidate(self) -> None:
        await self.evaluate(
            """
            (() => {
              const candidates = Array.from(document.querySelectorAll('a,button,input[type="button"],input[type="submit"]'));
              const target = candidates.find((el) => /로그인|login|portal|포털|kupid/i.test(el.innerText || el.value || el.getAttribute('aria-label') || ''));
              if (target) { setTimeout(() => target.click(), 0); return 'clicked'; }
              return 'none';
            })()
            """
        )

    def _on_event(self, event: dict[str, Any]) -> None:
        method = event.get("method")
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        if method == "Network.responseReceived":
            response = params.get("response") if isinstance(params.get("response"), dict) else {}
            mime = str(response.get("mimeType") or "").casefold()
            status = int(response.get("status") or 0)
            if "video/mp4" in mime and status == 206:
                self._network_seen["video_mp4_partial_content_seen"] = True
        if method == "Media.playerEvent":
            event = params.get("event") if isinstance(params.get("event"), dict) else {}
            name = str(event.get("value") or event.get("event") or event.get("name") or "").casefold()
            if "play" in name:
                self._media_seen["play"] = True
            if "pause" in name or "ended" in name:
                self._media_seen["pause"] = True
            if "duration" in name:
                self._media_seen["duration_changed"] = True
        if method == "Media.playerPropertiesChanged":
            properties = params.get("properties") or []
            for prop in properties:
                if not isinstance(prop, dict):
                    continue
                name = str(prop.get("name") or "").casefold()
                value = prop.get("value")
                if "duration" in name:
                    self._media_seen["duration_changed"] = True
                    try:
                        self._duration = float(value)
                    except (TypeError, ValueError):
                        pass
        if method == "Runtime.consoleAPICalled":
            values = params.get("args") or []
            text = " ".join(str(arg.get("value", "")) for arg in values if isinstance(arg, dict))
            if text.startswith("KU_LMS_MEDIA_EVENT:"):
                _, name, value = (text.split(":", 2) + [""])[:3]
                if name in self._media_seen:
                    self._media_seen[name] = True
                if name == "duration_changed":
                    try:
                        self._duration = float(value)
                    except ValueError:
                        pass

    def _require_client(self) -> "_CdpClient":
        if not self._client:
            raise LiveCommandError("browser session is not active")
        return self._client


class _CdpClient:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self._next_id = 1
        self.event_callback: Any | None = None

    @classmethod
    async def connect(cls, ws_url: str) -> "_CdpClient":
        try:
            import websockets  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when optional dep is absent
            raise LiveCommandError("live mode requires the 'websockets' package") from exc
        return cls(await websockets.connect(ws_url, max_size=32 * 1024 * 1024))

    async def send(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 60.0) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        await self.websocket.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(self.websocket.recv(), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError as exc:
                raise LiveCommandError(f"CDP command timed out: {method}") from exc
            message = json.loads(raw)
            if message.get("id") == msg_id:
                if "error" in message:
                    raise LiveCommandError(redact_text(message["error"]))
                return message.get("result", {})
            if message.get("method") == "Page.javascriptDialogOpening":
                await self._accept_dialog()
            if self.event_callback:
                self.event_callback(message)
        raise LiveCommandError(f"CDP command timed out: {method}")

    async def _accept_dialog(self) -> None:
        msg_id = self._next_id
        self._next_id += 1
        await self.websocket.send(json.dumps({"id": msg_id, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}))

    async def drain_events(self) -> None:
        while True:
            try:
                raw = await asyncio.wait_for(self.websocket.recv(), timeout=0.05)
            except asyncio.TimeoutError:
                return
            message = json.loads(raw)
            if message.get("method") == "Page.javascriptDialogOpening":
                await self._accept_dialog()
            if self.event_callback:
                self.event_callback(message)

    async def close(self) -> None:
        await self.websocket.close()


_MEDIA_INSTRUMENTATION_SCRIPT = r"""
(() => {
  if (window.__kuLmsMediaPlay) return true;
  const attach = () => {
    const v = document.querySelector('video');
    if (!v) return false;
    const emit = (name) => console.log('KU_LMS_MEDIA_EVENT:' + name + ':' + (v.duration || ''));
    v.addEventListener('play', () => emit('play'));
    v.addEventListener('pause', () => emit('pause'));
    v.addEventListener('durationchange', () => emit('duration_changed'));
    window.__kuLmsMediaPlay = () => v.play().catch(() => false);
    emit('duration_changed');
    return true;
  };
  if (!attach()) {
    const timer = setInterval(() => { if (attach()) clearInterval(timer); }, 500);
  }
  return true;
})()
"""


def _default_chrome_path() -> str | None:
    for candidate in (
        "/usr/lib64/chromium-browser/headless_shell",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if Path(candidate).exists():
            return candidate
    return shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("google-chrome")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_page_ws(port: int, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            page_url = _open_new_page(port)
            if page_url:
                return page_url
        except Exception as exc:  # noqa: BLE001 - redacted below
            last_error = redact_text(str(exc))
        time.sleep(0.25)
    raise LiveCommandError(f"browser DevTools endpoint did not become ready: {last_error}")


def _open_new_page(port: int) -> str:
    base = f"http://127.0.0.1:{port}"
    encoded = urllib.parse.quote("about:blank", safe="")
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(f"{base}/json/new?{encoded}", method=method)
            with urllib.request.urlopen(req, timeout=2) as response:  # noqa: S310 - localhost only
                data = json.loads(response.read().decode("utf-8"))
            if data.get("webSocketDebuggerUrl"):
                return str(data["webSocketDebuggerUrl"])
        except urllib.error.HTTPError:
            continue
    with urllib.request.urlopen(f"{base}/json", timeout=2) as response:  # noqa: S310 - localhost only
        pages = json.loads(response.read().decode("utf-8"))
    for page in pages:
        if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
            return str(page["webSocketDebuggerUrl"])
    raise LiveCommandError("no DevTools page target was available")
