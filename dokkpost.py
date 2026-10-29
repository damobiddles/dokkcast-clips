#!/usr/bin/env python3
"""Dokkcast social posting: approve clips and copy, then schedule them through Postiz.

    python dokkpost.py plan Marcela                 # posts.json from the clips + copy.json, one clip per weekday
    python dokkpost.py plan Marcela --start 2026-11-02 --time 12:00 --days mon,wed,fri
    python dokkpost.py review Marcela               # writes output/Marcela/posts.html to tick clips and edit copy
    python dokkpost.py review Marcela --music ~/Movies/Dokkcast/track.wav   # same, with a music slider to set the mix
    python dokkpost.py apply Marcela ~/Downloads/posts.json   # take the choices saved from posts.html
    python dokkpost.py kit Marcela                  # posting kit to upload by hand: output/Marcela/kit/
    python dokkpost.py zip Marcela                  # the kit as ~/Movies/Dokkcast/Dokkcast posts - <guest>.zip
    python dokkpost.py finish Marcela --music ~/Movies/Dokkcast/track.wav   # render approved clips, graphics, kit, zip
    python dokkpost.py channels                     # check the Postiz login and list connected accounts
    python dokkpost.py schedule Marcela             # dry run: what would go out, and each channel's rules
    python dokkpost.py schedule Marcela --go        # upload approved clips and schedule them in Postiz

The kit needs no Postiz: one folder per posting day with each platform's video and caption, and
kit.html with copy buttons and upload links, to post or schedule by hand in each app.

Nothing is sent to Postiz unless a post is approved in posts.json and schedule is run with --go.
Posts already sent are remembered in posts.json, so running schedule again only sends new approvals.

The review page works before the clips are rendered too: until a clip file exists it plays that
stretch of the episode instead. Clips left unapproved are marked skip in plan.json by apply, so
finish only renders the approved ones.

With a music track (review --music, or one already given to render/finish) the review page gets a
music slider: it plays the episode with the track underneath at that level, and the level saved
from the page is what the clips are rendered with (plan.json "music_db").

Copy lives in output/<name>/copy.json, written alongside the clips:

    {"1": {"tiktok": "...", "instagram": "...", "linkedin": "...", "youtube_title": "...", "youtube": "..."}}

Which clip shape each platform gets, the posting time zone and per-platform Postiz settings are in
brand.json under "social". Needs the Postiz CLI (npm install -g postiz) and `postiz auth:login`.
"""
import argparse, base64, json, os, re, shutil, subprocess, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dokkclip import ROOT, load_brand
from dokkepisode import load_plan

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Postiz integration identifiers for each platform we post to
IDENTIFIERS = {"tiktok": ["tiktok"], "instagram": ["instagram", "instagram-standalone"],
               "youtube": ["youtube"], "linkedin": ["linkedin-page", "linkedin"]}


def work_dir(name):
    return ROOT / "output" / name


def postiz(*args):
    """Run the Postiz CLI and return the JSON it prints after its one-line header."""
    if not shutil.which("postiz"):
        sys.exit("postiz not found: run `npm install -g postiz`, then `postiz auth:login`")
    r = subprocess.run(["postiz", *map(str, args)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"postiz {args[0]} failed:\n{(r.stderr or r.stdout)[-2000:]}")
    out = r.stdout
    i = min((k for k in (out.find("["), out.find("{")) if k >= 0), default=-1)
    return json.loads(out[i:]) if i >= 0 else out


# ------------------------------------------------------------------------ plan
def plan(name, brand, start=None, at=None, days=None):
    wd = work_dir(name)
    social = brand["social"]
    clips = [c for c in json.loads((wd / "plan.json").read_text()).get("clips", []) if not c.get("skip")]
    copy = json.loads((wd / "copy.json").read_text()) if (wd / "copy.json").exists() else {}
    days = [DAYS.index(d.strip()[:3].lower()) for d in (days or social["days"]).split(",")]
    at = at or social["time"]
    d = date.fromisoformat(start) if start else date.today() + timedelta(days=1)
    old = {p["clip"]: p for p in json.loads((wd / "posts.json").read_text())["posts"]} if (wd / "posts.json").exists() else {}
    posts = []
    for c in clips:
        while d.weekday() not in days:
            d += timedelta(days=1)
        cp = copy.get(str(c["id"]), {})
        text = {p: cp.get(p) or c.get("title", "") for p in social["platforms"]}
        if "youtube" in text:
            text["youtube_title"] = cp.get("youtube_title") or c.get("title", "")
        # keep what was already approved or sent if plan is run again
        posts.append(old.get(c["id"]) or {"clip": c["id"], "title": c.get("title", ""), "when": f"{d} {at}",
                                          "platforms": {p: True for p in social["platforms"]}, "copy": text,
                                          "approved": False, "sent": {}})
        d += timedelta(days=1)
    (wd / "posts.json").write_text(json.dumps({"episode": name, "tz": social["tz"], "posts": posts}, indent=1))
    missing = [str(c["id"]) for c in clips if str(c["id"]) not in copy]
    print(f"{len(posts)} posts -> {wd / 'posts.json'}" + (f" (no copy yet for clips {', '.join(missing)})" if missing else ""))
    review(name, brand)


# ---------------------------------------------------------------------- review
def studio_page(html, brand):
    """Give a page the studio's look and name (brand.json "studio"), with its logo inside the page so a
    zipped kit still shows it. The clips and covers keep the show's own branding."""
    st = dict(brand.get("studio", {}))
    logo = ROOT / st.get("logo", "")
    if st.get("logo") and logo.is_file():
        kind = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg"}.get(logo.suffix.lower())
        if kind:
            st["logo_data"] = f"data:{kind};base64," + base64.b64encode(logo.read_bytes()).decode()
    theme = (ROOT / "assets" / "studio-theme.html").read_text().replace("/*STUDIO*/null", json.dumps(st) if st else "null")
    return html.replace("<!--STUDIO-->", theme)


def loudness(path):
    """Integrated loudness (LUFS) of a file's audio."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else -16.0


def music_preview(wd, ep):
    """The track at the episode's own loudness, so the page's slider sets music against voice in dB
    the same way the render does (both are normalised to the same level there)."""
    out = wd / "music-preview.m4a"
    key = f"{ep['music']}|{ep.get('voice_lufs')}"
    if not out.exists() or ep.get("music_preview") != key:
        if ep.get("voice_lufs") is None:
            print("measuring the episode's loudness for the music preview ...")
            ep["voice_lufs"] = loudness(ep["video"])
            key = f"{ep['music']}|{ep['voice_lufs']}"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", ep["music"], "-vn", "-ac", "1", "-af",
                        f"loudnorm=I={ep['voice_lufs']:.1f}:TP=-2", "-c:a", "aac", "-b:a", "96k", str(out)], check=True)
        ep["music_preview"] = key
        (wd / "plan.json").write_text(json.dumps(ep, indent=1))
    return out.name


def review(name, brand, music=None):
    wd = work_dir(name)
    data = json.loads((wd / "posts.json").read_text())
    ep = load_plan(wd)
    if music:
        ep["music"] = str(Path(music).expanduser().resolve())
        (wd / "plan.json").write_text(json.dumps(ep, indent=1))
    clips = ep.get("clips", [])
    spans = {c["id"]: (c["s"], c["e"]) for c in clips}
    mcfg = dict(brand["episode"]["music"], **brand["clips"].get("music", {}))
    for p in data["posts"]:
        p["files"] = {}
        if p["clip"] in spans:  # that stretch of the episode, for the music preview and unrendered clips
            s, e = spans[p["clip"]]
            p["src"] = f"{Path(ep['video']).as_uri()}#t={s:.1f},{e:.1f}"
            # where in the track this clip's music starts, as render_clips does it
            p["music_at"] = [c["id"] for c in clips].index(p["clip"]) * mcfg.get("offset_step", 30)
        for pl, shape in brand["social"]["shapes"].items():
            f = wd / "clips" / f"{name}_clip{p['clip']}_{shape}.mp4"
            if f.exists():
                p["files"][pl] = f.as_uri()
            elif "src" in p:  # not rendered yet: play that stretch of the episode
                p["files"][pl] = p["src"]
    data["shapes"] = brand["social"]["shapes"]
    if ep.get("music") and Path(ep["music"]).exists() and mcfg.get("on", True):
        data["music"] = {"src": music_preview(wd, ep), "name": Path(ep["music"]).stem,
                         "db": ep["music_db"] if ep.get("music_db") is not None else mcfg["level_db"]}
    html = (ROOT / "assets" / "posts.html").read_text().replace("/*POSTS*/null", json.dumps(data))
    (wd / "posts.html").write_text(studio_page(html, brand))
    print(f"open {wd / 'posts.html'} in a browser")


def apply(name, brand, path):
    """Take the approvals and copy edits saved from posts.html. Posts already sent are left alone."""
    wd = work_dir(name)
    cur = json.loads((wd / "posts.json").read_text())
    new = {p["clip"]: p for p in json.loads(Path(path).expanduser().read_text())["posts"]}
    for p in cur["posts"]:
        n = new.get(p["clip"])
        if n:
            p.update({k: n[k] for k in ("when", "platforms", "copy", "approved") if k in n})
    (wd / "posts.json").write_text(json.dumps(cur, indent=1))
    ok = [p for p in cur["posts"] if p["approved"]]
    pj = wd / "plan.json"
    ep = json.loads(pj.read_text())
    if ok:  # only the approved clips get rendered
        keep = {p["clip"] for p in ok}
        for c in ep.get("clips", []):
            c["skip"] = c["id"] not in keep
    saved = json.loads(Path(path).expanduser().read_text()).get("music_db")
    if saved is not None:
        ep["music_db"] = saved
        print(f"music level {saved} dB")
    pj.write_text(json.dumps(ep, indent=1))
    print(f"{len(ok)} of {len(cur['posts'])} posts approved: clips {', '.join(str(p['clip']) for p in ok) or 'none'}")
    review(name, brand)


# ------------------------------------------------------------------------- kit
UPLOAD = {"tiktok": "https://www.tiktok.com/tiktokstudio/upload",
          "instagram": "https://business.facebook.com/latest/composer",
          "youtube": "https://studio.youtube.com",
          "linkedin": "https://www.linkedin.com/feed/?shareActive=true"}


def kit(name, brand):
    """Folder per post with each platform's video and caption, plus kit.html, for posting by hand."""
    wd = work_dir(name)
    data = json.loads((wd / "posts.json").read_text())
    posts = [p for p in data["posts"] if p["approved"]] or data["posts"]
    out = wd / "kit"
    shutil.rmtree(out, ignore_errors=True)
    for p in posts:
        day = datetime.fromisoformat(p["when"])
        p["dir"] = f"{day:%Y-%m-%d %a} clip{p['clip']}"
        d = out / p["dir"]
        d.mkdir(parents=True)
        p["files"] = {}
        text = []
        for pl, on in p["platforms"].items():
            if not on:
                continue
            src = wd / "clips" / f"{name}_clip{p['clip']}_{brand['social']['shapes'][pl]}.mp4"
            dst = d / f"{pl}.mp4"
            try:
                os.link(src, dst)  # no extra disk space
            except OSError:
                shutil.copy2(src, dst)
            p["files"][pl] = f"{p['dir']}/{pl}.mp4"  # relative, so the kit still works when the folder is moved or synced
            cap = (f"Title: {p['copy'].get('youtube_title', '')}\n\n" if pl == "youtube" else "") + p["copy"][pl]
            (d / f"{pl}.txt").write_text(cap + "\n")
            text.append(f"== {pl} ==\n{cap}\n")
        (d / "captions.txt").write_text("\n".join(text))
        for shape in sorted(set(brand["social"]["shapes"].values())):  # cover graphics, if dokkgraphics made them
            g = wd / "graphics" / f"{name}_clip{p['clip']}_{shape}.png"
            if g.exists():
                shutil.copy2(g, d / f"cover {shape}.png")
    data["posts"], data["upload"], data["shapes"] = posts, UPLOAD, brand["social"]["shapes"]
    (out / "kit.html").write_text(studio_page((ROOT / "assets" / "kit.html").read_text().replace("/*POSTS*/null", json.dumps(data)), brand))
    print(f"{len(posts)} posts{'' if any(p['approved'] for p in posts) else ' (none approved yet, so all of them)'}"
          f" -> {out}\nopen {out / 'kit.html'} in a browser")


def make_zip(name, title=None):
    """The kit as one zip for Slack, with real files (not links) inside."""
    wd = work_dir(name)
    if not (wd / "kit" / "kit.html").exists():
        sys.exit(f"no kit yet: run `dokkpost.py kit {name}` first")
    if not title:
        speakers = load_plan(wd).get("speakers", {}).values()
        guest = next((w["name"] for w in speakers if w.get("name") and "host" not in w.get("role", "").lower()), name)
        title = re.sub(r"^(Dr|Prof|Professor|Mr|Mrs|Ms|Miss)\.?\s+", "", guest)
    out = Path("~/Movies/Dokkcast").expanduser()  # where the episode videos live; next to the kit otherwise
    out = out if out.is_dir() else wd
    z = out / f"Dokkcast posts - {title}.zip"
    z.unlink(missing_ok=True)
    if shutil.which("ditto"):
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(wd / "kit"), str(z)], check=True)
    else:
        shutil.make_archive(str(z.with_suffix("")), "zip", wd, "kit")
    print(f"{z} ({z.stat().st_size / 1e6:.0f} MB)")
    return z


def finish(name, brand, music=None, title=None):
    """After approval: render the approved clips (with music), make graphics, then the kit and the zip."""
    wd = work_dir(name)
    cmd = [sys.executable, str(ROOT / "dokkepisode.py"), "render", name, "--only", "clips"]
    subprocess.run(cmd + (["--music", music] if music else []), check=True)
    g = ROOT / "dokkgraphics.py"
    if g.exists():
        # the covers name the guest: whichever speaker isn't the host (the speaker numbers vary by episode)
        host = brand.get("pick", {}).get("host", "Dr. Nara Daubeney")
        speakers = load_plan(wd).get("speakers", {})
        guest = next((k for k, v in speakers.items() if v.get("name") != host), None)
        if subprocess.run([sys.executable, str(g), name] + (["--guest", guest] if guest else [])).returncode:
            print("graphics failed; carrying on without covers")
    kit(name, brand)
    make_zip(name, title)


# -------------------------------------------------------------------- schedule
def channels(brand, quiet=False):
    """Map each platform to a connected Postiz account: brand.json social.channels wins, else the only match."""
    found = postiz("integrations:list")
    if not quiet:
        for i in found:
            print(f"{i['identifier']:22} {i['id']}  {i.get('name', '')}{'  (disabled)' if i.get('disabled') else ''}")
    pick = {}
    for pl in brand["social"]["platforms"]:
        want = brand["social"].get("channels", {}).get(pl)
        hits = [i for i in found if (i["id"] == want if want else i["identifier"] in IDENTIFIERS[pl]) and not i.get("disabled")]
        if len(hits) == 1:
            pick[pl] = hits[0]
        elif not quiet:
            print(f"{pl}: {'no connected account' if not hits else 'more than one account, set social.channels.' + pl + ' in brand.json'}")
    return pick


def settings_for(pl, post, brand):
    s = json.loads(json.dumps(brand["social"]["settings"].get(pl, {})))
    if pl == "youtube":
        s["title"] = post["copy"].get("youtube_title") or post["title"]
    return s


def schedule(name, brand, go=False):
    wd = work_dir(name)
    data = json.loads((wd / "posts.json").read_text())
    tz = ZoneInfo(data.get("tz", "Europe/London"))
    todo = [(p, pl) for p in data["posts"] if p["approved"] for pl, on in p["platforms"].items()
            if on and pl not in p.get("sent", {})]
    if not todo:
        sys.exit("nothing to send: no approved posts that haven't been scheduled already")
    pick = channels(brand, quiet=True)
    # each channel's own rules say which settings apply; a setting that doesn't is dropped silently by Postiz
    rules = {pl: postiz("integrations:settings", ch["id"]) for pl, ch in pick.items()}
    (wd / "postiz-settings.json").write_text(json.dumps(rules, indent=1))
    now = datetime.now(tz)
    for p, pl in todo:
        when = datetime.fromisoformat(p["when"]).replace(tzinfo=tz)
        f = wd / "clips" / f"{name}_clip{p['clip']}_{brand['social']['shapes'][pl]}.mp4"
        problem = ("no connected account" if pl not in pick else "clip file missing" if not f.exists()
                   else "date is in the past" if when < now else "")
        print(f"{'SKIP ' if problem else ''}clip {p['clip']} -> {pl:9} {when:%a %d %b %H:%M} {f.name}"
              + (f"  ({problem})" if problem else ""))
        if problem or not go:
            continue
        media = postiz("upload", f)["path"]
        res = postiz("posts:create", "-c", p["copy"][pl], "-m", media, "-s", when.astimezone(ZoneInfo("UTC")).isoformat(),
                     "-i", pick[pl]["id"], "--settings", json.dumps(settings_for(pl, p, brand)))
        p.setdefault("sent", {})[pl] = {"at": datetime.now(tz).isoformat(timespec="seconds"), "media": media, "result": res}
        (wd / "posts.json").write_text(json.dumps(data, indent=1))  # save after each so a failure doesn't resend
    if not go:
        print(f"\ndry run: nothing sent. Channel rules saved to {wd / 'postiz-settings.json'}. Add --go to schedule.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["plan", "review", "apply", "kit", "zip", "finish", "channels", "schedule"])
    p.add_argument("target", nargs="?", help="the episode name (as in output/<name>)")
    p.add_argument("file", nargs="?", help="apply: posts.json saved from posts.html")
    p.add_argument("--start", help="plan: first posting day, YYYY-MM-DD (default tomorrow)")
    p.add_argument("--time", help="plan: posting time, HH:MM in the social.tz time zone")
    p.add_argument("--days", help="plan: posting days, e.g. mon,wed,fri")
    p.add_argument("--music", help="review, finish: music file to put under the clips")
    p.add_argument("--title", help="zip, finish: name in the zip file, e.g. \"Marcela Fiuza (GLP-1)\"")
    p.add_argument("--go", action="store_true", help="schedule: really upload and schedule")
    p.add_argument("--brand", default=str(ROOT / "brand.json"))
    a = p.parse_args()
    brand = load_brand(a.brand)
    if a.command != "channels" and not a.target:
        p.error("give the episode name")
    name = a.target and Path(a.target).stem
    if a.command == "plan":
        plan(name, brand, a.start, a.time, a.days)
    elif a.command == "review":
        review(name, brand, a.music)
    elif a.command == "apply":
        apply(name, brand, a.file)
    elif a.command == "kit":
        kit(name, brand)
    elif a.command == "zip":
        make_zip(name, a.title)
    elif a.command == "finish":
        finish(name, brand, a.music, a.title)
    elif a.command == "channels":
        channels(brand)
    else:
        schedule(name, brand, a.go)


if __name__ == "__main__":
    main()
