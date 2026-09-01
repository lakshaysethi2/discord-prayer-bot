from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from bot.player_framework import Player
from bot.state_framework import GuildScopedState
from db.database import Database
from provider.client import TrackResponse


def test_player_volume_boost_enabled_and_disabled():
    async def run_test():
        with Database(":memory:") as db:
            guild_id = "test_guild_vol"
            state = GuildScopedState(db, guild_id)
            state.stream_volume_percent = 250  # 250% volume boost configured

            fake_vc = MagicMock()
            fake_vc.is_playing.return_value = True

            recorded_sources = []
            def fake_source_factory(path: str, seek_seconds: float, volume_percent: int):
                recorded_sources.append((path, seek_seconds, volume_percent))
                return MagicMock()

            player = Player(
                voice_client=fake_vc,
                provider=None,
                state=state,
                loop=asyncio.get_running_loop(),
                source_factory=fake_source_factory,
            )

            track_boosted = TrackResponse(
                track_id="christian.mp3",
                title="Christian Prayer",
                duration_seconds=120,
                local_path="/tmp/christian.mp3",
                provider_used="local",
                playlist_position=0,
                ready=True,
            )

            # 1. Start with volume_boost=True
            await player.start(track_boosted, volume_boost=True)
            assert len(recorded_sources) == 1
            assert recorded_sources[-1][2] == 250  # Uses guild's 250% volume

            # Change volume while playing boosted track -> should restart at new volume
            await player.set_volume(300)
            assert len(recorded_sources) == 2
            assert recorded_sources[-1][2] == 300

            # 2. Start with volume_boost=False (e.g. Psalm 91)
            track_unboosted = TrackResponse(
                track_id="psalm_91.mp3",
                title="The 91st Psalm",
                duration_seconds=180,
                local_path="/tmp/psalm_91.mp3",
                provider_used="local",
                playlist_position=0,
                ready=True,
            )

            await player.start(track_unboosted, volume_boost=False)
            assert len(recorded_sources) == 3
            assert recorded_sources[-1][2] == 100  # Plays at 100% standard volume

            # Change volume while playing unboosted track -> should NOT restart current playback
            count_before = len(recorded_sources)
            await player.set_volume(400)
            assert state.stream_volume_percent == 400
            assert len(recorded_sources) == count_before  # No restart

    asyncio.run(run_test())
