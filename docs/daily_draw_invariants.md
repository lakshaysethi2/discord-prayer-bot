# Daily Draw v2 invariants (issue #26)

These rules live in `DailyDrawV2Mixin` (`bot/daily_draw_runtime.py`). Do not "simplify" them away.

## `on_interaction` (spec §8)
This is the repo's **only** component handler. discord.py exposes a single `on_interaction` hook; any future UI component must route through this method or add an explicit dispatcher.

## `on_message` (issue #49)
Mention-prefix commands live here (`@bot setticketdrawchannel <channel-id>`). This is not a component handler and must not replace or wrap `on_interaction`.

## Guild resolution
`_resolve_daily_draw_guild_ids` prefers stored `daily_draw_config` rows for guilds the bot is in. `PRAYER_DRAW_CHANNEL_ID` / `DEFAULT_CHANNEL_ID` seed first-ever guild init only; they do not override an existing row.

Defer ephemeral **first**. After defer, every reply is a followup.

## `_daily_draw_loop` / `_daily_draw_tick` (spec §7 / §9)
Loop cadence is 60 seconds. Failures are bounded per cycle (`_DAILY_DRAW_MAX_RETRIES`) so a bad channel does not spin the API forever.

## `_handle_daily_draw_button` (spec §8 / §9)
Critical section is **log-first**. A failed log write **aborts the draw**: no cooldown row, no heart increment, no winner reply.

Cooldown is checked again **inside** the per-guild lock to close the double-click race.

## `_edit_daily_draw_message`
Self-heal by reposting and calling `repoint_active_message`. Never call `start_new_day` here — that resets hearts.

## `_send_daily_draw_cooldown_reply`
Custom catpray emoji first; on send failure degrade to 🙏.

## `repost_slot` (`db/daily_draw.py`)
UPDATE-only. Safe only while nothing DELETEs `daily_draw_state` rows. An INSERT would write NULL `active_cycle_date`.
