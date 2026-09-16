"""Watch a CDP page until its main video ends or the page/browser disappears."""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

PORT = sys.argv[1]


def find_page():
    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5))
    except Exception:
        return None
    return next((t for t in tabs if t["type"] == "page" and "kucom" in t["url"]), None)


def video_state(ws_url):
    ws = websocket.create_connection(ws_url, timeout=15, suppress_origin=True)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "(() => { const v=[...document.querySelectorAll('video')].find(v=>v.duration>60); return v ? {t:v.currentTime,d:v.duration,ended:v.ended} : null; })()",
                "returnByValue": True,
            },
        }))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m["result"]["result"].get("value")
    finally:
        ws.close()


while True:
    page = find_page()
    if page is None:
        print("PAGE_GONE", flush=True)
        break
    try:
        state = video_state(page["webSocketDebuggerUrl"])
    except Exception:
        state = None
    if state is None:
        print("NO_VIDEO", flush=True)
        break
    if state.get("ended"):
        print("VIDEO_ENDED", flush=True)
        break
    time.sleep(60)
