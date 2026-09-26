"""Independent, local-only Chromium fixtures through the production CDP surface."""
import asyncio
import base64
import contextlib
import http.server
import io
import threading
import wave
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

from ku_lms_cli.config import KuLmsConfig
from ku_lms_cli.errors import LiveCommandError, LoginExpired
from ku_lms_cli.live import CdpBrowserSession, LiveOptions
from ku_lms_cli.media import MediaPlayer


def wav_bytes(frames=4000):
    audio = io.BytesIO()
    with wave.open(audio, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * frames)
    return audio.getvalue()


def video_page(extra="", video_attributes=""):
    source = "data:audio/wav;base64," + base64.b64encode(wav_bytes()).decode()
    html = f'<html><body>{extra}<video {video_attributes} src="{source}"></video></body></html>'
    return "data:text/html," + quote(html)


@contextlib.contextmanager
def serve(routes):
    """Serve path -> (content type, body) from 127.0.0.1; bodies may use {port}."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            content_type, body = routes[self.path]
            payload = body.replace("{port}", str(port)).encode() if isinstance(body, str) else body
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


def browser():
    return CdpBrowserSession(KuLmsConfig("fixture", "fixture"), LiveOptions(timeout_seconds=10))


def test_native_pause_does_not_finish_and_resume_ends_at_normal_speed():
    async def scenario():
        async with browser() as session:
            paused = asyncio.Event()
            playing_seen = False

            def update(position, is_paused):
                nonlocal playing_seen
                if not is_paused:
                    playing_seen = True
                if is_paused and playing_seen:
                    paused.set()

            player = MediaPlayer(session)
            url = video_page(video_attributes='onplaying="if(!window.didPause){window.didPause=true;this.pause()}"')
            task = asyncio.create_task(player.play(url, update))
            try:
                await asyncio.wait_for(paused.wait(), 10)
                state = await session.evaluate("(() => {const v=document.querySelector('video');return {paused:v.paused,ended:v.ended,rate:v.playbackRate};})()")
                assert state == {"paused": True, "ended": False, "rate": 1}
                assert not task.done()
                await session.evaluate("document.querySelector('video').dispatchEvent(new Event('ended'))")
                assert not task.done()
                await session.evaluate("document.querySelector('video').play()")
                assert await asyncio.wait_for(task, 10) is True
                state = await session.evaluate("(() => {const v=document.querySelector('video');return {ended:v.ended,rate:v.playbackRate,position:v.currentTime,duration:v.duration};})()")
                assert state["ended"] is True
                assert state["rate"] == 1
                assert state["position"] == state["duration"]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_play_button_is_clicked_again_after_player_becomes_ready():
    async def scenario():
        async with browser() as session:
            lecture = video_page()
            source = lecture.split('src%3D%22', 1)[1].split('%22', 1)[0]
            button = (
                '<div class="vc-front-screen-play-btn" style="width:40px;height:40px"></div>'
                "<script>setTimeout(() => { const b = document.querySelector('.vc-front-screen-play-btn');"
                " b.onclick = () => { b.style.display = 'none'; const v = document.createElement('video');"
                f" v.src = decodeURIComponent('{source}'); document.body.appendChild(v); }}; }}, 1500);</script>"
            )
            url = "data:text/html," + quote(f"<html><body>{button}</body></html>")
            assert await asyncio.wait_for(MediaPlayer(session).play(url, lambda *_: None), 10) is True
    asyncio.run(scenario())


def test_player_events_survive_cross_site_navigation():
    routes = {
        "/start": ("text/html", "<script>location.assign('http://localhost:{port}/lecture')</script>"),
        "/lecture": ("text/html", unquote(video_page().split(",", 1)[1])),
    }

    async def scenario(port):
        async with browser() as session:
            url = f"http://127.0.0.1:{port}/start"
            assert await asyncio.wait_for(MediaPlayer(session).play(url, lambda *_: None), 10) is True

    with serve(routes) as port:
        asyncio.run(scenario(port))


def test_player_intro_clip_does_not_complete_the_lecture():
    page = (
        '<video src="/viewer/uniplayer/intro.wav" '
        "onended=\"if (this.src.includes('intro')) { this.src = '/media/screen.wav'; this.play(); }\"></video>"
    )
    routes = {
        "/": ("text/html", f"<html><body>{page}</body></html>"),
        "/viewer/uniplayer/intro.wav": ("audio/wav", wav_bytes(2000)),
        "/media/screen.wav": ("audio/wav", wav_bytes(12000)),
    }

    async def scenario(port):
        async with browser() as session:
            positions = []
            url = f"http://127.0.0.1:{port}/"
            player = MediaPlayer(session).play(url, lambda position, _paused: positions.append(position))
            assert await asyncio.wait_for(player, 10) is True
            assert max(positions) == pytest.approx(1.5)

    with serve(routes) as port:
        asyncio.run(scenario(port))


def test_native_media_error_is_reported():
    async def scenario():
        async with browser() as session:
            url = "data:text/html," + quote('<video src="data:video/mp4;base64,bm90LXZpZGVv"></video>')
            with pytest.raises(LiveCommandError):
                await asyncio.wait_for(MediaPlayer(session).play(url, lambda *_: None), 10)
    asyncio.run(scenario())


def test_login_form_is_reported_without_credential_submission():
    async def scenario():
        async with browser() as session:
            url = "data:text/html," + quote('<input type="password">')
            with pytest.raises(LoginExpired):
                await asyncio.wait_for(MediaPlayer(session).play(url, lambda *_: None), 10)
            assert await session.evaluate("document.querySelector('input').value") == ""
    asyncio.run(scenario())


def test_idle_cdp_reader_receives_events_and_concurrent_replies():
    async def scenario():
        async with browser() as session:
            client = session._require_client()
            observed = asyncio.Event()
            await client.send("Runtime.addBinding", {"name": "fixtureEvent"})
            client.event_callback = lambda event: observed.set() if event.get("method") == "Runtime.bindingCalled" else None
            # Subscribe before causing the exact event, including while another CDP
            # evaluation is awaiting a promise. No second websocket recv is permitted.
            blocked = asyncio.create_task(session.evaluate("new Promise(resolve => { window.fixtureResolve = resolve; fixtureEvent('ready'); })"))
            await asyncio.wait_for(observed.wait(), 10)
            results = await asyncio.gather(session.evaluate("1+1"), session.evaluate("2+2"))
            assert results == [2, 4]
            await session.evaluate("fixtureResolve(42)")
            assert await asyncio.wait_for(blocked, 10) == 42
    asyncio.run(scenario())


def test_inactive_media_source_does_not_steal_lecture_events():
    async def scenario():
        async with browser() as session:
            hidden = '<video style="display:none" src="data:video/mp4;base64,bm90LXZpZGVv"></video>'
            assert await asyncio.wait_for(MediaPlayer(session).play(video_page(extra=hidden), lambda *_: None), 10) is True
    asyncio.run(scenario())


def test_failed_browser_start_removes_owned_profile(tmp_path):
    async def scenario():
        session = CdpBrowserSession(KuLmsConfig("fixture", "fixture"), LiveOptions(chrome_path=str(tmp_path / "absent")))
        with pytest.raises(OSError):
            await session.__aenter__()
        assert session._tmp is not None
        assert not Path(session._tmp.name).exists()
    asyncio.run(scenario())
