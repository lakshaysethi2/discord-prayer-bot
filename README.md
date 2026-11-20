# Discord Prayer Bot

Plays scheduled prayer recordings into Discord voice channels, with a small FastAPI dashboard.

## Run

```bash
cp .env.example .env      # set the two tokens
make up                   # bot + dashboard
make logs
```

Dashboard: `127.0.0.1:8700`, behind a reverse proxy.

## Time and voice

A schedule stores the wall clock you typed plus the guild's zone, so a DST change does not move the prayer (`bot/prayer_time.py`). Audio rides Discord's E2EE session: the bot waits for a listener and restarts once if the room cannot decrypt (`bot/voice_e2ee.py`). `davey` is pinned `<0.2`: 0.2 derives keys in a call discord.py 2.7.1 never makes.

## Daily ticket draw

Once per local day the bot posts a **Draw your ticket** button: a random member of the configured role (drawer excluded) gets a private reply and a heart, cooldown 18h. Rows are logged before the reply — keep that order.

Needs **Server Members Intent** and **Send Messages** in the draw channel.

## Tests

`pip install -r requirements-dev.txt && pytest -q`
