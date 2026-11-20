# Changelog

## Unreleased
- Voice: prayers are audible to listeners who join or rejoin. Cause: `davey` 0.2 derives keys in a call discord.py 2.7.1 never makes; pinned `<0.2.0`.
- Voice: one silent-room restart, counted across restarts, then it pauses.
- Scheduler: saved times are wall clock in the guild's zone, so DST no longer moves a prayer; "next prayer" uses the guild's date.
- Dashboard: signed expiring cookie, refuses the default token, reports failed schedule rows; `/health` drops the commit.
- Queue: a malformed command cannot wedge later ones; Play Now reports back and does not block the queue.
- Config: pinned deps, `.dockerignore`, non-root image; ruff (errors only) in CI.
