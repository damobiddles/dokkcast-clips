# dokkcast-clips

Brands short Dokkcast clips for social: udokk logo on top, the picture in the middle,
word-highlighted Manrope captions underneath, on a navy-to-purple gradient (1080x1920).

## Use

```bash
pip install -r requirements.txt          # needs ffmpeg with libass on PATH
scripts/fetch_models.sh                  # one-off, ~700 MB
python dokkclip.py all input/clip.mp4    # -> output/clip_branded.mp4
```

Steps can be run separately: `transcribe` writes `output/<name>.words.json`
(a list of `{"w", "s", "e"}`), and `render` builds the video from it. Edit that JSON to
fix a misheard word, then re-run `render` (about 20 seconds).

## How it works

- **Text** comes from Whisper small.en; **timing** from a NeMo CTC model. Whisper's words are
  matched to the CTC words and inherit their timestamps.
- Letterboxed clips are cropped to the picture band automatically (top and bottom bars only).
- Captions are an ASS file rendered by FFmpeg: groups of up to 4 words, active word highlighted.

## Branding

Everything is in `brand.json`: colours, logo size and position, caption font, size and
highlight colour, caption position. Fonts are Manrope (SIL OFL, see `assets/fonts`).
