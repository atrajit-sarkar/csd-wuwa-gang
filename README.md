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

## Run Linae bot

- `python run_bot.py --type character --bot-name Linae --character-name Lynae --token-env LINAE_BOT_TOKEN`

## Run Admin bot

- `python run_bot.py --type admin --bot-name Admin --token-env ADMIN_BOT_TOKEN`

## Run all bots (manager)

- `python run_all.py`

## Add more API keys

API keys are stored in Firestore (not in `.env`).

Admin (server channel): run this slash command in the configured energy channel:

- `/add_more_energy keys:key1,key2,key3`

Users (DM): DM the admin bot:

- `add_more_energy key1,key2,key3`

All additions record who added the keys.
