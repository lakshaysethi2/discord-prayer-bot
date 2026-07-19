#!/usr/bin/env bash
# Download prayer audio files for the Discord Prayer Bot
# Run this locally where you have network access + yt-dlp installed
set -euo pipefail

DEST_DIR="${1:-media/prayers}"
mkdir -p "$DEST_DIR"

URLS=(
  "https://www.youtube.com/watch?v=wwvXl4RMPSM"
  "https://www.youtube.com/watch?v=76WXRUVKlgk"
  "https://www.youtube.com/watch?v=qANTA8j56FA"
  "https://www.youtube.com/watch?v=brtT39pm1tg"
  "https://www.youtube.com/watch?v=vLLD9381Jxg"
  "https://www.youtube.com/watch?v=f5JYnaSwcOY"
)

cd "$DEST_DIR"

for url in "${URLS[@]}"; do
  echo "Downloading: $url"
  yt-dlp -x --audio-format mp3 --audio-quality 0 \
    -o "%(title)s.%(ext)s" "$url"
done

echo "✅ All prayer audio files downloaded to $DEST_DIR"
echo "Now commit and push them (consider git-lfs for large files):"
echo "  git lfs track 'media/prayers/*.mp3'"
echo "  git add .gitattributes media/prayers/"
echo "  git commit -m 'Add prayer audio files'"
echo "  git push"