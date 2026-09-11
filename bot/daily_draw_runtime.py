"""Daily Draw v2 Discord runtime (issue #18).

Patched onto PrayerBot by install() / hook_prayer_bot().
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
import pytz

from bot.daily_draw_logic import (
    DAILY_DRAW_BASE_TEXT,
    DAILY_DRAW_BUTTON_CUSTOM_ID,
    DAILY_DRAW_BUTTON_LABEL,
    append_draw_log,
    build_message_text,
    cooldown_remaining,
    is_on_cooldown,
    pick_random_member,
)
from bot.daily_draw_v2 import (
    archive_needs_retry,
    decide_tick_action,
    draw_day,
    format_cooldown_reply,
    is_stale_draw_message,
    same_draw_day,
)
from db.daily_draw import (
    get_active,
    get_archive,
    get_or_seed_config,
    mark_archive_done,
    repost_slot,
    set_archive_pending,
    start_new_draw_day,
)

log = logging.getLogger(__name__)
_DAILY_DRAW_MAX_RETRIES = 5
_HOOKED = False


async def _daily_draw_loop(self) -> None:
    await self.wait_until_ready()
    missing_logged = False
    while not self.is_closed():
        try:
            guild_id = await self._resolve_daily_draw_guild()
            if guild_id is None:
                if not missing_logged:
                    log.warning(
                        "Daily draw: configured channel not found on any guild — will retry"
                    )
                    missing_logged = True
            else:
                missing_logged = False
                await self._daily_draw_tick(guild_id)
        except Exception:
            log.exception("Error in daily draw loop")
        await asyncio.sleep(60)


def _daily_draw_view(self) -> discord.ui.View:
    button = discord.ui.Button(
        style=discord.ButtonStyle.primary,
        label=DAILY_DRAW_BUTTON_LABEL,
        custom_id=DAILY_DRAW_BUTTON_CUSTOM_ID,
    )
    view = discord.ui.View()
    view.add_item(button)
    return view


async def _daily_draw_tick(self, guild_id: str) -> None:
    cfg = get_or_seed_config(self.db, guild_id)
    tz = ZoneInfo(cfg.timezone_name)
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    cycle = draw_day(now_local, cfg.post_hour)
    cycle_key = cycle.isoformat()

    channel = self.get_channel(int(cfg.channel_id))
    if channel is None:
        with contextlib.suppress(Exception):
            fetched = await self.fetch_channel(int(cfg.channel_id))
            if isinstance(fetched, discord.TextChannel):
                channel = fetched

    await self._retry_pending_archive(guild_id, channel)

    message_id, stored_cycle, stored_slot, stored_hearts = get_active(self.db, guild_id)
    action, due_k, hearts = decide_tick_action(
        now_local,
        message_id,
        stored_cycle,
        stored_slot,
        stored_hearts,
        anchor_hour=cfg.post_hour,
    )
    if action in ("skip", "noop") or due_k is None:
        return

    date_key, attempts = self._daily_draw_failures.get(guild_id, (cycle_key, 0))
    if date_key == cycle_key and attempts >= _DAILY_DRAW_MAX_RETRIES:
        return

    if channel is None or not isinstance(channel, discord.TextChannel):
        log.warning("Daily draw: channel %s not found in guild %s", cfg.channel_id, guild_id)
        self._bump_daily_draw_failure(guild_id, cycle_key)
        return

    if action == "new_day" and message_id:
        stripped = await self._strip_draw_button(channel, message_id)
        set_archive_pending(self.db, guild_id, message_id)
        if stripped:
            mark_archive_done(self.db, guild_id)

    if action == "repost" and message_id:
        await self._delete_draw_message(channel, message_id)

    content = build_message_text(cfg.base_text or DAILY_DRAW_BASE_TEXT, hearts)
    try:
        msg = await channel.send(content=content, view=self._daily_draw_view())
    except Exception:
        log.exception("Daily draw: failed to post in channel %s", cfg.channel_id)
        self._bump_daily_draw_failure(guild_id, cycle_key)
        return

    if action in ("post", "new_day"):
        start_new_draw_day(self.db, guild_id, str(msg.id), cycle, due_k)
    else:
        repost_slot(self.db, guild_id, str(msg.id), due_k)
    self._daily_draw_failures.pop(guild_id, None)
    log.info(
        "Daily draw: %s message %s cycle=%s slot=%s hearts=%s (%s)",
        action, msg.id, cycle_key, due_k, hearts, cfg.timezone_name,
    )


async def _retry_pending_archive(self, guild_id: str, channel) -> None:
    archive_id, removed = get_archive(self.db, guild_id)
    if not archive_needs_retry(archive_id, removed):
        return
    if channel is None:
        return
    stripped = await self._strip_draw_button(channel, archive_id)
    if stripped:
        mark_archive_done(self.db, guild_id)


async def _strip_draw_button(self, channel, message_id: str) -> bool:
    try:
        msg = await channel.fetch_message(int(message_id))
        await msg.edit(content=msg.content, view=None)
        return True
    except discord.NotFound:
        return True
    except Exception as exc:
        log.warning("Daily draw: could not strip button on %s (%s)", message_id, exc)
        return False


async def _delete_draw_message(self, channel, message_id: str) -> None:
    try:
        msg = await channel.fetch_message(int(message_id))
        await msg.delete()
    except discord.NotFound:
        return
    except Exception as exc:
        log.warning("Daily draw: could not delete predecessor %s (%s)", message_id, exc)


async def _handle_daily_draw_button(self, interaction: discord.Interaction) -> None:
    from db import daily_draw as daily_draw_db

    guild = interaction.guild
    if guild is None or interaction.user is None:
        await interaction.followup.send(
            "The draw is only available in the server channel.", ephemeral=True
        )
        return
    guild_id = str(guild.id)

    cfg = daily_draw_db.get_or_seed_config(self.db, guild_id)
    if cfg is None or not cfg.target_role_id:
        log.warning("Daily draw pressed in unconfigured guild %s — ignoring", guild_id)
        await interaction.followup.send(
            "The draw is not configured on this server yet.", ephemeral=True
        )
        return

    now_utc = datetime.now(pytz.utc)

    active_id, _cycle, _slot, _hearts = daily_draw_db.get_active(self.db, guild_id)
    clicked_id = getattr(interaction.message, "id", None)
    if is_stale_draw_message(clicked_id, active_id):
        link = ""
        if active_id:
            link = f"https://discord.com/channels/{guild_id}/{cfg.channel_id}/{active_id}"
        await interaction.followup.send(
            f"That draw has closed — a new one is up: {link}".rstrip(),
            ephemeral=True,
        )
        return

    last_draw = daily_draw_db.last_draw_at(self.db, guild_id, str(interaction.user.id))
    if is_on_cooldown(last_draw, now_utc, cfg.cooldown_hours):
        await self._send_daily_draw_cooldown_reply(interaction, cfg, last_draw, now_utc)
        return

    role = guild.get_role(int(cfg.target_role_id))
    member_ids = [str(m.id) for m in role.members] if role is not None else []
    drawee_id = pick_random_member(member_ids, interaction.user.id)
    if drawee_id is None:
        await interaction.followup.send(
            "The draw is unavailable right now — no candidates found. Please try again later.",
            ephemeral=True,
        )
        return
    drawee = guild.get_member(int(drawee_id))

    lock = self._daily_draw_locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        last_draw = daily_draw_db.last_draw_at(self.db, guild_id, str(interaction.user.id))
        if is_on_cooldown(last_draw, now_utc, cfg.cooldown_hours):
            await self._send_daily_draw_cooldown_reply(interaction, cfg, last_draw, now_utc)
            return

        message_id, cycle_date, _slot, heart_count = daily_draw_db.get_active(self.db, guild_id)
        new_count = heart_count + 1
        Path(self._daily_draw_log_path()).parent.mkdir(parents=True, exist_ok=True)
        try:
            append_draw_log(
                self._daily_draw_log_path(),
                interaction.user.id,
                getattr(interaction.user, "display_name", str(interaction.user)),
                drawee_id,
                getattr(drawee, "display_name", "unknown"),
                now_utc,
            )
        except Exception:
            log.error(
                "Daily draw log write failed (guild=%s) — draw aborted, nothing persisted",
                guild_id,
            )
            with contextlib.suppress(Exception):
                await interaction.followup.send(
                    "Couldn't record the draw, please try again shortly.", ephemeral=True
                )
            return

        daily_draw_db.set_last_draw(self.db, guild_id, str(interaction.user.id), now_utc)
        daily_draw_db.update_hearts(self.db, guild_id, new_count)
        with contextlib.suppress(Exception):
            await self._edit_daily_draw_message(guild, cfg, message_id, cycle_date, new_count)

    await interaction.followup.send(
        f"Your lucky draw today is <@{drawee_id}>!", ephemeral=True
    )


async def _send_daily_draw_cooldown_reply(self, interaction, cfg, last_draw=None, now_utc=None):
    if now_utc is None:
        now_utc = datetime.now(pytz.utc)
    remaining = cooldown_remaining(last_draw, now_utc, cfg.cooldown_hours)
    text = format_cooldown_reply(cfg.emoji_catpray, remaining)
    try:
        await interaction.followup.send(text, ephemeral=True)
    except Exception:
        log.warning("Daily draw: catpray emoji send failed — falling back to \U0001f64f")
        with contextlib.suppress(Exception):
            await interaction.followup.send(
                format_cooldown_reply("\U0001f64f", remaining), ephemeral=True
            )


async def _edit_daily_draw_message(self, guild, cfg, message_id, cycle_date, heart_count):
    from db import daily_draw as daily_draw_db

    channel = guild.get_channel(int(cfg.channel_id))
    if channel is None:
        channel = await guild.fetch_channel(int(cfg.channel_id))

    content = build_message_text(cfg.base_text or DAILY_DRAW_BASE_TEXT, heart_count)
    tz = ZoneInfo(cfg.timezone_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)

    if message_id and same_draw_day(cycle_date, now_local, cfg.post_hour):
        try:
            msg = await channel.fetch_message(int(message_id))
            if msg:
                await msg.edit(content=content)
                return
        except Exception as exc:
            log.warning(
                "Daily draw message %s not editable (%s) — reposting", message_id, exc
            )

    msg = await channel.send(content=content, view=self._daily_draw_view())
    daily_draw_db.repoint_active_message(self.db, str(guild.id), str(msg.id))


def install(bot_cls):
    """Replace v1 daily-draw methods on PrayerBot."""
    bot_cls._daily_draw_loop = _daily_draw_loop
    bot_cls._daily_draw_view = _daily_draw_view
    bot_cls._daily_draw_tick = _daily_draw_tick
    bot_cls._retry_pending_archive = _retry_pending_archive
    bot_cls._strip_draw_button = _strip_draw_button
    bot_cls._delete_draw_message = _delete_draw_message
    bot_cls._handle_daily_draw_button = _handle_daily_draw_button
    bot_cls._send_daily_draw_cooldown_reply = _send_daily_draw_cooldown_reply
    bot_cls._edit_daily_draw_message = _edit_daily_draw_message
    from bot.prayer_play_hooks import install as install_play
    install_play(bot_cls)
    return bot_cls


def hook_prayer_bot() -> None:
    """Install v2 methods when PrayerBot subclasses discord.Client."""
    global _HOOKED
    if _HOOKED:
        return
    original = discord.Client.__init_subclass__

    def _wrapped(cls, **kwargs):
        if hasattr(original, "__func__"):
            original.__func__(cls, **kwargs)
        elif callable(original):
            try:
                original(cls, **kwargs)
            except TypeError:
                pass
        if cls.__name__ == "PrayerBot":
            install(cls)

    discord.Client.__init_subclass__ = classmethod(_wrapped)
    _HOOKED = True
