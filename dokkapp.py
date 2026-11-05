#!/usr/bin/env python3
"""Offcuts, the studio app: the whole episode-to-posts routine as a small app on this Mac.

    python dokkapp.py            # opens http://127.0.0.1:8765 in the browser

Pick the episode video and a music track, and it runs each step in order:

    1. prep      transcribe and find the speakers            dokkepisode.py prep
    2. pick      choose the clips and write the copy          dokkpick.py: Claude on this Mac (Claude Code,
                                                              your Claude login), the Claude API, or Claude in chat
    3. review    plan the posting dates and open the review   dokkpost.py plan + review --music
                 page (served by the app): watch, set the music,
                 edit, approve, then Save and make the clips
    4. finish    render the approved clips with the music,    dokkpost.py apply + finish
                 covers, posting kit and zip

Each episode's progress is kept in output/<name>/app.json, so the app can be closed and opened
again at any point. Nothing leaves the Mac except the transcript, which goes to Claude to pick the clips.
"""
import json, os, re, shutil, subprocess, sys, threading, webbrowser
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from dokkclip import DEFAULT_KIT, KITS, READY, ROOT, STUDIO, VIDEOS, day_off, read_kit

OUT = ROOT / "output"  # in the app, a link to <Offcuts>/Working files
PY = sys.executable
PORT = int(os.environ.get("DOKK_PORT", 8765))
STEPS = ["prep", "pick", "review", "finish"]
jobs = {}  # name -> running thread
chosen = set()  # logos chosen in this run of the app, which the brand kit form may show before saving


def studio():
    """The app's own name, logo and colours, from brand.json "studio"."""
    b = json.loads((ROOT / "brand.json").read_text())
    return dict({"name": "Offcuts"}, **b.get("studio", {}))


def state_path(name):
    return OUT / name / "app.json"


def load_state(name):
    p = state_path(name)
    return json.loads(p.read_text()) if p.exists() else None


def save_state(name, st):
    state_path(name).parent.mkdir(parents=True, exist_ok=True)
    state_path(name).write_text(json.dumps(st, indent=1))


def log(name, text):
    with open(OUT / name / "app.log", "a") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def run(name, cmd):
    """Run one tool command, streaming its output into the episode's log."""
    log(name, f"\n$ {' '.join(map(str, cmd[1:]))}")
    with open(OUT / name / "app.log", "a") as f:
        env = dict(os.environ, OFFCUTS_KIT=(load_state(name) or {}).get("kit") or DEFAULT_KIT)  # the client's branding
        r = subprocess.Popen(list(map(str, cmd)), cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
        st = load_state(name); st["pid"] = r.pid; save_state(name, st)  # so a restarted app waits for it
        r.wait()
    st = load_state(name); st.pop("pid", None); save_state(name, st)
    if r.returncode:  # the tool's last line usually says what went wrong
        last = [l for l in (OUT / name / "app.log").read_text().splitlines() if l.strip()][-1:]
        raise RuntimeError(f"{Path(cmd[1]).name} {cmd[2]} failed: {last[0].strip()[:200] if last else 'see Details'}")


def mac(script):
    """Run AppleScript and return what it prints, or None if the user cancelled."""
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def notify(text):
    mac(f'display notification "{text.replace(chr(34), "")}" with title "{studio()["name"]}"')


def choose(kind):
    types = {"video": '{"public.movie"}', "music": '{"public.audio"}', "photo": '{"public.image"}',
             "logo": '{"public.image", "public.svg-image"}'}
    prompts = {"video": "Choose the episode video", "music": "Choose the music track",
               "photo": "Choose a photo of the guest (for the covers)",
               "logo": "Choose the logo (SVG or PNG, ideally one made for dark backgrounds)"}
    where = f' default location (POSIX file "{VIDEOS}")' if kind in ("video", "music") and VIDEOS.is_dir() else ""
    return mac(f'POSIX path of (choose file with prompt "{prompts[kind]}" of type {types[kind]}{where})')


def next_start():
    """The first weekday after the last post already planned for any episode."""
    last = date.today()
    for pj in OUT.glob("*/posts.json"):
        for p in json.loads(pj.read_text()).get("posts", []):
            try:
                last = max(last, datetime.strptime(p["when"][:10], "%Y-%m-%d").date())
            except (KeyError, ValueError):
                pass
    d = last + timedelta(days=1)
    social = json.loads((ROOT / "brand.json").read_text())["social"]
    while d.weekday() > 4 or day_off(d, social):
        d += timedelta(days=1)
    return d.isoformat()


def has_picks(name):
    wd = OUT / name
    plan = json.loads((wd / "plan.json").read_text()) if (wd / "plan.json").exists() else {}
    return bool(plan.get("clips")) and (wd / "copy.json").exists()


def settings():
    p = Path.home() / ".dokkcast" / "settings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def claude_cli():
    from dokkpick import claude_cli as find
    return find()


login = {"ok": None, "at": 0}  # is Claude Code signed in? checked in the background, at most once a minute


def claude_ready():
    """True when Claude Code answers, False when it's missing or not signed in, None while checking."""
    if login["ok"] or not claude_cli():
        return bool(login["ok"]) if claude_cli() else False
    if datetime.now().timestamp() - login["at"] > 60:
        login["at"] = datetime.now().timestamp()

        def check():
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            try:
                r = subprocess.run([claude_cli(), "-p", "--output-format", "json", "--no-session-persistence"],
                                   input="Reply with the word ok.", capture_output=True, text=True, timeout=120,
                                   cwd=Path.home(), env=env)
                login["ok"] = r.returncode == 0 and not json.loads(r.stdout or "{}").get("is_error")
            except (OSError, ValueError, subprocess.TimeoutExpired):
                login["ok"] = False
        threading.Thread(target=check, daemon=True).start()
    return login["ok"]


def readable(path, what):
    """macOS keeps apps out of Desktop, Documents and Downloads until allowed: say so plainly."""
    try:
        with open(path, "rb") as f:
            f.read(1)
    except PermissionError:
        raise ValueError(f"macOS isn't letting the app read the {what} there. Put it in Offcuts' Source files folder "
                         "(or allow the app in System Settings, Privacy & Security, Files and Folders)")


def pick_mode():
    """"mac": Claude Code on this Mac picks, on the user's Claude login (no API key); "api": the app asks
    the Claude API (needs a key); "chat": it pauses for Claude in chat to pick."""
    return settings().get("pick") or ("mac" if claude_cli() else "chat")


def can_pick():
    """How the app picks the clips by itself: "mac", "api", or None (wait for Claude in chat)."""
    mode = pick_mode()
    if mode == "mac":
        return "mac" if claude_cli() else None
    if mode != "api":
        return None
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None
    return "api" if os.environ.get("ANTHROPIC_API_KEY") or api_key() else None


def api_key():
    p = Path.home() / ".dokkcast" / "anthropic_key"
    return p.read_text().strip() if p.exists() else ""


def newest_saved(name):
    """The choices saved from this episode's review page: by the app into the episode folder, or
    (from a review page opened as a file) posts.json in Downloads. The newest wins."""
    files = [OUT / name / "posts-saved.json"]
    try:
        files += (Path.home() / "Downloads").glob("posts*.json")
    except OSError:  # macOS may keep the app out of Downloads
        pass
    files = sorted((f for f in files if f.exists()), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            if json.loads(f.read_text()).get("episode") == name:
                return f
        except (ValueError, OSError):
            continue
    return None


def keep_photo(name, path):
    """Copy the guest photo into the episode folder straight away: one dragged from Photos is a
    temporary file that macOS soon deletes."""
    if not path:
        return
    out = OUT / name / "photo.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:  # turned the right way up: phone photos are stored sideways with a note saying which way to turn them
        from PIL import Image, ImageOps
        ImageOps.exif_transpose(Image.open(path)).convert("RGB").save(out, quality=92)
        return
    except Exception:  # e.g. HEIC, which Pillow can't open: let macOS convert it (sips keeps the orientation note)
        pass
    r = subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", str(out)], capture_output=True, text=True)
    if r.returncode or not out.exists():
        out.unlink(missing_ok=True)
        raise ValueError("couldn't read that photo: save it to a folder first (from Photos: File, Export), then choose it")


# ------------------------------------------------------------------ the routine
def advance(name):
    """Run steps until one needs a person (picks, review) or everything is done."""
    st = load_state(name)
    try:
        if st.get("music") and not Path(st["music"]).exists() and (VIDEOS / Path(st["music"]).name).exists():
            st["music"] = str(VIDEOS / Path(st["music"]).name)  # moved into Source files since
            save_state(name, st)
        if st.get("photo") and not (OUT / name / "photo.jpg").exists():
            try:
                keep_photo(name, st["photo"])
            except ValueError as e:
                log(name, f"photo: {e}")
        while True:
            step = st["step"]
            st.update(status="running", error=None); save_state(name, st)
            if step == "prep":
                if not (OUT / name / "words.json").exists():
                    run(name, [PY, "dokkepisode.py", "prep", st["video"], "--speakers", st.get("speakers", 2)])
                st["step"] = "pick"
            elif step == "pick":
                if not has_picks(name):
                    who = ["--guest", st.get("guest", ""), "--role", st.get("role", "")]
                    how = can_pick()
                    if not how:  # leave the request ready for Claude in chat, then wait
                        run(name, [PY, "dokkpick.py", name, "--prompt"] + who)
                        st["status"] = "waiting"; st["waiting"] = "picks"; save_state(name, st); return
                    run(name, [PY, "dokkpick.py", name] + (["--claude"] if how == "mac" else []) + who)
                st["step"] = "review"
            elif step == "review":
                if not (OUT / name / "posts.json").exists():
                    run(name, [PY, "dokkpost.py", "plan", name, "--start", st.get("start") or next_start()])
                run(name, [PY, "dokkpost.py", "review", name] + (["--music", st["music"]] if st.get("music") else []))
                notify(f"{st.get('guest') or name}: the clips are ready to review")  # the app page opens the review
                st["status"] = "waiting"; st["waiting"] = "review"; save_state(name, st); return
            elif step == "finish":
                saved = newest_saved(name)
                if saved:
                    run(name, [PY, "dokkpost.py", "apply", name, saved])
                run(name, [PY, "dokkpost.py", "finish", name] + (["--title", st["guest"]] if st.get("guest") else [])
                    + (["--music", st["music"]] if st.get("music") else []))
                st["step"] = "done"; st["status"] = "done"; st["zip"] = find_zip(name, st.get("guest"))
                save_state(name, st)
                notify(f"{st.get('guest') or name}: the posting kit and zip are ready")
                return
            else:
                return
            st["waiting"] = None; save_state(name, st)
    except Exception as e:  # shown on the page with the log
        st.update(status="error", error=str(e)); save_state(name, st)
    finally:
        jobs.pop(name, None)


def page(name, path):
    """A page of an episode's folder, as the app serves it."""
    return f"http://127.0.0.1:{PORT}/ep/{quote(name)}/{quote(path)}"


UPLOAD = {"tiktok": "https://www.tiktok.com/tiktokstudio/upload",
          "instagram": "https://business.facebook.com/latest/composer",
          "youtube": "https://studio.youtube.com",
          "linkedin": "https://www.linkedin.com/feed/?shareActive=true"}


def calendar():
    """Every post from every finished episode, in date order: the videos, covers and copy from its
    kit, and which platforms have been posted (ticked in the app, kept in output/<name>/posted.json)."""
    out = []
    shapes = json.loads((ROOT / "brand.json").read_text())["social"]["shapes"]
    for st in episodes(logs=False):
        name, wd = st["name"], OUT / st["name"]
        if not (wd / "posts.json").exists() or not (wd / "kit").is_dir():
            continue
        posts = json.loads((wd / "posts.json").read_text())["posts"]
        posted = json.loads((wd / "posted.json").read_text()) if (wd / "posted.json").exists() else {}
        for p in [p for p in posts if p.get("approved")] or posts:  # as the kit has them
            d = f"{datetime.fromisoformat(p['when']):%Y-%m-%d %a} clip{p['clip']}"
            kit = wd / "kit" / d
            vids = {}  # a video per platform (older kits) or one per shape
            for pl, on in p.get("platforms", {}).items():
                f = next((f for f in (kit / f"{pl}.mp4", kit / f"{shapes.get(pl, 'portrait')}.mp4") if f.exists()), None)
                if on and f:
                    vids[pl] = f.name
            pls = list(vids) if not st.get("tidied") else [pl for pl, on in p.get("platforms", {}).items() if on]
            if not pls:
                continue
            out.append({"episode": name, "guest": st.get("guest") or name, "role": st.get("role", ""),
                        "when": p["when"].replace(" ", "T"), "clip": p["clip"], "title": p.get("title", ""),
                        "copy": dict(p.get("copy", {}), youtube_title=re.sub(r"\s*#\w+", "", p.get("copy", {}).get("youtube_title", "")).strip()),
                        "platforms": pls, "dir": f"kit/{d}",
                        "files": {pl: page(name, f"kit/{d}/{v}") for pl, v in vids.items()},
                        "paths": {pl: f"kit/{d}/{v}" for pl, v in vids.items()}, "tidied": bool(st.get("tidied")),
                        "covers": [page(name, f"kit/{d}/{c.name}") for c in sorted(kit.glob("cover *.png"))],
                        "posted": posted.get(str(p["clip"]), {})})
    return sorted(out, key=lambda p: p["when"])


# ------------------------------------------------------------------ brand kits
KIT_FIELDS = ("show", "host", "host_role", "about", "notes")
SEED = {  # made once, on first start; after that they're the person's to change
    "The Dokkcast": {"show": "The Dokkcast", "host": "Dr. Nara Daubeney", "host_role": "Consultant ENT surgeon and entrepreneur",
                     "about": "a health podcast", "notes": "No medical claims beyond what is said in the clip.",
                     "zip": "Dokkcast posts", "colors": {"top": "#0A0F2C", "bottom": "#251A63", "highlight": "#75C0FB"},
                     "logo_from": "assets/logo/udokk_logo_gradient.svg"},
    "MBMS Podcast Studio": {"show": "MBMS Podcast Studio", "host": "", "host_role": "", "about": "a podcast", "notes": "",
                            "colors": {"top": "#17161C", "bottom": "#2A2830", "highlight": "#FF5A36"},
                            "logo_from": "assets/studio/mbms-logo-for-dark-backgrounds.svg"},
}


def seed_kits():
    if KITS.exists():
        return
    for name, k in SEED.items():
        d = KITS / name
        d.mkdir(parents=True, exist_ok=True)
        k = dict(k)
        src = ROOT / k.pop("logo_from", "")
        if src.is_file():
            shutil.copy(src, d / f"logo{src.suffix}")
            k["logo"] = f"logo{src.suffix}"
        (d / "kit.json").write_text(json.dumps(k, indent=1, ensure_ascii=False))


def kits():
    out = []
    for d in sorted(KITS.glob("*/kit.json")) if KITS.is_dir() else []:
        try:
            k = read_kit(d.parent)
        except ValueError:
            continue
        k["logo_url"] = f"/kit-logo/{quote(k['name'])}" if k.get("logo") and (d.parent / k["logo"]).is_file() else None
        out.append(k)
    return out


def save_kit(b):
    """A brand kit from the form: the folder is named after the client, the logo is copied into it."""
    name = re.sub(r"[^\w\-&' .]", "", b.get("name", "")).strip(" .")
    if not name:
        raise ValueError("give the brand kit a name, e.g. the client or show")
    d = KITS / name
    if b.get("new") and d.exists():
        raise ValueError(f"there's already a brand kit called {name}")
    k = read_kit(d) if (d / "kit.json").exists() else {}
    k.pop("name", None)
    for f in KIT_FIELDS:
        if f in b:
            k[f] = str(b[f]).strip()
    colors = b.get("colors") or {}
    k["colors"] = {c: v for c, v in dict(k.get("colors", {}), **colors).items() if re.fullmatch(r"#[0-9A-Fa-f]{6}", v or "")}
    d.mkdir(parents=True, exist_ok=True)
    if b.get("logo"):  # a newly chosen logo file
        src = Path(b["logo"]).expanduser()
        if src.suffix.lower() not in (".svg", ".png", ".jpg", ".jpeg"):
            raise ValueError("the logo needs to be an SVG, PNG or JPEG")
        readable(src, "logo")
        for old in d.glob("logo.*"):
            old.unlink()
        shutil.copy(src, d / f"logo{src.suffix.lower()}")
        k["logo"] = f"logo{src.suffix.lower()}"
    elif b.get("no_logo"):
        for old in d.glob("logo.*"):
            old.unlink()
        k.pop("logo", None)
    (d / "kit.json").write_text(json.dumps(k, indent=1, ensure_ascii=False))
    return {"name": name}


def all_posted(wd):
    """Every platform of every post in the kit ticked as posted on the calendar."""
    if not (wd / "posts.json").exists():
        return False
    posts = json.loads((wd / "posts.json").read_text())["posts"]
    posted = json.loads((wd / "posted.json").read_text()) if (wd / "posted.json").exists() else {}
    posts = [p for p in posts if p.get("approved")] or posts
    return bool(posts) and all(posted.get(str(p["clip"]), {}).get(pl) for p in posts
                                for pl, on in p.get("platforms", {}).items() if on)


def tidy(name, st):
    """Once everything's posted: delete the episode's videos from Working files, keeping the zip (the one copy
    that's needed), the transcript, picks and captions, so it can still be remade from the source video."""
    wd = OUT / name
    z = st.get("zip") if st.get("zip") and Path(st["zip"]).exists() else find_zip(name, st.get("guest"))
    if not z:
        raise ValueError("can't find this episode's zip in Ready to post, so its videos are staying put")
    freed = 0
    for f in [*wd.glob("clips/*.mp4"), *wd.glob("clips/*.mov"), *wd.glob("kit/*/*.mp4"), wd / "audio16k.wav"]:
        if f.is_file():
            freed += f.stat().st_size if f.stat().st_nlink == 1 else 0  # a linked video frees its space with its last link
            f.unlink()
    if (wd / ".work").is_dir():
        freed += sum(f.stat().st_size for f in (wd / ".work").rglob("*") if f.is_file())
        shutil.rmtree(wd / ".work", ignore_errors=True)
    st.update(tidied=datetime.now().isoformat(timespec="seconds"), zip=z)
    save_state(name, st)
    return freed


def find_zip(name, guest):
    """This episode's zip, wherever it was left: Ready to post, Movies/Dokkcast, the Desktop or the episode folder."""
    surname = (guest or name).split()[-1].lower()  # the zip may use a shorter name, e.g. Jess for Jessica
    found = []
    for d in (READY, Path.home() / "Movies" / "Dokkcast", Path.home() / "Desktop", OUT / name):
        try:  # macOS may not let the app look in the Desktop
            found += [f for f in os.scandir(d) if " posts - " in f.name and f.name.endswith(".zip")
                      and surname in f.name.lower() and "before" not in f.name.lower()]
        except OSError:
            continue
    found = [Path(f.path) for f in found]
    return str(max(found, key=lambda f: f.stat().st_mtime)) if found else None


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def start(name):
    if name in jobs:
        return
    pid = (load_state(name) or {}).get("pid")
    if alive(pid):  # a step from before the app was restarted is still running: wait for it, don't start it twice
        def wait():
            while alive(pid):
                threading.Event().wait(5)
            jobs.pop(name, None)
            st = load_state(name); st.pop("pid", None); save_state(name, st)
            start(name)
        jobs[name] = threading.Thread(target=wait, daemon=True)
    else:
        jobs[name] = threading.Thread(target=advance, args=(name,), daemon=True)
    jobs[name].start()


def episodes(logs=True):
    out = []
    for p in sorted(OUT.glob("*/app.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = json.loads(p.read_text())
        st["name"] = p.parent.name
        st["running"] = st["name"] in jobs
        lg = p.parent / "app.log"
        st["log"] = lg.read_text()[-3000:] if logs and lg.exists() else ""
        st["photo_set"] = (p.parent / "photo.jpg").exists()
        st["review_url"] = page(st["name"], "posts.html")
        st["has_kit"] = (p.parent / "kit" / "kit.html").exists()
        st["kit_url"] = page(st["name"], "kit/kit.html")
        st["kit"] = st.get("kit") or DEFAULT_KIT
        st["all_posted"] = all_posted(p.parent)
        out.append(st)
    return out


SW = """// Offcuts, installed as an app: everything comes live from Offcuts on this Mac; when it isn't running,
// say how to start it instead of the browser's own error page.
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
     <title>Offcuts</title><body style="margin:0;height:100vh;display:grid;place-items:center;background:#0B0B0D;
     color:#F2F1EE;font:16px system-ui;text-align:center"><div><h1 style="font-size:28px">Offcuts isn't running</h1>
     <p style="color:#A3A4A9">Open Offcuts from the Dock or your Offcuts folder, then press Try again.</p>
     <button onclick="location.reload()" style="font:inherit;padding:9px 18px;border-radius:8px;border:0;
     background:#EDEBE6;color:#111214;font-weight:700;cursor:pointer">Try again</button></div>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
"""


# ------------------------------------------------------------------- the server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, kind="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, f, html_rewrite=None):
        """A file, with byte ranges so videos can be scrubbed (Safari won't play them without)."""
        kind = {".html": "text/html; charset=utf-8", ".mp4": "video/mp4", ".mov": "video/quicktime",
                ".m4v": "video/mp4", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".json": "application/json",
                ".txt": "text/plain; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
                ".svg": "image/svg+xml"}.get(f.suffix.lower(), "application/octet-stream")
        if html_rewrite and f.suffix.lower() == ".html":
            return self.send(200, html_rewrite(f.read_text()).encode(), kind)
        try:
            fh = open(f, "rb")
        except PermissionError:  # macOS privacy: the app hasn't been allowed into that folder
            return self.send(403, b"macOS isn't letting the app read this file", "text/plain")
        size = f.stat().st_size
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m[1] or m[2]):
            start, end = (int(m[1]), int(m[2] or end)) if m[1] else (max(0, size - int(m[2])), end)
            end = min(end, size - 1)
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", kind)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 18, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except OSError:  # the player stopped reading, as it does when seeking
            pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/ep/"):  # an episode's review page, kit and clips
            name, _, rest = unquote(u.path[4:]).partition("/")
            wd = OUT / name
            f = Path(os.path.normpath(wd / rest))
            if load_state(name) and f.is_relative_to(wd) and f.is_file():
                # the pages point at videos as files on this Mac: fetch those through the app instead
                return self.send_file(f, lambda h: h.replace('"file://', '"/media'))
        if u.path.startswith("/media/"):  # only the episode videos and music the app was given
            f = Path(unquote(u.path[6:]))
            allowed = {Path(v).resolve() for st in map(load_state, [p.parent.name for p in OUT.glob("*/app.json")])
                       for v in (st.get("video"), st.get("music")) if v}
            if f.is_file() and str(f.resolve()) in chosen:
                return self.send_file(f)
            if f.is_file() and (f.resolve() in allowed or f.resolve().is_relative_to(OUT.resolve())):
                return self.send_file(f)
        if u.path == "/":
            html = (ROOT / "assets" / "app.html").read_text().replace("/*STUDIO*/null", json.dumps(studio()))
            return self.send(200, html.encode(), "text/html; charset=utf-8")
        if u.path == "/manifest.webmanifest":  # so the browser can install Offcuts as an app with its own window
            st = studio()
            return self.send(200, json.dumps({
                "name": st["name"], "short_name": st["name"], "description": st.get("tagline", ""),
                "start_url": "/", "scope": "/", "display": "standalone", "id": "/",
                "background_color": st.get("colors", {}).get("bg", "#0B0B0D"),
                "theme_color": st.get("colors", {}).get("bg", "#0B0B0D"),
                "icons": [{"src": f"/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any"}
                          for n in (192, 512)]}).encode(), "application/manifest+json")
        m = re.fullmatch(r"/icon-(\d+)\.png", u.path)
        if m and studio().get("icon") and int(m[1]) in (180, 192, 512):
            from io import BytesIO
            from PIL import Image
            n, buf = int(m[1]), BytesIO()
            Image.open(ROOT / studio()["icon"]).convert("RGBA").resize((n, n), Image.LANCZOS).save(buf, "PNG")
            return self.send(200, buf.getvalue(), "image/png")
        if u.path == "/sw.js":  # the installed app's helper: says so plainly when Offcuts isn't running
            return self.send(200, SW.encode(), "text/javascript")
        if u.path == "/logo" and studio().get("logo"):
            f = ROOT / studio()["logo"]
            kind = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(f.suffix.lower())
            if f.exists() and kind:
                return self.send(200, f.read_bytes(), kind)
        if u.path.startswith("/kit-logo/"):
            k = next((k for k in kits() if k["name"] == unquote(u.path[10:])), None)
            if k and k.get("logo"):
                return self.send_file(KITS / k["name"] / k["logo"])
        if u.path == "/api/kits":
            return self.send(200, {"kits": kits(), "last": settings().get("kit") or DEFAULT_KIT})
        if u.path == "/api/calendar":
            return self.send(200, {"posts": calendar(), "upload": UPLOAD})
        if u.path == "/api/state":
            return self.send(200, {"episodes": episodes(), "next_start": next_start(), "auto_pick": bool(can_pick()),
                                  "mode": pick_mode(), "has_claude": bool(claude_cli()),
                                  "claude_ready": claude_ready() if pick_mode() == "mac" else None,
                                  "has_key": bool(os.environ.get("ANTHROPIC_API_KEY") or api_key())})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            self.send(200, self.action(u.path, body))
        except ValueError as e:
            self.send(400, {"error": str(e)})

    def action(self, path, b):
        if path == "/api/choose":
            p = choose(b["kind"])
            if p and b["kind"] == "logo":
                chosen.add(str(Path(p).resolve()))
            return {"path": p}
        if path == "/api/mode":
            p = Path.home() / ".dokkcast" / "settings.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(dict(settings(), pick=b.get("pick") if b.get("pick") in ("mac", "chat", "api") else "mac")))
            return {"ok": True}
        if path == "/api/key":  # kept on this Mac, readable only by this user
            try:  # check it works before keeping it
                import anthropic
                anthropic.Anthropic(api_key=b["key"].strip()).models.list(limit=1)
            except ImportError:
                raise ValueError("the anthropic package is missing: run ./setup.sh again")
            except anthropic.AuthenticationError:
                raise ValueError("Anthropic didn't accept that key: create one at console.anthropic.com, Settings, API keys")
            except anthropic.APIConnectionError:
                raise ValueError("couldn't reach Anthropic to check the key: check the internet connection")
            p = Path.home() / ".dokkcast" / "anthropic_key"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(b["key"].strip())
            p.chmod(0o600)
            return {"ok": True}
        if path == "/api/kit":
            return save_kit(b)
        if path == "/api/new":
            kit = b.get("kit") or DEFAULT_KIT
            if not (KITS / kit / "kit.json").exists():
                raise ValueError(f"can't find the brand kit {kit}: choose another")
            video = Path(b["video"]).expanduser()
            if not video.exists():
                raise ValueError("can't find that video")
            readable(video, "video")
            if b.get("music"):
                readable(Path(b["music"]).expanduser(), "music track")
            name = re.sub(r"[^\w\- ]", "", b.get("name") or video.stem).strip() or video.stem
            if load_state(name):
                raise ValueError(f"there's already an episode called {name}")
            st = {"video": str(video), "music": b.get("music") or None, "guest": b.get("guest", "").strip(),
                  "role": b.get("role", "").strip(), "start": b.get("start") or None, "photo": b.get("photo") or None,
                  "speakers": int(b.get("speakers") or 2), "step": "prep", "status": "queued", "kit": kit,
                  "created": datetime.now().isoformat(timespec="seconds")}
            if video.stem != name:  # prep names the work folder after the file
                link = VIDEOS / f"{name}{video.suffix}"
                if not link.exists():
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(video)
                st["video"] = str(link)
            save_state(name, st)
            p = Path.home() / ".dokkcast" / "settings.json"  # the next episode starts with the same kit
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(dict(settings(), kit=kit)))
            warning = None
            try:
                keep_photo(name, st["photo"])
            except ValueError as e:
                st["photo"] = None; save_state(name, st); warning = str(e)
            start(name)
            return {"name": name, "warning": warning}
        if path == "/api/home":  # the Offcuts folder, with Source files and Ready to post
            subprocess.run(["open", str(STUDIO or ROOT)])
            return {"ok": True}
        name = b.get("name", "")
        st = load_state(name)
        if not st:
            raise ValueError("no such episode")
        if path == "/api/continue":  # after the picks are in, or the review is saved
            if st.get("waiting") == "picks" and not has_picks(name):
                raise ValueError("no clips picked yet: ask Claude in chat to pick the clips for " + name)
            if st.get("waiting") == "review":
                if not newest_saved(name):
                    raise ValueError("press Save my choices on the review page first (it goes to Downloads)")
                st["step"] = "finish"
            st["waiting"] = None
            save_state(name, st)
            start(name)
            return {"ok": True}
        if path == "/api/save":  # from the review page: keep the choices, and make the clips if asked
            (OUT / name / "posts-saved.json").write_text(json.dumps(b["posts"], indent=1, ensure_ascii=False))
            if b.get("go") and st.get("waiting") == "review" and name not in jobs:
                st.update(step="finish", waiting=None); save_state(name, st); start(name)
            return {"ok": True}
        if path == "/api/posted":  # a platform ticked as posted (or scheduled) on the calendar
            f = OUT / name / "posted.json"
            posted = json.loads(f.read_text()) if f.exists() else {}
            posted.setdefault(str(b["clip"]), {})[b["platform"]] = bool(b.get("done"))
            f.write_text(json.dumps(posted, indent=1))
            return {"ok": True}
        if path == "/api/reveal":  # show one of the episode's files in Finder, ready to drag into a website
            f = Path(os.path.normpath(OUT / name / b.get("path", "")))
            if not f.is_relative_to(OUT / name) or not f.exists():
                raise ValueError("can't find that file any more")
            subprocess.run(["open", "-R", str(f)])
            return {"ok": True}
        if path == "/api/tidy":
            if name in jobs or st.get("status") != "done":
                raise ValueError("this episode isn't finished yet")
            if not all_posted(OUT / name):
                raise ValueError("tick every post as Posted on the calendar first")
            freed = tidy(name, st)
            return {"ok": True, "freed_mb": round(freed / 1e6)}
        if path == "/api/keep":  # changed their mind after Change and remake: keep what was made before
            if st.get("waiting") == "review" and (OUT / name / "kit" / "kit.html").exists():
                st.update(step="done", status="done", waiting=None); save_state(name, st)
            return {"ok": True}
        if path == "/api/reopen":  # back to the review page, e.g. to change captions after rendering
            st.update(step="review", status="queued", waiting=None); st.pop("tidied", None); save_state(name, st); start(name)
            return {"ok": True}
        if path == "/api/set":  # music, photo, guest, start date
            if b.get("photo"):
                keep_photo(name, b["photo"])
            for k in ("music", "photo", "guest", "role", "start"):
                if k in b:
                    st[k] = b[k]
            save_state(name, st)
            return {"ok": True}
        if path == "/api/open":
            what = {"review": OUT / name / "posts.html", "kit": OUT / name / "kit" / "kit.html",
                    "folder": OUT / name}.get(b.get("what"))
            if b.get("what") == "zip":
                z = st.get("zip") if st.get("zip") and Path(st["zip"]).exists() else find_zip(name, st.get("guest"))
                if not z:
                    raise ValueError("can't find the zip. If it's on your Desktop, macOS may be keeping Offcuts out: "
                                     "allow it in System Settings, Privacy & Security, Files and Folders")
                st["zip"] = z; save_state(name, st)
                subprocess.run(["open", "-R", z])
            elif what and what.exists():
                subprocess.run(["open", str(what)])
            return {"ok": True}
        raise ValueError("unknown action")


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found: run ./setup.sh first")
    OUT.mkdir(exist_ok=True)
    seed_kits()  # the brand kits to start with: The Dokkcast and MBMS Podcast Studio
    if STUDIO:  # the folders a person uses, and a note on what each is for
        if (STUDIO / "Videos").is_dir() and not VIDEOS.exists():  # its first name
            (STUDIO / "Videos").rename(VIDEOS)
        VIDEOS.mkdir(exist_ok=True); READY.mkdir(exist_ok=True)
        (STUDIO / "Where things are.txt").write_text(
            "Offcuts\n\n"
            "Source files     Put each new episode video and its music track here, then choose them in Offcuts.\n"
            "Ready to post    One zip per finished episode: the clips, covers and captions to share or post from.\n"
            "Brand kits       One folder per client or show: its logo and colours, made in Offcuts (Brand kits).\n"
            "Working files    Offcuts' own workings (transcripts, clips, covers). No need to open it.\n"
            "App              The program itself. Leave it be.\n\n"
            "Day to day, open Offcuts: the Calendar shows each day's posts, ready to copy and drag in.\n")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    for name in [p.parent.name for p in OUT.glob("*/app.json")]:  # carry on with anything that was running
        if load_state(name).get("status") in ("running", "queued"):
            start(name)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"{studio()['name']} at {url}")
    if not os.environ.get("DOKK_NO_BROWSER"):
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
