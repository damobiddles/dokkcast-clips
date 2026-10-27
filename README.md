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

## Full episodes

`dokkepisode.py` gives a whole episode a light edit and cuts branded clips from it.

```bash
./setup.sh                                         # one-off: ffmpeg, Python packages, models
.venv/bin/python dokkepisode.py prep ~/Videos/ep12.mp4     # transcript + automatic cut suggestions
.venv/bin/python dokkepisode.py review ep12                # output/ep12/review.html: check every suggestion
.venv/bin/python dokkepisode.py render ep12                # edited episode + clips
```

1. **prep** extracts the audio, transcribes it, works out who speaks when, and writes
   `output/<name>/plan.json` with the pauses (over 1 s), filler words and stutters it can find.
   Use `--speakers 3` for three people.
2. Claude reads `output/<name>/transcript.txt` and adds the editorial part of the plan: awkward or
   repeated passages to cut, the hook, 30 to 60 second clips and stat cards.
3. **review** builds a page that plays each suggestion with and without the cut. Tick or untick,
   name the speakers, then "Copy my choices" into the chat or download `plan.json` over the old one.
4. **render** writes `output/<name>/<name>_edited.mp4`: the hook, then the episode with accepted cuts,
   cleaned audio at -16 LUFS, name cards the first time each speaker talks, stat cards, and a quiet
   music bed under the intro and outro if a track is in `assets/music/`. Each clip is rendered three
   ways into `output/<name>/clips/`: `portrait` (9:16), `square` (1:1) and `landscape` (16:9).

The cuts, audio chain, music level and spellings of names the recogniser mishears are in the
`episode` section of `brand.json`. The video never leaves the machine it is on.
