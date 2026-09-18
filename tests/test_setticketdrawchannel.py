"""@bot setticketdrawchannel mention-prefix command (issue #49).

Drives the shipped parse/validate/persist path in
``bot.daily_draw_commands.process_setticketdrawchannel`` (the same function
``DailyDrawV2Mixin.on_message`` calls). Fakes are Discord-shaped message
objects; persistence uses a real SQLite Database.
"""

from __future__ import annotations

import asyncio
import inspect
import re

import discord
import pytest

from bot.daily_draw_commands import (
    COMMAND_TOKEN,
    REPLY_CHANNEL_NOT_FOUND,
    REPLY_NEED_CHANNEL_ID,
    REPLY_NEED_MANAGE_SERVER,
    REPLY_NEED_SEND_MESSAGES,
    REPLY_WRONG_CHANNEL_TYPE,
    process_setticketdrawchannel,
)
from bot.daily_draw_runtime import DailyDrawV2Mixin
from db.daily_draw import get_or_seed_config, set_channel_id
from db.database import Database

GID = "1234567890"
BOT_ID = 555555555555555555
OLD_CHANNEL_ID = "111111111111111111"
NEW_CHANNEL_ID = "222222222222222222"
MISSING_CHANNEL_ID = "333333333333333333"


@pytest.fixture()
def db():
    with Database(":memory:") as db:
        yield db


class _Perms:
    def __init__(self, *, manage_guild: bool = False, send_messages: bool = True) -> None:
        self.manage_guild = manage_guild
        self.send_messages = send_messages


class _Author:
    def __init__(self, *, bot: bool = False, manage_guild: bool = False) -> None:
        self.bot = bot
        self.guild_permissions = _Perms(manage_guild=manage_guild)


class _Channel:
    def __init__(
        self,
        channel_id: int,
        channel_type: discord.ChannelType,
        *,
        send_messages: bool = True,
    ) -> None:
        self.id = channel_id
        self.type = channel_type
        self._send_messages = send_messages
        self.sent: list[str] = []

    def permissions_for(self, _member: object) -> _Perms:
        return _Perms(send_messages=self._send_messages)

    async def send(self, content: str) -> None:
        self.sent.append(content)


class _Guild:
    def __init__(self, guild_id: str, channels: dict[int, _Channel], me: object) -> None:
        self.id = int(guild_id)
        self.me = me
        self._channels = channels

    def get_channel(self, channel_id: int) -> _Channel | None:
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> _Channel:
        channel = self._channels.get(channel_id)
        if channel is None:
            raise discord.NotFound(None, "unknown channel")  # type: ignore[arg-type]
        return channel


class _Message:
    def __init__(
        self,
        content: str,
        *,
        author: _Author,
        guild: _Guild | None,
        channel: _Channel | None = None,
    ) -> None:
        self.content = content
        self.author = author
        self.guild = guild
        self.channel = channel or _Channel(1, discord.ChannelType.text)


def _mention(command: str) -> str:
    return f"<@{BOT_ID}> {command}"


def _seed_old_channel(db: Database) -> None:
    set_channel_id(db, GID, OLD_CHANNEL_ID)


def _guild_with(*channels: _Channel) -> _Guild:
    return _Guild(GID, {ch.id: ch for ch in channels}, me=object())


def _admin_msg(content: str, guild: _Guild | None, *, source: _Channel | None = None) -> _Message:
    return _Message(
        content,
        author=_Author(manage_guild=True),
        guild=guild,
        channel=source,
    )


def _run(db: Database, message: _Message) -> str | None:
    return asyncio.run(process_setticketdrawchannel(db, BOT_ID, message))


def _stored(db: Database) -> str:
    return get_or_seed_config(db, GID).channel_id


def test_success_persists_and_confirms_with_mention(db, monkeypatch):
    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", OLD_CHANNEL_ID)
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with(target)
    reply = _run(
        db,
        _admin_msg(_mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"), guild),
    )
    assert reply is not None
    assert f"<#{NEW_CHANNEL_ID}>" in reply
    assert NEW_CHANNEL_ID in reply
    assert "Next tick will use it." in reply
    assert _stored(db) == NEW_CHANNEL_ID


def test_persist_survives_new_connection_and_later_env_change(tmp_path, monkeypatch):
    path = tmp_path / "draw.db"
    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", OLD_CHANNEL_ID)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with(target)
    with Database(str(path)) as db:
        _seed_old_channel(db)
        reply = _run(
            db,
            _admin_msg(_mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"), guild),
        )
        assert reply is not None
        assert f"<#{NEW_CHANNEL_ID}>" in reply

    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", "9999999999999999999")
    with Database(str(path)) as db2:
        cfg = get_or_seed_config(db2, GID)
        assert cfg.channel_id == NEW_CHANNEL_ID


def test_non_admin_denied_db_unchanged(db):
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with(target)
    msg = _Message(
        _mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"),
        author=_Author(manage_guild=False),
        guild=guild,
    )
    reply = _run(db, msg)
    assert reply == REPLY_NEED_MANAGE_SERVER
    assert _stored(db) == OLD_CHANNEL_ID


def test_dm_is_silent_and_does_not_write(db):
    _seed_old_channel(db)
    msg = _admin_msg(_mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"), guild=None)
    assert _run(db, msg) is None
    assert _stored(db) == OLD_CHANNEL_ID


def test_bot_author_ignored(db):
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with(target)
    msg = _Message(
        _mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"),
        author=_Author(bot=True, manage_guild=True),
        guild=guild,
    )
    assert _run(db, msg) is None
    assert _stored(db) == OLD_CHANNEL_ID


def test_missing_channel_id_rejected(db):
    _seed_old_channel(db)
    reply = _run(db, _admin_msg(_mention(COMMAND_TOKEN), _guild_with()))
    assert reply == REPLY_NEED_CHANNEL_ID
    assert _stored(db) == OLD_CHANNEL_ID


def test_non_snowflake_rejected(db):
    _seed_old_channel(db)
    reply = _run(db, _admin_msg(_mention(f"{COMMAND_TOKEN} not-an-id"), _guild_with()))
    assert reply == REPLY_NEED_CHANNEL_ID
    assert _stored(db) == OLD_CHANNEL_ID


def test_short_id_rejected(db):
    _seed_old_channel(db)
    reply = _run(db, _admin_msg(_mention(f"{COMMAND_TOKEN} 12345"), _guild_with()))
    assert reply == REPLY_NEED_CHANNEL_ID
    assert _stored(db) == OLD_CHANNEL_ID


def test_unknown_channel_rejected(db):
    _seed_old_channel(db)
    reply = _run(
        db,
        _admin_msg(_mention(f"{COMMAND_TOKEN} {MISSING_CHANNEL_ID}"), _guild_with()),
    )
    assert reply == REPLY_CHANNEL_NOT_FOUND
    assert _stored(db) == OLD_CHANNEL_ID


def test_voice_channel_rejected(db):
    _seed_old_channel(db)
    voice = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.voice)
    reply = _run(
        db,
        _admin_msg(_mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"), _guild_with(voice)),
    )
    assert reply == REPLY_WRONG_CHANNEL_TYPE
    assert _stored(db) == OLD_CHANNEL_ID


def test_missing_send_messages_rejected(db):
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text, send_messages=False)
    reply = _run(
        db,
        _admin_msg(_mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"), _guild_with(target)),
    )
    assert reply == REPLY_NEED_SEND_MESSAGES
    assert _stored(db) == OLD_CHANNEL_ID


def test_case_insensitive_command_token(db):
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    reply = _run(
        db,
        _admin_msg(_mention(f"SetTicketDrawChannel {NEW_CHANNEL_ID}"), _guild_with(target)),
    )
    assert reply is not None
    assert f"<#{NEW_CHANNEL_ID}>" in reply
    assert _stored(db) == NEW_CHANNEL_ID


def test_nickname_mention_prefix(db):
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    content = f"<@!{BOT_ID}> {COMMAND_TOKEN} {NEW_CHANNEL_ID}"
    reply = _run(db, _admin_msg(content, _guild_with(target)))
    assert reply is not None
    assert _stored(db) == NEW_CHANNEL_ID


def test_unrelated_mention_is_ignored(db):
    _seed_old_channel(db)
    reply = _run(db, _admin_msg(_mention("help"), _guild_with()))
    assert reply is None
    assert _stored(db) == OLD_CHANNEL_ID


def test_on_message_sends_confirmation(db):
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with(target)
    source = _Channel(99, discord.ChannelType.text)
    msg = _admin_msg(_mention(f"{COMMAND_TOKEN} {NEW_CHANNEL_ID}"), guild, source=source)

    class _Bot(DailyDrawV2Mixin):
        def __init__(self) -> None:
            self.db = db
            self.user = type("U", (), {"id": BOT_ID})()

    asyncio.run(_Bot().on_message(msg))
    assert source.sent
    assert f"<#{NEW_CHANNEL_ID}>" in source.sent[0]
    assert _stored(db) == NEW_CHANNEL_ID


def test_resolve_uses_stored_channel_not_env(db, monkeypatch):
    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", OLD_CHANNEL_ID)
    set_channel_id(db, GID, NEW_CHANNEL_ID)

    class _G:
        def __init__(self) -> None:
            self.id = int(GID)

    class _Bot(DailyDrawV2Mixin):
        def __init__(self) -> None:
            self.db = db
            self._g = _G()

        def get_guild(self, guild_id: int) -> _G | None:
            return self._g if int(guild_id) == int(GID) else None

        @property
        def guilds(self) -> list[_G]:
            return [self._g]

    ids = asyncio.run(_Bot()._resolve_daily_draw_guild_ids())
    assert ids == [GID]
    assert get_or_seed_config(db, GID).channel_id == NEW_CHANNEL_ID


def test_command_token_is_setticketdrawchannel():
    assert COMMAND_TOKEN == "setticketdrawchannel"


def test_no_setticketdrawchannel_slash_command(monkeypatch, tmp_path):
    from bot.prayer_bot_status import PrayerBotStatusMixin

    src = inspect.getsource(PrayerBotStatusMixin._setup_slash_commands)
    declared = re.findall(r'@self\.tree\.command\(\s*name=["\']([^"\']+)["\']', src)
    assert COMMAND_TOKEN not in declared

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "slash.db"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    monkeypatch.setenv("ADMIN_TOKEN", "x")
    from bot.main import PrayerBot

    bot = PrayerBot()
    try:
        assert bot.intents.message_content is True
        bot._setup_slash_commands()
        names = {cmd.name for cmd in bot.tree.get_commands()}
        assert COMMAND_TOKEN not in names
        assert "setticketdrawchannel" not in names
        assert "start" in names
        assert "exit" in names
    finally:
        bot.db.close()
