
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from bot.state_framework import BotState
from bot.track import TrackResponse

log = logging.getLogger(__name__)
VOLUME_MIN = 50
VOLUME_MAX = 750

FinishCallback = Callable[["Player", TrackResponse], Awaitable[None]]

@dataclass(slots=True)
class ElapsedClock:

    resume_from: float = 0.0
    started_at: float | None = None

    def start(self, resume_from: float = 0.0, *, now: float | None = None) -> None:
        self.resume_from = max(0.0, resume_from)
        self.started_at = now if now is not None else time.monotonic()

    def stop(self, *, now: float | None = None) -> float:
        elapsed = self.elapsed(now=now)
        self.resume_from = elapsed
        self.started_at = None
        return elapsed

    def elapsed(self, *, now: float | None = None) -> float:
        if self.started_at is None:
            return self.resume_from
        cur = now if now is not None else time.monotonic()
        return self.resume_from + max(0.0, cur - self.started_at)

    def reset(self) -> None:
        self.resume_from = 0.0
        self.started_at = None

FFmpegSourceFactory = Callable[
    [str, float, int], object
]

LOUDNESS_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"
VOLUME_LIMITER = "alimiter=level=0:limit=0.891"

def build_audio_options(seek_seconds: float, volume_percent: int) -> tuple[str, str]:
    before = ""
    if seek_seconds > 0:
        before = f"-ss {seek_seconds:.3f}"
    filters = [LOUDNESS_FILTER]
    if volume_percent != 100:
        filters.append(f"volume={volume_percent / 100:.2f}")
        if volume_percent > 100:
            filters.append(VOLUME_LIMITER)
    options = f"-vn -af {','.join(filters)} -loglevel warning"
    return before, options

def default_ffmpeg_source(
    path: str, seek_seconds: float, volume_percent: int
):
    import discord

    before, options = build_audio_options(seek_seconds, volume_percent)
    return discord.FFmpegPCMAudio(path, before_options=before, options=options)

class Player:

    def __init__(
        self,
        *,
        voice_client,
        state: BotState,
        loop: asyncio.AbstractEventLoop,
        source_factory: FFmpegSourceFactory = default_ffmpeg_source,
        persist_pause_state: bool = True,
    ) -> None:
        self.voice_client = voice_client
        self.state = state
        self.loop = loop
        self.source_factory = source_factory
        self.persist_pause_state = persist_pause_state
        self.clock = ElapsedClock()
        self.current_track: TrackResponse | None = None
        self._on_finish: FinishCallback | None = None
        self._suppress_finish = False
        self._play_seq = 0
        self._lock = asyncio.Lock()

    def on_finish(self, cb: FinishCallback) -> None:
        self._on_finish = cb

    def is_playing(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_playing())

    def elapsed_seconds(self) -> int:
        return int(self.clock.elapsed())

    async def start(self, track: TrackResponse, *, seek_seconds: float = 0.0) -> None:
        async with self._lock:
            await self._start_locked(track, seek_seconds=seek_seconds)

    async def _start_locked(self, track: TrackResponse, *, seek_seconds: float) -> None:
        if self.voice_client.is_playing():
            self.voice_client.stop()
        self._suppress_finish = False

        self._play_seq += 1
        my_seq = self._play_seq

        source = self.source_factory(
            track.local_path, seek_seconds, self.state.stream_volume_percent
        )
        self.current_track = track
        self.clock.start(resume_from=seek_seconds)

        self.state.current_track_id = track.track_id
        self.state.playlist_position = track.playlist_position
        if self.persist_pause_state:
            self.state.playback_position_seconds = int(seek_seconds)
            self.state.is_paused = False

        def _after_bound(exc: BaseException | None, _seq: int = my_seq) -> None:
            self._after(exc, expected_seq=_seq)

        self.voice_client.play(source, after=_after_bound)

    def _after(self, exc: BaseException | None, *, expected_seq: int | None = None) -> None:
        if exc is not None:
            log.warning("ffmpeg after-callback error: %s", exc)
        if expected_seq is not None and expected_seq != self._play_seq:
            log.debug(
                "ignoring after-callback for stale seq %s (current %s)",
                expected_seq,
                self._play_seq,
            )
            return
        if self._suppress_finish:
            return
        cb = self._on_finish
        track = self.current_track
        if cb is None or track is None:
            return
        if self.loop.is_closed():
            return
        coro = cb(self, track)
        try:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError as err:
            log.warning("could not schedule on_finish: %s", err)
            coro.close()

    async def pause(self) -> None:
        async with self._lock:
            self._play_seq += 1
            self._suppress_finish = True
            if self.voice_client.is_playing():
                self.voice_client.stop()
            elapsed = self.clock.stop()
            if self.persist_pause_state:
                self.state.playback_position_seconds = int(elapsed)
                self.state.is_paused = True

    async def resume(self) -> None:
        async with self._lock:
            track_id = self.state.current_track_id
            if not track_id:
                return
            resume_at = float(self.state.playback_position_seconds)

            track = self.current_track

            if track is None or not track.ready or not track.local_path:
                log.warning("resume: track %s is not ready", track_id)
                return

            await self._start_locked(track, seek_seconds=resume_at)
            if self.persist_pause_state:
                self.state.is_paused = False

    async def set_volume(self, volume_percent: int) -> int:
        volume = min(VOLUME_MAX, max(VOLUME_MIN, int(volume_percent)))
        async with self._lock:
            self.state.stream_volume_percent = volume
            if self.current_track is not None and self.is_playing():
                position = self.clock.stop()
                await self._start_locked(self.current_track, seek_seconds=position)
        return volume

    async def skip(self) -> None:
        async with self._lock:
            if self.voice_client.is_playing():
                self.voice_client.stop()

    async def stop_hard(self) -> None:
        async with self._lock:
            self._play_seq += 1
            self._suppress_finish = True
            if self.voice_client.is_playing():
                self.voice_client.stop()
