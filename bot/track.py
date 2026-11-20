from __future__ import annotations

from dataclasses import dataclass

@dataclass(slots=True)
class TrackResponse:

    track_id: str
    title: str
    duration_seconds: int
    local_path: str
    provider_used: str
    playlist_position: int
    ready: bool
    has_video: bool = False
