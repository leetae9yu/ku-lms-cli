"""Deterministic CDP boundary; real MediaPlayer and queue logic run above it."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from ku_lms_cli.config import KuLmsConfig
from ku_lms_cli.live import LiveLmsProvider, LiveOptions


class EventClient:
    def __init__(self):
        self.event_callback: Callable[[dict[str, Any]], None] | None = None
        self.connected = True
        self.generation = ""
        self.navigations = asyncio.Queue()

    async def send(self, method, params=None, *, timeout=60.0):
        params = params or {}
        if method == "Page.addScriptToEvaluateOnNewDocument":
            match = re.search(r'const generation = "([a-f0-9]+)"', params["source"])
            assert match is not None
            self.generation = match[1]
            return {"identifier": "script"}
        if method == "Page.navigate":
            self.navigations.put_nowait(params["url"])
            self.emit("playing", paused=False)
        return {}

    def emit(self, event, *, generation=None, trusted=True, ended=False, paused=True):
        assert self.event_callback is not None
        self.event_callback({"method": "Runtime.bindingCalled", "params": {
            "name": "kuLmsPlayback", "payload": json.dumps({
                "generation": generation or self.generation, "event": event,
                "position": 0.2, "trusted": trusted, "ended": ended,
                "paused": paused, "rate": 1,
            }),
        }})


class QueueSession:
    def __init__(self):
        self.config = KuLmsConfig("fixture", "fixture")
        self.options = LiveOptions(timeout_seconds=2)
        self.client = EventClient()
        self._client = self.client
        self.entries = 0
        self.logins = 0
        self.discoveries = 0
        self.closed = asyncio.Event()

    def _require_client(self):
        return self.client

    async def __aenter__(self):
        self.entries += 1
        return self

    async def __aexit__(self, *args):
        self.closed.set()

    async def login(self):
        self.logins += 1

    async def fetch_json(self, path_or_url):
        if path_or_url.startswith("/api/v1/courses?"):
            return [{"id": "course", "name": "fixture"}]
        self.discoveries += 1
        return [{"name": "week", "items": [
            {"type": "ExternalTool", "title": title, "html_url": title}
            for title in ["one", "two"]
        ]}]

    def provider(self):
        return LiveLmsProvider(self.config, self.options, session_factory=lambda: self)
