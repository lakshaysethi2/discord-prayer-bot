"""Discord wiring for issue #9. Installed from prayer_play_hooks."""

from __future__ import annotations

import asyncio
import logging

import discord

from bot.prayer_listen_logic import (
    EMPTY_BOARD,
    display_name,
    embed_footer,
    embed_title,
    format_row,
    should_credit,
)
from db.prayer_listen import (
    checkpoint_open,
    close_guild_sessions,
    close_orphan_sessions,
    close_session,
    format_hms,
    open_session,
    top_listeners,
    user_rank,
    week_key,
)
from db.prayers import get_guild_config

log = logging.getLogger(__name__)

_START_ORIG = None
_VOICE_ORIG = None
_SLASH_ORIG = None
_READY_ORIG = None
_FINISH_FACTORY_ORIG = None


def _session_open(self, guild_id: str) -> bool:
    checker = getattr(self, "_is_prayer_playing", None)
    if callable(checker):
        return bool(checker(guild_id))
    player = self.players.get(guild_id)
    if player is None:
        return False
    return bool(player.is_playing()) and guild_id not in getattr(self, "_tts_playing", set())


def _tts(self, guild_id: str) -> bool:
    return guild_id in getattr(self, "_tts_playing", set())


def _bot_in_prayer_vc(self, guild_id: str, prayer_vc_id: str | None) -> bool:
    if not prayer_vc_id:
        return False
    vc = self.voice_connections.get(guild_id)
    if vc is None or not vc.is_connected() or vc.channel is None:
        return False
    return str(vc.channel.id) == str(prayer_vc_id)


def _member_meta(member) -> tuple[str, str, str | None]:
    username = getattr(member, "name", None) or str(member.id)
    nick = getattr(member, "nick", None) or getattr(member, "display_name", None)
    if nick == username:
        nick = None
    return str(member.id), username, nick


def _open_present_humans(self, guild_id: str) -> None:
    cfg = get_guild_config(self.db, guild_id)
    if cfg is None or not cfg.voice_channel_id:
        return
    if not _session_open(self, guild_id):
        return
    guild = self.get_guild(int(guild_id))
    if guild is None:
        return
    channel = guild.get_channel(int(cfg.voice_channel_id))
    if channel is None:
        return
    self_id = str(self.user.id) if self.user else ""
    for member in list(channel.members):
        if member.bot or str(member.id) == self_id:
            continue
        uid, name, nick = _member_meta(member)
        open_session(self.db, guild_id, uid, name, nick)


async def _start_prayer_playback(self, guild_id: str, prayer_type, filename: str, is_adhoc: bool = False) -> bool:
    ok = await _START_ORIG(self, guild_id, prayer_type, filename, is_adhoc)
    if ok:
        _open_present_humans(self, guild_id)
    return ok


def _make_schedule_disconnect(self, guild_id: str):
    inner = _FINISH_FACTORY_ORIG(self, guild_id)

    async def _on_finish(player, track):
        close_guild_sessions(self.db, guild_id)
        await inner(player, track)

    return _on_finish


async def on_voice_state_update(self, member, before, after) -> None:
    await _VOICE_ORIG(self, member, before, after)
    if member.bot:
        return
    guild_id = str(member.guild.id)
    cfg = get_guild_config(self.db, guild_id)
    if cfg is None or not cfg.voice_channel_id:
        return
    prayer_vc = str(cfg.voice_channel_id)
    after_id = str(after.channel.id) if after.channel else None
    before_id = str(before.channel.id) if before.channel else None
    uid, name, nick = _member_meta(member)
    credit = should_credit(
        member_is_bot=False,
        is_self=bool(self.user and member.id == self.user.id),
        prayer_session_open=_session_open(self, guild_id),
        tts_playing=_tts(self, guild_id),
        in_prayer_vc=after_id == prayer_vc,
        bot_in_prayer_vc=_bot_in_prayer_vc(self, guild_id, prayer_vc),
    )
    if credit:
        open_session(self.db, guild_id, uid, name, nick)
    elif before_id == prayer_vc:
        close_session(self.db, guild_id, uid)


async def on_ready(self) -> None:
    close_orphan_sessions(self.db)
    await _READY_ORIG(self)
    if not getattr(self, "_listen_checkpoint_task", None) or self._listen_checkpoint_task.done():
        self._listen_checkpoint_task = asyncio.create_task(_checkpoint_loop(self))


async def _checkpoint_loop(self) -> None:
    await self.wait_until_ready()
    while not self.is_closed():
        try:
            checkpoint_open(self.db)
        except Exception:
            log.exception("prayer-listen checkpoint failed")
        await asyncio.sleep(3600)


def _setup_slash_commands(self) -> None:
    _SLASH_ORIG(self)

    @self.tree.command(name="leaderboard", description="Who spent the most time in the prayer room")
    @discord.app_commands.describe(period="weekly (default) or all-time")
    @discord.app_commands.choices(period=[
        discord.app_commands.Choice(name="weekly", value="weekly"),
        discord.app_commands.Choice(name="alltime", value="alltime"),
    ])
    async def leaderboard(interaction: discord.Interaction, period: str = "weekly"):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(EMPTY_BOARD, ephemeral=True)
            return
        period = period if period in ("weekly", "alltime") else "weekly"
        rows = top_listeners(self.db, str(guild.id), period, 10)
        if not rows:
            await interaction.followup.send(EMPTY_BOARD, ephemeral=True)
            return
        lines = []
        for i, row in enumerate(rows, start=1):
            name = discord.utils.escape_markdown(
                display_name(row["username"], row["server_nickname"])
            )
            lines.append(format_row(i, name, int(row["seconds"])))
        mine = user_rank(self.db, str(guild.id), str(interaction.user.id), period)
        if mine and mine["rank"] > 10:
            lines.append(f"Your time: {format_hms(mine['seconds'])}  (#{mine['rank']})")
        embed = discord.Embed(
            title=embed_title(period),
            description="\n".join(lines),
            colour=discord.Colour.gold(),
        )
        key = rows[0]["week_key"] if period == "weekly" else None
        embed.set_footer(text=embed_footer(period, key or week_key()))
        await interaction.followup.send(embed=embed, ephemeral=True)


def install(bot_cls):
    global _START_ORIG, _VOICE_ORIG, _SLASH_ORIG, _READY_ORIG, _FINISH_FACTORY_ORIG
    _START_ORIG = bot_cls._start_prayer_playback
    _VOICE_ORIG = bot_cls.on_voice_state_update
    _SLASH_ORIG = bot_cls._setup_slash_commands
    _READY_ORIG = bot_cls.on_ready
    _FINISH_FACTORY_ORIG = bot_cls._make_schedule_disconnect
    bot_cls._start_prayer_playback = _start_prayer_playback
    bot_cls.on_voice_state_update = on_voice_state_update
    bot_cls._setup_slash_commands = _setup_slash_commands
    bot_cls.on_ready = on_ready
    bot_cls._make_schedule_disconnect = _make_schedule_disconnect
    return bot_cls
