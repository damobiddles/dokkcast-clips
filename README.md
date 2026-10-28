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
2. Claude reads `output/<name>/transcript.txt` and writes the editorial part of the plan: awkward or
   repeated passages to cut, the hook, 30 to 60 second clips and stat cards. Items can give word
   numbers from the transcript (`"w": [first, last]`) instead of times. Merge them with
   `python dokkepisode.py suggest <name> suggestions.json`.
3. **review** builds a page that plays each suggestion with and without the cut. Tick or untick,
   name the speakers, then "Copy my choices" into the chat or download `plan.json` over the old one.
4. **render** writes `output/<name>/<name>_edited.mp4`: the hook, then the episode with accepted cuts,
   cleaned audio at -16 LUFS, name cards the first time each speaker talks, stat cards, and a quiet
   music bed under the intro and outro if a track is in `assets/music/`. Each clip is rendered three
   ways into `output/<name>/clips/`: `portrait` (9:16), `square` (1:1) and `landscape` (16:9).

The cuts, audio chain, music level and spellings of names the recogniser mishears are in the
`episode` section of `brand.json`. The video never leaves the machine it is on.

## Posting to socials

`dokkpost.py` schedules finished clips to TikTok, Instagram, YouTube Shorts and LinkedIn through
[Postiz](https://postiz.com), with an approval step first. One-off setup: connect the accounts in
Postiz, then `npm install -g postiz` and `postiz auth:login`.

```
python dokkpost.py plan Marcela        # posts.json from the clips and output/Marcela/copy.json
python dokkpost.py review Marcela      # posts.html: tick clips, edit copy and dates, save choices
python dokkpost.py apply Marcela ~/Downloads/posts.json
python dokkpost.py schedule Marcela    # dry run
python dokkpost.py schedule Marcela --go
```

Nothing goes to Postiz unless it is approved and `--go` is given. Shapes per platform, time zone,
posting days and Postiz settings are under `social` in `brand.json`.

To post by hand instead, `python dokkpost.py kit Marcela` needs no Postiz. It writes
`output/Marcela/kit/`: a folder per post with each platform's video and caption, and `kit.html`
with copy buttons, upload links and a done tick for each platform. It uses the approved posts, or
all of them if none are approved yet.

### The studio app

The whole routine below in one window. Once, after `./setup.sh`: `scripts/make_app.sh`, which puts
the app in ~/Applications (drag it to the Dock). Open it, paste an Anthropic API key
(console.anthropic.com, kept in ~/.dokkcast/anthropic_key), then for each episode choose the video
and a music track, type the guest's name and job, and press Start. It transcribes, has Claude
(`dokkpick.py`) pick the clips and write the captions, and opens the review page; set the music,
edit and approve there, Save, then press **Make the clips** for the clips, covers, kit and zip.
Progress is kept per episode, so the app can be closed and reopened at any point.

The app's own name, logo, icon, fonts and colours are in brand.json under `studio` (MBMS Podcast
Studio); the clips, covers and kit keep the show's branding from the rest of brand.json. The MBMS
logo and icon files go in `assets/studio/` (from the MBMS brand kit; not kept in git). Run
`scripts/make_app.sh` again after changing them.

### One episode, start to finish

1. `python dokkepisode.py prep ~/Movies/Dokkcast/<Guest>.mp4`: transcript in output/<Guest>/.
2. Claude picks the clips (`dokkepisode.py suggest`) and writes `output/<Guest>/copy.json`.
3. `python dokkpost.py plan <Guest> --start <date>`, then
   `python dokkpost.py review <Guest> --music ~/Movies/Dokkcast/<track>.wav` writes the review page: each
   clip plays from the episode before it's rendered, with the track underneath and a Music slider to set
   the mix by ear, plus its titles, captions, hashtags and date. Tick Approve, edit, then Save my choices
   and run `python dokkpost.py apply <Guest> ~/Downloads/posts.json` (this also keeps the music level).
4. `python dokkpost.py finish <Guest>`: renders only the approved clips with the music underneath at the
   saved level (default `clips.music` in brand.json, lightly ducked under speech, each clip starting at a
   different point in the track), makes the cover graphics, the kit and the zip.
