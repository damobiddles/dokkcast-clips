# dokkcast-clips

Brands short Dokkcast clips for social: udokk logo on top, the picture in the middle,
word-highlighted Manrope captions underneath, on a navy-to-purple gradient (1080x1920).

## Use

```bash
pip install -r requirements.txt          # needs ffmpeg with libass on PATH
scripts/fetch_models.sh                  # one-off, ~700 MB
python dokkclip.py all input/clip.mp4                    # -> output/clip_wide.mp4
python dokkclip.py render input/clip.mp4 --layout speaker # -> output/clip_speaker.mp4
```

Steps can be run separately: `transcribe` writes `output/<name>.words.json`
(a list of `{"w", "s", "e"}`), and `render` builds the video from it. Edit that JSON to
fix a misheard word, then re-run `render` (about 20 seconds).

## Layouts

- `wide` keeps the whole shot, picture band in the middle.
- `speaker` zooms in (720 px wide window) and pans to whoever is talking. Who-speaks-when comes
  from a diarization model and is cached in `output/<name>.turns.json`; edit that file to fix a
  wrong cut and re-run `render`. Turns shorter than `min_hold` are ignored so brief
  "mm-hms" don't cause cutaways. Which speaker sits left/right is guessed from motion
  (`speaker_sides: "auto"`); set `{"0": "right", "1": "left"}` in `brand.json` to pin it.

## How it works

- **Text** comes from Whisper small.en; **timing** from a NeMo CTC model. Whisper's words are
  matched to the CTC words and inherit their timestamps.
- Letterboxed clips are cropped to the picture band automatically (top and bottom bars only).
- Captions are an ASS file rendered by FFmpeg: groups of up to 4 words, active word highlighted.

## Branding

Everything is in `brand.json`: colours, logo size and position, caption font, size and
highlight colour, caption position. Fonts are Manrope (SIL OFL, see `assets/fonts`).
