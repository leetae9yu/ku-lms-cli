"""Domain models for KU LMS CLI outputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    url: str = ""


@dataclass(frozen=True)
class Material:
    id: str
    course_id: str
    title: str
    filename: str = ""
    url: str = ""


@dataclass(frozen=True)
class Assignment:
    id: str
    course_id: str
    title: str
    due_at: str = ""
    attachments: list[Material] = field(default_factory=list)


@dataclass(frozen=True)
class Recording:
    id: str
    course_id: str
    title: str
    url: str = ""
    duration: str = ""


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    date: str
    type: str = "assignment"
    course: str = ""
    due_at: str = ""


def to_dicts(items: list[Any]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]
