# Personal Discord Music Bot

A simple, self-hostable Discord music bot for a **single server** (yours). Built
with [discord.py](https://discordpy.readthedocs.io/), [Wavelink 3](https://wavelink.dev/),
and [Lavalink v4](https://lavalink.dev/).

Commands: `/play`, `/skip`, `/queue`, `/stop`, `/pause`, `/resume`, plus
autoplay of recommended tracks when your queue runs dry.

---

## How the pieces fit together

```
  You (Discord) ──▶ bot.py (discord.py + Wavelink) ──▶ Lavalink.jar ──▶ YouTube / SoundCloud
```

- **Lavalink** is a standalone Java program that does the actual audio work. You
  run it locally as a `.jar`.
- **bot.py** is your Python bot. It talks to Discord and tells Lavalink what to play.

Both need to be running at the same time.

---

## 1. Create the bot in the Discord Developer Portal

1. Go to <https://discord.com/developers/applications> and click **New Application**. Name it.
2. Open the **Bot** tab on the left.
3. Click **Reset Token**, then **Copy** — this is your `DISCORD_TOKEN`. Keep it secret.
4. Scroll to **Privileged Gateway Intents**:
   - **Server Members Intent** — not required.
   - **Message Content Intent** — **not required** for this bot (it uses slash
     commands only). Leave it off unless you later add prefix commands.
   - The **voice** capability (`GUILD_VOICE_STATES`) is a normal, non-privileged
     intent that `discord.Intents.default()` already enables — nothing to toggle here.

> In short: for this slash-command bot you do **not** need to enable any
> privileged intents. Voice works out of the box.

### Invite the bot with the right permissions

1. Go to the **OAuth2 → URL Generator** tab.
2. Under **Scopes**, check **`bot`** and **`applications.commands`**
   (the second one is required for slash commands to appear).
3. Under **Bot Permissions**, check:
   - **View Channels**
   - **Send Messages**
   - **Embed Links**
   - **Connect** (join voice)
   - **Speak** (transmit audio)
4. Copy the generated URL at the bottom, open it in your browser, and invite the
   bot to **your** server.

---

## 2. Install Java (for Lavalink)

Lavalink v4 needs **Java 17 or newer**.

- Download from [Adoptium (Temurin)](https://adoptium.net/) — pick JDK 17 (or newer).
- Verify in a terminal:
  ```
  java -version
  ```
  You should see version 17 or higher.

---

## 3. Download and run Lavalink

1. Download `Lavalink.jar` from the
   [Lavalink releases page](https://github.com/lavalink-devs/Lavalink/releases)
   (grab the latest **v4** release).
2. Put `Lavalink.jar` and the included **`application.yml`** in the **same folder**.
3. From that folder, start it:
   ```
   java --enable-native-access=ALL-UNNAMED -jar Lavalink.jar
   ```
4. On first launch it downloads the YouTube plugin listed in `application.yml`.
   Wait until you see a line like **“Lavalink is ready to accept connections”**.
   Leave this terminal running.

> The `password` in `application.yml` (`youshallnotpass`) must match
> `LAVALINK_PASSWORD` in your `.env`. Change both together if you want.

---

## 4. Install the Python bot

From this project folder (a virtual environment is recommended):

```bash
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 5. Fill in your `.env`

1. Copy the example file:
   ```bash
   # Windows (PowerShell):
   Copy-Item .env.example .env
   # macOS/Linux:
   cp .env.example .env
   ```
2. Open `.env` and set:
   - `DISCORD_TOKEN` — the token you copied in step 1.
   - `GUILD_ID` — your server's ID (optional but recommended so slash commands
     appear instantly). Enable **Developer Mode** in Discord
     (Settings → Advanced), then right-click your server icon → **Copy Server ID**.
   - `LAVALINK_URI` / `LAVALINK_PASSWORD` — leave as-is if you didn't change
     `application.yml`.

---

## 6. Run the bot

With Lavalink still running in its own terminal, start the bot in a new one:

```bash
python bot.py
```

You should see the Lavalink node connect and the slash commands sync. In Discord,
join a voice channel and type `/play <song name>`.

---

## Desktop app (no typing needed)

`start.bat` also opens **MusicGang**, a window that controls the bot without
slash commands. Left column: the queue ("Up next"). Middle: artwork, what's
playing, a clickable progress bar, back / play-pause / skip, Shuffle, Loop and
Autoplay pills, and volume. Right: search — type a song, artist or link, press
Search, then tap **+** to queue a result (or ▶ to play it next). The **Output**
dropdown picks the voice channel.

The window has no Discord identity, so when the bot isn't in voice yet it joins
the voice channel with the most people in it. Pick a different one from the
dropdown at the bottom and press **Join**.

It talks to the bot over `http://127.0.0.1:27432` (local to your PC only, no
login — never expose that port). Port is `WEB_PORT` in `.env`. To open the
window on its own later, double-click `musicgang_app.pyw`.

The window and taskbar use `musicgang.ico`. For a desktop shortcut with the
same icon, right-click the shortcut → Properties → Change Icon → pick that file.

## Let friends control it from their PCs (Tailscale)

With [Tailscale](https://tailscale.com) (free private VPN, encrypted, nothing
exposed to the internet) everyone's MusicGang window controls one shared bot
and one shared queue — and the bot follows whoever is online:

- Whoever opens the MusicGang window first **hosts** (the bot and Lavalink
  start on their PC).
- Everyone else's window connects to that host.
- If the host closes their window, their bot stops and the open window with
  the lowest Tailscale IP takes over: it starts the bot on its PC and
  re-queues what was playing.

**Every PC in the group (same steps for all):**
1. Install Tailscale, sign in to the same tailnet (invite friends from the
   Tailscale admin console), and note each PC's IP: `tailscale ip -4`.
2. Install Java 17+, Python 3.11+, and put `Lavalink.jar` in the folder
   (README steps 2-4).
3. Use the **same** `.env` on all PCs: same `DISCORD_TOKEN`, same `GUILD_ID`,
   `WEB_HOST=tailscale`, the same long random `WEB_TOKEN`, and
   `MUSICGANG_PEERS=` with everyone's Tailscale IPs, comma-separated.
4. Allow the two ports through Windows Firewall, from Tailscale addresses
   only (run once, in an **administrator** PowerShell):
   ```powershell
   New-NetFirewallRule -DisplayName "MusicGang (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 27432-27433 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
5. Open the window: double-click `musicgang_app.pyw` (or `app.bat`). It
   finds the host, or becomes it.

A friend without Java/Lavalink can still join as a window-only client: set
`MUSICGANG_HOST=<a host's Tailscale IP>` instead of `MUSICGANG_PEERS`; they
never host.

Safety: the API listens only on `127.0.0.1` and the Tailscale address, refuses
anything else, and requires `WEB_TOKEN` on every request when exposed. The
bot token never leaves `.env`. Do not port-forward 27432 on your router.

## Usage

| Command | What it does |
|---|---|
| `/play <query>` | Search a term (shows a dropdown of the top 5 matches to pick from) or paste a URL/playlist link (plays/queues directly, no dropdown). |
| `/skip` | Skip the current track. |
| `/back` | Play the previous track again. |
| `/queue` | Show what's playing and what's next. |
| `/nowplaying` | Show the current track with a progress bar. |
| `/pause` | Pause the current track. |
| `/resume` | Resume a paused track. |
| `/stop` | Stop playback and disconnect from voice. |
| `/join` | Join your voice channel without playing anything. |
| `/disconnect` | Leave the voice channel (same as `/stop`). |
| `/volume <0-1000>` | Set this session's volume (100 = normal). |
| `/defaultvolume <0-1000>` | Set the volume used whenever the bot joins voice — persists to `.env`. |
| `/loop <off\|track\|queue>` | Loop off, repeat the current track, or repeat the whole queue. |
| `/autoplay <on\|partial\|off>` | Control whether recommended tracks auto-play when the queue empties. |
| `/seek <time>` | Jump to a position, e.g. `90` (seconds) or `1:30` (mm:ss). |
| `/fastforward [seconds]` | Jump forward in the track (default 10s). |
| `/rewind [seconds]` | Jump back in the track (default 10s). |
| `/replay` | Restart the current track from 0:00. |

`/stop` and `/disconnect` currently do the same thing (stop + leave voice) —
both are included since either name is a natural thing to reach for.

**Autoplay:** the bot uses `AutoPlayMode.enabled`. When your manually queued
songs run out, it automatically plays recommended/related tracks. Anything you
add with `/play` always plays **before** the recommendations. The "Now Playing"
message notes whether a track was queued by a user or picked by autoplay.

**Auto-disconnect:** the bot leaves voice on its own when it has been idle
(paused / nothing playing) for `INACTIVE_TIMEOUT` seconds (default 180, set in
`.env`), and immediately when every human leaves the voice channel — otherwise
autoplay would keep it DJ-ing an empty room indefinitely.

---

## Optional: Spotify-based recommendations (LavaSrc)

The default setup uses YouTube's related-tracks for autoplay, which works with no
extra setup. If you'd prefer Spotify-driven search/recommendations later:

1. In `application.yml`, uncomment the **`lavasrc`** block under `plugins:` and
   the **lavasrc dependency** under `lavalink.plugins:`.
2. Create a Spotify app at <https://developer.spotify.com/dashboard> and paste
   the `clientId` / `clientSecret` into the config.
3. Restart Lavalink.

---

## Troubleshooting

- **Slash commands don't show up:** set `GUILD_ID` in `.env` (global sync can
  take up to an hour). Make sure you invited the bot with the
  `applications.commands` scope.
- **Bot connects but no audio / YouTube errors:** update the `youtube-plugin`
  version in `application.yml` to the latest from the
  [youtube-source releases](https://github.com/lavalink-devs/youtube-source/releases),
  then restart Lavalink. YouTube occasionally changes things and the plugin gets patched.
- **"Sign in to confirm you're not a bot" in the Lavalink log:** YouTube is
  bot-checking your IP. Enable the commented `oauth` block in `application.yml`
  (instructions are inline there) and link a **throwaway** Google account.
- **`voice will NOT be supported` / `davey is not installed` in the log:** the
  voice libraries are missing for the Python that runs the bot. discord.py 2.7+
  needs both `PyNaCl` and `davey`. Run `pip install -r requirements.txt` with
  that same Python (`start.bat` does this automatically every launch).
- **`KeyError: 'DISCORD_TOKEN'`:** your `.env` is missing or the bot was started
  from a different folder. Run `python bot.py` from the folder containing `.env`.
- **Bot can't join voice:** confirm it has **Connect** and **Speak** permissions
  in that channel, and that you're in a voice channel when you run `/play`.
