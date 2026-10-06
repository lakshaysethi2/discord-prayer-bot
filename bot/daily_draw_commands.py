"""Daily-draw channel setting (issue #49).

Primary UX is the admin-only slash command ``/setticketdrawchannel`` with a
text-channel dropdown, plus the dashboard Servers picker. Shared validation
lives here so both surfaces persist through :func:`db.daily_draw.set_channel_id`.
"""

from __future__ import annotations

from typing import Any

import discord

from db.daily_draw import set_channel_id
from db.database import Database

COMMAND_TOKEN = "setticketdrawchannel"

REPLY_NEED_MANAGE_SERVER = "You need Manage Server to do that."
REPLY_NEED_CHANNEL = "Provide a text channel."
REPLY_CHANNEL_NOT_FOUND = "Channel not found in this guild."
REPLY_WRONG_CHANNEL_TYPE = "That is not a text channel."
REPLY_NEED_SEND_MESSAGES = "I need the Send Messages permission in that channel."
REPLY_GUILD_ONLY = "Use this command in a server."


def is_guild_text_channel(channel: Any) -> bool:
    """True for guild text channels (discord.TextChannel / ChannelType.text)."""
    if channel is None:
        return False
    channel_type = getattr(channel, "type", None)
    if channel_type is discord.ChannelType.text:
        return True
    return isinstance(channel, discord.TextChannel)


def format_channel_set_confirmation(channel_id: str) -> str:
    """Public confirmation naming the new channel with a clickable mention."""
    return (
        f"Daily draw channel set to <#{channel_id}> ({channel_id}). "
        "Next tick will use it."
    )


def author_has_manage_guild(author: Any) -> bool:
    """True when the member has Manage Server in this guild."""
    perms = getattr(author, "guild_permissions", None)
    return bool(getattr(perms, "manage_guild", False))


def bot_can_send_messages(channel: Any, guild: Any) -> bool:
    """True when the bot may Send Messages in ``channel``."""
    me = getattr(guild, "me", None)
    permissions_for = getattr(channel, "permissions_for", None)
    if me is None or not callable(permissions_for):
        return False
    perms = permissions_for(me)
    return bool(getattr(perms, "send_messages", False))


def channel_belongs_to_guild(channel: Any, guild: Any) -> bool:
    """True when ``channel`` is in ``guild`` (id match)."""
    if channel is None or guild is None:
        return False
    channel_guild = getattr(channel, "guild", None)
    if channel_guild is not None:
        return int(getattr(channel_guild, "id", 0)) == int(getattr(guild, "id", 0))
    getter = getattr(guild, "get_channel", None)
    if callable(getter):
        found = getter(int(getattr(channel, "id", 0)))
        if found is not None:
            return True
    return int(getattr(channel, "id", 0)) in {
        int(getattr(ch, "id", 0))
        for ch in getattr(guild, "text_channels", []) or []
    }


def validate_draw_text_channel(channel: Any, guild: Any) -> str | None:
    """Return a rejection reply, or None if ``channel`` is safe to persist."""
    if channel is None:
        return REPLY_NEED_CHANNEL
    if not channel_belongs_to_guild(channel, guild):
        return REPLY_CHANNEL_NOT_FOUND
    if not is_guild_text_channel(channel):
        return REPLY_WRONG_CHANNEL_TYPE
    if not bot_can_send_messages(channel, guild):
        return REPLY_NEED_SEND_MESSAGES
    return None


def apply_draw_channel(db: Database, guild: Any, channel: Any) -> str:
    """Validate and persist the daily-draw channel. Returns the user-facing reply.

    Writes via :func:`db.daily_draw.set_channel_id` only after every check
    passes. Callers still enforce Manage Server themselves.
    """
    error = validate_draw_text_channel(channel, guild)
    if error is not None:
        return error
    stored_id = str(getattr(channel, "id"))
    set_channel_id(db, str(guild.id), stored_id)
    return format_channel_set_confirmation(stored_id)


async def process_slash_setticketdrawchannel(
    db: Database,
    interaction: Any,
    channel: Any,
) -> str:
    """Validate permissions + channel and optionally persist. Returns reply text.

    DMs / missing guild → guild-only message, no write. Callers without
    Manage Server are denied. Used by ``/setticketdrawchannel``.
    """
    guild = getattr(interaction, "guild", None)
    if guild is None:
        return REPLY_GUILD_ONLY
    user = getattr(interaction, "user", None)
    if not author_has_manage_guild(user):
        return REPLY_NEED_MANAGE_SERVER
    if channel is None:
        return REPLY_NEED_CHANNEL
    return apply_draw_channel(db, guild, channel)
