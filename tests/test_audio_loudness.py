from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot import prayer_bot_tts
from bot.player_framework import LOUDNESS_FILTER, VOLUME_LIMITER, build_audio_options
from bot.prayer_bot_tts import PrayerBotTtsMixin

GUILD = "1194598173742731284"
PRAYER_AUDIO = Path("media/prayers/The 91st Psalm.mp3")
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not PRAYER_AUDIO.exists(),
    reason="needs ffmpeg and the prayer media files",
)

def test_audio_options_always_normalise_loudness():
    before, options = build_audio_options(0, 100)
    assert before == ""
    assert LOUDNESS_FILTER in options
    assert "volume=" not in options.split("-af", 1)[1]

def test_audio_options_keep_seek_and_volume():
    before, options = build_audio_options(12.5, 50)
    assert before == "-ss 12.500"
    assert "volume=0.50" in options
    assert LOUDNESS_FILTER in options

def test_audio_options_limit_gain_above_unity():
    _, boosted = build_audio_options(0, 750)
    filters = boosted.split("-af", 1)[1]
    assert "volume=7.50" in filters
    assert VOLUME_LIMITER in filters
    assert filters.index("volume=7.50") < filters.index(VOLUME_LIMITER)
    _, quiet = build_audio_options(0, 50)
    assert VOLUME_LIMITER not in quiet

def test_tts_cache_rejects_missing_and_tiny_files(tmp_path):
    assert prayer_bot_tts._tts_cache_valid(tmp_path / "missing.mp3") is False
    tiny = tmp_path / "tiny.mp3"
    tiny.write_bytes(b"not audio")
    assert prayer_bot_tts._tts_cache_valid(tiny) is False

def test_tts_probe_failure_keeps_cache_file(tmp_path):
    path = tmp_path / "cached.mp3"
    path.write_bytes(b"x" * 4096)
    with patch.object(prayer_bot_tts.subprocess, "run", side_effect=OSError("no ffmpeg")):
        assert prayer_bot_tts._tts_cache_valid(path) is True

def test_tts_cache_rejects_file_with_decode_errors(tmp_path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"x" * 4096)
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"[mp3] bad frame")
    with patch.object(prayer_bot_tts.subprocess, "run", return_value=fake):
        assert prayer_bot_tts._tts_cache_valid(path) is False

@needs_ffmpeg
def test_normalised_prayer_is_audible(tmp_path):
    _, options = build_audio_options(0, 100)
    filters = options.split("-af", 1)[1].split("-loglevel")[0].strip().rstrip(",")
    out = tmp_path / "norm.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-t", "2", "-i", str(PRAYER_AUDIO),
         "-af", filters, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(out)],
        check=True,
    )
    stats = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    ).stderr
    mean = float(stats.split("mean_volume:")[1].split("dB")[0])
    peak = float(stats.split("max_volume:")[1].split("dB")[0])
    assert mean > -30, f"prayer still inaudible after normalising (mean {mean} dB)"
    assert peak <= -1.0, f"normalised audio exceeds the true-peak target ({peak} dB)"

@needs_ffmpeg
def test_boosted_volume_does_not_clip(tmp_path):
    _, options = build_audio_options(0, 750)
    filters = options.split("-af", 1)[1].split("-loglevel")[0].strip().rstrip(",")
    out = tmp_path / "loud.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-t", "2", "-i", str(PRAYER_AUDIO),
         "-af", filters, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(out)],
        check=True,
    )
    stats = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    ).stderr
    peak = float(stats.split("max_volume:")[1].split("dB")[0])
    assert peak <= -0.5, f"limited output still hits full scale ({peak} dB)"

class FakeComm:
    def __init__(self, text, voice):
        pass

    async def save(self, path):
        Path(path).write_bytes(b"x" * 4096)

class FakeVC:
    def is_connected(self):
        return True

    def is_playing(self):
        return False

    def play(self, source, after=None):
        if after:
            after(None)

    def stop(self):
        pass

class FakeBot(PrayerBotTtsMixin):
    def __init__(self, db=None):
        self.db = db
        self._tts_playing = set()
        self.voice_connections = {GUILD: FakeVC()}

    def _is_prayer_active(self, guild_id):
        return False

    def _source_factory(self, path, seek_seconds, volume_percent):
        return object()

@pytest.fixture
def tts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(prayer_bot_tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(
        prayer_bot_tts,
        "get_guild_config",
        lambda db, guild_id: SimpleNamespace(tts_voice="en-US-GuyNeural"),
    )
    return tmp_path

def _cache_path(text: str) -> Path:
    key = hashlib.sha1(f"en-US-GuyNeural:{text}".encode()).hexdigest()
    return prayer_bot_tts.TTS_DIR / f"tts_{key}.mp3"

def test_corrupt_cache_is_regenerated(tts_dir):
    filepath = _cache_path("hello")
    filepath.write_bytes(b"broken")
    valid = MagicMock(side_effect=[False, True])

    with patch.object(prayer_bot_tts, "_tts_cache_valid", valid), \
            patch.object(prayer_bot_tts.edge_tts, "Communicate", wraps=FakeComm):
        asyncio.run(PrayerBotTtsMixin._process_tts(FakeBot(), GUILD, "hello"))

    assert filepath.read_bytes() == b"x" * 4096
    assert not list(tts_dir.glob("*.part"))

def test_good_cache_is_reused(tts_dir):
    filepath = _cache_path("hello")
    filepath.write_bytes(b"good")

    with patch.object(prayer_bot_tts, "_tts_cache_valid", return_value=True), \
            patch.object(prayer_bot_tts.edge_tts, "Communicate", wraps=FakeComm) as comm:
        asyncio.run(PrayerBotTtsMixin._process_tts(FakeBot(), GUILD, "hello"))

    comm.assert_not_called()
    assert filepath.read_bytes() == b"good"

def test_failed_generation_is_not_cached(tts_dir):
    filepath = _cache_path("hello")
    valid = MagicMock(side_effect=[False, False])

    with patch.object(prayer_bot_tts, "_tts_cache_valid", valid), \
            patch.object(prayer_bot_tts.edge_tts, "Communicate", wraps=FakeComm):
        asyncio.run(PrayerBotTtsMixin._process_tts(FakeBot(), GUILD, "hello"))

    assert not filepath.exists()
    assert not list(tts_dir.glob("*.part"))

class ExplodingComm:
    def __init__(self, text, voice):
        pass

    async def save(self, path):
        Path(path).write_bytes(b"half a file")
        raise RuntimeError("edge-tts connection dropped")

def test_failed_save_leaves_no_part_file(tts_dir):
    filepath = _cache_path("hello")

    with patch.object(prayer_bot_tts, "_tts_cache_valid", return_value=False), \
            patch.object(prayer_bot_tts.edge_tts, "Communicate", wraps=ExplodingComm):
        with pytest.raises(RuntimeError):
            asyncio.run(PrayerBotTtsMixin._process_tts(FakeBot(), GUILD, "hello"))

    assert not filepath.exists()
    assert not list(tts_dir.glob("*.part"))

