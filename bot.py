"""
Personal Discord Music Bot
==========================

A simple, self-hostable Discord music bot built with:
  - discord.py   (slash commands via app_commands)
  - Wavelink 3   (Python wrapper around a Lavalink node)
  - Lavalink v4  (the audio server you run separately as a .jar)

This is intended for a SINGLE server (yours). It is deliberately not built for
scale or multi-guild use — it keeps state simple and readable.

Control panel (the main UI):
  Whenever a track starts, the bot posts a "player panel" message: an embed
  showing the queue size, current track, who requested it, and the volume/loop
  state — with a "Select a suggested song" dropdown and a grid of control
  buttons (play/pause/skip/back/loop, volume, rewind/forward, shuffle, stop,
  nightcore filter, autoplay, replay). Everything is clickable; the slash
  commands below do the same things for people who prefer typing.

Commands:
  /play <query>     -> search, show the top 5 matches in a dropdown, and queue
                       whichever one you pick (or play/add it directly if the
                       query was a URL or resolved to a playlist)
  /panel            -> re-post the control panel at the bottom of the channel
  /skip             -> skip the current track
  /back             -> play the previous track again
  /queue            -> show what's coming up
  /nowplaying       -> show what's currently playing, with a progress bar
  /pause            -> pause the current track
  /resume           -> resume a paused track
  /stop             -> stop playback and disconnect
  /join             -> join your voice channel without playing anything
  /disconnect       -> leave the voice channel
  /volume <0-1000>  -> set this session's playback volume (100 = normal)
  /defaultvolume    -> set (and persist to .env) the volume used on join
  /loop <mode>      -> loop off / current track / whole queue
  /autoplay <mode>  -> turn recommended-track autoplay on, off, or partial
  /seek <time>      -> jump to a specific position, e.g. "1:30" or "90"
  /fastforward [s]  -> jump forward in the track (default 10s)
  /rewind [s]       -> jump back in the track (default 10s)
  /replay           -> restart the current track from 0:00

Autoplay:
  When your manually-queued songs run out, Wavelink's AutoPlayMode.enabled makes
  the bot keep going by playing recommended/related tracks automatically. Your
  own queued songs ALWAYS play before recommendations, because Wavelink keeps
  them in a separate queue (player.queue) that is drained before the auto queue
  (player.auto_queue).
"""

import os
import sys
import logging
from pathlib import Path

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands
from discord.voice_client import has_nacl

import wavelink
from dotenv import find_dotenv, load_dotenv, set_key


# --------------------------------------------------------------------------- #
# Configuration (read from .env — never hardcode secrets)
# --------------------------------------------------------------------------- #
ENV_PATH = find_dotenv() or ".env"
load_dotenv(ENV_PATH)  # loads variables from a local .env file into os.environ

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]                 # required
LAVALINK_URI = os.getenv("LAVALINK_URI", "http://127.0.0.1:2333")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

# Optional: syncing slash commands to a single guild is INSTANT (global sync can
# take up to an hour to propagate). Set GUILD_ID in .env to your server's ID for
# immediate command availability while developing.
GUILD_ID = os.getenv("GUILD_ID")

# How long (in seconds) the bot may sit in voice doing nothing (paused, or
# stopped with no autoplay results) before it disconnects itself.
INACTIVE_TIMEOUT = int(os.getenv("INACTIVE_TIMEOUT", "180"))

# Volume applied whenever the bot joins a voice channel. 100 = Lavalink's
# normal, unamplified volume. Changed at runtime via /defaultvolume, which
# also persists the new value back into .env for future sessions.
DEFAULT_VOLUME = int(os.getenv("DEFAULT_VOLUME", "100"))

# Control API (see the "Local control API" section) used by the desktop app,
# musicgang_app.pyw.
#
# WEB_HOST: where to listen.
#   127.0.0.1  (default) this PC only, no password needed.
#   tailscale  also listen on this PC's Tailscale IP so friends on your
#              tailnet can use their MusicGang window against your bot.
#              REQUIRES WEB_TOKEN. Tailscale traffic is encrypted and only
#              your tailnet can reach the address.
#   Anything else (an IP, or 0.0.0.0) is refused: that would expose the bot to
#   the whole LAN / internet. Use Tailscale instead.
# WEB_TOKEN: shared password every API request must carry. Friends type it
#   once into their MusicGang window. Any long random string.
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1").strip().lower()
WEB_PORT = int(os.getenv("WEB_PORT", "27432"))
WEB_TOKEN = os.getenv("WEB_TOKEN", "").strip()
UI_HTML = Path(__file__).with_name("ui.html")  # the page the desktop app shows

logging.basicConfig(level=logging.INFO)


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
def preflight() -> None:
    """
    Check the things that break the bot in ways that don't look like their cause.

    Without a voice backend the bot still logs in, still answers slash commands,
    and still finds tracks — it only fails when it tries to hold a voice
    connection. The symptom is "the bot joins and instantly leaves" plus a
    command stuck on "thinking...", which points nowhere near the real problem:
    a missing library. discord.py only whispers this as a startup WARNING that
    scrolls away, so check it here and refuse to start instead.
    """
    # discord.py >= 2.7 needs both PyNaCl and davey (Discord's DAVE end-to-end
    # voice encryption); has_nacl is False if either one is missing.
    if not has_nacl:
        sys.exit(
            "\n"
            "FATAL: PyNaCl and/or davey is not installed, so Discord voice cannot be encrypted.\n"
            "       The bot would log in fine, then drop out of voice on /play.\n"
            "\n"
            f"       Fix:  \"{sys.executable}\" -m pip install -r requirements.txt\n"
        )


# --------------------------------------------------------------------------- #
# The bot
# --------------------------------------------------------------------------- #
class GuildOnlyTree(app_commands.CommandTree):
    """
    Every command here reads interaction.guild, which is None in DMs — that
    would crash all 18 handlers. One tree-level check beats 18 per-command ones.
    """

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This bot only works inside a server.", ephemeral=True
            )
            return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """
        Report every unhandled command error back to whoever ran the command.

        Commands here defer first (voice + search are too slow for Discord's 3s
        window), and a deferred command that raises leaves Discord showing
        "<bot> is thinking..." forever — the user gets no error, and the reason
        only ever appears in the terminal. Always answering means a failure is
        visible where it happened, so it can never masquerade as a hang again.
        """
        logging.exception("Unhandled error in /%s", getattr(interaction.command, "name", "?"), exc_info=error)

        message = f"⚠️ That command failed: `{type(error).__name__}: {error}`"[:1900]
        try:
            # A deferred interaction has already "responded", so it needs a
            # followup; an un-deferred one still needs its first response.
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass  # interaction expired or already gone — the log above still has it


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        # A slash-command-only bot needs very few intents. voice_states (required
        # to join/track voice channels) is included in Intents.default().
        # message_content is NOT needed for slash commands — leave it off unless
        # you later add prefix commands.
        intents = discord.Intents.default()

        # command_prefix is unused (we only use slash commands) but required by
        # the commands.Bot constructor. when_mentioned is the one prefix that
        # works without the message_content intent, so discord.py doesn't log a
        # misleading "Privileged message content intent is missing" warning.
        super().__init__(
            command_prefix=commands.when_mentioned, intents=intents, tree_cls=GuildOnlyTree
        )

    async def setup_hook(self) -> None:
        """
        Runs once, after login but before the bot is fully ready.
        This is the correct place to connect to the Lavalink node and to sync
        our slash commands with Discord.
        """
        # Connect to the Lavalink node. Wavelink manages the connection pool for
        # us; for a personal bot a single node is all you need.
        #
        # A failure here must not kill the bot: if Lavalink simply isn't up yet,
        # a dead bot can't tell anyone why. Staying alive lets /play answer with
        # the real reason (see lavalink_ready) instead of timing out into
        # Discord's useless "The application did not respond".
        node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
        try:
            await wavelink.Pool.connect(nodes=[node], client=self)
        except Exception:
            logging.exception(
                "Could not reach Lavalink at %s — is it running? "
                "/play will report this until it is.",
                LAVALINK_URI,
            )

        # Register the slash commands with Discord.
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)

            # Also push an EMPTY global command list. If the bot ever synced
            # globally (e.g. before GUILD_ID was set), those stale global
            # copies would otherwise show every command twice in the picker.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            logging.info("Slash commands synced to guild %s", GUILD_ID)
        else:
            await self.tree.sync()
            logging.info("Slash commands synced globally (may take up to 1 hour)")

        # Local control API for the desktop app. A failure here (port in use)
        # must not kill the bot — slash commands still work.
        try:
            await start_control_api()
        except Exception:
            logging.exception("Could not start the control API on port %s", WEB_PORT)


bot = MusicBot()


def format_duration(milliseconds: int) -> str:
    """Format track length (ms) as m:ss, e.g. 3:07."""
    minutes, seconds = divmod(milliseconds // 1000, 60)
    return f"{minutes}:{seconds:02d}"


def parse_timestamp(text: str) -> int:
    """
    Parse a "seconds" or "mm:ss" / "h:mm:ss" string into milliseconds.
    Raises ValueError with a user-friendly message on bad input.
    """
    try:
        parts = [int(part) for part in text.strip().split(":")]
    except ValueError:
        raise ValueError('Invalid time — use seconds (e.g. "90") or mm:ss (e.g. "1:30").')

    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds * 1000


def progress_bar(position_ms: int, length_ms: int, size: int = 12) -> str:
    """Render a text progress bar, e.g. ▬▬▬🔘▬▬▬▬▬▬▬▬."""
    if not length_ms:
        return ""
    filled = min(size, int(size * position_ms / length_ms))
    return "▬" * filled + "🔘" + "▬" * (size - filled)


async def fetch_suggestions(track: wavelink.Playable, limit: int = 10) -> list[wavelink.Playable]:
    """
    Return tracks related to `track` (for the /suggest picker).

    For YouTube/YouTube Music we ask for the track's "Mix" radio playlist
    (the RD<id> list), which is exactly what YouTube itself recommends next.
    The seed track is the first entry, so we drop it. Other sources don't have
    an equivalent related-tracks endpoint here, so we return nothing for them.
    """
    if track.source in ("youtube", "youtubemusic") and track.identifier:
        mix_url = (
            f"https://www.youtube.com/watch?v={track.identifier}&list=RD{track.identifier}"
        )
        result = await wavelink.Playable.search(mix_url)
        if isinstance(result, wavelink.Playlist):
            return [t for t in result.tracks if t.identifier != track.identifier][:limit]

    return []


def build_track_embed(track: wavelink.Playable, *, position: int | None = None) -> discord.Embed:
    """
    Build the "Now Playing" embed. Shared by the on_wavelink_track_start event
    and the /nowplaying command — the latter additionally passes the current
    playback position to render a progress bar.
    """
    # Wavelink marks tracks that came from autoplay recommendations with
    # .recommended == True. getattr keeps us safe across minor version changes.
    recommended = bool(getattr(track, "recommended", False))

    # Some sources don't provide a URL — fall back to plain text so we never
    # render a broken markdown link.
    title = f"**[{track.title}]({track.uri})**" if track.uri else f"**{track.title}**"
    embed = discord.Embed(title="Now Playing", description=title, color=discord.Color.blurple())
    embed.add_field(name="Author", value=track.author or "Unknown", inline=True)

    if track.length:
        if position is not None:
            embed.add_field(
                name="Progress",
                value=(
                    f"{progress_bar(position, track.length)}\n"
                    f"{format_duration(position)} / {format_duration(track.length)}"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="Duration", value=format_duration(track.length), inline=True)

    embed.add_field(
        name="Source",
        value="🎲 Recommended (autoplay)" if recommended else "🎵 Queued by a user",
        inline=False,
    )

    if track.artwork:
        embed.set_thumbnail(url=track.artwork)

    return embed


# --------------------------------------------------------------------------- #
# Control-panel presentation (LunaBot-style: queue embed + suggestion dropdown
# + a grid of playback control buttons)
# --------------------------------------------------------------------------- #
# How many suggested songs to list in the dropdown (Discord allows up to 25).
MAX_SUGGESTIONS = 10

# queue.mode -> ("footer label", "icon") shown as "Volume: N || <label> <icon>".
LOOP_LABELS = {
    wavelink.QueueMode.normal: ("Off", ""),
    wavelink.QueueMode.loop: ("Song", "🔂"),
    wavelink.QueueMode.loop_all: ("Queue", "🔁"),
}

# Guild ids that currently have the nightcore filter toggled on (via the panel).
_nightcore_guilds: set[int] = set()


def track_requester(track: wavelink.Playable, guild: discord.Guild) -> str:
    """
    Return a mention for whoever queued `track`, or "Autoplay" if it came from
    recommendations. We stash the requester's id in track.extras when queuing.
    """
    try:
        rid = track.extras.requester_id
    except AttributeError:
        rid = None

    if rid == "web":
        return "🖥️ Control panel"
    if rid:
        member = guild.get_member(int(rid))
        if member is not None:
            return member.mention
    return "Autoplay"


def build_panel_embed(player: wavelink.Player) -> discord.Embed:
    """The "N tracks in queue" embed shown above the control panel."""
    queue = player.queue
    total = len(queue) + (1 if player.current else 0)
    plural = "s" if total != 1 else ""
    embed = discord.Embed(
        title=f"{total} track{plural} in queue", color=discord.Color.dark_embed()
    )

    current = player.current
    if current is not None:
        duration = format_duration(current.length) if current.length else "LIVE"
        link = f"[{current.title}]({current.uri})" if current.uri else current.title
        embed.description = (
            f"🔊 {link} - [ {duration} ],\n"
            f"*Requested by* - {track_requester(current, player.guild)}"
        )
        if current.artwork:
            embed.set_thumbnail(url=current.artwork)
    else:
        embed.description = "_Nothing playing right now._"

    label, icon = LOOP_LABELS.get(queue.mode, ("Off", ""))
    embed.set_footer(text=f"Volume: {player.volume} || {label} {icon}".strip())
    return embed


# --------------------------------------------------------------------------- #
# Wavelink events
# --------------------------------------------------------------------------- #
@bot.event
async def on_ready() -> None:
    """
    Clean up "ghost" voice connections left over from a previous run. If the bot
    was restarted while connected, Discord still shows it in the channel but this
    fresh process has no player to control it — so leave any such channel now.
    """
    for guild in bot.guilds:
        if guild.voice_client is None and guild.me and guild.me.voice and guild.me.voice.channel:
            await guild.change_voice_state(channel=None)
            logging.info("Cleared a stale voice connection in guild %s.", guild.id)


@bot.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload) -> None:
    """Fired when the Lavalink node is connected and ready to accept requests."""
    logging.info("Lavalink node '%s' is ready.", payload.node.identifier)


@bot.event
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    """
    Fired whenever a track begins playing — whether we queued it manually or it
    was pulled in by autoplay. We use this single event to post the
    "Now Playing" message so it's consistent for both cases.
    """
    player: wavelink.Player = payload.player
    if player is None:
        return

    # "home" is a custom attribute we set on the player in /play so we know which
    # text channel to post updates to. Fall back silently if it isn't set.
    channel = getattr(player, "home", None)
    if channel is None:
        return

    # Fetch songs related to the new track for the panel's suggestion dropdown,
    # then (re)post the control panel so it follows the music to the bottom.
    player.suggestions = await fetch_suggestions(payload.track, limit=MAX_SUGGESTIONS)
    await show_panel(player, repost=True)


@bot.event
async def on_wavelink_inactive_player(player: wavelink.Player) -> None:
    """
    Fired when the player has been idle (not playing anything) for
    player.inactive_timeout seconds. Without this, a paused or silent bot would
    sit in the voice channel forever.
    """
    channel = getattr(player, "home", None)
    if channel is not None:
        await channel.send(
            f"Left the voice channel after {INACTIVE_TIMEOUT} seconds of inactivity."
        )
    await player.disconnect()


@bot.event
async def on_wavelink_track_exception(
    payload: wavelink.TrackExceptionEventPayload,
) -> None:
    """
    Fired when Lavalink fails to play a track (age-restricted video, region
    block, source hiccup...). Without this the track silently vanishes; a short
    message makes failures visible instead of mysterious.
    """
    player = payload.player
    if player is None:
        return

    channel = getattr(player, "home", None)
    if channel is not None:
        await channel.send(
            f"⚠️ Couldn't play **{payload.track.title}** — skipping. "
            f"({payload.exception.get('message') or 'no details from source'})"
        )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    """
    Auto-disconnect when the bot is left alone in a voice channel. With autoplay
    enabled the bot would otherwise happily DJ for an empty room all night.
    """
    # Only care about humans leaving a channel (ignore the bot's own updates).
    if member.bot or before.channel is None:
        return

    player: wavelink.Player | None = member.guild.voice_client  # type: ignore
    if player is None or player.channel != before.channel:
        return

    # Any non-bot members still in the bot's channel? Then nothing to do.
    if any(not m.bot for m in player.channel.members):
        return

    channel = getattr(player, "home", None)
    if channel is not None:
        await channel.send("Everyone left the voice channel — stopping playback.")
    await player.disconnect()


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def lavalink_ready() -> bool:
    """
    True when a Lavalink node is actually connected and able to serve audio.

    Lavalink is a separate process, so it can be down (or still booting) while
    the bot is perfectly healthy. Every audio path checks this first, because
    without it the failure surfaces as an unexplained hang.
    """
    return any(
        node.status is wavelink.NodeStatus.CONNECTED
        for node in wavelink.Pool.nodes.values()
    )


async def get_player(interaction: discord.Interaction) -> wavelink.Player | None:
    """
    Return the guild's active player, connecting to the user's voice channel if
    needed. Returns None (after replying with an error) when the user isn't in a
    voice channel.
    """
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore

    if player is None:
        # The user must be in a voice channel for us to know where to join.
        if not interaction.user.voice or not interaction.user.voice.channel:
            msg = "You need to join a voice channel first."
            # Callers defer before calling us (voice connects can be slow), so
            # reply via followup when the initial response is already used.
            if interaction.response.is_done():
                await interaction.followup.send(msg)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return None

        player = await connect_player(interaction.user.voice.channel)

    return player


async def connect_player(channel: discord.VoiceChannel) -> wavelink.Player:
    """
    Join `channel` and return the new player with this bot's standard settings.
    Shared by the slash commands and the web control panel so both behave the
    same way after joining.
    """
    player: wavelink.Player = await channel.connect(cls=wavelink.Player)

    # Enable autoplay: when player.queue empties, Wavelink fills and plays
    # from player.auto_queue with recommended tracks automatically.
    player.autoplay = wavelink.AutoPlayMode.enabled

    # Disconnect after this many seconds of sitting idle (see the
    # on_wavelink_inactive_player event above).
    player.inactive_timeout = INACTIVE_TIMEOUT

    await player.set_volume(DEFAULT_VOLUME)
    return player


async def report_component_error(interaction: discord.Interaction, error: Exception) -> None:
    """
    Tell the clicker why a button or dropdown failed.

    discord.py swallows errors raised inside a component callback: the user just
    sees "didn't respond in time", which reads as a network problem and hides a
    real bug. Shared by both views so no component can fail silently.
    """
    message = f"⚠️ That didn't work: `{type(error).__name__}: {error}`"[:1900]
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass  # interaction already expired — the caller's log still has the detail


# --------------------------------------------------------------------------- #
# Search-result picker (the dropdown shown by /play)
# --------------------------------------------------------------------------- #
class TrackSelectView(discord.ui.View):
    """
    Shows up to 5 search results in a dropdown so the user can pick which one
    to queue, instead of the bot silently grabbing the top match.
    """

    def __init__(
        self,
        tracks: list[wavelink.Playable],
        player: wavelink.Player,
        requester: discord.abc.User,
    ) -> None:
        super().__init__(timeout=30)  # dropdown expires after 30s of no reply
        self.tracks = tracks
        self.player = player
        self.requester = requester
        self.message: discord.InteractionMessage | None = None

        options = []
        for i, track in enumerate(tracks):
            description = track.author or "Unknown artist"
            if track.length:
                description += f" • {format_duration(track.length)}"
            options.append(
                discord.SelectOption(
                    label=track.title[:100],
                    description=description[:100],
                    value=str(i),
                )
            )

        select = discord.ui.Select(placeholder="Choose a track to queue...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction) -> None:
        # Only the person who ran /play should be able to use their own picker.
        if interaction.user != self.requester:
            await interaction.response.send_message(
                "Only the person who ran /play can pick from this list.", ephemeral=True
            )
            return

        # Acknowledge FIRST. Discord kills a component interaction that isn't
        # answered within 3 seconds, and queuing a track calls Lavalink and then
        # redraws the panel over the Discord API — easily more than 3s on a slow
        # search. Deferring stops that from surfacing as "didn't respond in time"
        # on a track that actually queued fine.
        await interaction.response.defer()

        select: discord.ui.Select = self.children[0]  # type: ignore
        track = self.tracks[int(select.values[0])]

        message = await enqueue_track(self.player, track, interaction.user.id)

        select.disabled = True
        select.placeholder = f"Added: {track.title}"
        # Deferred, so the first response is spent — edit it rather than send.
        await interaction.edit_original_response(content=message, view=self)
        self.stop()

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        """Surface dropdown failures instead of letting them look like a timeout."""
        logging.exception("Error in track picker", exc_info=error)
        await report_component_error(interaction, error)

    async def on_timeout(self) -> None:
        # Disable the dropdown once it expires so it doesn't look clickable.
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="Track selection timed out.", view=self)
            except discord.HTTPException:
                pass  # message was deleted or otherwise unreachable — nothing to do


class PlayerControlView(discord.ui.View):
    """
    The LunaBot-style control panel attached under the queue embed:

      Row 0: "Select a suggested song" dropdown (related tracks)
      Row 1: ▶️ resume · ⏮️ back · ⏸️ pause · ⏭️ skip · 🔁 loop
      Row 2: 🔉 vol-down · ⏪ rewind · 🤍 like · ⏩ forward · 🔊 vol-up
      Row 3: 🔀 shuffle · ⏹️ stop · 🎚️ filter · ♾️ autoplay · 🔁 replay

    timeout=None so the panel stays interactive for the whole session. Every
    handler re-reads the live player from the guild, so it keeps working even
    after tracks change underneath it.
    """

    def __init__(
        self, player: wavelink.Player, suggestions: list[wavelink.Playable]
    ) -> None:
        super().__init__(timeout=None)
        self.suggestions = suggestions or []

        # Row 0 — suggestions dropdown (only if we actually have any).
        if self.suggestions:
            options = []
            for i, track in enumerate(self.suggestions):
                meta = f"{track.author or 'Unknown'} - {track.source} - "
                meta += format_duration(track.length) if track.length else "LIVE"
                options.append(
                    discord.SelectOption(
                        label=track.title[:100], description=meta[:100], value=str(i)
                    )
                )
            self.select = discord.ui.Select(
                placeholder="Select a suggested song", options=options, row=0
            )
            self.select.callback = self._pick_suggestion
            self.add_item(self.select)

        # Rows 1-3 — control buttons laid out to match the reference bot.
        # (emoji, row, handler).
        buttons = [
            ("▶️", 1, self._resume), ("⏮️", 1, self._back), ("⏸️", 1, self._pause),
            ("⏭️", 1, self._skip), ("🔁", 1, self._loop),
            ("🔉", 2, self._vol_down), ("⏪", 2, self._rewind), ("🤍", 2, self._like),
            ("⏩", 2, self._forward), ("🔊", 2, self._vol_up),
            ("🎤", 3, self._info), ("🔀", 3, self._shuffle), ("✖️", 3, self._stop),
            ("🔽", 3, self._filter), ("👤", 3, self._autoplay),
        ]
        for emoji, row, handler in buttons:
            button = discord.ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary, row=row)
            button.callback = handler
            self.add_item(button)

    # ---- helpers ---------------------------------------------------------- #
    @staticmethod
    def _player(interaction: discord.Interaction) -> wavelink.Player | None:
        return interaction.guild.voice_client  # type: ignore

    async def _rerender(self, interaction: discord.Interaction, player: wavelink.Player) -> None:
        """
        Re-render this panel in place with the updated embed.

        Handles both cases: a fast handler that hasn't answered yet edits its
        response directly, while one that deferred (because its work could
        outrun Discord's 3s window) has already spent that response and must
        edit it instead. Checking here lets either kind call this safely.
        """
        embed = build_panel_embed(player)
        view = PlayerControlView(player, self.suggestions)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)

    async def _need_player(self, interaction: discord.Interaction) -> wavelink.Player | None:
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return player

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        """Surface panel-button failures instead of letting them look like a timeout."""
        logging.exception("Error in player panel", exc_info=error)
        await report_component_error(interaction, error)

    # ---- suggestion dropdown ---------------------------------------------- #
    async def _pick_suggestion(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        # Same 3s component window as the /play picker: queuing hits Lavalink and
        # redraws the panel, so acknowledge before starting that work.
        await interaction.response.defer()
        track = self.suggestions[int(self.select.values[0])]
        track.extras = {"requester_id": str(interaction.user.id)}
        await player.queue.put_wait(track)
        if not player.playing:
            await player.play(player.queue.get())
        await self._rerender(interaction, player)

    # ---- row 1 ------------------------------------------------------------ #
    async def _resume(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        await player.pause(False)
        await self._rerender(interaction, player)

    async def _pause(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        await player.pause(True)
        await self._rerender(interaction, player)

    async def _skip(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        # The track_start event reposts the panel; just ack this click.
        await player.skip(force=True)
        await interaction.response.defer()

    async def _back(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        history = list(player.queue.history or [])
        if history and player.current and history[-1] == player.current:
            history.pop()
        if not history:
            await interaction.response.send_message("No previous track.", ephemeral=True)
            return
        await interaction.response.defer()
        await player.play(history[-1])

    async def _loop(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        # Cycle normal -> loop (track) -> loop_all (queue) -> normal.
        nxt = {
            wavelink.QueueMode.normal: wavelink.QueueMode.loop,
            wavelink.QueueMode.loop: wavelink.QueueMode.loop_all,
            wavelink.QueueMode.loop_all: wavelink.QueueMode.normal,
        }
        player.queue.mode = nxt[player.queue.mode]
        await self._rerender(interaction, player)

    # ---- row 2 ------------------------------------------------------------ #
    async def _vol_down(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        await player.set_volume(max(0, player.volume - 10))
        await self._rerender(interaction, player)

    async def _vol_up(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        await player.set_volume(min(1000, player.volume + 10))
        await self._rerender(interaction, player)

    async def _rewind(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None or not player.current:
            await interaction.response.defer()
            return
        await player.seek(max(0, player.position - 10_000))
        await interaction.response.defer()

    async def _forward(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None or not player.current:
            await interaction.response.defer()
            return
        await player.seek(min(player.current.length, player.position + 10_000))
        await interaction.response.defer()

    async def _like(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        title = player.current.title if player and player.current else "this track"
        await interaction.response.send_message(f"🤍 Liked **{title}**.", ephemeral=True)

    # ---- row 3 ------------------------------------------------------------ #
    async def _shuffle(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        player.queue.shuffle()
        await self._rerender(interaction, player)

    async def _stop(self, interaction: discord.Interaction) -> None:
        left = await force_leave(interaction.guild)
        msg = "⏹️ Stopped and disconnected." if left else "I'm not connected."
        # The panel's player is gone now — drop the components.
        await interaction.response.edit_message(content=msg, embed=None, view=None)

    async def _filter(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        # Toggle a "nightcore" (faster + higher pitch) effect via timescale.
        gid = interaction.guild.id
        filters = player.filters
        if gid in _nightcore_guilds:
            filters.timescale.reset()
            _nightcore_guilds.discard(gid)
            note = "Nightcore off"
        else:
            filters.timescale.set(speed=1.2, pitch=1.2)
            _nightcore_guilds.add(gid)
            note = "Nightcore on"
        await player.set_filters(filters)
        await interaction.response.send_message(f"🎚️ {note}.", ephemeral=True)

    async def _autoplay(self, interaction: discord.Interaction) -> None:
        player = await self._need_player(interaction)
        if player is None:
            return
        if player.autoplay == wavelink.AutoPlayMode.enabled:
            player.autoplay = wavelink.AutoPlayMode.disabled
            note = "Autoplay off"
        else:
            player.autoplay = wavelink.AutoPlayMode.enabled
            note = "Autoplay on"
        await interaction.response.send_message(f"♾️ {note}.", ephemeral=True)

    async def _info(self, interaction: discord.Interaction) -> None:
        # 🎤 — show the current track's details (with a progress bar) privately.
        player = self._player(interaction)
        if player is None or not player.current:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        embed = build_track_embed(player.current, position=player.position)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def show_panel(player: wavelink.Player, *, repost: bool = False) -> None:
    """
    Create or update the control panel in the player's home channel.

    repost=True deletes the old panel and posts a fresh one at the bottom of the
    channel (used when a new track starts, so the panel "follows" the music).
    repost=False edits the existing panel in place (used when only the queue or
    settings changed).
    """
    channel = getattr(player, "home", None)
    if channel is None:
        return

    embed = build_panel_embed(player)
    view = PlayerControlView(player, getattr(player, "suggestions", []))
    message = getattr(player, "panel_message", None)

    if repost and message is not None:
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        message = None

    if message is not None:
        try:
            await message.edit(embed=embed, view=view)
            return
        except discord.HTTPException:
            pass  # message vanished — fall through and post a new one

    player.panel_message = await channel.send(embed=embed, view=view)


# --------------------------------------------------------------------------- #
# Slash commands
# --------------------------------------------------------------------------- #
async def enqueue_track(
    player: wavelink.Player, track: wavelink.Playable, requester_id: int | str
) -> str:
    """
    Add one track to the queue, tagging its requester, and start playback if the
    player is idle. Returns the LunaBot-style "added to the queue" message, then
    (re)renders the control panel so the queue count/position stays in sync.
    """
    track.extras = {"requester_id": str(requester_id)}
    await player.queue.put_wait(track)
    position = len(player.queue)  # the track we just appended is last in line
    duration = format_duration(track.length) if track.length else "LIVE"

    if not player.playing:
        # Nothing was playing — start now; the track_start event posts the panel.
        await player.play(player.queue.get())
    else:
        # Already playing — just refresh the existing panel's queue count.
        await show_panel(player, repost=False)

    return f"🎶 **{track.title}** added to the queue ( {duration} ) - at position {position}"


@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(query="A search term or a direct URL (YouTube, SoundCloud, ...)")
async def play(interaction: discord.Interaction, query: str) -> None:
    # Defer FIRST: joining voice + searching can exceed Discord's 3-second
    # acknowledgement window, which would kill the interaction entirely.
    await interaction.response.defer()

    # Lavalink is a separate process and is the usual thing to be missing. Say so
    # plainly rather than joining voice and dying halfway through a search.
    if not lavalink_ready():
        await interaction.followup.send(
            "⚠️ The audio server (Lavalink) isn't running, so I can't play anything.\n"
            "Start it with **start.bat**, wait for *“Lavalink is ready to accept "
            "connections”*, then try again."
        )
        return

    player = await get_player(interaction)
    if player is None:
        return

    # Remember which channel to post the control panel in for this session.
    player.home = interaction.channel

    # Playable.search returns a list of tracks, or a Playlist object if the query
    # resolved to a playlist. An empty list means nothing was found.
    tracks: wavelink.Search = await wavelink.Playable.search(query)
    if not tracks:
        await interaction.followup.send("No tracks found for that query.")
        return

    if isinstance(tracks, wavelink.Playlist):
        # Add the whole playlist to the manual queue — no picker for playlists.
        for track in tracks.tracks:
            track.extras = {"requester_id": str(interaction.user.id)}
        added = await player.queue.put_wait(tracks)
        await interaction.followup.send(
            f"Added **{added}** tracks from playlist **{tracks.name}** to the queue."
        )
        if not player.playing:
            await player.play(player.queue.get())
        else:
            await show_panel(player, repost=False)
        return

    if len(tracks) == 1:
        # A direct URL resolves to exactly one track — nothing to choose between.
        message = await enqueue_track(player, tracks[0], interaction.user.id)
        await interaction.followup.send(message)
        return

    # A text search — let the user pick which of the top results to queue.
    view = TrackSelectView(tracks[:5], player, interaction.user)
    view.message = await interaction.followup.send(
        "Select a track to add to the queue:", view=view
    )


@bot.tree.command(name="skip", description="Skip the current track.")
async def skip(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None or not player.playing:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    # force=True stops the current track immediately and advances the queue
    # (or triggers autoplay if the queue is empty).
    await player.skip(force=True)
    await interaction.response.send_message("⏭️ Skipped.")


@bot.tree.command(name="back", description="Play the previous track again.")
async def back(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return

    # Manually-played tracks land in queue.history (autoplay recommendations do
    # NOT — wavelink keeps those in auto_queue.history). So the tail of history
    # is the current track only when the current track was manually queued;
    # drop it in that case, then the new tail is the true "previous" track.
    history = list(player.queue.history or [])
    if history and player.current and history[-1] == player.current:
        history.pop()

    if not history:
        await interaction.response.send_message("No previous track to go back to.", ephemeral=True)
        return

    previous_track = history[-1]
    await interaction.response.send_message(f"⏮️ Going back to **{previous_track.title}**.")
    await player.play(previous_track)


@bot.tree.command(name="queue", description="Show the current queue.")
async def queue(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return

    embed = discord.Embed(title="Queue", color=discord.Color.blurple())

    # Currently playing track.
    if player.current:
        embed.add_field(
            name="Now Playing",
            value=f"**{player.current.title[:200]}** — {player.current.author[:100]}",
            inline=False,
        )

    # Upcoming manually-queued tracks (these always play before autoplay picks).
    # Embed fields cap at 1024 chars, so show 10 entries with title/author
    # truncated so the total can never overflow (10 × ~98 chars < 1024).
    if player.queue:
        upcoming = "\n".join(
            f"`{i}.` {t.title[:60]} — {t.author[:30]}"
            for i, t in enumerate(player.queue, start=1)
            if i <= 10
        )
        embed.add_field(name="Up Next", value=upcoming, inline=False)
        if len(player.queue) > 10:
            embed.set_footer(text=f"...and {len(player.queue) - 10} more")
    else:
        embed.add_field(
            name="Up Next",
            value="_Queue is empty — autoplay will suggest tracks._",
            inline=False,
        )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nowplaying", description="Show what's currently playing.")
async def nowplaying(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None or not player.current:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    embed = build_track_embed(player.current, position=player.position)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="panel", description="Re-post the player control panel at the bottom of the channel.")
async def panel(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return

    # Refresh suggestions for the current track, then repost the panel.
    player.home = interaction.channel
    if player.current:
        player.suggestions = await fetch_suggestions(player.current, limit=MAX_SUGGESTIONS)
    await interaction.response.send_message("Here's the player:", ephemeral=True)
    await show_panel(player, repost=True)


async def force_leave(guild: discord.Guild) -> bool:
    """
    Make the bot leave voice — robustly.

    Normally there's a wavelink Player and we just disconnect it. But if the bot
    process was restarted (or crashed) while connected, the NEW process has no
    Player object even though Discord still shows the bot sitting in the channel
    (a "ghost" connection). In that case we force-leave at the gateway level with
    change_voice_state(channel=None), which clears the stuck connection.

    Returns True if the bot was in voice (and has now left), False otherwise.
    """
    player: wavelink.Player | None = guild.voice_client  # type: ignore
    if player is not None:
        await player.disconnect()
        return True

    # No Player. Discord may still think the bot is in a channel (ghost). We know
    # that for sure only if the voice state is cached, but we send the gateway
    # "leave" op unconditionally anyway — it's harmless if the bot isn't actually
    # connected, and it rescues ghosts whose voice state never made it to cache.
    me = guild.me
    in_voice = bool(me and me.voice and me.voice.channel)
    await guild.change_voice_state(channel=None)
    return in_voice


@bot.tree.command(name="stop", description="Stop playback and disconnect.")
async def stop(interaction: discord.Interaction) -> None:
    left = await force_leave(interaction.guild)
    if left:
        await interaction.response.send_message("⏹️ Stopped and disconnected.")
    else:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)


@bot.tree.command(name="disconnect", description="Leave the voice channel.")
async def disconnect(interaction: discord.Interaction) -> None:
    left = await force_leave(interaction.guild)
    if left:
        await interaction.response.send_message("👋 Disconnected.")
    else:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)


@bot.tree.command(name="join", description="Join your voice channel.")
async def join(interaction: discord.Interaction) -> None:
    existing: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if existing is not None:
        await interaction.response.send_message(
            f"Already connected to **{existing.channel.name}**.", ephemeral=True
        )
        return

    # Defer first — voice connects can exceed the 3s acknowledgement window.
    await interaction.response.defer()

    player = await get_player(interaction)
    if player is None:
        return

    player.home = interaction.channel
    await interaction.followup.send(f"Joined **{player.channel.name}**.")


@bot.tree.command(name="pause", description="Pause the current track.")
async def pause(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None or not player.playing:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    await player.pause(True)
    await interaction.response.send_message("⏸️ Paused.")


@bot.tree.command(name="resume", description="Resume a paused track.")
async def resume(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return

    await player.pause(False)
    await interaction.response.send_message("▶️ Resumed.")


@bot.tree.command(name="volume", description="Set this session's playback volume.")
@app_commands.describe(level="Volume percentage, 0-1000 (100 = normal volume)")
async def volume(interaction: discord.Interaction, level: app_commands.Range[int, 0, 1000]) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return

    await player.set_volume(level)
    await interaction.response.send_message(f"🔊 Volume set to {level}%.")


@bot.tree.command(
    name="defaultvolume",
    description="Set the volume the bot uses whenever it joins a voice channel (saved to .env).",
)
@app_commands.describe(level="Default volume percentage, 0-1000 (100 = normal volume)")
async def defaultvolume(interaction: discord.Interaction, level: app_commands.Range[int, 0, 1000]) -> None:
    global DEFAULT_VOLUME
    DEFAULT_VOLUME = level
    set_key(ENV_PATH, "DEFAULT_VOLUME", str(level))
    await interaction.response.send_message(
        f"Default join volume set to {level}% (applies next time the bot joins a channel)."
    )


@bot.tree.command(name="loop", description="Loop the current track, the whole queue, or turn looping off.")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Track", value="track"),
        app_commands.Choice(name="Queue", value="queue"),
    ]
)
async def loop(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return

    queue_modes = {
        "off": wavelink.QueueMode.normal,
        "track": wavelink.QueueMode.loop,
        "queue": wavelink.QueueMode.loop_all,
    }
    player.queue.mode = queue_modes[mode.value]
    await interaction.response.send_message(f"🔁 Loop mode set to **{mode.name}**.")


@bot.tree.command(name="autoplay", description="Control recommended-track autoplay when the queue is empty.")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="On", value="enabled"),
        app_commands.Choice(name="Partial (populate queue, don't auto-start)", value="partial"),
        app_commands.Choice(name="Off", value="disabled"),
    ]
)
async def autoplay(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None:
        await interaction.response.send_message("I'm not connected.", ephemeral=True)
        return

    autoplay_modes = {
        "enabled": wavelink.AutoPlayMode.enabled,
        "partial": wavelink.AutoPlayMode.partial,
        "disabled": wavelink.AutoPlayMode.disabled,
    }
    player.autoplay = autoplay_modes[mode.value]
    await interaction.response.send_message(f"Autoplay set to **{mode.name}**.")


@bot.tree.command(name="seek", description="Jump to a specific position in the current track.")
@app_commands.describe(time='Position to seek to, e.g. "90" (seconds) or "1:30" (mm:ss)')
async def seek(interaction: discord.Interaction, time: str) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None or not player.current:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    try:
        position = parse_timestamp(time)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    position = max(0, min(position, player.current.length))
    await player.seek(position)
    await interaction.response.send_message(f"⏩ Seeked to {format_duration(position)}.")


@bot.tree.command(name="fastforward", description="Skip forward in the current track.")
@app_commands.describe(seconds="How many seconds to jump forward (default 10)")
async def fastforward(interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 3600] = 10) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None or not player.current:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    position = min(player.position + seconds * 1000, player.current.length)
    await player.seek(position)
    await interaction.response.send_message(f"⏩ Jumped forward to {format_duration(position)}.")


@bot.tree.command(name="rewind", description="Skip backward in the current track.")
@app_commands.describe(seconds="How many seconds to jump back (default 10)")
async def rewind(interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 3600] = 10) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None or not player.current:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    position = max(player.position - seconds * 1000, 0)
    await player.seek(position)
    await interaction.response.send_message(f"⏪ Jumped back to {format_duration(position)}.")


@bot.tree.command(name="replay", description="Restart the current track from the beginning.")
async def replay(interaction: discord.Interaction) -> None:
    player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore
    if player is None or not player.current:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return

    await player.seek(0)
    await interaction.response.send_message("🔁 Restarted the track.")


# --------------------------------------------------------------------------- #
# Local control API
# --------------------------------------------------------------------------- #
# A tiny HTTP JSON API (aiohttp ships with discord.py, so no new dependency)
# that lets the desktop app (musicgang_app.pyw) drive the bot instead of typing
# slash commands. It runs on the bot's own event loop, so handlers call the
# same player methods the slash commands use.
#
# The server has no authentication and is bound to 127.0.0.1 ONLY (see
# WEB_HOST). Do not change that to 0.0.0.0 — anyone on the network could then
# control the bot.
def main_guild() -> discord.Guild | None:
    """The single server this bot serves: GUILD_ID if set, else the first one."""
    if GUILD_ID:
        guild = bot.get_guild(int(GUILD_ID))
        if guild is not None:
            return guild
    return bot.guilds[0] if bot.guilds else None


def pick_voice_channel(guild: discord.Guild) -> discord.VoiceChannel | None:
    """
    The voice channel a web request should join: the one with the most humans
    in it. The browser has no Discord identity, so "your" channel is unknown;
    the busiest one is the right guess for a single-server bot.
    """
    best: discord.VoiceChannel | None = None
    best_count = 0
    for channel in guild.voice_channels:
        count = sum(1 for m in channel.members if not m.bot)
        if count > best_count:
            best, best_count = channel, count
    return best


def _track_json(track: wavelink.Playable, guild: discord.Guild | None) -> dict:
    try:
        rid = track.extras.requester_id
    except AttributeError:
        rid = None
    if rid == "web":
        requester = "Control panel"
    elif rid and guild is not None and guild.get_member(int(rid)):
        requester = guild.get_member(int(rid)).display_name
    elif rid:
        requester = "Discord user"
    else:
        requester = "Autoplay"
    return {
        "title": track.title,
        "author": track.author,
        "length": track.length,
        "uri": track.uri,
        "artwork": track.artwork,
        "source": track.source,
        "requester": requester,
    }


def web_state() -> dict:
    """Everything panel.html needs to draw itself, in one JSON object."""
    guild = main_guild()
    player: wavelink.Player | None = guild.voice_client if guild else None  # type: ignore

    channels = []
    if guild is not None:
        for channel in guild.voice_channels:
            channels.append({
                "id": str(channel.id),
                "name": channel.name,
                "humans": sum(1 for m in channel.members if not m.bot),
            })

    state = {
        "bot": bot.user.name if bot.user else None,
        "guild": guild.name if guild else None,
        "lavalink": lavalink_ready(),
        "channels": channels,
        "connected": player is not None,
        "channel": player.channel.name if player else None,
        "channel_id": str(player.channel.id) if player else None,
        "current": None,
        "position": 0,
        "paused": False,
        "volume": DEFAULT_VOLUME,
        "loop": "off",
        "autoplay": "enabled",
        "queue": [],
        "history": [],
    }
    if player is not None:
        loop_names = {
            wavelink.QueueMode.normal: "off",
            wavelink.QueueMode.loop: "track",
            wavelink.QueueMode.loop_all: "queue",
        }
        state.update({
            "current": _track_json(player.current, guild) if player.current else None,
            "position": player.position,
            "paused": player.paused,
            "volume": player.volume,
            "loop": loop_names.get(player.queue.mode, "off"),
            "autoplay": player.autoplay.name,
            "queue": [_track_json(t, guild) for t in list(player.queue)[:50]],
            "history": [_track_json(t, guild) for t in list(player.queue.history or [])[-10:]],
        })
    return state


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"error": message}, status=status)


async def _web_player(*, connect: bool) -> wavelink.Player | web.Response:
    """
    Resolve the guild's player for a web request. With connect=True, join the
    busiest voice channel when not connected. Returns an error Response
    instead of a player when that isn't possible.
    """
    guild = main_guild()
    if guild is None:
        return _error("The bot is not in any server yet.", 503)
    player: wavelink.Player | None = guild.voice_client  # type: ignore
    if player is not None:
        return player
    if not connect:
        return _error("Not connected to a voice channel.", 409)
    if not lavalink_ready():
        return _error("Lavalink (the audio server) is not running.", 503)
    channel = pick_voice_channel(guild)
    if channel is None:
        return _error("Nobody is in a voice channel — join one in Discord first.", 409)
    return await connect_player(channel)


async def web_index(_request: web.Request) -> web.Response:
    """The desktop app's page (musicgang_app.pyw loads this URL)."""
    return web.FileResponse(UI_HTML, headers={"Cache-Control": "no-store"})


async def web_get_state(_request: web.Request) -> web.Response:
    return _json(web_state())


async def web_search(request: web.Request) -> web.Response:
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return _error("Empty search.")
    if not lavalink_ready():
        return _error("Lavalink (the audio server) is not running.", 503)
    result: wavelink.Search = await wavelink.Playable.search(query)
    if isinstance(result, wavelink.Playlist):
        return _json({
            "playlist": {"name": result.name, "count": len(result.tracks)},
            "tracks": [_track_json(t, None) for t in result.tracks[:10]],
        })
    return _json({"playlist": None, "tracks": [_track_json(t, None) for t in result[:10]]})


async def web_play(request: web.Request) -> web.Response:
    """
    Queue a track (or whole playlist) from a search string or URL. For a text
    search the top result is used; the page's search box lets the user pick a
    specific result and sends its URL here instead.
    """
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return _error("Nothing to play.")
    player = await _web_player(connect=True)
    if isinstance(player, web.Response):
        return player

    result: wavelink.Search = await wavelink.Playable.search(query)
    if not result:
        return _error("No tracks found for that query.", 404)

    if isinstance(result, wavelink.Playlist):
        for track in result.tracks:
            track.extras = {"requester_id": "web"}
        added = await player.queue.put_wait(result)
        if not player.playing:
            await player.play(player.queue.get())
        else:
            await show_panel(player, repost=False)
        message = f"Added {added} tracks from {result.name}"
    else:
        # enqueue_track's text is Discord markdown; the desktop app shows plain text.
        message = (await enqueue_track(player, result[0], "web")).replace("**", "")

    return _json({"message": message, "state": web_state()})


async def web_action(request: web.Request) -> web.Response:
    """One endpoint for the simple controls: /api/<action> with an optional JSON body."""
    action = request.match_info["action"]
    body = await request.json() if request.can_read_body else {}

    if action == "join":
        guild = main_guild()
        if guild is None:
            return _error("The bot is not in any server yet.", 503)
        channel = guild.get_channel(int(body["channel_id"]))
        if not isinstance(channel, discord.VoiceChannel):
            return _error("Unknown voice channel.", 404)
        if not lavalink_ready():
            return _error("Lavalink (the audio server) is not running.", 503)
        perms = channel.permissions_for(guild.me)
        if not (perms.connect and perms.speak):
            return _error(f"I don't have permission to join {channel.name}.", 403)
        existing: wavelink.Player | None = guild.voice_client  # type: ignore
        if existing is not None:
            # Already connected: move the player (keeps playback) rather than
            # disconnect + reconnect, which races and can end up nowhere.
            if existing.channel.id != channel.id:
                await existing.move_to(channel)
        else:
            await connect_player(channel)
        return _json(web_state())

    if action == "stop":
        guild = main_guild()
        if guild is not None:
            await force_leave(guild)
        return _json(web_state())

    player = await _web_player(connect=False)
    if isinstance(player, web.Response):
        return player

    if action == "pause":
        await player.pause(True)
    elif action == "resume":
        await player.pause(False)
    elif action == "skip":
        await player.skip(force=True)
    elif action == "back":
        history = list(player.queue.history or [])
        if history and player.current and history[-1] == player.current:
            history.pop()
        if not history:
            return _error("No previous track.", 409)
        await player.play(history[-1])
    elif action == "seek":
        if not player.current:
            return _error("Nothing is playing.", 409)
        position = max(0, min(int(body.get("position", 0)), player.current.length))
        await player.seek(position)
    elif action == "volume":
        await player.set_volume(max(0, min(1000, int(body.get("level", 100)))))
    elif action == "shuffle":
        player.queue.shuffle()
    elif action == "remove":
        index = int(body.get("index", -1))
        if not 0 <= index < len(player.queue):
            return _error("No such queue entry.", 404)
        player.queue.delete(index)
    elif action == "clear":
        player.queue.clear()
    elif action == "loop":
        modes = {
            "off": wavelink.QueueMode.normal,
            "track": wavelink.QueueMode.loop,
            "queue": wavelink.QueueMode.loop_all,
        }
        player.queue.mode = modes.get(body.get("mode", "off"), wavelink.QueueMode.normal)
    elif action == "autoplay":
        modes = {
            "enabled": wavelink.AutoPlayMode.enabled,
            "partial": wavelink.AutoPlayMode.partial,
            "disabled": wavelink.AutoPlayMode.disabled,
        }
        player.autoplay = modes.get(body.get("mode", "enabled"), wavelink.AutoPlayMode.enabled)
    else:
        return _error(f"Unknown action: {action}", 404)

    # Keep the Discord panel (if one is posted) in sync with what the browser did.
    if getattr(player, "panel_message", None) is not None and action not in ("skip", "back", "seek"):
        await show_panel(player, repost=False)
    return _json(web_state())


@web.middleware
async def _require_token(request: web.Request, handler) -> web.Response:
    """
    Password check. The page itself (/) is always served — it's just HTML and
    prompts for the password — but every /api/* call must carry it in the
    X-MusicGang-Token header when WEB_TOKEN is set. Constant-time compare so
    the password can't be guessed letter by letter from response timing.
    """
    if WEB_TOKEN and request.path.startswith("/api/"):
        import hmac
        supplied = request.headers.get("X-MusicGang-Token", "")
        if not hmac.compare_digest(supplied.encode(), WEB_TOKEN.encode()):
            return _error("Wrong password.", 401)
    return await handler(request)


@web.middleware
async def _errors_as_json(request: web.Request, handler) -> web.Response:
    """
    Turn an unexpected exception into a JSON error the desktop app can show as
    a toast (and log it), instead of aiohttp's bare 500 "Internal Server Error".
    """
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - reported to the user and logged
        logging.exception("Control API error on %s %s", request.method, request.path)
        return _error(f"{type(exc).__name__}: {exc}"[:300], 500)


def tailscale_ip() -> str | None:
    """This PC's Tailscale IPv4 (100.x.y.z), or None if Tailscale isn't up."""
    import shutil
    import subprocess

    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    ip = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
    return ip if ip.startswith("100.") else None


async def start_control_api() -> None:
    # Decide where to listen BEFORE binding anything, and refuse unsafe setups.
    hosts = ["127.0.0.1"]
    if WEB_HOST == "tailscale":
        if not WEB_TOKEN:
            raise RuntimeError("WEB_HOST=tailscale needs WEB_TOKEN set in .env (a password).")
        ip = tailscale_ip()
        if ip is None:
            logging.warning("WEB_HOST=tailscale but Tailscale is not running — API is local-only until restart.")
        else:
            hosts.append(ip)
    elif WEB_HOST != "127.0.0.1":
        raise RuntimeError(
            f"WEB_HOST={WEB_HOST!r} is not allowed. Use 127.0.0.1 or tailscale (see .env.example)."
        )

    app = web.Application(middlewares=[_errors_as_json, _require_token])
    app.add_routes([
        web.get("/", web_index),
        web.get("/api/state", web_get_state),
        web.post("/api/search", web_search),
        web.post("/api/play", web_play),
        web.post("/api/{action}", web_action),
    ])
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    for host in hosts:
        await web.TCPSite(runner, host, WEB_PORT).start()
        logging.info(
            "Control API for the desktop app: http://%s:%s%s",
            host, WEB_PORT, " (password required)" if WEB_TOKEN else "",
        )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    preflight()
    bot.run(DISCORD_TOKEN)
