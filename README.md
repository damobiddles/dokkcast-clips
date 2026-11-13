# Offcuts

Turns a podcast episode into a month of social posts: short captioned clips in portrait (TikTok,
Instagram, YouTube Shorts) and square (LinkedIn), a cover graphic for each, the captions to post with
them and one zip, then hands them to Posting Desk for whoever does the posting. Made by MBMS Podcast Studio; each client or show has
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
`scripts/make_app.sh` for the Offcuts app and launcher. The app is its own window (a small Swift
wrapper, `assets/mac/OffcutsWindow.swift`, built by make_app.sh with the Xcode command line tools)
around a local server on http://127.0.0.1:8765 that answers only its own pages. Closing the window
leaves the server running, so anything being made carries on; outside links open in the browser.

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
6. **Posting Desk:** **Upload to Posting Desk** puts the episode's posts, videos and covers on the shared
   posting page (offcuts-posting-desk.netlify.app) and gives a private link to send to whoever posts.
   The admin key is pasted once under New episode and kept in ~/.dokkcast/desk_key. The zip in Ready to
   post has the same posts, a kit.html and a How to post.txt, for posting without the desk.
   **Tidy up** then deletes the videos from Working files and keeps the zip.

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
| `dokkapp.py` | the Offcuts app: episodes, review, brand kits, Posting Desk upload |
| `dokkepisode.py prep <video>` | transcript, speakers and plan.json in output/<name>/ |
| `dokkpick.py <name>` | Claude picks the clips and writes copy.json |
| `dokkpost.py plan / review / apply / finish <name>` | posting dates, the review page, saved choices, make everything |
| `dokkepisode.py render <name> --only clips` | the clips, framed as chosen on the review page |
| `dokkgraphics.py <name>` | cover graphics per clip and shape |
| `dokkpost.py kit / zip <name>` | the posting kit folder and its zip |
| `dokkpost.py desk <name>` | upload the kit to Posting Desk and print its private link |
| `dokkclip.py` | the shared rendering: captions, logo, background, layouts |

Layouts, caption style, music level and posting days and times are in `brand.json`. `dokkepisode.py`
only makes clips now; the full-length edit and Postiz scheduling were removed (they are in git history).
