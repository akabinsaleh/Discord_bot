"""
MusicGang desktop app
=====================

A native window (pywebview, rendered by the Edge WebView2 engine built into
Windows) showing ui.html: queue on the left, player in the middle, search on
the right. No browser chrome, no tabs — it's just the app window.

The page itself is served by the bot's local control API (bot.py, port
WEB_PORT, default 27432), so the window talks to the bot on this PC only.
Until the bot is up, the window shows a "starting" screen and keeps retrying.

The .pyw extension means Windows runs it with pythonw.exe: no console window.
"""

import os
import socket
import threading
import time

import webview

PORT = int(os.getenv("WEB_PORT", "27432"))
URL = f"http://127.0.0.1:{PORT}/"

WAITING_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{height:100%;margin:0;background:#000;color:#fff;font-family:"Segoe UI Variable Display","Segoe UI",system-ui,sans-serif;
  display:grid;place-items:center;text-align:center}
  h1{font-size:34px;font-weight:700;letter-spacing:-.02em;margin:0 0 12px}
  p{color:#a3a3a3;font-size:18px;margin:0}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#fff;margin:0 4px;animation:b 1.2s infinite}
  .dot:nth-child(2){animation-delay:.2s}.dot:nth-child(3){animation-delay:.4s}
  @keyframes b{0%,80%,100%{opacity:.2}40%{opacity:1}}
</style></head><body><div>
  <h1>MusicGang</h1>
  <p>Waiting for the bot to start…</p>
  <p style="margin-top:18px"><span class="dot"></span><span class="dot"></span><span class="dot"></span></p>
  <p style="margin-top:26px;font-size:14px;color:#6b6b6b">Not starting? Run start.bat.</p>
</div></body></html>
"""


def bot_is_up() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def wait_then_load(window: webview.Window) -> None:
    """Poll the API port; once it answers, swap the waiting screen for the real UI."""
    while not bot_is_up():
        time.sleep(1)
    window.load_url(URL)


if __name__ == "__main__":
    window = webview.create_window(
        "MusicGang",
        url=URL if bot_is_up() else None,
        html=None if bot_is_up() else WAITING_HTML,
        width=1600,
        height=1000,
        min_size=(1100, 720),
        background_color="#000000",
        text_select=False,
    )
    if not bot_is_up():
        threading.Thread(target=wait_then_load, args=(window,), daemon=True).start()
    webview.start()
