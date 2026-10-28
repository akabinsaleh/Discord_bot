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
