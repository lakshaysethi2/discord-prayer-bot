# Content Agent — Discord Prayer Bot

## Responsibilities
- Verify media/prayers files; maintain download script; ensure LFS tracking.
- Acceptance: 6 MP3 files present; `.gitattributes` exists.

## Media Inventory

File | Size | Status
---|---|---
`Buddhist prayers - DND community.mp3` | 8.5 MB | ✅
`Christian prayers - DND community.mp3` | 14.0 MB | ✅
`Jewish prayers - DND community.mp3` | 8.0 MB | ✅
`Sufi prayers - DND community.mp3` | 9.0 MB | ✅
`The three daily prayers - DND community.mp3` | 2.7 MB | ✅
`Vedantic prayers - DND community.mp3` | 8.2 MB | ✅

Total: 6 MP3 files, ~50 MB.

## `.gitattributes` Check
- [x] `.gitattributes` exists (`media/prayers/*.mp3 filter=lfs diff=lfs merge=lfs -text`).
- [x] MP3 files tracked by Git LFS.
- [ ] Verify `git lfs ls-files` shows all 6 files.

## Download Script (`scripts/download_prayers.sh`)
- [x] Script exists and uses `yt-dlp`.
- [x] URLs match expected sources.
- [ ] Script executable (`chmod +x`).

## Acceptance Criteria
- [x] 6 MP3 files in `media/prayers/`.
- [x] `.gitattributes` exists.
- [x] `scripts/download_prayers.sh` present.
- [ ] `git lfs` tracking verified.
