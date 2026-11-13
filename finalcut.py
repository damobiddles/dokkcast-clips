#!/usr/bin/env python3
"""Final Cut: raw camera files in, the finished full-length episode out, ready for Offcuts.

    python finalcut.py "~/Offcuts/Final Cut/Episodes/2026-11-12 Simon"            # runs (or carries on) the episode
    python finalcut.py <episode> --go                       # carry on after checking the cuts in the Auphonic Editor
    python finalcut.py <episode> --retakes 1,3              # cut retakes 1 and 3 (or all / none) and carry on
    python finalcut.py <episode> --excerpt 10:00-12:30 --music both   # a short test, both music modes
    python finalcut.py <episode> --from cut                 # redo from a stage (and every stage after it)

An episode folder holds:

    main.*            the main video (one camera, about an hour)
    intro.*           the host introducing the episode
    intro-music.wav   the music under the intro (optional: the show's default music otherwise)
    outro.*           optional: the show's shared outro otherwise
    episode.json      optional: {"show": "The Dokkcast", "title": "Simon Daglish"}

Stages, in order (each one is saved in <episode>/final-cut/state.json, so a rerun starts after the
last finished stage; Auphonic uploads of an hour of footage take a while):

    ingest     check the files, ffprobe them, note what has to be conformed to the main video
    auphonic   send the main video to Auphonic (preset + silence, filler and cough cutters) and wait
    review     optional pause to check the cuts in the Auphonic Editor (rerun with --go)
    fetch      download Auphonic's cleaned audio and transcript, and find its cuts
    retakes    find retakes and false starts in the transcript -> retakes.md (rerun with --retakes)
    cut        cut the camera video: Auphonic's cuts plus the approved retakes, a slight zoom on
               alternate segments after the bigger cuts so they look deliberate
    intro      the intro voice through Auphonic (no cutting), conformed to the main video
    assemble   intro + main + outro, music bed under the intro (fixed level or ducked), loudness
               matched to the Auphonic target, H.264 MP4 for YouTube
    handoff    copy the episode and its transcript into Offcuts' Source files

How Auphonic's cuts are used: Auphonic runs with cut mode "set_cuts_to_silence", so its cleaned audio
keeps the camera's timeline with each cut faded to silence. Final Cut then cuts the original camera
picture itself (no second generation of video compression from Auphonic), and every cut, Auphonic's
and the retakes, is in one timeline, so the transcript maps straight onto the finished episode.

Settings are in final-cut.config.json; the Auphonic API key goes in .env (AUPHONIC_API_KEY=...),
which is never committed.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGES = ["ingest", "auphonic", "review", "fetch", "retakes", "cut", "intro", "assemble", "handoff"]
SR = 48000
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".mxf", ".avi")


class Pause(Exception):
    """A stage that needs Damien before it can carry on."""


# ------------------------------------------------------------------ helpers
def sh(cmd, quiet=True):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"\nfailed: {' '.join(str(c) for c in cmd)[:600]}\n{(r.stderr or r.stdout)[-2500:]}")
    return r


def ff(*args):
    return sh(["ffmpeg", "-y", "-hide_banner", "-v", "error", *args])


def say(msg):
    print(msg, flush=True)


def mmss(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:04.1f}" if t >= 3600 else f"{int(t // 60):02d}:{t % 60:04.1f}"


def parse_time(s):
    secs = 0.0
    for part in s.strip().split(":"):
        secs = secs * 60 + float(part)
    return secs


def load_env():
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if p.is_file():
            for line in p.read_text().splitlines():
                m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
                if m and not line.lstrip().startswith("#"):
                    os.environ.setdefault(m.group(1), m.group(2).strip("'\""))


def load_config(path=None):
    cfg = json.loads(Path(path or ROOT / "final-cut.config.json").read_text())
    for k, v in cfg["paths"].items():
        cfg["paths"][k] = Path(v).expanduser()
    return cfg


def probe(path):
    j = json.loads(sh(["ffprobe", "-v", "error", "-show_entries",
                       "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels:format=duration",
                       "-of", "json", path]).stdout)
    info = {"file": str(path), "dur": float(j["format"].get("duration") or 0)}
    for st in j.get("streams", []):
        if st["codec_type"] == "video" and "w" not in info:
            num, den = map(int, st["r_frame_rate"].split("/"))
            info.update(w=st["width"], h=st["height"], fps=st["r_frame_rate"], fps_f=num / max(den, 1), vcodec=st["codec_name"])
        elif st["codec_type"] == "audio" and "sr" not in info:
            info.update(sr=int(st["sample_rate"]), ch=st["channels"], acodec=st["codec_name"])
    return info


def duration(path):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).stdout)


def loudness(path, af=""):
    """Integrated loudness (LUFS) and the rest of loudnorm's first-pass measurement."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af",
                        (af + "," if af else "") + "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        sys.exit(f"couldn't measure the loudness of {path}:\n{r.stderr[-1500:]}")
    return json.loads(m.group(0))


def find(folder, stem, exts):
    hits = sorted(p for p in folder.iterdir() if p.stem.lower() == stem and p.suffix.lower() in exts)
    return hits[0] if hits else None


def select_expr(segs):
    """ffmpeg rejects a flat sum of more than ~100 terms; a balanced tree is fine."""
    def tree(terms):
        if len(terms) == 1:
            return terms[0]
        mid = len(terms) // 2
        return f"({tree(terms[:mid])}+{tree(terms[mid:])})"
    return tree([f"between(t,{a:.3f},{b:.3f})" for a, b in segs])


def keep_segments(cuts, start, end, min_len=0.04):
    segs, t = [], start
    for s, e in sorted(cuts):
        if e <= t or s >= end:
            continue
        if s > t:
            segs.append((t, s))
        t = max(t, e)
    if t < end:
        segs.append((t, end))
    return [(a, b) for a, b in segs if b - a > min_len]


def merge(spans, gap=0.0):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


# ------------------------------------------------------------------ episode
class Episode:
    def __init__(self, folder, cfg, show=None):
        self.dir = Path(folder).expanduser().resolve()
        if not self.dir.is_dir():
            sys.exit(f"no episode folder at {self.dir}")
        self.cfg = cfg
        self.work = self.dir / "final-cut"
        self.work.mkdir(exist_ok=True)
        self.state_file = self.work / "state.json"
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {"done": []}
        meta = json.loads((self.dir / "episode.json").read_text()) if (self.dir / "episode.json").exists() else {}
        self.show = show or meta.get("show") or self.state.get("show") or cfg.get("default_show")
        self.title = meta.get("title") or self.dir.name
        self.state["show"] = self.show

    def save(self):
        self.state_file.write_text(json.dumps(self.state, indent=1))

    def done(self, stage):
        if stage not in self.state["done"]:
            self.state["done"].append(stage)
        self.save()

    def show_cfg(self):
        return (self.cfg.get("shows") or {}).get(self.show) or {}

    def asset(self, rel):
        if not rel:
            return None
        p = Path(rel).expanduser()
        p = p if p.is_absolute() else self.cfg["paths"]["assets"] / p
        return p if p.is_file() else None


# ------------------------------------------------------------------ Auphonic
def auphonic(*args):
    if not shutil.which("auphonic"):
        sys.exit("the Auphonic command line tool isn't installed: get it from https://auphonic.com/cli")
    if not os.environ.get("AUPHONIC_API_KEY") and not (Path.home() / ".auphonic/config.json").exists():
        sys.exit(f"no Auphonic API key: put AUPHONIC_API_KEY=... in {ROOT / '.env'}")
    return sh(["auphonic", *args])


def auphonic_production(ep, key, src, title, cutters):
    """Create and start a production for src (once: its id is saved, so a rerun just waits for it)."""
    a = ep.cfg["auphonic"]
    uid = ep.state.get(f"{key}_uuid")
    if not uid:
        args = ["create", src, "--title", title, "--cut-mode", "set_cuts_to_silence", "--fade-time", str(a.get("fade_ms", 100))]
        if a.get("preset"):
            args += ["--preset", a["preset"]]
        if cutters:
            for name, on in a["cutters"].items():
                if on:
                    args.append(f"--{name}-cutter")
        for o in a["outputs"]:
            args += ["--output", o]
        if a.get("speech_recognition", True):
            args += ["--speech-recognition"] + (["--speech-language", a["language"]] if a.get("language") else [])
        size = Path(src).stat().st_size / 1e9
        say(f"  uploading {Path(src).name} ({size:.1f} GB) to Auphonic ...")
        uid = auphonic(*args, "-q").stdout.strip().split()[-1]
        ep.state[f"{key}_uuid"] = uid; ep.save()
        auphonic("start", uid)
        say(f"  started production {uid}")
    say(f"  waiting for Auphonic to finish {key} (production {uid}) ...")
    auphonic("wait", uid, "--wait-timeout", str(a.get("wait_timeout", 6 * 3600)))
    return uid


def auphonic_fetch(ep, key, uid):
    out = ep.work / f"auphonic-{key}"
    if out.exists():
        shutil.rmtree(out)  # the Editor's Apply Changes reprocesses: always take the latest
    out.mkdir()
    auphonic("download", uid, "--dir", out)
    (out / "production.json").write_text(auphonic("get", uid, "--json").stdout)
    audio = sorted((p for p in out.rglob("*") if p.suffix.lower() in (".wav", ".flac", ".m4a", ".mp4", ".mov")),
                   key=lambda p: (p.suffix.lower() not in (".wav", ".flac"), -p.stat().st_size))
    if not audio:
        sys.exit(f"Auphonic's download in {out} has no audio file")
    return out, audio[0]


def walk(x):
    yield x
    if isinstance(x, dict):
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)


def words_from_speech_json(folder):
    """Words [{"w", "s", "e"}] from Auphonic's speech recognition JSON: every list of timed words in it
    (one per sentence or segment), put back together in time order."""
    found = {}
    for p in folder.rglob("*.json"):
        if p.name == "production.json":
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        for x in walk(data):
            if not (isinstance(x, list) and x and all(isinstance(d, dict) for d in x)):
                continue
            words = []
            for d in x:
                w = d.get("word") or d.get("text") or d.get("token") or d.get("w")
                s = next((d[k] for k in ("start", "begin", "s", "start_time") if k in d), None)
                e = next((d[k] for k in ("end", "stop", "e", "end_time") if k in d), None)
                if isinstance(w, str) and isinstance(s, (int, float)) and isinstance(e, (int, float)):
                    words.append({"w": w.strip(), "s": round(float(s), 3), "e": round(float(e), 3)})
            # a list of words, not of sentences (a sentence's text has spaces in it)
            if len(words) >= 0.9 * len(x) and all(" " not in w["w"] for w in words):
                for w in words:
                    if w["w"]:
                        found[(w["s"], w["e"], w["w"])] = w
    return sorted(found.values(), key=lambda w: w["s"])


def cuts_from_production(folder):
    """Cut regions [(s, e)] from production.json or a downloaded Audacity/region list, if Auphonic gives them."""
    spans = []
    try:
        data = json.loads((folder / "production.json").read_text())
        for x in walk(data):
            if isinstance(x, dict):
                for k, v in x.items():
                    if "cut" in k.lower() and isinstance(v, list):
                        for c in v:
                            if isinstance(c, dict):
                                s, e = c.get("start"), c.get("end", c.get("stop"))
                            elif isinstance(c, (list, tuple)) and len(c) >= 2:
                                s, e = c[0], c[1]
                            else:
                                continue
                            if isinstance(s, (int, float)) and isinstance(e, (int, float)) and e > s:
                                spans.append((float(s), float(e)))
    except Exception:
        pass
    for p in list(folder.rglob("*Regions*.txt")) + list(folder.rglob("*Regions*.csv")):
        for line in p.read_text(errors="ignore").splitlines():
            nums = re.findall(r"\d+(?:\.\d+)?", line)
            if len(nums) >= 2 and "\t" in line:
                s, e = float(nums[0]), float(nums[1])
                if e > s:
                    spans.append((s, e))
    return merge(spans)


def digital_silence(audio, min_len):
    """The regions Auphonic faded to silence (set_cuts_to_silence leaves true digital silence)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(audio), "-af",
                        f"silencedetect=noise=-80dB:d={min_len}", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


# ------------------------------------------------------------------ retakes
RETAKE_PHRASES = [
    r"let me (start|say|do|try) (that|this|it)? ?again", r"(start|say|do|try) (that|this|it) again",
    r"can (we|i) (go|do|start) (that )?again", r"let'?s (go|do|start) (that )?again", r"(i'?ll|let me) start again",
    r"from the top", r"take two", r"let me rephrase", r"scratch that", r"one more time", r"go back to the start",
    r"sorry,? (let me|i'll|can i)", r"we can cut (that|this)", r"cut that( out| bit)?", r"edit (that|this) out",
    r"i messed (that|it) up", r"let me (go|come) back",
]


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def find_retakes(words, window=20.0, ngram=6):
    """Suggested retakes [{"s", "e", "why"}]: cue phrases, and a run of words said again soon after."""
    toks = [norm(w["w"]) for w in words]
    text, pos = "", []
    for i, t in enumerate(toks):
        pos.append(len(text)); text += t + " "
    def word_at(ch):
        return max(i for i, p in enumerate(pos) if p <= ch)
    found = []
    for pat in RETAKE_PHRASES:
        for m in re.finditer(r"\b" + pat + r"\b", text):
            i, j = word_at(m.start()), word_at(max(m.end() - 2, m.start()))
            k = i  # back to the start of the abandoned attempt: a sentence end or a long pause, at most 25 s
            while k > 0 and words[i]["s"] - words[k - 1]["s"] < 25 and not re.search(r"[.?!]$", words[k - 1]["w"]) \
                    and words[k]["s"] - words[k - 1]["e"] < 0.8:
                k -= 1
            end = words[j + 1]["s"] - 0.05 if j + 1 < len(words) else words[j]["e"] + 0.3
            found.append({"s": words[k]["s"] - 0.05, "e": end, "why": f'said "{" ".join(w["w"] for w in words[i:j + 1])}"'})
    seen, skip = {}, 0
    for i in range(len(toks) - ngram):
        g = tuple(toks[i:i + ngram])
        if i < skip or not all(g) or len(set(g)) < 3:
            seen[g] = i
            continue
        a = seen.get(g)
        if a is not None and words[i]["s"] - words[a]["s"] < window and i - a >= ngram:
            found.append({"s": words[a]["s"] - 0.05, "e": words[i]["s"] - 0.05,
                          "why": f'"{" ".join(w["w"] for w in words[a:a + ngram])}..." is said again'})
            skip = i + ngram  # one suggestion per repeat, not one per overlapping run of words
        seen[g] = i
    found.sort(key=lambda r: r["s"])
    out = []
    for r in found:  # overlapping suggestions become one
        if out and r["s"] <= out[-1]["e"]:
            out[-1]["e"] = max(out[-1]["e"], r["e"])
            if r["why"] not in out[-1]["why"] and not any(q in out[-1]["why"] for q in re.findall(r'"([^"]+)"', r["why"])):
                out[-1]["why"] += "; " + r["why"]
        else:
            out.append(dict(r))
    return [r for r in out if 0.3 < r["e"] - r["s"] < 90]


def context(words, s, e, pad=8.0):
    before = " ".join(w["w"] for w in words if s - pad <= w["s"] < s)
    inside = " ".join(w["w"] for w in words if s <= w["s"] < e)
    after = " ".join(w["w"] for w in words if e <= w["s"] < e + pad)
    return before, inside, after


# ------------------------------------------------------------------ encoding
def venc(cfg):
    x = cfg["export"]
    if x.get("encoder") == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", x.get("bitrate", "20M"), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", x.get("preset", "medium"), "-crf", str(x.get("crf", 18)),
            "-profile:v", "high", "-pix_fmt", "yuv420p"]


def conform(spec):
    """Filters that turn any picture into the main video's size and frame rate."""
    w, h = spec["w"], spec["h"]
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={spec['fps']},format=yuv420p")


def piece(cfg, spec, video, audio, out_stem, start=0.0, end=None, segs=None, zoom=0.0, fade=0.015, audio_af=""):
    """One piece of the final episode: a video-only MP4 and a WAV of exactly the same length.
    segs are the kept parts (seconds from start); zoom crops in by that fraction."""
    vf, af = [], [f"aresample={SR}", "aformat=sample_fmts=s16:channel_layouts=stereo"]
    if audio_af:
        af.insert(0, audio_af)
    if segs:
        vf.append(f"select='{select_expr(segs)}',setpts=N/({spec['fps']})/TB")
        af.insert(0, f"aselect='{select_expr(segs)}',asetpts=N/SR/TB")
    if zoom:
        vf.append(f"crop=trunc(iw/{1 + zoom}/2)*2:trunc(ih/{1 + zoom}/2)*2")
    vf.append(conform(spec))
    span = ["-ss", f"{start:.3f}"] + (["-t", f"{end - start:.3f}"] if end is not None else [])
    mp4, wav = Path(f"{out_stem}.mp4"), Path(f"{out_stem}.wav")
    ff(*span, "-i", video, "-an", "-vf", ",".join(vf), *venc(cfg), "-video_track_timescale", "90000", mp4)
    vdur = duration(mp4)
    af += [f"afade=t=in:d={fade}", "apad", f"atrim=end={vdur:.6f}", f"afade=t=out:st={max(vdur - fade, 0):.6f}:d={fade}"]
    ff(*span, "-i", audio, "-vn", "-af", ",".join(af), "-c:a", "pcm_s16le", wav)
    return mp4, wav, vdur


# ------------------------------------------------------------------ stages
def st_ingest(ep, a):
    d = ep.dir
    main = find(d, "main", VIDEO_EXT)
    intro = find(d, "intro", VIDEO_EXT)
    music = find(d, "intro-music", (".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac")) or ep.asset(ep.show_cfg().get("music"))
    outro = find(d, "outro", VIDEO_EXT) or ep.asset(ep.show_cfg().get("outro"))
    if not main:
        sys.exit(f"no main video (main.mp4 / main.mov ...) in {d}")
    for what, f in (("intro", intro), ("intro music", music), ("outro", outro)):
        if not f:
            say(f"  no {what}: the episode goes without one")
    if a.excerpt:
        s, e = (parse_time(x) for x in a.excerpt.split("-"))
        cut = ep.work / f"main-excerpt{main.suffix.lower()}"
        say(f"  test run: main video {mmss(s)} to {mmss(e)} only")
        ff("-ss", f"{s:.3f}", "-i", main, "-t", f"{e - s:.3f}", "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", cut)
        main = cut
        ep.state["test"] = True
    files = {"main": main, "intro": intro, "music": music, "outro": outro}
    info = {k: probe(v) if v else None for k, v in files.items()}
    m = info["main"]
    say(f"  main   {Path(m['file']).name}: {m['w']}x{m['h']} at {m['fps_f']:.3f} fps, {mmss(m['dur'])}, "
        f"audio {m.get('sr')} Hz x{m.get('ch')}")
    for k in ("intro", "outro"):
        i = info[k]
        if i:
            same = (i.get("w"), i.get("h"), round(i.get("fps_f", 0), 3)) == (m["w"], m["h"], round(m["fps_f"], 3))
            say(f"  {k:<6} {Path(i['file']).name}: {i.get('w')}x{i.get('h')} at {i.get('fps_f', 0):.3f} fps, {mmss(i['dur'])}"
                + ("" if same else "  -> conformed to the main video"))
    if info["music"]:
        say(f"  music  {Path(info['music']['file']).name}: {mmss(info['music']['dur'])}")
    if m["h"] > 1080 and ep.cfg["auphonic"].get("send", "video") == "video":
        say("  note: Auphonic's video cutting above 1080p needs a paid plan. Set auphonic.send to \"audio\" in "
            "final-cut.config.json to send only the sound (Final Cut cuts the picture itself either way).")
    ep.state["files"] = {k: str(v) if v else None for k, v in files.items()}
    ep.state["spec"] = {"w": m["w"], "h": m["h"], "fps": m["fps"], "fps_f": m["fps_f"]}
    ep.state["info"] = info


def st_auphonic(ep, a):
    src = Path(ep.state["files"]["main"])
    if ep.cfg["auphonic"].get("send", "video") == "audio":
        wav = ep.work / "main-for-auphonic.flac"
        if not wav.exists():
            say("  extracting the sound to send ...")
            ff("-i", src, "-vn", "-c:a", "flac", wav)
        src = wav
    auphonic_production(ep, "main", src, ep.title, cutters=True)


def st_review(ep, a):
    if not ep.cfg["review"].get("auphonic", True) or a.go:
        return
    uid = ep.state["main_uuid"]
    raise Pause(f"Check the cuts in the Auphonic Editor: https://auphonic.com/engine/status/{uid}\n"
                f"(or run: auphonic open {uid}). Click Apply Changes there if you change anything, then run:\n"
                f'  python finalcut.py "{ep.dir}" --go')


def st_fetch(ep, a):
    folder, audio = auphonic_fetch(ep, "main", ep.state["main_uuid"])
    main_dur = ep.state["info"]["main"]["dur"]
    adur = duration(audio)
    if abs(adur - main_dur) > 0.2:
        say(f"  warning: Auphonic's audio is {adur:.2f} s long and the camera's {main_dur:.2f} s. Its cuts should "
            "leave the length alone (set_cuts_to_silence): check the preset's cut mode.")
    words = words_from_speech_json(folder)
    if not words:
        say("  no word timings from Auphonic: transcribing on this Mac instead ...")
        words = local_words(ep, audio)
    min_len = ep.cfg["auphonic"].get("min_cut", 0.15)
    listed, silent = cuts_from_production(folder), digital_silence(audio, min_len)
    cuts = merge(listed + silent)
    edge = 0.02  # cut inside the faded silence, so the joins stay quiet
    cuts = [(s + edge, e - edge) for s, e in cuts if e - s >= min_len]
    say(f"  {len(words)} words, {len(cuts)} Auphonic cuts ({sum(e - s for s, e in cuts):.0f} s; "
        f"{len(listed)} from its cut list, {len(silent)} from the silenced regions)")
    (ep.work / "words.json").write_text(json.dumps(words, indent=1))
    (ep.work / "auphonic-cuts.json").write_text(json.dumps([[round(s, 3), round(e, 3)] for s, e in cuts]))
    ep.state["main_audio"] = str(audio)


def local_words(ep, audio):
    sys.path.insert(0, str(ROOT))
    import dokkclip
    wav = ep.work / "audio16k.wav"
    ff("-i", audio, "-ac", "1", "-ar", "16000", wav)
    out = ep.work / "words-local.json"
    return dokkclip.transcribe(wav, out, dokkclip.load_brand(ROOT / "brand.json")["models"])


def st_retakes(ep, a):
    words = json.loads((ep.work / "words.json").read_text())
    rj = ep.dir / "retakes.json"
    if rj.exists():
        retakes = json.loads(rj.read_text())
    else:
        retakes = [dict(r, id=i + 1, cut=False) for i, r in enumerate(find_retakes(words))]
    if a.retakes is not None:
        pick = a.retakes.strip().lower()
        ids = {r["id"] for r in retakes} if pick == "all" else set() if pick == "none" else \
            {int(x) for x in re.findall(r"\d+", pick)}
        for r in retakes:
            r["cut"] = r["id"] in ids
        ep.state["retakes_decided"] = True
    rj.write_text(json.dumps(retakes, indent=1))
    lines = [f"# Retakes and false starts: {ep.title}", "",
             "Suggestions from the transcript. Nothing is cut until you choose: run",
             f'`python finalcut.py "{ep.dir}" --retakes 1,3` (or `all` / `none`), or edit retakes.json',
             "(set \"cut\": true, and change \"s\" / \"e\" in seconds to move a cut).", ""]
    if not retakes:
        lines.append("None found.")
    for r in retakes:
        before, inside, after = context(words, r["s"], r["e"])
        lines += [f"## {r['id']}. {mmss(r['s'])} to {mmss(r['e'])} ({r['e'] - r['s']:.1f} s){'  ✓ cut' if r['cut'] else ''}",
                  f"Why: {r['why']}", "", f"> …{before} **[{inside}]** {after}…", ""]
    (ep.dir / "retakes.md").write_text("\n".join(lines))
    say(f"  {len(retakes)} suggested retakes -> {ep.dir / 'retakes.md'}")
    if not retakes or not ep.cfg["review"].get("retakes", True) or ep.state.get("retakes_decided"):
        return
    raise Pause(f"Read {ep.dir / 'retakes.md'} and choose which retakes to cut, then run:\n"
                f'  python finalcut.py "{ep.dir}" --retakes 1,3     (or all / none)')


def chunks(ep):
    """The kept parts of the main video, grouped into chunks split at the bigger cuts."""
    main_dur = ep.state["info"]["main"]["dur"]
    auph = [tuple(c) for c in json.loads((ep.work / "auphonic-cuts.json").read_text())]
    rj = ep.dir / "retakes.json"
    retakes = [(r["s"], r["e"]) for r in json.loads(rj.read_text()) if r.get("cut")] if rj.exists() else []
    cuts = merge(auph + retakes)
    z = ep.cfg["zoom"]
    big = [c for c in cuts if any(c[0] <= r[0] + 0.05 and r[1] - 0.05 <= c[1] for r in retakes)
           or c[1] - c[0] >= z.get("min_cut_seconds", 2.0)]
    keeps = keep_segments(cuts, 0, main_dur)
    groups, cur, bigs = [], [], iter(sorted(big))
    nxt = next(bigs, None)
    for s, e in keeps:
        while nxt and nxt[1] <= s + 1e-6:
            if cur:
                groups.append(cur); cur = []
            nxt = next(bigs, None)
        cur.append((s, e))
    if cur:
        groups.append(cur)
    return groups, cuts, keeps


def st_cut(ep, a):
    spec, cfg = ep.state["spec"], ep.cfg
    groups, cuts, keeps = chunks(ep)
    z = cfg["zoom"]
    main_dur = ep.state["info"]["main"]["dur"]
    kept = sum(e - s for s, e in keeps)
    say(f"  {mmss(main_dur)} -> {mmss(kept)}: {len(cuts)} cuts, {len(groups)} segments"
        + (f", zoom {z['amount'] * 100:.0f}% on alternate segments after the bigger cuts" if z.get("on") else ""))
    out = ep.work / "main-pieces"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    pieces = []
    for i, g in enumerate(groups):
        s, e = g[0][0], g[-1][1]
        zoom = z["amount"] if z.get("on") and i % 2 == 1 else 0.0
        say(f"  segment {i + 1}/{len(groups)}: {mmss(s)} to {mmss(e)}{'  (zoomed)' if zoom else ''}")
        mp4, wav, d = piece(cfg, spec, ep.state["files"]["main"], ep.state["main_audio"], out / f"{i:03d}",
                            s, e, [(x - s, y - s) for x, y in g], zoom)
        pieces.append({"mp4": str(mp4), "wav": str(wav), "dur": d, "segs": g})
    ep.state["main_pieces"] = pieces


def st_intro(ep, a):
    intro = ep.state["files"]["intro"]
    if not intro:
        ep.state["intro_piece"] = None
        return
    uid = auphonic_production(ep, "intro", intro, f"{ep.title} intro", cutters=False)
    folder, audio = auphonic_fetch(ep, "intro", uid)
    ep.state["intro_words"] = words_from_speech_json(folder)
    mp4, wav, d = piece(ep.cfg, ep.state["spec"], intro, audio, ep.work / "intro")
    ep.state["intro_piece"] = {"mp4": str(mp4), "wav": str(wav), "dur": d}


def st_assemble(ep, a):
    cfg, spec = ep.cfg, ep.state["spec"]
    target, tp = cfg["loudness"]["target_lufs"], cfg["loudness"]["true_peak"]
    pieces = ([ep.state["intro_piece"]] if ep.state.get("intro_piece") else []) + ep.state["main_pieces"]
    outro = ep.state["files"]["outro"]
    if outro:
        lv = loudness(outro)  # the outro hasn't been through Auphonic: bring it to the episode's loudness
        af = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={lv['input_i']}:measured_TP={lv['input_tp']}:"
              f"measured_LRA={lv['input_lra']}:measured_thresh={lv['input_thresh']}:offset={lv['target_offset']}:linear=true")
        mp4, wav, d = piece(cfg, spec, outro, outro, ep.work / "outro", audio_af=af)
        pieces.append({"mp4": str(mp4), "wav": str(wav), "dur": d})
    vlist, alist = ep.work / "video.txt", ep.work / "audio.txt"
    vlist.write_text("".join(f"file '{p['mp4']}'\n" for p in pieces))
    alist.write_text("".join(f"file '{p['wav']}'\n" for p in pieces))
    say(f"  joining {len(pieces)} pieces ...")
    video, voice = ep.work / "video.mp4", ep.work / "voice.wav"
    ff("-f", "concat", "-safe", "0", "-i", vlist, "-c", "copy", video)
    ff("-f", "concat", "-safe", "0", "-i", alist, "-c", "copy", voice)
    total = duration(video)
    intro_dur = ep.state["intro_piece"]["dur"] if ep.state.get("intro_piece") else 0.0
    music = ep.state["files"]["music"]
    modes = ["fixed", "duck"] if a.music == "both" else [a.music or cfg["music"]["mode"]]
    if not (music and intro_dur):
        modes = ["none"]
    finals = {}
    for mode in modes:
        mixed = ep.work / f"mix-{mode}.wav"
        mix_music(ep, voice, music, intro_dur, mode, mixed) if mode != "none" else shutil.copy(voice, mixed)
        lv = loudness(mixed)
        out = ep.dir / (f"{ep.title}.mp4" if len(modes) == 1 else f"{ep.title} - music {mode}.mp4")
        say(f"  exporting {out.name} ({mmss(total)}, loudness {float(lv['input_i']):.1f} -> {target} LUFS) ...")
        ff("-i", video, "-i", mixed, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af",
           f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={lv['input_i']}:measured_TP={lv['input_tp']}:"
           f"measured_LRA={lv['input_lra']}:measured_thresh={lv['input_thresh']}:offset={lv['target_offset']}:"
           f"linear=true,aresample={SR}", "-c:a", "aac", "-b:a", cfg["export"].get("audio_bitrate", "256k"),
           "-movflags", "+faststart", "-shortest", out)
        finals[mode] = str(out)
    ep.state["finals"] = finals
    ep.state["final"] = finals.get(cfg["music"]["mode"]) or next(iter(finals.values()))
    write_transcript(ep, intro_dur)


def mix_music(ep, voice, music, intro_dur, mode, out):
    m = ep.cfg["music"]
    target = ep.cfg["loudness"]["target_lufs"]
    fade_in, fade_out = m["fade_in"], m["fade_out"]
    bed = intro_dur + fade_out  # it fades out over the first seconds of the main episode
    mlen = duration(music)
    loop = mlen < bed and m.get("if_shorter", "loop") == "loop"
    length = bed if (loop or mlen >= bed) else mlen
    below = m["fixed_db_below_voice"] if mode == "fixed" else m["duck"]["db_below_voice"]
    gain = target - below - float(loudness(music)["input_i"])
    shape = [f"afade=t=in:d={fade_in}"]
    if length >= bed:
        shape.append(f"afade=t=out:st={max(bed - fade_out, 0):.3f}:d={fade_out}")
    elif m.get("end_fade", 1.0):
        shape.append(f"afade=t=out:st={max(length - m['end_fade'], 0):.3f}:d={m['end_fade']}")
    mus = (f"[1:a]aresample={SR},aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=end={length:.3f},"
           f"volume={gain:.2f}dB,{','.join(shape)},apad[m0]")
    if mode == "duck":
        d = m["duck"]
        fc = (f"{mus};[0:a]asplit[v][key];[m0][key]sidechaincompress=threshold={d['threshold']}:ratio={d['ratio']}:"
              f"attack={d['attack_ms']}:release={d['release_ms']}[m];[v][m]amix=inputs=2:duration=first:normalize=0[out]")
    else:
        fc = f"{mus};[0:a][m0]amix=inputs=2:duration=first:normalize=0[out]"
    say(f"  music ({mode}): {Path(music).name}, {mmss(length)}{' looped' if loop else ''}, about {below} dB under the voice")
    ff("-i", voice, *(["-stream_loop", "-1"] if loop else []), "-i", music, "-filter_complex", fc, "-map", "[out]",
       "-c:a", "pcm_s16le", out)


def write_transcript(ep, intro_dur):
    """The finished episode's words, in Offcuts' words.json format ({"w", "s", "e"} in seconds)."""
    words = json.loads((ep.work / "words.json").read_text())
    out = [dict(w) for w in ep.state.get("intro_words") or [] if w["e"] <= intro_dur + 0.05]
    t0 = intro_dur
    for p in ep.state["main_pieces"]:
        acc = t0
        for s, e in p["segs"]:
            for w in words:
                if s <= w["s"] and w["e"] <= e + 0.05:
                    out.append({"w": w["w"], "s": round(acc + w["s"] - s, 3), "e": round(acc + min(w["e"], e) - s, 3)})
            acc += e - s
        t0 += p["dur"]
    (ep.work / "final.words.json").write_text(json.dumps(out, indent=1))
    lines, line, start = [], [], 0.0
    for w in out:
        if not line:
            start = w["s"]
        line.append(w["w"])
        if re.search(r"[.?!]$", w["w"]) and len(line) > 8:
            lines.append(f"[{mmss(start)}] {' '.join(line)}"); line = []
    if line:
        lines.append(f"[{mmss(start)}] {' '.join(line)}")
    (ep.work / "final.transcript.txt").write_text("\n".join(lines) + "\n")


def st_handoff(ep, a):
    if ep.state.get("test") and not a.handoff:
        say("  test run: not copied to Offcuts (add --handoff to copy it anyway)")
        return
    dest = ep.cfg["paths"]["offcuts_source"]
    dest.mkdir(parents=True, exist_ok=True)
    final = Path(ep.state["final"])
    target = dest / f"{ep.title}.mp4"
    if target.exists() and target.stat().st_size != final.stat().st_size:
        sys.exit(f"Source files already has a different {target.name}: rename one of them")
    say(f"  copying to {target} ...")
    shutil.copy2(final, target)
    shutil.copy2(ep.work / "final.words.json", dest / f"{ep.title}.words.json")
    shutil.copy2(ep.work / "final.transcript.txt", dest / f"{ep.title}.transcript.txt")


RUN = {"ingest": st_ingest, "auphonic": st_auphonic, "review": st_review, "fetch": st_fetch, "retakes": st_retakes,
       "cut": st_cut, "intro": st_intro, "assemble": st_assemble, "handoff": st_handoff}


def main():
    p = argparse.ArgumentParser(description="Final Cut: raw camera files in, finished episode out.")
    p.add_argument("episode", help="the episode folder (main, intro, intro-music, outro)")
    p.add_argument("--go", action="store_true", help="carry on after checking the cuts in the Auphonic Editor")
    p.add_argument("--retakes", help="which suggested retakes to cut: 1,3 / all / none")
    p.add_argument("--from", dest="start", choices=STAGES, help="redo from this stage")
    p.add_argument("--excerpt", help="test on part of the main video, e.g. 10:00-12:30")
    p.add_argument("--music", choices=["fixed", "duck", "both"], help="music mode (default from the config)")
    p.add_argument("--show", help="the show, for its outro and default music")
    p.add_argument("--handoff", action="store_true", help="copy a test run to Offcuts too")
    p.add_argument("--config", help="another config file")
    a = p.parse_args()
    load_env()
    cfg = load_config(a.config)
    ep = Episode(a.episode, cfg, a.show)
    if a.start:
        ep.state["done"] = [s for s in ep.state["done"] if STAGES.index(s) < STAGES.index(a.start)]
    if a.retakes is not None and "retakes" in ep.state["done"]:  # a new choice of retakes redoes the cut
        ep.state["done"] = [s for s in ep.state["done"] if STAGES.index(s) < STAGES.index("retakes")]
    if a.music and "assemble" in ep.state["done"]:
        ep.state["done"] = [s for s in ep.state["done"] if STAGES.index(s) < STAGES.index("assemble")]
    if a.excerpt and "ingest" in ep.state["done"]:
        sys.exit("this episode has already started: use --from ingest to start again with an excerpt")
    say(f"Final Cut: {ep.title} ({ep.show})")
    for i, stage in enumerate(STAGES, 1):
        if stage in ep.state["done"]:
            continue
        say(f"\n[{i}/{len(STAGES)}] {stage}")
        t = time.time()
        try:
            RUN[stage](ep, a)
        except Pause as why:
            ep.save()
            say(f"\nPaused.\n{why}")
            sys.exit(10)
        ep.done(stage)
        say(f"  done ({time.time() - t:.0f} s)")
    finals = ep.state.get("finals") or {}
    say("\nFinished: " + ", ".join(finals.values()))


if __name__ == "__main__":
    main()
