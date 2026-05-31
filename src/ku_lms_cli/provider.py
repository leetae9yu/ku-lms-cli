"""Fixture-backed LMS provider abstractions."""
from __future__ import annotations

import json
from pathlib import Path

from .domain import Assignment, Course, Material, Recording
from .paths import PathPolicy
from .redaction import redact_text


class FixtureProvider:
    """A deterministic provider used until live discovery selects API/browser providers."""

    def __init__(self, fixture_path: Path | None = None) -> None:
        self.fixture_path = fixture_path
        self._data = self._load_fixture(fixture_path)

    def _load_fixture(self, fixture_path: Path | None) -> dict:
        if fixture_path and fixture_path.exists():
            return json.loads(fixture_path.read_text(encoding="utf-8"))
        return {
            "courses": [{"id": "sample-course", "name": "Sample Course", "url": "https://mylms.korea.ac.kr/courses/sample"}],
            "materials": [
                {"id": "sample-material", "course_id": "sample-course", "title": "Sample Lecture Material", "filename": "sample-material.txt", "url": "https://mylms.korea.ac.kr/files/sample"}
            ],
            "assignments": [
                {
                    "id": "sample-assignment",
                    "course_id": "sample-course",
                    "title": "Sample Assignment",
                    "due_at": "2099-12-31T23:59:00+09:00",
                    "attachments": [
                        {"id": "sample-assignment-file", "course_id": "sample-course", "title": "Sample Assignment Attachment", "filename": "sample-assignment.txt", "url": "https://mylms.korea.ac.kr/files/assignment-sample"}
                    ],
                }
            ],
            "recordings": [
                {"id": "sample-recording", "course_id": "sample-course", "title": "Sample Recording", "url": "https://mylms.korea.ac.kr/recordings/sample", "duration": "00:10:00"}
            ],
        }

    def courses(self) -> list[Course]:
        return [Course(**item) for item in self._data.get("courses", [])]

    def materials(self) -> list[Material]:
        return [Material(**item) for item in self._data.get("materials", [])]

    def assignments(self) -> list[Assignment]:
        assignments: list[Assignment] = []
        for item in self._data.get("assignments", []):
            attachments = [Material(**attachment) for attachment in item.get("attachments", [])]
            assignments.append(Assignment(id=item["id"], course_id=item["course_id"], title=item["title"], due_at=item.get("due_at", ""), attachments=attachments))
        return assignments

    def recordings(self) -> list[Recording]:
        return [Recording(**item) for item in self._data.get("recordings", [])]

    def download_material(self, material_id: str, policy: PathPolicy) -> Path:
        material = _find(self.materials(), material_id)
        filename = material.filename or f"{material.id}.txt"
        target = policy.resolve(policy.downloads_dir / filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redact_text(f"Placeholder for {material.title}\nSource: {material.url}\n"), encoding="utf-8")
        return target

    def download_assignment_attachment(self, attachment_id: str, policy: PathPolicy) -> Path:
        for assignment in self.assignments():
            for attachment in assignment.attachments:
                if attachment.id == attachment_id:
                    filename = attachment.filename or f"{attachment.id}.txt"
                    target = policy.resolve(policy.downloads_dir / filename)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(redact_text(f"Placeholder for {attachment.title}\nSource: {attachment.url}\n"), encoding="utf-8")
                    return target
        raise KeyError(attachment_id)

    def playback_plan(self, recording_id: str, keepalive: bool = False) -> dict[str, str | bool]:
        recording = _find(self.recordings(), recording_id)
        return {
            "recording_id": recording.id,
            "title": recording.title,
            "keepalive": keepalive,
            "side_effects_accepted": True,
            "implementation": "browser-provider-pending-live-discovery",
        }


def _find(items, item_id: str):
    for item in items:
        if item.id == item_id:
            return item
    raise KeyError(item_id)
