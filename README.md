# csd-wuwa-gang bots

## Setup

- Install deps: `pip install -r requirements.txt`
- Ensure `.env` has:
  - One token per bot (matches `token_env` in `bots.json`), e.g. `LINAE_BOT_TOKEN`
  - `GUILD_ID`
  - `TARGET_CHANNEL_ID` (bot replies only here)
  - `CHANNEL_ID` (energy/admin channel; used for key failure notices and `/add_more_energy`)
  - `OLLAMA_API_KEY` (single) and/or `OLLAMA_API_KEYS` (comma-separated list)

## Run Linae bot

- `python run_bot.py --bot-name Linae --character-name Lynae --token-env LINAE_BOT_TOKEN`

## Run all bots (manager)

- `python run_all.py`

## Add more API keys

Run this slash command in the configured energy channel:

- `/add_more_energy keys:key1,key2,key3`

The keys are stored into `.env` under `OLLAMA_API_KEYS`.
