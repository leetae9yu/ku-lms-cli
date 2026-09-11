import asyncio

import pytest

from ku_lms_cli.live import CdpBrowserSession, LiveOptions, _recording_candidates, _recording_accessible
from ku_lms_cli.config import KuLmsConfig


@pytest.mark.parametrize("metadata", [
    {"published": False}, {"locked_for_user": True}, {"state": "locked"},
    {"unlock_at": "2099-01-01T00:00:00Z"}, {"lock_at": "2000-01-01T00:00:00Z"},
    {"content_details": {"locked_for_user": True}},
    {"content_details": {"unlock_at": "2099-01-01T00:00:00Z"}},
    {"unlock_at": "unparseable"},
])
def test_unavailable_recordings_are_excluded(metadata):
    assert _recording_accessible(metadata) is False


def test_available_recordings_remain_playable():
    assert _recording_accessible({"published": True, "unlock_at": "2000-01-01T00:00:00Z", "lock_at": "2099-01-01T00:00:00Z"}) is True
    assert _recording_accessible({}) is True


def test_truncated_module_items_are_discovered_once_from_items_api():
    class Session(CdpBrowserSession):
        def __init__(self):
            super().__init__(KuLmsConfig("fixture", "fixture"), LiveOptions())
            self.paths = []

        async def fetch_json(self, path_or_url):
            self.paths.append(path_or_url)
            one = {"type": "ExternalTool", "title": "one", "html_url": "one"}
            two = {"type": "ExternalTool", "title": "two", "html_url": "two"}
            if "/modules/" in path_or_url:
                return [one, two]
            return [{"id": 1, "name": "week", "items_count": 2, "items": [one]}]

    session = Session()
    queue = asyncio.run(_recording_candidates(session, {"id": 1, "name": "fixture"}))
    assert [item["title"] for item in queue] == ["one", "two"]
    assert len(session.paths) == 2
