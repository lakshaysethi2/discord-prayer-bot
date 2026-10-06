"""/setticketdrawchannel slash command + dashboard picker (issue #49).

Drives ``bot.daily_draw_commands.process_slash_setticketdrawchannel`` (the
same function the slash command calls). Fakes are Discord-shaped objects;
persistence uses a real SQLite Database.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re

import discord
import pytest
from fastapi.testclient import TestClient

from bot.daily_draw_commands import (
    COMMAND_TOKEN,
    REPLY_CHANNEL_NOT_FOUND,
    REPLY_GUILD_ONLY,
    REPLY_NEED_MANAGE_SERVER,
    REPLY_NEED_SEND_MESSAGES,
    REPLY_WRONG_CHANNEL_TYPE,
    process_slash_setticketdrawchannel,
)
from bot.daily_draw_logic import DAILY_DRAW_BUTTON_CUSTOM_ID
from bot.daily_draw_runtime import DailyDrawTicketView, DailyDrawV2Mixin
from db.daily_draw import get_config, get_or_seed_config, set_channel_id
from db.database import Database
from db.guilds import ChannelRow, apply_guild_config, discover_guild, replace_guild_channels

GID = "1234567890"
OLD_CHANNEL_ID = "111111111111111111"
NEW_CHANNEL_ID = "222222222222222222"
MISSING_CHANNEL_ID = "333333333333333333"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-token-change-me")


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
        guild: "_Guild | None" = None,
    ) -> None:
        self.id = channel_id
        self.type = channel_type
        self._send_messages = send_messages
        self.guild = guild
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
        for ch in channels.values():
            ch.guild = self

    def get_channel(self, channel_id: int) -> _Channel | None:
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> _Channel:
        channel = self._channels.get(channel_id)
        if channel is None:
            raise discord.NotFound(None, "unknown channel")  # type: ignore[arg-type]
        return channel


class _Interaction:
    def __init__(
        self,
        *,
        author: _Author,
        guild: _Guild | None,
        channel: _Channel | None = None,
    ) -> None:
        self.user = author
        self.guild = guild
        self.channel = channel


def _seed_old_channel(db: Database) -> None:
    set_channel_id(db, GID, OLD_CHANNEL_ID)


def _guild_with(*channels: _Channel) -> _Guild:
    return _Guild(GID, {ch.id: ch for ch in channels}, me=object())


def _run(db: Database, interaction: _Interaction, channel: _Channel | None) -> str:
    return asyncio.run(process_slash_setticketdrawchannel(db, interaction, channel))


def _stored(db: Database) -> str:
    return get_or_seed_config(db, GID).channel_id


def test_success_persists_and_confirms_with_mention(db, monkeypatch):
    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", OLD_CHANNEL_ID)
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with(target)
    reply = _run(
        db,
        _Interaction(author=_Author(manage_guild=True), guild=guild),
        target,
    )
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
            _Interaction(author=_Author(manage_guild=True), guild=guild),
            target,
        )
        assert f"<#{NEW_CHANNEL_ID}>" in reply

    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", "9999999999999999999")
    with Database(str(path)) as db2:
        cfg = get_or_seed_config(db2, GID)
        assert cfg.channel_id == NEW_CHANNEL_ID
        assert get_config(db2, GID) is not None
        assert get_config(db2, GID).channel_id == NEW_CHANNEL_ID


def test_non_admin_denied_db_unchanged(db):
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with(target)
    reply = _run(
        db,
        _Interaction(author=_Author(manage_guild=False), guild=guild),
        target,
    )
    assert reply == REPLY_NEED_MANAGE_SERVER
    assert _stored(db) == OLD_CHANNEL_ID


def test_dm_is_denied_and_does_not_write(db):
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    reply = _run(
        db,
        _Interaction(author=_Author(manage_guild=True), guild=None),
        target,
    )
    assert reply == REPLY_GUILD_ONLY
    assert _stored(db) == OLD_CHANNEL_ID


def test_voice_channel_rejected(db):
    _seed_old_channel(db)
    voice = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.voice)
    guild = _guild_with(voice)
    reply = _run(
        db,
        _Interaction(author=_Author(manage_guild=True), guild=guild),
        voice,
    )
    assert reply == REPLY_WRONG_CHANNEL_TYPE
    assert _stored(db) == OLD_CHANNEL_ID


def test_missing_send_messages_rejected(db):
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text, send_messages=False)
    guild = _guild_with(target)
    reply = _run(
        db,
        _Interaction(author=_Author(manage_guild=True), guild=guild),
        target,
    )
    assert reply == REPLY_NEED_SEND_MESSAGES
    assert _stored(db) == OLD_CHANNEL_ID


def test_channel_from_other_guild_rejected(db):
    _seed_old_channel(db)
    target = _Channel(int(NEW_CHANNEL_ID), discord.ChannelType.text)
    guild = _guild_with()
    other = _Guild("999", {target.id: target}, me=object())
    target.guild = other
    reply = _run(
        db,
        _Interaction(author=_Author(manage_guild=True), guild=guild),
        target,
    )
    assert reply == REPLY_CHANNEL_NOT_FOUND
    assert _stored(db) == OLD_CHANNEL_ID


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


def test_slash_command_is_registered(monkeypatch, tmp_path):
    from bot.prayer_bot_status import PrayerBotStatusMixin

    src = inspect.getsource(PrayerBotStatusMixin._setup_slash_commands)
    declared = re.findall(r'@self\.tree\.command\(\s*name=["\']([^"\']+)["\']', src)
    assert COMMAND_TOKEN in declared
    assert "channel: discord.TextChannel" in src
    assert "default_permissions(manage_guild=True)" in src

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "slash.db"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    monkeypatch.setenv("ADMIN_TOKEN", "x")
    import bot.main as main_mod

    monkeypatch.setattr(main_mod, "DB_PATH", str(tmp_path / "slash.db"))
    bot = main_mod.PrayerBot()
    try:
        assert bot.intents.message_content is False
        assert bot.intents.members is True
        bot._setup_slash_commands()
        names = {cmd.name for cmd in bot.tree.get_commands()}
        assert COMMAND_TOKEN in names
        assert "start" in names
        assert "exit" in names
        cmd = next(c for c in bot.tree.get_commands() if c.name == COMMAND_TOKEN)
        assert cmd.default_permissions is not None
        assert cmd.default_permissions.manage_guild is True
        assert cmd.guild_only is True
    finally:
        bot.db.close()


def test_draw_ticket_view_is_persistent():
    view = DailyDrawTicketView()
    assert view.timeout is None
    assert view.is_persistent()
    custom_ids = [getattr(item, "custom_id", None) for item in view.children]
    assert DAILY_DRAW_BUTTON_CUSTOM_ID in custom_ids


def test_get_config_does_not_seed(db):
    assert get_config(db, GID) is None
    set_channel_id(db, GID, NEW_CHANNEL_ID)
    cfg = get_config(db, GID)
    assert cfg is not None
    assert cfg.channel_id == NEW_CHANNEL_ID


def test_dashboard_servers_shows_draw_channel_dropdown(db):
    discover_guild(db, GID, "Test Guild")
    apply_guild_config(db, GID, enabled=True)
    replace_guild_channels(
        db,
        GID,
        [
            ChannelRow(GID, NEW_CHANNEL_ID, "draw-here", "text"),
            ChannelRow(GID, OLD_CHANNEL_ID, "voice-room", "voice"),
        ],
    )
    set_channel_id(db, GID, NEW_CHANNEL_ID)

    from dashboard.app import app
    from dashboard.prayers_routes import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/servers", headers={"authorization": f"Bearer {ADMIN_TOKEN}"})
        assert response.status_code == 200
        assert 'name="draw_channel_id"' in response.text
        assert "Daily draw channel" in response.text
        assert NEW_CHANNEL_ID in response.text
        assert "draw-here" in response.text
    finally:
        app.dependency_overrides.clear()


def test_dashboard_save_persists_draw_channel(db, monkeypatch):
    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", OLD_CHANNEL_ID)
    discover_guild(db, GID, "Test Guild")
    apply_guild_config(db, GID, enabled=True)
    replace_guild_channels(
        db,
        GID,
        [ChannelRow(GID, NEW_CHANNEL_ID, "draw-here", "text")],
    )
    _seed_old_channel(db)

    from dashboard.app import app
    from dashboard.prayers_routes import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/servers/update",
            headers={"authorization": f"Bearer {ADMIN_TOKEN}"},
            data={
                "guild_id": GID,
                "enabled": "on",
                "draw_channel_id": NEW_CHANNEL_ID,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert _stored(db) == NEW_CHANNEL_ID
    finally:
        app.dependency_overrides.clear()


def test_dashboard_rejects_unknown_draw_channel(db):
    discover_guild(db, GID, "Test Guild")
    apply_guild_config(db, GID, enabled=True)
    replace_guild_channels(
        db,
        GID,
        [ChannelRow(GID, NEW_CHANNEL_ID, "draw-here", "text")],
    )
    _seed_old_channel(db)

    from dashboard.app import app
    from dashboard.prayers_routes import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/servers/update",
            headers={"authorization": f"Bearer {ADMIN_TOKEN}"},
            data={
                "guild_id": GID,
                "enabled": "on",
                "draw_channel_id": MISSING_CHANNEL_ID,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert _stored(db) == OLD_CHANNEL_ID
    finally:
        app.dependency_overrides.clear()
