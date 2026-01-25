# csd-wuwa-gang bots

## Setup

- Install deps: `pip install -r requirements.txt`
- Ensure `.env` has:
  - One token per bot (matches `token_env` in `bots.json`), e.g. `LINAE_BOT_TOKEN`
  - `ADMIN_BOT_TOKEN` (utility-only bot for managing API keys)
  - `GUILD_ID`
  - `TARGET_CHANNEL_ID` (bot replies only here)
  - `CHANNEL_ID` (energy/admin channel; used for key failure notices and `/add_more_energy`)
  - `FIREBASE_CREDENTIALS_PATH` (defaults to `service.json`)
  - `FIRESTORE_COLLECTION`
  - Optional: `FIRESTORE_ADMIN_KEYS_DOC` (defaults to `admin_keys`)

Note: by default, `.env` will NOT override existing process environment variables. If you run under a process manager like `pm2` and you updated `.env` but the process still uses old values, set `DOTENV_OVERRIDE=1` and restart.

## Run Linae bot

- `python run_bot.py --type character --bot-name Linae --character-name Lynae --token-env LINAE_BOT_TOKEN`

## Run Admin bot

- `python run_bot.py --type admin --bot-name Admin --token-env ADMIN_BOT_TOKEN`

## Run all bots (manager)

- `python run_all.py`

## Reset Firestore (start fresh)

This project stores per-user memory and API keys in Firestore under `FIRESTORE_COLLECTION` (default `wuwa-gang`).

To wipe that collection (including nested subcollections like `recent_messages`) so the bots restart from a clean slate:

- Dry run (shows target project + collection): `python tools/wipe_firestore.py`
- Actually delete: `python tools/wipe_firestore.py --yes`

Notes:

- Uses `.env` + `FIREBASE_CREDENTIALS_PATH` (default `service.json`) to pick the Firebase project.
- This is destructive; it deletes *everything* under the configured collection.

## Add more API keys

API keys are stored in Firestore (not in `.env`).

Admin (server channel): run this slash command in the configured energy channel:

- `/add_more_energy keys:key1,key2,key3`

Users (DM): DM the admin bot:

- `add_more_energy key1,key2,key3`

All additions record who added the keys.

## ElevenLabs voice replies (optional)

Bots can (sometimes) send an audio attachment (TTS) to feel more human.

### 1) Store ElevenLabs API keys (Firestore)

Admin (server channel): run this slash command in the configured energy channel:

- `/add_voice_energy keys:key1,key2,key3`

Users (DM): DM the admin bot:

- `/submit_voice_energy keys:key1,key2,key3`

### 2) Enable voice + choose voices per character (.env)

Add these to `.env` (voice IDs are from your ElevenLabs account):

- `ELEVENLABS_VOICE_ENABLED=1`
- `ELEVENLABS_VOICE_ID_LYNAE=...`
- `ELEVENLABS_VOICE_ID_SHOREKEEPER=...`
- `ELEVENLABS_VOICE_ID_CHISA=...`
- `ELEVENLABS_VOICE_ID_CANTARELLA=...`

Optional tuning:

- `ELEVENLABS_DEFAULT_VOICE_ID=...` (fallback if a per-character ID is missing)
- `ELEVENLABS_MODEL_ID=eleven_multilingual_v2`
- `ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128`
- `ELEVENLABS_VOICE_MAX_CHARS=420`
- `ELEVENLABS_VOICE_COOLDOWN_S=120`
- `ELEVENLABS_VOICE_FUN_PROB=0.12`

Character voice vibe suggestions (pick voices in ElevenLabs that match these):

- **Lynae**: upbeat, youthful, energetic, slightly rebellious
- **Shorekeeper**: soft, dreamy, calm, “timeless/poetic”
- **Chisa**: quiet, controlled, slightly eerie, precise
- **Cantarella**: elegant, sultry, confident, teasing

## Voice channel speaking (optional)

Bots can also join a Discord voice channel and play TTS audio there.

Requirements:

- Install deps: `pip install -r requirements.txt` (includes `PyNaCl`)
- Install `ffmpeg` and ensure it is on your `PATH` (required for voice playback)

Commands (run in the server):

- `/join_voice` — bot joins your current voice channel
- `/leave_voice` — bot leaves voice
- `/startspeak` — bot will speak its chat replies in voice (you must `/join_voice` first)
- `/stopspeak` — bot stops speaking chat replies in voice

## Discord privileged intents (deployment fix)

If you see `discord.errors.PrivilegedIntentsRequired`, at least one bot application is requesting a privileged intent (most commonly `MESSAGE CONTENT INTENT`) that is not enabled in the Discord Developer Portal for that bot.

Recommended fix (keeps normal chat replies working):

- Discord Developer Portal → your Application → **Bot** → enable **MESSAGE CONTENT INTENT**.
- Do this for **each** bot application/token you run (Admin + each character bot).

Temporary workaround (connect without message content):

- Set `DISCORD_MESSAGE_CONTENT_INTENT=0` for character bots.
- Set `DISCORD_MESSAGE_CONTENT_INTENT_ADMIN=0` for admin bot (default is `0`).
