"""
MusicGang desktop app
=====================

A native window (pywebview, rendered by the Edge WebView2 engine built into
Windows) showing ui.html: queue on the left, player in the middle, search on
the right. No browser chrome, no tabs — it's just the app window.

It is also the thing that decides WHO RUNS THE BOT when several friends share
one bot over Tailscale ("MUSICGANG_PEERS" in .env):

  * On open it looks for a peer that is already hosting. Found one → this
    window just controls that bot (client).
  * Nobody hosting → this PC starts Lavalink + bot itself (host). First to
    open becomes the host.
  * The host closes its window → its bot stops. Every other open window
    notices, and the one with the lowest Tailscale IP takes over: it starts
    the bot on its PC and re-queues what was playing, so the music continues.

Every open window runs a tiny "beacon" (port WEB_PORT+1) that peers query to
see who is online and who is hosting. All of that stays inside the tailnet.

With no peers configured it simply hosts on this PC, like before.

The .pyw extension means Windows runs it with pythonw.exe: no console window.
"""

import ctypes
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview

HERE = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
except ImportError:
    pass  # python-dotenv missing — environment variables still work

# pythonw has no console, so problems would vanish silently. Log to a file.
logging.basicConfig(
    filename=HERE / "musicgang_app.log", level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

PORT = int(os.getenv("WEB_PORT", "27432"))
BEACON_PORT = PORT + 1
TOKEN = os.getenv("WEB_TOKEN", "").strip()
PEERS = [p.strip() for p in os.getenv("MUSICGANG_PEERS", "").split(",") if p.strip()]
FIXED_HOST = os.getenv("MUSICGANG_HOST", "").strip()  # old-style: always use this host, never host myself
ICON = HERE / "musicgang.ico"
CREATE_NO_WINDOW = 0x08000000

# Give the window its own taskbar identity (and icon) instead of being grouped
# under pythonw.exe with the generic Python icon.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MusicGang.App")
except Exception:
    pass  # not Windows, or shell32 unavailable — cosmetic only


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def tailscale_ip() -> str | None:
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5,
                             creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    ip = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
    return ip if ip.startswith("100.") else None


MY_IP = tailscale_ip() if PEERS else None


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def api(host: str, path: str, body: dict | None = None, timeout: float = 4) -> dict:
    """Call the bot API at host (GET when body is None). Raises on failure."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://{host}:{PORT}/api/{path}", data=data,
        method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json", "X-MusicGang-Token": TOKEN},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def beacon(host: str, timeout: float = 1.5) -> dict | None:
    """Ask a peer's window who it is and whether it hosts. None if unreachable."""
    try:
        with urllib.request.urlopen(f"http://{host}:{BEACON_PORT}/", timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return None


# Under pythonw the standard handles are invalid; a child that inherits them
# can fail with "WinError 6". Always give children explicit handles.
_QUIET = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def powershell(script: str, *, wait: bool) -> None:
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    if wait:
        subprocess.run(args, creationflags=CREATE_NO_WINDOW, timeout=120, **_QUIET)
    else:
        subprocess.Popen(args, creationflags=CREATE_NO_WINDOW, **_QUIET)


def start_local_bot() -> None:
    """Start Lavalink + bot on this PC via start.ps1 (it skips what already runs)."""
    runner = HERE / "start.ps1"
    logging.info("starting local bot via %s", runner)
    # Run the launcher hidden and keep its output in start.log: Lavalink and
    # the bot still get their own windows (start.ps1 opens those itself).
    log = open(HERE / "start.log", "ab")
    python = Path(sys.executable).with_name("python.exe")  # we run under pythonw.exe
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
         "-Python", str(python)],
        cwd=str(HERE), creationflags=CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
    )


def stop_local_bot() -> None:
    """Stop the bot, its restart loop and Lavalink on this PC."""
    powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_bot.ps1*' "
        "-or $_.CommandLine -like '*bot.py*' -or $_.CommandLine -like '*Lavalink.jar*' } "
        "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
        wait=True,
    )


# --------------------------------------------------------------------------- #
# Node: host / client state machine
# --------------------------------------------------------------------------- #
class Node:
    def __init__(self) -> None:
        self.mode = "searching"          # searching | client | host
        self.host_ip: str | None = None  # who we control (127.0.0.1 when hosting)
        self.last_state: dict | None = None
        self.started = time.time()
        self.window: webview.Window | None = None
        self.shown_url: str | None = None
        self.lock = threading.Lock()

    # ---- what the beacon tells peers --------------------------------------
    def info(self) -> dict:
        return {"ip": MY_IP, "hosting": self.mode == "host", "since": self.started,
                "mode": self.mode, "host": self.host_ip}

    # ---- UI ----------------------------------------------------------------
    def show_waiting(self, message: str, hint: str = "") -> None:
        if self.window is None:
            return
        self.shown_url = None
        self.window.load_html(WAITING_HTML.replace("__MSG__", message).replace("__HINT__", hint))

    def show_player(self, host: str) -> None:
        url = f"http://{host}:{PORT}/"
        if self.window is None or self.shown_url == url:
            return
        self.shown_url = url
        self.window.load_url(url)
        self.window.set_title("MusicGang" if host == "127.0.0.1" else f"MusicGang — host {host}")

    # ---- transitions ---------------------------------------------------------
    def become_client(self, host: str) -> None:
        logging.info("becoming client of %s", host)
        self.mode, self.host_ip = "client", host
        self.show_player(host)

    def become_host(self) -> None:
        logging.info("becoming host")
        self.mode, self.host_ip = "host", "127.0.0.1"
        self.show_waiting("Starting the bot on this PC…", "You are the host now.")
        start_local_bot()
        for _ in range(120):
            if port_open("127.0.0.1", PORT):
                break
            time.sleep(1)
        else:
            self.show_waiting("The bot did not start.", "Check the bot and Lavalink windows for the error.")
            return
        self.restore_state()
        self.show_player("127.0.0.1")

    def restore_state(self) -> None:
        """Re-queue what the previous host was playing so the music continues."""
        s = self.last_state
        if not s or not s.get("lavalink", True):
            return
        try:
            tracks = ([s["current"]] if s.get("current") else []) + list(s.get("queue") or [])
            for t in tracks:
                q = t.get("uri") or t.get("title")
                if q:
                    try:
                        api("127.0.0.1", "play", {"query": q}, timeout=30)
                    except Exception:
                        pass  # one bad track must not stop the rest
            if s.get("volume") is not None:
                api("127.0.0.1", "volume", {"level": int(s["volume"])})
            api("127.0.0.1", "loop", {"mode": s.get("loop", "off")})
            api("127.0.0.1", "autoplay", {"mode": s.get("autoplay", "enabled")})
            if s.get("current") and s.get("position"):
                api("127.0.0.1", "seek", {"position": int(s["position"])})
        except Exception:
            pass

    # ---- discovery -----------------------------------------------------------
    def find_host(self) -> str | None:
        """A peer whose bot API answers (prefer ones whose beacon says hosting)."""
        candidates = [p for p in PEERS if p != MY_IP]
        hosting = [p for p in candidates if (beacon(p) or {}).get("hosting")]
        for p in hosting + [c for c in candidates if c not in hosting]:
            try:
                api(p, "state", timeout=2)
                return p
            except Exception:
                continue
        return None

    def alive_peers(self) -> list[str]:
        alive = [MY_IP] if MY_IP else []
        for p in PEERS:
            if p != MY_IP and beacon(p) is not None:
                alive.append(p)
        return sorted(alive, key=lambda ip: tuple(int(x) for x in ip.split(".")))

    # ---- the loop --------------------------------------------------------------
    def run(self) -> None:
        failures = 0
        while True:
            try:
                if FIXED_HOST:
                    # Old-style fixed host: never host, just wait for it.
                    if port_open(FIXED_HOST, PORT):
                        self.become_client(FIXED_HOST)
                    elif self.mode != "searching":
                        self.mode = "searching"
                        self.show_waiting(f"Waiting for the bot at {FIXED_HOST}…",
                                          "Is the host's bot running, and are you both on Tailscale?")
                    time.sleep(2)
                    continue

                if not PEERS or not MY_IP:
                    # Solo mode: host on this PC, like before.
                    if self.mode != "host":
                        if not port_open("127.0.0.1", PORT):
                            self.become_host()
                        else:
                            self.mode, self.host_ip = "host", "127.0.0.1"
                            self.show_player("127.0.0.1")
                    time.sleep(3)
                    continue

                if self.mode == "host":
                    if port_open("127.0.0.1", PORT):
                        try:
                            self.last_state = api("127.0.0.1", "state", timeout=3)
                        except Exception:
                            pass
                        failures = 0
                    else:
                        failures += 1
                        if failures >= 20:  # bot gone for ~1 min: let start.ps1 bring it back
                            failures = 0
                            start_local_bot()
                    time.sleep(3)

                elif self.mode == "client":
                    try:
                        self.last_state = api(self.host_ip, "state", timeout=3)
                        failures = 0
                    except Exception:
                        failures += 1
                        if failures >= 3:
                            failures = 0
                            self.mode = "searching"
                            self.show_waiting("The host went away — finding a new one…",
                                              "The music resumes on whichever PC takes over.")
                    time.sleep(3)

                else:  # searching
                    # Someone else already hosting? Use them.
                    if port_open("127.0.0.1", PORT):
                        self.mode, self.host_ip = "host", "127.0.0.1"
                        self.show_player("127.0.0.1")
                        continue
                    host = self.find_host()
                    if host:
                        self.become_client(host)
                        continue
                    # Election: lowest Tailscale IP among open windows hosts.
                    alive = self.alive_peers()
                    if alive and alive[0] == MY_IP:
                        self.become_host()
                    else:
                        leader = alive[0] if alive else "a peer"
                        self.show_waiting(f"Waiting for {leader} to start the bot…",
                                          "It takes over automatically.")
                        time.sleep(3)
            except Exception:
                logging.exception("node loop error (mode=%s)", self.mode)
                time.sleep(3)


node = Node()


# --------------------------------------------------------------------------- #
# Beacon: lets peers see this window is open and whether it hosts
# --------------------------------------------------------------------------- #
class BeaconHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = json.dumps(node.info()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass  # quiet


def start_beacon() -> None:
    if not MY_IP:
        return
    try:
        server = ThreadingHTTPServer((MY_IP, BEACON_PORT), BeaconHandler)
    except OSError:
        return  # port busy or Tailscale IP not bound — peers just won't see us
    threading.Thread(target=server.serve_forever, daemon=True).start()


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #
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
  <p>__MSG__</p>
  <p style="margin-top:18px"><span class="dot"></span><span class="dot"></span><span class="dot"></span></p>
  <p style="margin-top:26px;font-size:14px;color:#6b6b6b">__HINT__</p>
</div></body></html>
"""


def apply_icon(window: webview.Window) -> None:
    """
    Set the title-bar / taskbar icon. pywebview's own `icon` argument only
    works on GTK/Qt; on Windows the underlying WinForms Form must be given a
    System.Drawing.Icon directly.
    """
    if not ICON.exists():
        return
    try:
        import clr  # pythonnet, installed with pywebview on Windows
        clr.AddReference("System.Drawing")
        from System.Drawing import Icon  # type: ignore[import-not-found]
        form = window.native
        form.Invoke(lambda: setattr(form, "Icon", Icon(str(ICON))))
    except Exception:
        pass  # icon is cosmetic — never block the app on it


def on_loaded() -> None:
    """Hand the page the shared password so nobody has to type it."""
    if TOKEN and node.window is not None and node.shown_url:
        try:
            # Reload once if the page started without it, so no prompt appears.
            node.window.evaluate_js(
                "try{var t=%s;if(localStorage.getItem('musicgang_token')!==t){"
                "localStorage.setItem('musicgang_token',t);location.reload();}}catch(e){}"
                % json.dumps(TOKEN)
            )
        except Exception:
            pass


def on_closed() -> None:
    """Closing the host's window stops its bot so another PC can take over."""
    if node.mode == "host":
        stop_local_bot()


def main(window: webview.Window) -> None:
    node.window = window
    apply_icon(window)
    start_beacon()
    node.show_waiting("Looking for the bot…", "Checking this PC and your friends' PCs.")
    threading.Thread(target=node.run, daemon=True).start()


if __name__ == "__main__":
    # Open at 1600x1000 on big screens, smaller on small ones. The page itself
    # adapts to any size (ui.html stacks its columns when narrow).
    screen = webview.screens[0] if webview.screens else None
    width = min(1600, screen.width - 80) if screen else 1280
    height = min(1000, screen.height - 120) if screen else 800

    window = webview.create_window(
        "MusicGang", html=WAITING_HTML.replace("__MSG__", "Starting…").replace("__HINT__", ""),
        width=width, height=height, min_size=(480, 600),
        background_color="#000000", text_select=False,
    )
    window.events.loaded += on_loaded
    window.events.closed += on_closed
    # private_mode=False so localStorage (the remembered password) survives restarts.
    webview.start(main, window, private_mode=False)
