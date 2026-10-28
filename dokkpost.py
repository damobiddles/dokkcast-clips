#!/usr/bin/env python3
"""Dokkcast social posting: approve clips and copy, then schedule them through Postiz.

    python dokkpost.py plan Marcela                 # posts.json from the clips + copy.json, one clip per weekday
    python dokkpost.py plan Marcela --start 2026-11-02 --time 12:00 --days mon,wed,fri
    python dokkpost.py review Marcela               # writes output/Marcela/posts.html to tick clips and edit copy
    python dokkpost.py apply Marcela ~/Downloads/posts.json   # take the choices saved from posts.html
    python dokkpost.py kit Marcela                  # posting kit to upload by hand: output/Marcela/kit/
    python dokkpost.py channels                     # check the Postiz login and list connected accounts
    python dokkpost.py schedule Marcela             # dry run: what would go out, and each channel's rules
    python dokkpost.py schedule Marcela --go        # upload approved clips and schedule them in Postiz

The kit needs no Postiz: one folder per posting day with each platform's video and caption, and
kit.html with copy buttons and upload links, to post or schedule by hand in each app.

Nothing is sent to Postiz unless a post is approved in posts.json and schedule is run with --go.
Posts already sent are remembered in posts.json, so running schedule again only sends new approvals.

Copy lives in output/<name>/copy.json, written alongside the clips:

    {"1": {"tiktok": "...", "instagram": "...", "linkedin": "...", "youtube_title": "...", "youtube": "..."}}

Which clip shape each platform gets, the posting time zone and per-platform Postiz settings are in
brand.json under "social". Needs the Postiz CLI (npm install -g postiz) and `postiz auth:login`.
"""
import argparse, json, os, shutil, subprocess, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dokkclip import ROOT, load_brand

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
def review(name, brand):
    wd = work_dir(name)
    data = json.loads((wd / "posts.json").read_text())
    for p in data["posts"]:
        p["files"] = {pl: (wd / "clips" / f"{name}_clip{p['clip']}_{shape}.mp4").as_uri()
                      for pl, shape in brand["social"]["shapes"].items()}
    data["shapes"] = brand["social"]["shapes"]
    html = (ROOT / "assets" / "posts.html").read_text().replace("/*POSTS*/null", json.dumps(data))
    (wd / "posts.html").write_text(html)
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
            p["files"][pl] = dst.as_uri()
            cap = (f"Title: {p['copy'].get('youtube_title', '')}\n\n" if pl == "youtube" else "") + p["copy"][pl]
            (d / f"{pl}.txt").write_text(cap + "\n")
            text.append(f"== {pl} ==\n{cap}\n")
        (d / "captions.txt").write_text("\n".join(text))
    data["posts"], data["upload"], data["shapes"] = posts, UPLOAD, brand["social"]["shapes"]
    (out / "kit.html").write_text((ROOT / "assets" / "kit.html").read_text().replace("/*POSTS*/null", json.dumps(data)))
    print(f"{len(posts)} posts{'' if any(p['approved'] for p in posts) else ' (none approved yet, so all of them)'}"
          f" -> {out}\nopen {out / 'kit.html'} in a browser")


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
    p.add_argument("command", choices=["plan", "review", "apply", "kit", "channels", "schedule"])
    p.add_argument("target", nargs="?", help="the episode name (as in output/<name>)")
    p.add_argument("file", nargs="?", help="apply: posts.json saved from posts.html")
    p.add_argument("--start", help="plan: first posting day, YYYY-MM-DD (default tomorrow)")
    p.add_argument("--time", help="plan: posting time, HH:MM in the social.tz time zone")
    p.add_argument("--days", help="plan: posting days, e.g. mon,wed,fri")
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
        review(name, brand)
    elif a.command == "apply":
        apply(name, brand, a.file)
    elif a.command == "kit":
        kit(name, brand)
    elif a.command == "channels":
        channels(brand)
    else:
        schedule(name, brand, a.go)


if __name__ == "__main__":
    main()
