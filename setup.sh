#!/usr/bin/env bash
# One-off setup on a Mac (or Linux): ffmpeg, a Python environment and the speech models.
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg
  else echo "Please install ffmpeg first (Mac: install Homebrew from https://brew.sh, then run this again)"; exit 1; fi
fi
ffmpeg -hide_banner -filters 2>/dev/null | grep -q " subtitles " || { echo "your ffmpeg has no libass (captions); reinstall it with: brew reinstall ffmpeg"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f models/whisper-small.en/small.en-encoder.int8.onnx ] && [ -f models/diar/emb.onnx ] || scripts/fetch_models.sh
echo "ready: .venv/bin/python dokkepisode.py prep /path/to/episode.mp4"
