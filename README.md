# Offcuts

Turns a podcast episode into a month of social posts: short captioned clips in portrait (TikTok,
Instagram, YouTube Shorts) and square (LinkedIn), a cover graphic for each, the captions to post with
them, a posting calendar and one zip to share. Made by MBMS Podcast Studio; each client or show has
its own brand kit. Everything runs on the Mac it's on, and the video never leaves it.

## On the Mac

Everything lives in one folder, `~/Offcuts` (macOS won't let the app open Desktop, Documents or Downloads):

    Source files/      episode videos and music tracks (Choose… opens here)
    Ready to post/     one zip per finished episode: clips, covers and captions
    Brand kits/        one folder per client or show: kit.json and its logo
    Working files/     each episode's workings; the app looks after it
    App/               this repository, with its .venv and models (output/ links to ../Working files)
    Offcuts            the launcher
    Where things are.txt

Set up once from `App/`: `./setup.sh` (ffmpeg, Python packages, ~700 MB of models), then
`scripts/make_app.sh` for the Offcuts app and launcher. Offcuts is a small local web app on
http://127.0.0.1:8765 that Chrome can also install as an app; it answers only its own pages.

## An episode, start to finish

1. **New episode:** choose the video, a music track and the brand kit, add the guest's name and job, Start.
2. **Transcribe** (about 10 minutes): Whisper for the words, a CTC model for the timing, a diarization
   model for who speaks when.
3. **Pick clips:** Claude chooses 30 to 60 second clips and writes the captions for each platform.
   By default the app pauses here for Claude in chat; it can also use Claude Code on the Mac or the API.
4. **Review:** each clip plays with the brand's logo, colours and captions over it, in each shape.
   Choose how it's framed (Fill and drag, Whole shot, or Follow speaker for two people), set the music
   level, edit the captions and dates, tick Approve, then **Save and make the clips**.
5. **Make** (about 20 minutes): renders the approved clips with music, the covers, the kit and the zip.
6. **Calendar:** each day's post with its captions to copy and videos to drag in. Tick Posted; once all
   are posted, **Tidy up** deletes the videos from Working files and keeps the zip.

An episode card can also **Change brand** (no new transcription) or **Cancel project** (its working
files go to the Bin; the video stays in Source files).

## Brand kits

A kit is a folder with `kit.json` and a logo (SVG, PNG or JPEG), made and edited in the app:

    {"show": "The Acme Show", "logo": "logo.png",
     "colors": {"top": "#101010", "bottom": "#303030", "highlight": "#00FF88", "text": "#FFFFFF"},
     "host": "", "host_role": "", "about": "a podcast about small business", "notes": "",
     "spellings": {"acme fun": "ACME Fun"}}

It goes on top of `brand.json`: background, caption colours, logo, show name on the covers and zip, and
what Claude is told when it picks clips (with style examples only from the same kit's episodes). Its
spellings fix names the transcriber mishears; brand.json's own spellings are The Dokkcast's and apply
only to that kit. The app passes the kit to every tool in `OFFCUTS_KIT`; run by hand, a tool uses the
kit in the episode's `app.json`.

## The tools

The app runs these; each also works on its own from `App/` with `.venv/bin/python`.

| Tool | Does |
|---|---|
| `dokkapp.py` | the Offcuts app: episodes, review, calendar, brand kits |
| `dokkepisode.py prep <video>` | transcript, speakers and plan.json in output/<name>/ |
| `dokkpick.py <name>` | Claude picks the clips and writes copy.json |
| `dokkpost.py plan / review / apply / finish <name>` | posting dates, the review page, saved choices, make everything |
| `dokkepisode.py render <name> --only clips` | the clips, framed as chosen on the review page |
| `dokkgraphics.py <name>` | cover graphics per clip and shape |
| `dokkpost.py kit / zip <name>` | the posting kit folder and its zip |
| `dokkclip.py` | the shared rendering: captions, logo, background, layouts |

Layouts, caption style, music level and posting days and times are in `brand.json`. `dokkepisode.py`
can still make a full-length edit (`review`, then `render` without `--only clips`), and `dokkpost.py
schedule` can post through Postiz, but neither is part of the routine.

## Final Cut: the full episode, before Offcuts

`finalcut.py` turns the raw camera files into the finished full-length episode and puts it in
Offcuts' Source files folder (brief: `docs/final-cut-brief.md`). One folder per episode, with
`main.*`, `intro.*`, `intro-music.wav` and optionally `outro.*` (otherwise the show's shared outro
from `final-cut.config.json`):

    python finalcut.py "~/Offcuts/Final Cut/Episodes/<episode>"          # runs, or carries on
    python finalcut.py <episode> --go                    # after checking the cuts in the Auphonic Editor
    python finalcut.py <episode> --retakes 1,3           # cut those retakes (all / none) and carry on
    python finalcut.py <episode> --excerpt 10:00-12:30 --music both     # a short test, both music modes

It needs the Auphonic command line tool (https://auphonic.com/cli) and `AUPHONIC_API_KEY=...` in
`.env` next to the app (never committed). Settings, including the Auphonic preset, music mode and
levels, zoom on cuts and the review pauses, are in `final-cut.config.json`.
