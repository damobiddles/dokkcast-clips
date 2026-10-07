#!/usr/bin/env python3
"""Dokkcast clip branding: transcribe a clip, then render it with the udokk logo,
a branded background and word-highlighted Manrope captions.

    python dokkclip.py all input/clip.mp4            # transcribe + render
    python dokkclip.py transcribe input/clip.mp4     # writes output/clip.words.json
    python dokkclip.py render input/clip.mp4         # uses output/clip.words.json

Branding lives in brand.json. Words can come from any source: render only needs
a JSON list of {"w": "word", "s": start_seconds, "e": end_seconds}.
"""
import argparse, difflib, io, json, re, subprocess, sys, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"command failed: {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r


def load_brand(path):
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------------ transcribe
def load_audio(video):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "wav", "-"], capture_output=True, check=True).stdout
    w = wave.open(io.BytesIO(raw))
    return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def chunk_bounds(x, sr=16000, target=24.0, limit=28.0):
    """Cut long audio at the quietest moment between `target` and `limit` seconds."""
    import numpy as np
    bounds, start, n = [], 0, len(x)
    hop = sr // 100
    while n - start > limit * sr:
        a, b = start + int(target * sr), start + int(limit * sr)
        env = np.array([np.abs(x[i:i + hop]).mean() for i in range(a, b, hop)])
        env = np.convolve(env, np.ones(10) / 10, mode="same")
        cut = a + int(env.argmin()) * hop
        bounds.append((start, cut))
        start = cut
    bounds.append((start, n))
    return bounds


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def transcribe(video, out_json, models):
    import sherpa_onnx
    x = load_audio(video)
    wd, cd = ROOT / models["whisper_dir"], ROOT / models["ctc_dir"]
    whisper = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=str(wd / "small.en-encoder.int8.onnx"), decoder=str(wd / "small.en-decoder.int8.onnx"),
        tokens=str(wd / "small.en-tokens.txt"), num_threads=4, language="en", task="transcribe")
    ctc = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=str(cd / "model.int8.onnx"), tokens=str(cd / "tokens.txt"), num_threads=4)

    words = []
    for a, b in chunk_bounds(x):
        seg, off = x[a:b], a / 16000
        s = whisper.create_stream(); s.accept_waveform(16000, seg); whisper.decode_stream(s)
        text = s.result.text.strip()
        s = ctc.create_stream(); s.accept_waveform(16000, seg); ctc.decode_stream(s)
        res = s.result
        # merge CTC sub-word tokens into timed words
        timed = []
        for tok, t in zip(res.tokens, res.timestamps):
            if tok.startswith(" ") or not timed:
                timed.append([tok.strip(), t + off])
            else:
                timed[-1][0] += tok
        words += align([w for w in text.split() if any(c.isalnum() for c in w)], timed, off, len(seg) / 16000 + off)
    words = fix_ends(words, len(x) / 16000)
    Path(out_json).write_text(json.dumps(words, indent=1))
    print(f"{len(words)} words -> {out_json}")
    return words


def align(text_words, timed, t0, t1):
    """Give Whisper's words (good text) the timings of the CTC words (good timing)."""
    a = [norm(w) for w in text_words]
    b = [norm(w) for w, _ in timed]
    start = [None] * len(text_words)
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            start[blk.a + k] = timed[blk.b + k][1]
    # interpolate unmatched words between matched anchors
    anchors = [(-1, t0)] + [(i, s) for i, s in enumerate(start) if s is not None] + [(len(start), t1)]
    for (i0, s0), (i1, s1) in zip(anchors, anchors[1:]):
        for i in range(i0 + 1, i1):
            start[i] = s0 + (s1 - s0) * (i - i0) / (i1 - i0)
    out, last = [], t0
    for w, s in zip(text_words, start):
        s = max(s, last)
        out.append({"w": w, "s": round(s, 3), "e": round(s + 0.3, 3)})
        last = s
    return out


def fix_ends(words, dur):
    for i, w in enumerate(words):
        nxt = words[i + 1]["s"] if i + 1 < len(words) else dur
        w["e"] = round(min(max(nxt, w["s"] + 0.08), w["s"] + 0.6), 3)
    return words


# ----------------------------------------------------------------------- render
def hex_bgr(h):  # "#75C0FB" -> ASS "&HFBC075&"
    h = h.lstrip("#"); return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_time(t):
    cs = round(t * 100); return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def group_words(words, cap):
    groups, cur = [], []
    for w in words:
        cur.append(w)
        n = sum(len(x["w"]) + 1 for x in cur)
        if len(cur) >= cap["max_words"] or n >= cap["max_chars"] or re.search(r"[.?!]$", w["w"]) \
                or (w["e"] - w["s"] > 0 and re.search(r"[,;:]$", w["w"]) and len(cur) >= 3):
            groups.append(cur); cur = []
    if cur:
        groups.append(cur)
    return groups


def make_ass(words, brand, path, dur):
    W, H = brand["canvas"]["width"], brand["canvas"]["height"]
    cap = brand["captions"]
    base, hi = hex_bgr(cap["color"]), hex_bgr(cap["highlight"])
    y = brand["layout"]["captions_center_y"]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Cap,{cap['font_family']},{cap['size']},{base},{base},&H00000000&,&H80000000&,0,0,0,0,100,100,0,0,1,"
        f"{cap['outline']},{cap['shadow']},5,{cap['margin_x']},{cap['margin_x']},0,1", "",
        "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    groups = group_words(words, cap)
    for gi, g in enumerate(groups):
        gend = g[-1]["e"] + 0.2
        if gi + 1 < len(groups):
            gend = min(gend, groups[gi + 1][0]["s"])
        for i, w in enumerate(g):
            s = w["s"]
            e = g[i + 1]["s"] if i + 1 < len(g) else gend
            if e <= s:
                continue
            text = " ".join((f"{{\\c{hi}}}{x['w']}{{\\c{base}}}" if j == i else x["w"]) for j, x in enumerate(g))
            lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(min(e, dur))},Cap,,0,0,0,,{{\\an5\\pos({W // 2},{y})}}{text}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_background(brand, path):
    from PIL import Image
    c = brand["canvas"]; W, H = c["width"], c["height"]
    top = tuple(int(c["bg_top"][i:i + 2], 16) for i in (1, 3, 5))
    bot = tuple(int(c["bg_bottom"][i:i + 2], 16) for i in (1, 3, 5))
    col = Image.new("RGB", (1, H))
    col.putdata([tuple(round(top[k] + (bot[k] - top[k]) * y / (H - 1)) for k in range(3)) for y in range(H)])
    col.resize((W, H)).save(path)


def make_logo(brand, path):
    import cairosvg
    cairosvg.svg2png(url=str(ROOT / brand["logo"]["file"]), write_to=str(path),
                     output_width=brand["logo"]["width"])


def detect_crop(video):
    """Find the picture band inside a letterboxed frame. Only vertical bars are
    trimmed: dark studio backdrops at the sides would otherwise be cropped off."""
    wid = int(sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width",
                  "-of", "csv=p=0", str(video)]).stdout)
    r = sh(["ffmpeg", "-i", str(video), "-t", "10", "-vf", "cropdetect=limit=24:round=2:reset=0",
            "-f", "null", "-"])
    m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    if not m:
        sys.exit("could not detect picture area; set layout.crop in brand.json as 'w:h:x:y'")
    _, h, _, y = m[-1]
    return f"{wid}:{h}:0:{y}"


def render(video, words_json, out_mp4, brand, work):
    work.mkdir(parents=True, exist_ok=True)
    words = json.loads(Path(words_json).read_text())
    c, L = brand["canvas"], brand["layout"]
    W, H = c["width"], c["height"]
    dur = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)]).stdout)
    crop = L["crop"] if L["crop"] != "auto" else detect_crop(video)
    cw, ch, _, _ = map(int, crop.split(":"))
    vid_h = round(ch * W / cw / 2) * 2
    vid_y = (H - vid_h) // 2 + L["video_y_offset"]
    bg, logo, ass = work / "bg.png", work / "logo.png", work / "captions.ass"
    make_background(brand, bg); make_logo(brand, logo); make_ass(words, brand, ass, dur)
    from PIL import Image
    lh = Image.open(logo).height
    logo_y = L["logo_center_y"] - lh // 2
    fonts = (ROOT / "assets/fonts").as_posix()
    fc = (f"[0:v]crop={crop},scale={W}:{vid_h}[v];[1:v][v]overlay=0:{vid_y}[a];"
          f"[a][2:v]overlay=(W-w)/2:{logo_y}[b];[b]subtitles={ass.as_posix()}:fontsdir={fonts}[out]")
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-loop", "1", "-i", str(bg), "-loop", "1", "-i", str(logo),
        "-filter_complex", fc, "-map", "[out]", "-map", "0:a?", "-t", f"{dur}", "-c:v", "libx264", "-crf", "18",
        "-preset", "medium", "-pix_fmt", "yuv420p", "-r", "24000/1001", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out_mp4)])
    print(f"rendered -> {out_mp4}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["transcribe", "render", "all"])
    p.add_argument("video")
    p.add_argument("--brand", default=str(ROOT / "brand.json"))
    p.add_argument("--words", help="word-timing JSON (default: output/<name>.words.json)")
    p.add_argument("--out", help="output mp4 (default: output/<name>_branded.mp4)")
    a = p.parse_args()
    brand, video = load_brand(a.brand), Path(a.video)
    outdir = ROOT / "output"; outdir.mkdir(exist_ok=True)
    words = Path(a.words) if a.words else outdir / f"{video.stem}.words.json"
    if a.command in ("transcribe", "all"):
        transcribe(video, words, brand["models"])
    if a.command in ("render", "all"):
        render(video, words, Path(a.out) if a.out else outdir / f"{video.stem}_branded.mp4", brand, outdir / ".work")


if __name__ == "__main__":
    main()
