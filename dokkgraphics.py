#!/usr/bin/env python3
"""Social graphics for an episode's clips: one branded cover per clip in each shape that's posted
(brand.json social "shapes": portrait and square), using a photo of the guest and the clip titles from plan.json.

    python dokkgraphics.py Marcela --photo output/Marcela/photo.jpg --title "The truth about GLP-1 weight loss"

Writes output/<name>/graphics/<name>_clipN_<shape>.png (landscape too with --all-shapes), plus
<name>_episode_*.png when --title is given. --focus x,y (0-1) says where the face is in the photo.
"""
import argparse, json, tempfile
from pathlib import Path

from dokkclip import LOGO_SIZE, ROOT, load_brand, make_logo

SHAPES = {  # canvas, photo box, fade (side, px), text box, logo (x, y, width)
    "landscape": dict(size=(1920, 1080), photo=(900, 0, 1920, 1080), fade=("left", 420),
                      text=(110, 300, 1000, 1000), logo=(110, 60, 150)),
    "square": dict(size=(1080, 1080), photo=(0, 0, 1080, 640), fade=("bottom", 300),
                   text=(80, 560, 1000, 1010), logo=(60, 56, 190)),
    "portrait": dict(size=(1080, 1920), photo=(0, 0, 1080, 1200), fade=("bottom", 440),
                     text=(90, 1100, 990, 1820), logo=(70, 70, 200)),
}


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def font(weight, size):
    from PIL import ImageFont
    return ImageFont.truetype(str(ROOT / f"assets/fonts/Manrope-{weight}.ttf"), size)


def wrap(text, f, width):
    lines, cur = [], ""
    for word in text.split():
        nxt = f"{cur} {word}".strip()
        if cur and f.getlength(nxt) > width:
            lines.append(cur); cur = word
        else:
            cur = nxt
    return lines + [cur] if cur else lines


def cover(photo, box, focus):
    """Scale and crop the photo to fill box, keeping the focus point in view."""
    from PIL import Image
    bw, bh = box[2] - box[0], box[3] - box[1]
    k = max(bw / photo.width, bh / photo.height)
    im = photo.resize((round(photo.width * k), round(photo.height * k)), Image.LANCZOS)
    x = min(max(0, round(im.width * focus[0] - bw / 2)), im.width - bw)
    y = min(max(0, round(im.height * focus[1] - bh / 2)), im.height - bh)
    return im.crop((x, y, x + bw, y + bh))


def still(video, clip):
    """A frame from the middle of a clip, for its cover when there's no photo of the guest."""
    import subprocess
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "still.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(clip['s'] + clip['e']) / 2:.2f}", "-i", str(video),
                        "-frames:v", "1", "-q:v", "2", str(f)], capture_output=True)
        return Image.open(f).convert("RGB") if f.exists() else None


def graphic(brand, shape, photo, focus, logo, kicker, title, name, role, out):
    from PIL import Image, ImageDraw
    L = SHAPES[shape]; W, H = L["size"]
    top, bot, hi = rgb(brand["canvas"]["bg_top"]), rgb(brand["canvas"]["bg_bottom"]), rgb(brand["captions"]["highlight"])
    col = Image.new("RGB", (1, H))
    col.putdata([tuple(round(top[c] + (bot[c] - top[c]) * y / (H - 1)) for c in range(3)) for y in range(H)])
    img = col.resize((W, H))
    box = L["photo"]; bw, bh = box[2] - box[0], box[3] - box[1]
    pic = cover(photo, box, focus)
    side, n = L["fade"]
    mask = Image.new("L", (bw, bh), 255)
    md = ImageDraw.Draw(mask)
    for i in range(n):  # photo fades into the background on the text side
        a = round(255 * (i / n) ** 1.6)
        if side == "left":
            md.line([(i, 0), (i, bh)], fill=a)
        else:
            md.line([(0, bh - 1 - i), (bw, bh - 1 - i)], fill=a)
    img.paste(pic, box[:2], mask)
    if shape != "landscape":  # darken the top edge so the logo reads over the photo
        shade = Image.new("L", (bw, 220))
        ImageDraw.Draw(shade).rectangle([0, 0, bw, 220], fill=0)
        for y in range(220):
            ImageDraw.Draw(shade).line([(0, y), (bw, y)], fill=round(150 * (1 - y / 220)))
        img.paste(Image.new("RGB", (bw, 220), top), (0, 0), shade)

    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = L["text"]; tw = x1 - x0
    size = {"landscape": 120, "square": 104, "portrait": 130}[shape]
    while True:  # largest title that fits in four lines and the box
        tf, kf = font(800, size), font(800, max(30, round(size * 0.3)))
        lines = wrap(title, tf, tw)
        nf, rf = font(800, round(size * 0.42)), font(700, round(size * 0.34))
        while role and rf.size > 18 and rf.getlength(role) > tw:  # a long job title gets smaller, not cut off
            rf = font(700, rf.size - 2)
        th = len(lines) * size * 1.08 + size * 0.9 + nf.size * 1.3 + rf.size * 1.3 + kf.size * 1.8
        if (len(lines) <= 4 and th <= y1 - y0) or size <= 48:
            break
        size -= 4
    y = y0 + (y1 - y0 - th) / 2 if shape == "landscape" else y0
    d.text((x0, y), kicker.upper(), font=kf, fill=hi)
    y += kf.size * 1.8
    for line in lines:
        d.text((x0, y), line, font=tf, fill=(255, 255, 255))
        y += size * 1.08
    if name:  # the guest's name under an accent bar (a solo show has none)
        y += size * 0.35
        d.rounded_rectangle([x0, y, x0 + 120, y + 10], radius=5, fill=hi)
        y += size * 0.55
        d.text((x0, y), name, font=nf, fill=(255, 255, 255))
        y += nf.size * 1.3
    if role:
        d.text((x0, y), role, font=rf, fill=hi)
    if logo:
        lx, ly, lw = L["logo"]
        lw = round(lw * brand["logo"].get("scale", LOGO_SIZE / 100))  # the kit's logo size
        lg = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
        img.paste(lg, (lx, ly), lg)
    img.save(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", help="episode name, as in output/<name>/")
    p.add_argument("--photo", help="photo of the guest (default output/<name>/photo.jpg; none: a still from each clip)")
    p.add_argument("--focus", default="0.5,0.35", help="where the face is in the photo, as x,y from 0 to 1")
    p.add_argument("--guest", default="1", help="speaker number of the guest in plan.json")
    p.add_argument("--title", help="episode title: also makes an episode graphic")
    p.add_argument("--brand", default=str(ROOT / "brand.json"))
    p.add_argument("--all-shapes", action="store_true", help="landscape too, though nothing posts it")
    a = p.parse_args()
    from PIL import Image
    brand = load_brand(a.brand, a.name)
    wd = ROOT / "output" / a.name
    plan = json.loads((wd / "plan.json").read_text())
    who = plan.get("speakers", {}).get(a.guest, {})
    pf = Path(a.photo) if a.photo else wd / "photo.jpg"
    photo = Image.open(pf).convert("RGB") if pf.exists() else None  # none: each cover uses a still from its clip
    focus = tuple(float(v) for v in a.focus.split(","))
    with tempfile.TemporaryDirectory() as tmp:
        lb = dict(brand, logo=dict(brand["logo"], width=900, scale=1))
        logo = Image.open(Path(tmp) / "logo.png").convert("RGBA") if make_logo(lb, Path(tmp) / "logo.png") else None
    out = wd / "graphics"; out.mkdir(exist_ok=True)
    clips = [c for c in plan.get("clips", []) if not c.get("skip")]
    jobs = [(f"{a.name}_episode", "New episode", a.title, clips[0] if clips else None)] if a.title else []
    jobs += [(f"{a.name}_clip{c['id']}", brand["show"]["kicker"], c["title"], c) for c in clips]
    shapes = list(SHAPES) if a.all_shapes else [s for s in SHAPES if s in brand["social"]["shapes"].values()]
    for stem, kicker, title, clip in jobs:
        pic = photo or (still(plan["video"], clip) if clip else None)
        if pic is None:
            print(f"{stem}: no photo and no still from the video, so no cover"); continue
        for shape in shapes:
            graphic(brand, shape, pic, focus, logo, kicker, title, who.get("name", ""), who.get("role", ""),
                    out / f"{stem}_{shape}.png")
        print(f"{stem}: {title}")
    print(f"{len(jobs) * len(shapes)} graphics -> {out}")


if __name__ == "__main__":
    main()
