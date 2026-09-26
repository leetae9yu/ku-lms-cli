"""Native media events for one owned CDP page; no progress writes or seeking."""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from .errors import LiveCommandError, LoginExpired

class PlayerOptions(Protocol):
    @property
    def timeout_seconds(self) -> float: ...


class PlayerClient(Protocol):
    event_callback: Callable[[dict[str, Any]], None] | None
    connected: bool

    async def send(self, method: str, params: dict[str, Any] | None = None,
                   *, timeout: float = 60.0) -> dict[str, Any]: ...


class PlayerSession(Protocol):
    @property
    def options(self) -> PlayerOptions: ...

    def _require_client(self) -> PlayerClient: ...


class MediaPlayer:
    """Own each navigation's subscription and discard events from older videos."""

    def __init__(self, session: PlayerSession) -> None:
        self.session = session

    async def play(self, url: str, update: Callable[[float, bool], None],
                   *, seconds: float | None = None, timeout: float | None = None) -> bool:
        client = self.session._require_client()
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()
        ended: asyncio.Future[None] = loop.create_future()
        generation = uuid.uuid4().hex
        previous = client.event_callback

        def fail(error: LiveCommandError) -> None:
            if not ended.done():
                ended.set_exception(error)
            if not ready.done():
                ready.set_result(None)

        def receive(event: dict[str, Any]) -> None:
            if previous:
                previous(event)
            method = event.get("method")
            params = event.get("params", {})
            if method in {"Inspector.detached", "Inspector.targetCrashed", "CDP.disconnected"}:
                fail(LiveCommandError("player browser disconnected"))
            if method == "Runtime.executionContextCreated" and params.get("context", {}).get("auxData", {}).get("isDefault"):
                # Cross-site player navigations get a fresh renderer without the page binding.
                rebind = asyncio.ensure_future(client.send("Runtime.addBinding", {"name": "kuLmsPlayback"}))
                rebind.add_done_callback(
                    lambda task: task.cancelled() or task.exception() is None
                    or fail(LiveCommandError("player event binding failed")))
            if method == "Network.responseReceived":
                response = params.get("response", {})
                if response.get("status") in {401, 403} and params.get("type") in {"Document", "XHR", "Fetch"}:
                    fail(LoginExpired("player login expired or access denied"))
            if method != "Runtime.bindingCalled" or params.get("name") != "kuLmsPlayback":
                return
            try:
                data = json.loads(params["payload"])
                if data.get("generation") != generation:
                    return
                name = data["event"]
                position = float(data.get("position", 0))
                if not math.isfinite(position) or position < 0:
                    raise ValueError("invalid position")
            except (ValueError, TypeError, KeyError):
                fail(LiveCommandError("invalid player event"))
                return
            if ended.done():
                return
            update(position, bool(data.get("paused", True)))
            if name == "login_expired":
                fail(LoginExpired("player login expired"))
            elif name == "error":
                fail(LiveCommandError("native media playback error"))
            elif name == "ratechange" and data.get("rate") != 1:
                fail(LiveCommandError("player changed playback rate from normal speed"))
            elif name == "playing" and not ready.done():
                ready.set_result(None)
            elif name == "ended" and data.get("trusted") is True and data.get("ended") is True:
                ended.set_result(None)
                if not ready.done():
                    ready.set_result(None)

        client.event_callback = receive
        script_id = None
        try:
            await client.send("Runtime.addBinding", {"name": "kuLmsPlayback"})
            script = _PLAYER_SCRIPT.replace("__GENERATION__", json.dumps(generation))
            registered = await client.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})
            script_id = registered["identifier"]
            navigation = await client.send("Page.navigate", {"url": url})
            if navigation.get("errorText"):
                raise LiveCommandError("recording navigation failed")
            await asyncio.wait_for(ready, self.session.options.timeout_seconds)
            if seconds is not None and not ended.done():
                done, _ = await asyncio.wait({ended}, timeout=seconds)
                if not done:
                    return False
            else:
                await asyncio.wait_for(asyncio.shield(ended), timeout)
            await ended
            return True
        except asyncio.TimeoutError as exc:
            raise LiveCommandError("player did not start or finish before timeout") from exc
        finally:
            client.event_callback = previous
            # Retrieve an error even if navigation failed before we could await it.
            if ended.done() and not ended.cancelled():
                ended.exception()
            else:
                ended.cancel()
            if script_id is not None and client.connected:
                await client.send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": script_id})


_PLAYER_SCRIPT = r"""
(() => {
  if (window !== window.top) return;
  const generation = __GENERATION__;
  let selected = null, launched = false;
  const clickedAt = new WeakMap();
  const send = (event, v, trusted = false) => window.kuLmsPlayback(JSON.stringify({
    generation, event, trusted, position: v ? v.currentTime : 0,
    paused: v ? v.paused : true, ended: v ? v.ended : false,
    rate: v ? v.playbackRate : 1
  }));
  const choose = v => v instanceof HTMLVideoElement &&
    (v.currentSrc || v.getAttribute('src')) && v.getBoundingClientRect().width > 0 &&
    v.getBoundingClientRect().height > 0 && (!selected || selected === v);
  const start = v => {
    if (!choose(v)) return;
    selected = v;
    v.playbackRate = 1;
    v.play().catch(() => send('error', v));
  };
  const playerAsset = v => /\/uniplayer\//.test(v.currentSrc || v.getAttribute('src') || '');
  for (const name of ['playing', 'timeupdate', 'pause', 'ended', 'error', 'ratechange']) {
    document.addEventListener(name, e => {
      const v = e.target;
      if (!choose(v) || !e.isTrusted || playerAsset(v)) return;
      selected = v;
      send(name, v, e.isTrusted);
    }, true);
  }
  document.addEventListener('loadedmetadata', e => start(e.target), true);
  const inspect = () => {
    if (document.querySelector('input[type="password"]')) {
      send('login_expired', null); return;
    }
    if (selected && !selected.isConnected) selected = null;
    if (!selected) {
      const video = Array.from(document.querySelectorAll('video')).find(choose);
      if (video) start(video);
    }
    for (const button of document.querySelectorAll('.vc-front-screen-play-btn, .vc-front-mixed-play-btn, .vc-front-multi-play-btn, .confirm-ok-btn')) {
      if (button.getBoundingClientRect().width > 0 && Date.now() - (clickedAt.get(button) || 0) > 2000) {
        clickedAt.set(button, Date.now()); button.click();
      }
    }
    if (selected || launched) return;
    const form = document.querySelector('form#tool_form, form[action*=learningx], form[action*=lti]');
    if (form) {
      launched = true; form.target = '_self';
      HTMLFormElement.prototype.submit.call(form); return;
    }
    const frame = Array.from(document.querySelectorAll('iframe[src]')).find(f =>
      f.src && !f.src.startsWith('about:') && !f.src.includes('post_message_forwarding'));
    if (frame) { launched = true; location.assign(frame.src); }
  };
  new MutationObserver(inspect).observe(document, {childList: true, subtree: true});
  setInterval(inspect, 1000);
  document.addEventListener('DOMContentLoaded', inspect, {once: true});
})();
"""
