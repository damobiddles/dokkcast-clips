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

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
PY = sys.executable
PORT = int(os.environ.get("DOKK_PORT", 8765))
STEPS = ["prep", "pick", "review", "finish"]
jobs = {}  # name -> running thread


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
        r = subprocess.Popen(list(map(str, cmd)), cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
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
    types = {"video": '{"public.movie"}', "music": '{"public.audio"}', "photo": '{"public.image"}'}
    prompts = {"video": "Choose the episode video", "music": "Choose the music track",
               "photo": "Choose a photo of the guest (for the covers)"}
    return mac(f'POSIX path of (choose file with prompt "{prompts[kind]}" of type {types[kind]})')


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
    while d.weekday() > 4:
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
        raise ValueError(f"macOS isn't letting the app read the {what} there. Put it in your Movies folder "
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


# ------------------------------------------------------------------ the routine
def advance(name):
    """Run steps until one needs a person (picks, review) or everything is done."""
    st = load_state(name)
    try:
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
                if st.get("photo") and not (OUT / name / "photo.jpg").exists():
                    subprocess.run(["sips", "-s", "format", "jpeg", st["photo"], "--out", str(OUT / name / "photo.jpg")],
                                   capture_output=True)
                run(name, [PY, "dokkpost.py", "finish", name] + (["--title", st["guest"]] if st.get("guest") else []))
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
    return f"http://127.0.0.1:{PORT}/ep/{quote(name)}/{path}"


def find_zip(name, guest):
    for d in (Path.home() / "Movies" / "Dokkcast", OUT / name):
        zs = sorted(d.glob("Dokkcast posts - *.zip"), key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []
        if zs:
            return str(zs[0])
    return None


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


def episodes():
    out = []
    for p in sorted(OUT.glob("*/app.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = json.loads(p.read_text())
        st["name"] = p.parent.name
        st["running"] = st["name"] in jobs
        lg = p.parent / "app.log"
        st["log"] = lg.read_text()[-3000:] if lg.exists() else ""
        st["photo_set"] = (p.parent / "photo.jpg").exists()
        st["review_url"] = page(st["name"], "posts.html")
        st["kit_url"] = page(st["name"], "kit/kit.html")
        out.append(st)
    return out


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
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):  # the player stopped reading, as it does when seeking
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
            if f.is_file() and (f.resolve() in allowed or f.resolve().is_relative_to(OUT.resolve())):
                return self.send_file(f)
        if u.path == "/":
            html = (ROOT / "assets" / "app.html").read_text().replace("/*STUDIO*/null", json.dumps(studio()))
            return self.send(200, html.encode(), "text/html; charset=utf-8")
        if u.path == "/logo" and studio().get("logo"):
            f = ROOT / studio()["logo"]
            kind = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(f.suffix.lower())
            if f.exists() and kind:
                return self.send(200, f.read_bytes(), kind)
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
            return {"path": choose(b["kind"])}
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
        if path == "/api/new":
            video = Path(b["video"]).expanduser()
            if not video.exists():
                raise ValueError("can't find that video")
            readable(video, "video")
            if b.get("music"):
                readable(Path(b["music"]).expanduser(), "music track")
            if b.get("photo"):
                readable(Path(b["photo"]).expanduser(), "photo")
            name = re.sub(r"[^\w\- ]", "", b.get("name") or video.stem).strip() or video.stem
            if load_state(name):
                raise ValueError(f"there's already an episode called {name}")
            st = {"video": str(video), "music": b.get("music") or None, "guest": b.get("guest", "").strip(),
                  "role": b.get("role", "").strip(), "start": b.get("start") or None, "photo": b.get("photo") or None,
                  "speakers": int(b.get("speakers") or 2), "step": "prep", "status": "queued",
                  "created": datetime.now().isoformat(timespec="seconds")}
            if video.stem != name:  # prep names the work folder after the file
                link = Path.home() / "Movies" / "Dokkcast" / f"{name}{video.suffix}"
                if not link.exists():
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(video)
                st["video"] = str(link)
            save_state(name, st)
            start(name)
            return {"name": name}
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
        if path == "/api/keep":  # changed their mind after Change and remake: keep what was made before
            if st.get("waiting") == "review" and st.get("zip") and Path(st["zip"]).exists():
                st.update(step="done", status="done", waiting=None); save_state(name, st)
            return {"ok": True}
        if path == "/api/reopen":  # back to the review page, e.g. to change captions after rendering
            st.update(step="review", status="queued", waiting=None); save_state(name, st); start(name)
            return {"ok": True}
        if path == "/api/set":  # music, photo, guest, start date
            for k in ("music", "photo", "guest", "role", "start"):
                if k in b:
                    st[k] = b[k]
            if "photo" in b:
                (OUT / name / "photo.jpg").unlink(missing_ok=True)
            save_state(name, st)
            return {"ok": True}
        if path == "/api/open":
            what = {"review": OUT / name / "posts.html", "kit": OUT / name / "kit" / "kit.html",
                    "folder": OUT / name}.get(b.get("what"))
            if b.get("what") == "zip" and st.get("zip"):
                subprocess.run(["open", "-R", st["zip"]])
            elif what and what.exists():
                subprocess.run(["open", str(what)])
            return {"ok": True}
        raise ValueError("unknown action")


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found: run ./setup.sh first")
    OUT.mkdir(exist_ok=True)
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
