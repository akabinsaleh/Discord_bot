"""
MusicGang desktop app
=====================

A small native window (tkinter, ships with Python — nothing to install) that
controls the music bot without typing slash commands in Discord: search a song,
pick a result, see the queue, play/pause/skip, volume, loop, autoplay.

It talks to the bot's local control API (bot.py, http://127.0.0.1:27432). The
bot must be running — start.bat starts the bot and then opens this window.

The .pyw extension means Windows runs it with pythonw.exe: no console window.
"""

import json
import os
import queue
import threading
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import ttk

API = f"http://127.0.0.1:{os.getenv('WEB_PORT', '27432')}/api"
POLL_MS = 1000  # how often the window refreshes from the bot

# Colours: a Discord-like dark theme.
BG = "#1e1f22"
PANEL = "#2b2d31"
FIELD = "#383a40"
FG = "#f2f3f5"
MUTED = "#949ba4"
ACCENT = "#5865f2"
GREEN = "#23a559"
RED = "#da373c"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 11, "bold")
FONT_TITLE = ("Segoe UI", 13, "bold")


# --------------------------------------------------------------------------- #
# HTTP helpers — every call runs in a worker thread so the window never freezes
# --------------------------------------------------------------------------- #
def api_call(path: str, body: dict | None = None) -> dict:
    """GET (body None) or POST JSON to the bot. Raises on network/API errors."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}/{path}", data=data, method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error") or str(exc)
        except Exception:
            message = str(exc)
        raise RuntimeError(message) from None
    except urllib.error.URLError:
        raise RuntimeError("Bot offline — start it with start.bat") from None


def fmt(ms: int | None) -> str:
    if not ms:
        return "0:00"
    minutes, seconds = divmod(int(ms) // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MusicGang")
        self.geometry("560x760")
        self.minsize(480, 640)
        self.configure(bg=BG)

        self.results: list[dict] = []       # last search results (dicts from the API)
        self.state: dict = {}               # last /api/state
        self.channels: list[dict] = []
        self.ui_queue: queue.Queue = queue.Queue()  # worker threads -> UI thread
        self.dragging_volume = False
        self.last_position_ms = 0

        self._style()
        self._build()
        self.after(100, self._drain)
        self._poll()

    # ---- styling --------------------------------------------------------- #
    def _style(self) -> None:
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=FONT, borderwidth=0)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=PANEL, foreground=FG, font=FONT_TITLE)
        s.configure("Head.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9, "bold"))
        s.configure("TButton", background=FIELD, foreground=FG, padding=(10, 6), relief="flat")
        s.map("TButton", background=[("active", "#4e5058"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=ACCENT, foreground="white")
        s.map("Accent.TButton", background=[("active", "#4752c4")])
        s.configure("Danger.TButton", background=RED, foreground="white")
        s.map("Danger.TButton", background=[("active", "#a12d31")])
        s.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=6)
        s.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=FG,
                    arrowcolor=FG, padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        s.configure("Horizontal.TScale", background=PANEL, troughcolor=FIELD)
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD, thickness=6)

    # ---- layout ---------------------------------------------------------- #
    def _build(self) -> None:
        pad = {"padx": 14}

        # Status line.
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(12, 6), **pad)
        self.status = ttk.Label(top, text="Connecting to the bot…", style="Head.TLabel")
        self.status.pack(side="left")
        self.error = ttk.Label(top, text="", foreground=RED, background=BG, font=("Segoe UI", 9))
        self.error.pack(side="right")

        # Search.
        ttk.Label(self, text="SEARCH", style="Head.TLabel").pack(anchor="w", **pad)
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(2, 4), **pad)
        self.search_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.search())
        entry.focus_set()
        ttk.Button(row, text="Search", command=self.search).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Play now", style="Accent.TButton",
                   command=self.play_query).pack(side="left", padx=(6, 0))
        ttk.Label(self, text="Enter = search · Play now = top result or paste a link/playlist",
                  style="Head.TLabel").pack(anchor="w", **pad)

        self.results_box = self._listbox(self, height=6)
        self.results_box.pack(fill="x", pady=(4, 2), **pad)
        self.results_box.bind("<Double-Button-1>", lambda _e: self.add_selected())
        self.results_box.bind("<Return>", lambda _e: self.add_selected())
        rrow = ttk.Frame(self)
        rrow.pack(fill="x", pady=(0, 8), **pad)
        ttk.Button(rrow, text="Add selected to queue", style="Accent.TButton",
                   command=self.add_selected).pack(side="left")
        ttk.Label(rrow, text="(or double-click a result)", style="Head.TLabel").pack(
            side="left", padx=8)

        # Now playing card.
        card = ttk.Frame(self, style="Panel.TFrame", padding=12)
        card.pack(fill="x", **pad)
        self.np_title = ttk.Label(card, text="Nothing playing", style="Title.TLabel",
                                  wraplength=500)
        self.np_title.pack(anchor="w")
        self.np_sub = ttk.Label(card, text="", style="Muted.TLabel")
        self.np_sub.pack(anchor="w", pady=(0, 8))

        self.progress = ttk.Progressbar(card, orient="horizontal", mode="determinate",
                                        style="Horizontal.TProgressbar")
        self.progress.pack(fill="x")
        self.progress.bind("<Button-1>", self._seek_click)
        trow = ttk.Frame(card, style="Panel.TFrame")
        trow.pack(fill="x")
        self.time_left = ttk.Label(trow, text="0:00", style="Muted.TLabel")
        self.time_left.pack(side="left")
        self.time_right = ttk.Label(trow, text="0:00", style="Muted.TLabel")
        self.time_right.pack(side="right")

        ctl = ttk.Frame(card, style="Panel.TFrame")
        ctl.pack(fill="x", pady=(10, 0))
        ttk.Button(ctl, text="⏮  Back", command=lambda: self.action("back")).pack(side="left")
        self.play_btn = ttk.Button(ctl, text="⏸  Pause", style="Accent.TButton",
                                   command=self.toggle_pause)
        self.play_btn.pack(side="left", padx=6)
        ttk.Button(ctl, text="⏭  Skip", command=lambda: self.action("skip")).pack(side="left")
        ttk.Button(ctl, text="⏹  Stop & leave", style="Danger.TButton",
                   command=lambda: self.action("stop")).pack(side="right")

        vrow = ttk.Frame(card, style="Panel.TFrame")
        vrow.pack(fill="x", pady=(10, 0))
        ttk.Label(vrow, text="Volume", style="Muted.TLabel").pack(side="left")
        self.volume_var = tk.IntVar(value=100)
        self.volume = ttk.Scale(vrow, from_=0, to=200, orient="horizontal",
                                variable=self.volume_var, command=self._volume_drag)
        self.volume.pack(side="left", fill="x", expand=True, padx=8)
        self.volume.bind("<ButtonPress-1>", lambda _e: setattr(self, "dragging_volume", True))
        self.volume.bind("<ButtonRelease-1>", self._volume_release)
        self.volume_label = ttk.Label(vrow, text="100%", style="Muted.TLabel", width=5)
        self.volume_label.pack(side="left")

        mrow = ttk.Frame(card, style="Panel.TFrame")
        mrow.pack(fill="x", pady=(10, 0))
        ttk.Label(mrow, text="Loop", style="Muted.TLabel").pack(side="left")
        self.loop_var = tk.StringVar(value="off")
        loop = ttk.Combobox(mrow, textvariable=self.loop_var, state="readonly", width=7,
                            values=["off", "track", "queue"])
        loop.pack(side="left", padx=(6, 14))
        loop.bind("<<ComboboxSelected>>", lambda _e: self.action("loop", {"mode": self.loop_var.get()}))
        ttk.Label(mrow, text="Autoplay", style="Muted.TLabel").pack(side="left")
        self.autoplay_var = tk.StringVar(value="enabled")
        auto = ttk.Combobox(mrow, textvariable=self.autoplay_var, state="readonly", width=9,
                            values=["enabled", "partial", "disabled"])
        auto.pack(side="left", padx=(6, 14))
        auto.bind("<<ComboboxSelected>>",
                  lambda _e: self.action("autoplay", {"mode": self.autoplay_var.get()}))
        ttk.Button(mrow, text="🔀 Shuffle", command=lambda: self.action("shuffle")).pack(side="right")

        # Queue.
        qhead = ttk.Frame(self)
        qhead.pack(fill="x", pady=(12, 2), **pad)
        self.queue_head = ttk.Label(qhead, text="QUEUE", style="Head.TLabel")
        self.queue_head.pack(side="left")
        ttk.Button(qhead, text="Clear", command=lambda: self.action("clear")).pack(side="right")
        ttk.Button(qhead, text="Remove selected", command=self.remove_selected).pack(
            side="right", padx=6)
        self.queue_box = self._listbox(self, height=8)
        self.queue_box.pack(fill="both", expand=True, **pad)

        # Voice channel.
        vc = ttk.Frame(self)
        vc.pack(fill="x", pady=10, **pad)
        ttk.Label(vc, text="Voice channel", style="Head.TLabel").pack(side="left")
        self.channel_var = tk.StringVar()
        self.channel_box = ttk.Combobox(vc, textvariable=self.channel_var, state="readonly")
        self.channel_box.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(vc, text="Join", command=self.join_channel).pack(side="left")

    def _listbox(self, parent: tk.Misc, height: int) -> tk.Listbox:
        box = tk.Listbox(
            parent, height=height, bg=FIELD, fg=FG, selectbackground=ACCENT,
            selectforeground="white", activestyle="none", relief="flat",
            highlightthickness=0, font=FONT, borderwidth=0,
        )
        return box

    # ---- threading plumbing ------------------------------------------------ #
    def run(self, fn, on_done=None) -> None:
        """Run fn() in a worker thread; deliver its result (or error) to the UI thread."""
        def worker() -> None:
            try:
                result = fn()
                self.ui_queue.put((on_done, result, None))
            except Exception as exc:  # noqa: BLE001 - shown to the user
                self.ui_queue.put((on_done, None, exc))
        threading.Thread(target=worker, daemon=True).start()

    def _drain(self) -> None:
        try:
            while True:
                on_done, result, exc = self.ui_queue.get_nowait()
                if exc is not None:
                    self.show_error(str(exc))
                elif on_done is not None:
                    on_done(result)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def show_error(self, message: str) -> None:
        self.error.config(text=message[:80])
        self.after(6000, lambda: self.error.config(text="") if self.error.cget("text") == message[:80] else None)

    # ---- polling ------------------------------------------------------------- #
    def _poll(self) -> None:
        self.run(lambda: api_call("state"), self._apply_state)
        self.after(POLL_MS, self._poll)

    def _apply_state(self, state: dict) -> None:
        self.state = state
        self.error.config(text="") if self.error.cget("text").startswith("Bot offline") else None

        # Status line.
        if not state.get("lavalink"):
            self.status.config(text="⚠ Lavalink (audio server) not running", foreground=RED)
        elif state.get("connected"):
            self.status.config(text=f"● In {state['channel']}  ·  {state.get('guild') or ''}",
                               foreground=GREEN)
        else:
            self.status.config(text="○ Not in a voice channel — Play joins the busiest one",
                               foreground=MUTED)

        # Now playing.
        current = state.get("current")
        if current:
            self.np_title.config(text=current["title"])
            self.np_sub.config(
                text=f"{current.get('author') or 'Unknown'}  ·  {current.get('requester')}"
                     f"  ·  {current.get('source')}")
            length = current.get("length") or 0
            pos = state.get("position") or 0
            self.progress.config(maximum=max(length, 1), value=min(pos, length))
            self.time_left.config(text=fmt(pos))
            self.time_right.config(text=fmt(length) if length else "LIVE")
            self.last_position_ms = pos
        else:
            self.np_title.config(text="Nothing playing")
            self.np_sub.config(text="Search a song above, then Play now or double-click a result.")
            self.progress.config(maximum=1, value=0)
            self.time_left.config(text="0:00")
            self.time_right.config(text="0:00")
        self.play_btn.config(text="▶  Resume" if state.get("paused") else "⏸  Pause")

        if not self.dragging_volume:
            self.volume_var.set(int(state.get("volume", 100)))
            self.volume_label.config(text=f"{int(state.get('volume', 100))}%")
        self.loop_var.set(state.get("loop", "off"))
        self.autoplay_var.set(state.get("autoplay", "enabled"))

        # Queue (keep the selection stable while it refreshes).
        selected = self.queue_box.curselection()
        self.queue_box.delete(0, "end")
        q = state.get("queue") or []
        for i, t in enumerate(q, start=1):
            self.queue_box.insert("end", f"{i:>2}.  {t['title']}  —  {t.get('author') or ''}  [{fmt(t.get('length'))}]")
        if selected and selected[0] < len(q):
            self.queue_box.selection_set(selected[0])
        self.queue_head.config(text=f"QUEUE  ·  {len(q)} track{'s' if len(q) != 1 else ''}")

        # Voice channels.
        channels = state.get("channels") or []
        names = [f"{c['name']}  ({c['humans']} people)" for c in channels]
        if names != list(self.channel_box["values"]):
            self.channels = channels
            self.channel_box["values"] = names
            if names and not self.channel_var.get():
                busiest = max(range(len(channels)), key=lambda i: channels[i]["humans"])
                self.channel_box.current(busiest)

    # ---- actions ------------------------------------------------------------ #
    def action(self, name: str, body: dict | None = None) -> None:
        self.run(lambda: api_call(name, body or {}), self._apply_state)

    def toggle_pause(self) -> None:
        self.action("resume" if self.state.get("paused") else "pause")

    def search(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            return
        self.results_box.delete(0, "end")
        self.results_box.insert("end", "Searching…")

        def done(result: dict) -> None:
            self.results = result.get("tracks") or []
            self.results_box.delete(0, "end")
            if result.get("playlist"):
                pl = result["playlist"]
                self.results_box.insert("end", f"▶ Playlist: {pl['name']} ({pl['count']} tracks) — use Play now to add all")
            for t in self.results:
                self.results_box.insert("end", f"{t['title']}  —  {t.get('author') or ''}  [{fmt(t.get('length'))}]")
            if not self.results:
                self.results_box.insert("end", "No results.")
        self.run(lambda: api_call("search", {"query": query}), done)

    def play_query(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            return
        self._play(query)

    def add_selected(self) -> None:
        sel = self.results_box.curselection()
        if not sel:
            return
        index = sel[0]
        # A playlist header occupies row 0 and shifts the track rows down by one.
        if self.results_box.get(0).startswith("▶ Playlist"):
            if index == 0:
                self.play_query()
                return
            index -= 1
        if index < len(self.results):
            track = self.results[index]
            self._play(track.get("uri") or track["title"])

    def _play(self, query: str) -> None:
        def done(result: dict) -> None:
            self._apply_state(result["state"])
            self.status.config(text=f"✓ {result['message']}"[:90], foreground=GREEN)
        self.run(lambda: api_call("play", {"query": query}), done)

    def remove_selected(self) -> None:
        sel = self.queue_box.curselection()
        if sel:
            self.action("remove", {"index": sel[0]})

    def join_channel(self) -> None:
        idx = self.channel_box.current()
        if 0 <= idx < len(self.channels):
            self.action("join", {"channel_id": self.channels[idx]["id"]})

    def _seek_click(self, event: tk.Event) -> None:
        current = self.state.get("current")
        if not current or not current.get("length"):
            return
        fraction = max(0.0, min(1.0, event.x / max(1, self.progress.winfo_width())))
        self.action("seek", {"position": int(fraction * current["length"])})

    def _volume_drag(self, _value: str) -> None:
        self.volume_label.config(text=f"{int(self.volume_var.get())}%")

    def _volume_release(self, _event: tk.Event) -> None:
        self.dragging_volume = False
        self.action("volume", {"level": int(self.volume_var.get())})


if __name__ == "__main__":
    App().mainloop()
