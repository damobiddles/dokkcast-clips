#!/usr/bin/env python3
"""Episodes: transcribe one, then render its branded clips.

    python dokkepisode.py prep ~/Videos/ep12.mp4     # transcript, speakers and plan.json -> output/ep12/
    python dokkepisode.py render ep12               # the clips, each shape framed as chosen on the review page
    python dokkepisode.py render ep12 --clip 3      # just clip 3

Everything for an episode lives in output/<name>/, driven by plan.json there:

    speakers  {"0": {"name": "Damien", "role": "Host"}}   who diarization speaker 0, 1... is
    cuts      [{"id", "s", "e", "kind", "accept"}]         pauses, fillers and stutters found by prep, cut from the clips
    clips     [{"id", "s", "e", "title", "frame"}]         30-60 s clips, picked by dokkpick.py
"""
import argparse, json, re, shutil, subprocess, sys, wave
from pathlib import Path

import dokkclip
from dokkclip import ROOT, sh, load_brand, norm

SR = 48000


def work_dir(name):
    return ROOT / "output" / name


def probe(video):
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(video)])
    j = json.loads(r.stdout)
    st = j["streams"][0]
    num, den = map(int, st["r_frame_rate"].split("/"))
    return {"w": st["width"], "h": st["height"], "fps": num / den, "fps_str": st["r_frame_rate"],
            "dur": float(j["format"]["duration"])}


def mmss(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


# ------------------------------------------------------------------------ prep
def prep(video, brand, speakers=2):
    name = Path(video).expanduser().stem  # the link's name, as the app gave it, not the file it points at
    video = Path(video).expanduser().resolve()
    wd = work_dir(name); wd.mkdir(parents=True, exist_ok=True)
    wav = wd / "audio16k.wav"
    if not wav.exists():
        print("extracting audio ...")
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    words_json, turns_json = wd / "words.json", wd / "turns.json"
    if not words_json.exists():
        print("transcribing (roughly a quarter of the episode's length) ...")
        dokkclip.transcribe(wav, words_json, brand["models"])
    if not turns_json.exists():
        print("working out who speaks when ...")
        dokkclip.diarize(wav, turns_json, brand["models"], speakers)
    words, turns = json.loads(words_json.read_text()), json.loads(turns_json.read_text())
    tag_speakers(words, turns)
    words_json.write_text(json.dumps(words, indent=1))
    write_transcript(words, wd / "transcript.txt")
    plan_json = wd / "plan.json"
    if plan_json.exists():
        print(f"kept existing {plan_json}")
    else:
        plan = {"video": str(video), "speakers": {str(s): {"name": "", "role": ""} for s in sorted({t["spk"] for t in turns})},
                "cuts": auto_cuts(words, brand["episode"]), "hook": None, "clips": [], "stats": []}
        plan_json.write_text(json.dumps(plan, indent=1))
        print(f"{len(plan['cuts'])} suggested cuts -> {plan_json}")
    print(f"transcript -> {wd / 'transcript.txt'}")


def load_plan(wd):
    """plan.json, with any item given by word numbers ("w": [first, last], as numbered in
    transcript.txt) turned into times. Cuts run to the start of the next word."""
    plan = json.loads((wd / "plan.json").read_text())
    words = json.loads((wd / "words.json").read_text())

    def at(item, kind):
        if "w" not in item:
            return item
        a, b = item["w"]
        s, e = words[a]["s"], words[b]["e"]
        if kind == "cut":
            e = words[b + 1]["s"] if b + 1 < len(words) else e
        elif kind == "stat":
            e = max(e + 1.5, s + 5)
        else:  # hook, clip: a little air either side
            s, e = max(0, s - 0.15), e + 0.3
        return dict(item, s=round(s, 3), e=round(e, 3))
    plan["cuts"] = [at(c, "cut") for c in plan["cuts"]]
    plan["clips"] = [at(c, "clip") for c in plan.get("clips", [])]
    plan["stats"] = [at(c, "stat") for c in plan.get("stats", [])]
    if plan.get("hook"):
        plan["hook"] = at(plan["hook"], "clip")
    return plan


def fix_names(words, spellings):
    """Respell names the recogniser mishears ("doc cast" -> "Dokkcast"), merging words where needed."""
    out, i = [], 0
    while i < len(words):
        for heard, right in spellings.items():
            h = heard.split()
            got = words[i:i + len(h)]
            if len(got) == len(h) and [norm(w["w"]) for w in got] == h:
                tail = re.sub(r"^.*?([^\w']*)$", r"\1", got[-1]["w"])  # keep trailing punctuation
                out.append(dict(got[0], w=right + tail, e=got[-1]["e"]))
                i += len(h)
                break
        else:
            out.append(words[i]); i += 1
    return out


def tag_speakers(words, turns):
    for w in words:
        mid = (w["s"] + w["e"]) / 2
        best = min(turns, key=lambda t: 0 if t["s"] <= mid < t["e"] else min(abs(mid - t["s"]), abs(mid - t["e"])))
        w["spk"] = best["spk"]


def write_transcript(words, path):
    """One line per phrase: [index] mm:ss.s S<speaker>: text. Breaks at speaker changes and pauses."""
    lines, cur = [], []
    for i, w in enumerate(words):
        if cur and (w["spk"] != words[cur[0]]["spk"] or w["s"] - words[cur[-1]]["e"] > 0.6 or len(cur) >= 25):
            lines.append(cur); cur = []
        cur.append(i)
    if cur:
        lines.append(cur)
    out = [f"[{c[0]}] {mmss(words[c[0]]['s'])} S{words[c[0]]['spk']}: " + " ".join(words[i]["w"] for i in c) for c in lines]
    Path(path).write_text("\n".join(out) + "\n")


def auto_cuts(words, cfg):
    cuts = []
    fillers = set(cfg["fillers"])
    for i, w in enumerate(words):
        if i + 1 < len(words):
            gap = words[i + 1]["s"] - w["e"]
            if gap > cfg["pause_max"]:
                pad = cfg["pause_keep"] / 2
                cuts.append({"s": w["e"] + pad, "e": words[i + 1]["s"] - pad, "kind": "pause",
                             "note": f"{gap:.1f}s pause", "accept": True})
        if norm(w["w"]) in fillers:
            nxt = words[i + 1]["s"] if i + 1 < len(words) else w["e"]
            cuts.append({"s": w["s"], "e": min(w["e"], nxt), "kind": "filler", "note": f'"{w["w"]}"', "accept": True})
    # stutters: the same 1-4 words said twice in a row ("I think I think") -> drop the first
    i = 0
    while i < len(words):
        for n in (4, 3, 2, 1):
            a = [norm(x["w"]) for x in words[i:i + n]]
            b = [norm(x["w"]) for x in words[i + n:i + 2 * n]]
            if len(b) < n or a != b or not all(a):
                continue
            if n > 1 and words[i + n - 1]["w"][-1] in ".?!":  # "One page. One page with..." is deliberate
                continue
            if words[i + 2 * n - 1]["e"] - words[i]["s"] < 4:
                cuts.append({"s": words[i]["s"], "e": words[i + n]["s"], "kind": "repeat",
                             "note": "repeated: " + " ".join(x["w"] for x in words[i:i + n]), "accept": True})
                i += n - 1
                break
        i += 1
    cuts.sort(key=lambda c: c["s"])
    for k, c in enumerate(cuts, 1):
        c["id"], c["s"], c["e"] = k, round(c["s"], 3), round(c["e"], 3)
    return [c for c in cuts if c["e"] - c["s"] > 0.05]


# --------------------------------------------------------------------- suggest
# ---------------------------------------------------------------------- review
# ---------------------------------------------------------------------- render
def keep_segments(cuts, start, end):
    """The parts of [start, end] that survive the accepted cuts."""
    segs, t = [], start
    for c in sorted((c for c in cuts if c.get("accept")), key=lambda c: c["s"]):
        if c["e"] <= t or c["s"] >= end:
            continue
        if c["s"] > t:
            segs.append((t, c["s"]))
        t = max(t, c["e"])
    if t < end:
        segs.append((t, end))
    return [(a, b) for a, b in segs if b - a > 0.04]


def mapper(segs, offset=0.0):
    """Original time -> edited time, None if the moment was cut."""
    starts, acc = [], offset
    for a, b in segs:
        starts.append(acc); acc += b - a

    def f(t):
        for (a, b), o in zip(segs, starts):
            if a <= t <= b:
                return o + t - a
        return None
    return f


def kept_pieces(s, e, segs):
    """The parts of original span s..e that survive the cuts, as (a, b) in original time."""
    return [(max(s, a), min(e, b)) for a, b in segs if min(e, b) > max(s, a)]


def load_audio48(video):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def splice(x, segs, xfade=0.012):
    """Join audio segments with short crossfades so cuts don't click."""
    import numpy as np
    n = int(xfade * SR)
    out = np.zeros(0, dtype=np.float32)
    for a, b in segs:
        seg = x[int(a * SR):int(b * SR)].copy()
        if len(out) >= n and len(seg) >= n:
            ramp = np.linspace(0, 1, n, dtype=np.float32)
            seg[:n] = seg[:n] * ramp + out[-n:] * (1 - ramp)
            out = out[:-n]
        out = np.concatenate([out, seg])
    return out


def write_wav(x, path):
    import numpy as np
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def video_trim(segs, fps):
    """select filter keeping only the given original-time segments, with continuous timestamps."""
    def tree(terms):  # ffmpeg rejects a flat sum of more than ~100 terms; a balanced tree is fine
        if len(terms) == 1:
            return terms[0]
        mid = len(terms) // 2
        return f"({tree(terms[:mid])}+{tree(terms[mid:])})"
    expr = tree([f"between(t,{a:.3f},{b:.3f})" for a, b in segs])
    return f"select='{expr}',setpts=N/({fps})/TB"


def music_track(cfg, dur, beds, work, track=None, offset=0.0):
    """A music bed of length dur, audible only inside beds [(s, e)], or None if no music is set up.
    track overrides the first file in cfg["dir"]; offset starts that far into the track."""
    mdir = ROOT / cfg["dir"]
    tracks = [Path(track).expanduser()] if track else (
        sorted(p for p in mdir.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".flac")) if mdir.exists() else [])
    if not tracks or not beds or not tracks[0].exists():
        return None
    if offset:  # wrap the start point so it always lands inside the track
        length = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(tracks[0])]).stdout)
        offset %= max(length - 5, 1)
    f = cfg["fade"]
    gain = "+".join(f"clip((t-{s:.2f})/{f},0,1)*clip(({e:.2f}-t)/{f},0,1)" for s, e in beds)
    out = work / "music.wav"
    sh(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", str(tracks[0]), "-t", f"{dur:.3f}", "-af",
        f"aresample={SR},pan=mono|c0=0.5*c0+0.5*c1,loudnorm=I=-16:TP=-2,volume={cfg['level_db']}dB,"
        f"volume='min(1,{gain})':eval=frame", "-ac", "1", str(out)])
    return out


def finish_audio(voice_wav, music_wav, cfg, out, duck=6):
    """Clean the voice; duck the music under it (duck = compressor ratio, 1 = no ducking)."""
    if music_wav:
        fc = (f"[0:a]{cfg['audio']},asplit[v][key];[1:a][key]sidechaincompress=threshold=0.02:ratio={duck}:"
              f"attack=20:release=400[m];[v][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[out]")
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(voice_wav), "-i", str(music_wav), "-filter_complex", fc,
            "-map", "[out]", "-ar", str(SR), str(out)])
    else:
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(voice_wav), "-af", cfg["audio"], "-ar", str(SR), str(out)])


def framing(clip, shape, layout, speakers, present=None):
    """How a clip sits in one shape: plan.json clip "frame" {shape: {"mode": "fill"|"fit"|"auto", "x": 0..1}},
    chosen on the review page. Unset, a one-person clip fills the frame and a two-person one follows whoever's
    talking. Returns the layout to render with and the fill position (None for the layout's own framing)."""
    fr = (clip.get("frame") or {}).get(shape) or {}
    mode = fr.get("mode") or ("fill" if speakers < 2 else "auto")
    if layout == "speaker" and ((present or speakers) < 2 or mode != "auto"):
        layout = "wide"  # following needs two people; fit and fill keep the picture still
    return layout, ({"x": fr.get("x", 0.5)} if mode == "fill" else None)


def render_clips(name, brand, only_id=None):
    wd = work_dir(name); cdir = wd / "clips"; work = wd / ".work"
    cdir.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
    plan = load_plan(wd)
    video, cfg = Path(plan["video"]), brand["episode"]
    info = probe(video)
    words = json.loads((wd / "words.json").read_text())
    turns = json.loads((wd / "turns.json").read_text())
    # music under the clips: plan.json "music" (set with render --music) or the episode track, quieter than on the episode
    mcfg = dict(cfg["music"], **brand["clips"].get("music", {}))
    if plan.get("music_db") is not None:  # the level picked with the slider on posts.html
        mcfg["level_db"] = plan["music_db"]
    x = None
    for n, clip in enumerate(plan.get("clips", [])):
        if (only_id and clip["id"] != only_id) or (not only_id and clip.get("skip")):
            continue
        stem = f"{name}_clip{clip['id']}"
        segs = keep_segments(plan["cuts"], clip["s"], clip["e"])
        fwd = mapper(segs)
        dur = sum(b - a for a, b in segs)
        if x is None:
            x = load_audio48(video)
        write_wav(splice(x, segs), work / "clip_voice.wav")
        # each clip starts somewhere else in the track so they don't all open on the same bars
        music = music_track(mcfg, dur, [(0, dur)], work, plan.get("music"), offset=n * mcfg.get("offset_step", 30)) \
            if mcfg.get("on", True) else None
        finish_audio(work / "clip_voice.wav", music, cfg, work / "clip_mix.wav", mcfg.get("duck_ratio", 6))
        src = work / f"{stem}.mp4"
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(work / "clip_mix.wav"), "-filter_complex",
            f"[0:v]{video_trim(segs, info['fps'])}[v]", "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "14",
            "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-t", f"{dur:.3f}", str(src.with_suffix(".mov"))])
        src = src.with_suffix(".mov")
        cw, aside = [], 0
        for w in words:
            # sound notes from the transcriber, e.g. "(laughs)" or "[static]", never go on screen; one left
            # unclosed ends after a few words rather than hiding the rest of the captions
            if aside or re.match(r"[(\[]", w["w"]):
                aside = 0 if re.search(r"[)\]][.,!?]*$", w["w"]) or aside >= 5 else aside + 1
                continue
            # a word straddling a join keeps only its surviving piece; mostly-cut words are dropped so they don't flash
            ps = kept_pieces(w["s"], w["e"], segs)
            if not ps or sum(b - a for a, b in ps) < 0.5 * max(w["e"] - w["s"], 0.01):
                continue
            a, b = max(ps, key=lambda q: q[1] - q[0])
            cw.append({"w": w["w"], "s": round(fwd(a), 3), "e": round(fwd(b), 3)})
        # the show's spellings, then this episode's own (plan.json "spellings", e.g. a guest's name)
        cw = dokkclip.fix_ends(fix_names(cw, dict(cfg["spellings"], **plan.get("spellings", {}))), dur)
        (cdir / f"{stem}.words.json").write_text(json.dumps(cw, indent=1))
        ct = []
        for t in turns:
            # map each surviving piece, so a turn whose start or end was cut still steers the camera
            for a, b in kept_pieces(t["s"], t["e"], segs):
                a, b = round(fwd(a), 2), round(fwd(b), 2)
                if ct and ct[-1]["spk"] == t["spk"] and a - ct[-1]["e"] < 0.05:
                    ct[-1]["e"] = b
                elif b > a:
                    ct.append({"s": a, "e": b, "spk": t["spk"]})
        (cdir / f"{stem}.turns.json").write_text(json.dumps(ct or [{"s": 0, "e": dur, "spk": 0}], indent=1))
        for shape, layout in brand["clips"]["formats"].items():
            layout, frame = framing(clip, shape, layout, len(dokkclip.talkers(ct, 0, dur)), len({t["spk"] for t in ct}))
            sides = None
            if layout == "speaker" and brand["layouts"][layout].get("speaker_sides") == "auto":
                sides = dokkclip.episode_sides(video, turns, wd / "sides.json")
                sides = {k: v for k, v in sides.items() if k in {t["spk"] for t in ct}} or None
            dokkclip.render(src, cdir / f"{stem}.words.json", cdir / f"{stem}_{shape}.mp4", brand, work / "clip",
                            layout, turns_json=cdir / f"{stem}.turns.json", frame=frame, sides=sides)
        print(f"clip {clip['id']} '{clip.get('title', '')}' ({dur:.0f}s) done")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["prep", "render"])
    p.add_argument("target", help="prep: the episode video; render: the episode name (file name without .mp4)")
    p.add_argument("--only", choices=["clips"], help="(the clips are all render makes now)")
    p.add_argument("--clip", type=int, help="render just this clip id")
    p.add_argument("--music", help="render: music file to put under the clips (remembered in plan.json)")
    p.add_argument("--speakers", type=int, default=2, help="prep: how many people talk in the episode")
    p.add_argument("--brand", default=str(ROOT / "brand.json"))
    a = p.parse_args()
    brand = load_brand(a.brand, Path(a.target).stem)
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found: run ./setup.sh first")
    if a.command == "prep":
        prep(a.target, brand, a.speakers)
    else:
        name = Path(a.target).stem
        if a.music:
            pj = work_dir(name) / "plan.json"
            pl = json.loads(pj.read_text())
            pl["music"] = str(Path(a.music).expanduser().resolve())
            pj.write_text(json.dumps(pl, indent=1))
        render_clips(name, brand, a.clip)


if __name__ == "__main__":
    main()
