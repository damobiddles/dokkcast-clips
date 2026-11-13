# Final Cut — brief for Claude Code

Paste this into Claude Code in the **Offcuts** project, or save it as `docs/final-cut-brief.md` and ask Claude to read it.

## Goal

Add a "Final Cut" stage in front of Offcuts. It takes the raw camera files from a podcast recording, cleans, cuts and assembles them into the finished full-length episode, then drops that episode into the Offcuts source files folder so Offcuts can make clips from it.

The tools it replaces for long-form editing are Descript and Riverside. It relies on my camera, Auphonic and ffmpeg.

## Inputs (per episode)

- `main.*` — the main podcast video: one static camera, two people on a sofa, roughly 1 hour long
- `intro.*` — the intro video: the host on camera describing what's coming up in the episode
- `intro-music.wav` — the music bed that plays under the intro (sometimes a different track per episode)
- `outro.*` — the outro (usually the same every episode, so keep it in a shared assets folder)

## Pipeline

1. **Ingest.** Read the episode folder and check what's in it. Use ffprobe to get the resolution, frame rate and audio format of each file. If the intro or outro doesn't match the main video, conform it to the main video's specs.
2. **Auphonic: main video.** Send the full main video (video, not just the extracted audio) to Auphonic through its CLI or API, with my preset plus automatic cutting:
   - silence cutter, filler-word cutter, cough cutter
   - output: video MP4
   - also request the cut list and the transcript
   - CLI example from Auphonic: `auphonic process --silence-cutter --filler-cutter --cough-cutter -o video:mp4 recording.mp4`
   - check the CLI and API docs for the current option names, preset selection and how authentication works
   - note: 4K video cutting needs a paid Auphonic plan; 1080p is free
3. **Review point (optional, switched on by default to start with).** Pause so I can check the cuts in the Auphonic Editor before carrying on.
4. **Retakes and false starts.** Go through the transcript to find retakes ("sorry, let me start that again", repeated sentences, abandoned starts). Write `retakes.md` with timestamps and the surrounding text, and only cut after I approve. Auphonic doesn't catch these.
5. **Jump-cut handling.** It's a static two-shot, so cuts are visible. Short silence cuts can be left as they are. For larger cuts (retakes), add a slight digital zoom-in (around 5–10%) on alternate segments so the cuts look deliberate. Make this configurable.
6. **Intro.** Run the intro voice through Auphonic too, with no cutting, so it matches the episode's sound. Then mix in the music bed with ffmpeg:
   - two modes, set in config:
     - `fixed`: the music sits about 18–24 dB below the voice
     - `duck`: sidechain compression, so the music dips when the host speaks
   - fade the music in at the start; fade it out over 2–3 seconds into the main episode
   - if the music runs longer than the intro, trim it and fade it; if it's shorter, loop it or let it end (configurable)
   - mix the music after the Auphonic processing, so Auphonic doesn't treat it as noise
7. **Assemble.** Join intro, main and outro. Normalise the loudness across the joins to match the Auphonic target (for example -16 LUFS for podcast/YouTube).
8. **Export.** Export an H.264 MP4 that's ready for YouTube, at the main video's resolution and frame rate.
9. **Handoff.** Copy the final episode into the Offcuts source files folder, and pass along the transcript, since Offcuts can probably reuse it rather than transcribing again.

## Config (one file, e.g. `final-cut.config.json`)

- paths: episode inbox, shared assets (outro, default music), Offcuts source folder
- Auphonic: API key (keep it in `.env`, never commit it), preset name, which cutters to turn on
- music mode (`fixed` / `duck`), music level, fade lengths
- zoom on cuts: on/off, zoom amount
- review pauses: on/off

## How it should run

One command per episode, e.g. `final-cut ./episodes/2026-11-12-dokkcast-ep42`. It should print what it's doing at each stage, and be able to restart from the last finished stage if something fails partway, because Auphonic uploads of an hour of footage take a while.

## Questions to ask me before building

1. Where does Offcuts expect its source files (exact folder), and does it want anything alongside the video?
2. What resolution and frame rate does the camera record at? (This decides whether I need a paid Auphonic plan.)
3. What's the name of my Auphonic preset?
4. Is the outro the same for every show, or different per show (Dokkcast, Pod Pivot, Omens…)? If it's different, add per-show profiles.

## First test

Use a 2–3 minute excerpt of the main video, plus the real intro, music and outro. Render both music modes so I can choose by ear.
