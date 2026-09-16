"""Compact workload classification for live LMS data."""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Protocol, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import KuLmsConfig
from .errors import LiveCommandError
from .live import (
    CdpBrowserSession,
    LiveOptions,
    run_live,
)


@dataclass(frozen=True)
class WorkloadRequest:
    as_of: date
    lookahead_days: int
    timezone: ZoneInfo


class WorkloadRow(TypedDict, total=False):
    course: str
    title: str
    due_at: str
    submitted_at: str
    submission_workflow_state: str


class WorkloadItem(TypedDict):
    course: str
    title: str
    due: str


class WorkloadSummary(TypedDict):
    as_of: str
    timezone: str
    overdue: list[WorkloadItem]
    due_today: list[WorkloadItem]
    completed_before_cutoff: int
    upcoming: list[WorkloadItem]


class WorkloadBrowserSession(Protocol):
    async def __aenter__(self) -> "WorkloadBrowserSession": ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    async def login(self) -> None: ...

    async def fetch_json(self, path_or_url: str) -> object: ...


class LiveWorkloadProvider:
    def __init__(
        self,
        config: KuLmsConfig,
        options: LiveOptions | None = None,
        session_factory: Callable[[], WorkloadBrowserSession] | None = None,
    ) -> None:
        self.config: KuLmsConfig = config
        self.options: LiveOptions = options or LiveOptions()
        self._session_factory: Callable[[], WorkloadBrowserSession] = session_factory or (
            lambda: CdpBrowserSession(config, self.options)
        )

    def workload(self, request: WorkloadRequest) -> WorkloadSummary:
        return run_live(self._workload_async(request))

    async def _workload_async(self, request: WorkloadRequest) -> WorkloadSummary:
        rows: list[WorkloadRow] = []
        async with self._session_factory() as session:
            await session.login()
            courses_data = await session.fetch_json("/api/v1/courses?per_page=100&enrollment_state=active")
            if not isinstance(courses_data, list):
                raise LiveCommandError("courses API returned an unexpected shape")
            courses = [
                item
                for item in courses_data
                if isinstance(item, dict) and item.get("id") and item.get("name")
            ]
            if not courses:
                raise LiveCommandError("no active courses found")
            for course in courses:
                assignments = await session.fetch_json(
                    f"/api/v1/courses/{course['id']}/assignments?per_page=100&include[]=submission"
                )
                if not isinstance(assignments, list):
                    raise LiveCommandError("assignment API returned an unexpected shape")
                for item in assignments:
                    if isinstance(item, dict):
                        submission = item.get("submission")
                        submission = submission if isinstance(submission, dict) else {}
                        rows.append(
                            WorkloadRow(
                                course=_compact_course_name(str(course.get("name") or "")),
                                title=str(item.get("name") or item.get("title") or ""),
                                due_at=str(item.get("due_at") or ""),
                                submitted_at=str(submission.get("submitted_at") or ""),
                                submission_workflow_state=str(submission.get("workflow_state") or ""),
                            )
                        )

            planner_end = request.as_of + timedelta(days=request.lookahead_days)
            planner_params = urllib.parse.urlencode(
                {
                    "per_page": 100,
                    "start_date": request.as_of.isoformat(),
                    "end_date": planner_end.isoformat(),
                }
            )
            planner_items = await session.fetch_json(f"/api/v1/planner/items?{planner_params}")
            todo_items = await session.fetch_json("/api/v1/users/self/todo?per_page=100")
            if not isinstance(planner_items, list) or not isinstance(todo_items, list):
                raise LiveCommandError("workload cross-check API returned an unexpected shape")

        for item in planner_items:
            if not isinstance(item, dict):
                continue
            plannable = item.get("plannable")
            plannable = plannable if isinstance(plannable, dict) else {}
            submissions = item.get("submissions")
            submissions = submissions if isinstance(submissions, dict) else {}
            item_type = str(item.get("plannable_type") or plannable.get("type") or "")
            submitted = bool(submissions.get("submitted") or submissions.get("submitted_at"))
            if item_type not in {"assignment", "quiz"} or submitted:
                continue
            rows.append(
                WorkloadRow(
                    course=_compact_course_name(str(item.get("context_name") or "")),
                    title=str(plannable.get("title") or plannable.get("name") or item.get("title") or ""),
                    due_at=str(item.get("plannable_date") or plannable.get("due_at") or ""),
                    submission_workflow_state="unsubmitted",
                    submitted_at="",
                )
            )
        for item in todo_items:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") != "submitting":
                continue
            assignment = item.get("assignment")
            assignment = assignment if isinstance(assignment, dict) else {}
            rows.append(
                WorkloadRow(
                    course=_compact_course_name(str(item.get("context_name") or "")),
                    title=str(assignment.get("name") or assignment.get("title") or item.get("type") or ""),
                    due_at=str(assignment.get("due_at") or ""),
                    submission_workflow_state="unsubmitted",
                    submitted_at="",
                )
            )
        return summarize_workload(rows, request)


def _compact_course_name(name: str) -> str:
    compact = re.sub(r"^\d{3}R\s+\([^)]*\)", "", name.strip())
    compact = re.sub(r"^(?:\[[^]]+\]\s*)+", "", compact)
    compact = re.sub(r"\((?:영강|English)\)", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\([^)]*[A-Za-z][^)]*\)", "", compact)
    compact = re.sub(r"-\d+분반$", "", compact)
    return compact.strip(" -") or name.strip()


def parse_workload_request(as_of: str, lookahead_days: int, timezone_name: str) -> WorkloadRequest:
    if lookahead_days < 0:
        raise LiveCommandError("--lookahead must be zero or greater")
    try:
        parsed_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise LiveCommandError(f"unknown timezone: {timezone_name}") from exc
    if as_of:
        try:
            parsed_date = date.fromisoformat(as_of)
        except ValueError as exc:
            raise LiveCommandError("--as-of must use YYYY-MM-DD") from exc
    else:
        parsed_date = datetime.now(parsed_timezone).date()
    return WorkloadRequest(as_of=parsed_date, lookahead_days=lookahead_days, timezone=parsed_timezone)


def summarize_workload(rows: list[WorkloadRow], request: WorkloadRequest) -> WorkloadSummary:
    start = datetime.combine(request.as_of, time.min, request.timezone)
    today_end = start + timedelta(days=1)
    upcoming_end = today_end + timedelta(days=request.lookahead_days)
    completed_before_cutoff = 0
    buckets: dict[str, list[tuple[datetime, WorkloadItem]]] = {
        "overdue": [],
        "due_today": [],
        "upcoming": [],
    }
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        due_at = str(row.get("due_at") or "")
        if not due_at:
            continue
        try:
            due = datetime.fromisoformat(due_at.replace("Z", "+00:00")).astimezone(request.timezone)
        except ValueError as exc:
            raise LiveCommandError("LMS returned an invalid assignment deadline") from exc
        course = str(row.get("course") or "")
        title = str(row.get("title") or "").strip()
        key = (course.casefold(), title.casefold(), due.isoformat())
        if key in seen:
            continue
        seen.add(key)

        submitted_at = str(row.get("submitted_at") or "")
        workflow = str(row.get("submission_workflow_state") or "").casefold()
        submitted = bool(submitted_at) or workflow in {"submitted", "graded", "complete"}
        if submitted:
            if due < today_end:
                completed_before_cutoff += 1
            continue

        item = WorkloadItem(course=course, title=title, due=due.strftime("%Y-%m-%d %H:%M"))
        if due < start:
            buckets["overdue"].append((due, item))
        elif due < today_end:
            buckets["due_today"].append((due, item))
        elif due < upcoming_end:
            buckets["upcoming"].append((due, item))

    for values in buckets.values():
        values.sort(key=lambda value: (value[0], value[1]["course"], value[1]["title"]))

    return {
        "as_of": request.as_of.isoformat(),
        "timezone": request.timezone.key,
        "overdue": [item for _, item in buckets["overdue"]],
        "due_today": [item for _, item in buckets["due_today"]],
        "completed_before_cutoff": completed_before_cutoff,
        "upcoming": [item for _, item in buckets["upcoming"]],
    }
