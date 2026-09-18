"""Mention-prefix command: `@bot setticketdrawchannel <channel-id>` (issue #49).

Not a slash command. Owner preference is in-channel mention commands for
this config change. `/setticketdrawchannel` must not be registered.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from db.daily_draw import set_channel_id
from db.database import Database

COMMAND_TOKEN = "setticketdrawchannel"
_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")

REPLY_NEED_MANAGE_SERVER = "You need Manage Server to do that."
REPLY_NEED_CHANNEL_ID = "Provide a valid channel ID."
REPLY_CHANNEL_NOT_FOUND = "Channel not found in this guild."
REPLY_WRONG_CHANNEL_TYPE = "That is not a text channel."
REPLY_NEED_SEND_MESSAGES = "I need the Send Messages permission in that channel."


def is_discord_snowflake(value: str) -> bool:
    """True if `value` is a 17–20 digit Discord snowflake string."""
    return bool(_SNOWFLAKE_RE.fullmatch(value))


def remainder_after_bot_mention(content: str, bot_user_id: int) -> str | None:
    """Return text after a leading bot mention, or None if this is not mention-prefix."""
    text = content.strip()
    prefixes = (f"<@{bot_user_id}>", f"<@!{bot_user_id}>")
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return None


def parse_setticketdrawchannel_argument(remainder: str) -> str | None:
    """If `remainder` is this command, return the argument (possibly empty).

    Returns None when the token is some other mention-prefix command (ignore).
    Command token match is case-insensitive.
    """
    stripped = remainder.strip()
    if not stripped:
        return None
    parts = stripped.split(None, 1)
    if parts[0].lower() != COMMAND_TOKEN:
        return None
    if len(parts) == 1:
        return ""
    return parts[1].strip()


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


async def _lookup_guild_channel(guild: Any, channel_id: int) -> Any | None:
    getter = getattr(guild, "get_channel", None)
    channel = getter(channel_id) if callable(getter) else None
    if channel is not None:
        return channel
    fetcher = getattr(guild, "fetch_channel", None)
    if not callable(fetcher):
        return None
    try:
        return await fetcher(channel_id)
    except Exception:
        return None


def _author_has_manage_guild(author: Any) -> bool:
    perms = getattr(author, "guild_permissions", None)
    return bool(getattr(perms, "manage_guild", False))


def _bot_can_send_messages(channel: Any, guild: Any) -> bool:
    me = getattr(guild, "me", None)
    permissions_for = getattr(channel, "permissions_for", None)
    if me is None or not callable(permissions_for):
        return False
    perms = permissions_for(me)
    return bool(getattr(perms, "send_messages", False))


async def process_setticketdrawchannel(
    db: Database,
    bot_user_id: int,
    message: Any,
) -> str | None:
    """Parse, validate, and optionally persist `@bot setticketdrawchannel`.

    Returns the in-channel reply text, or None to stay silent (ignore).
    Writes via :func:`db.daily_draw.set_channel_id` only after every check
    passes. DMs are silent. Callers without Manage Server are denied.
    """
    author = getattr(message, "author", None)
    if author is None or getattr(author, "bot", False):
        return None

    content = getattr(message, "content", "") or ""
    remainder = remainder_after_bot_mention(content, bot_user_id)
    if remainder is None:
        return None
    argument = parse_setticketdrawchannel_argument(remainder)
    if argument is None:
        return None

    guild = getattr(message, "guild", None)
    if guild is None:
        return None

    if not _author_has_manage_guild(author):
        return REPLY_NEED_MANAGE_SERVER

    if not is_discord_snowflake(argument):
        return REPLY_NEED_CHANNEL_ID

    channel = await _lookup_guild_channel(guild, int(argument))
    if channel is None:
        return REPLY_CHANNEL_NOT_FOUND
    if not is_guild_text_channel(channel):
        return REPLY_WRONG_CHANNEL_TYPE
    if not _bot_can_send_messages(channel, guild):
        return REPLY_NEED_SEND_MESSAGES

    stored_id = str(getattr(channel, "id", argument))
    set_channel_id(db, str(guild.id), stored_id)
    return format_channel_set_confirmation(stored_id)
