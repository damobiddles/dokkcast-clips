#!/usr/bin/env python3
"""Dokkcast Studio: the whole episode-to-posts routine as a small app on this Mac.

    python dokkapp.py            # opens http://127.0.0.1:8765 in the browser

Pick the episode video and a music track, and it runs each step in order:

    1. prep      transcribe and find the speakers            dokkepisode.py prep
    2. pick      choose the clips and write the copy          dokkpick.py (Claude API), or Claude in chat
    3. review    plan the posting dates and open the review   dokkpost.py plan + review --music
                 page: watch, set the music, edit, approve
    4. finish    render the approved clips with the music,    dokkpost.py apply + finish
                 covers, posting kit and zip

Each episode's progress is kept in output/<name>/app.json, so the app can be closed and opened
again at any point. Nothing leaves the Mac except the transcript, if the Claude API picks the clips.
"""
import json, os, re, shutil, subprocess, sys, threading, webbrowser
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
PY = sys.executable
PORT = int(os.environ.get("DOKK_PORT", 8765))
STEPS = ["prep", "pick", "review", "finish"]
jobs = {}  # name -> running thread


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
        r = subprocess.run(list(map(str, cmd)), cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:  # the tool's last line usually says what went wrong
        last = [l for l in (OUT / name / "app.log").read_text().splitlines() if l.strip()][-1:]
        raise RuntimeError(f"{Path(cmd[1]).name} {cmd[2]} failed: {last[0].strip()[:200] if last else 'see Details'}")


def mac(script):
    """Run AppleScript and return what it prints, or None if the user cancelled."""
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


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


def can_pick():
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return (ROOT / "dokkpick.py").exists() and bool(os.environ.get("ANTHROPIC_API_KEY") or api_key())


def api_key():
    p = Path.home() / ".dokkcast" / "anthropic_key"
    return p.read_text().strip() if p.exists() else ""


def newest_saved(name):
    """The posts.json saved from this episode's review page, newest first in Downloads."""
    files = sorted((Path.home() / "Downloads").glob("posts*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
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
                    if not can_pick():
                        st["status"] = "waiting"; st["waiting"] = "picks"; save_state(name, st); return
                    env_key = os.environ.get("ANTHROPIC_API_KEY") or api_key()
                    os.environ["ANTHROPIC_API_KEY"] = env_key
                    run(name, [PY, "dokkpick.py", name, "--guest", st.get("guest", ""), "--role", st.get("role", "")])
                st["step"] = "review"
            elif step == "review":
                if not (OUT / name / "posts.json").exists():
                    run(name, [PY, "dokkpost.py", "plan", name, "--start", st.get("start") or next_start()])
                run(name, [PY, "dokkpost.py", "review", name] + (["--music", st["music"]] if st.get("music") else []))
                subprocess.run(["open", str(OUT / name / "posts.html")])
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
                subprocess.run(["open", str(OUT / name / "kit" / "kit.html")])
                return
            else:
                return
            st["waiting"] = None; save_state(name, st)
    except Exception as e:  # shown on the page with the log
        st.update(status="error", error=str(e)); save_state(name, st)
    finally:
        jobs.pop(name, None)


def find_zip(name, guest):
    for d in (Path.home() / "Movies" / "Dokkcast", OUT / name):
        zs = sorted(d.glob("Dokkcast posts - *.zip"), key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []
        if zs:
            return str(zs[0])
    return None


def start(name):
    if name not in jobs:
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

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self.send(200, (ROOT / "assets" / "app.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/state":
            return self.send(200, {"episodes": episodes(), "next_start": next_start(), "auto_pick": can_pick()})
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
        if path == "/api/key":  # kept on this Mac, readable only by this user
            if not b["key"].strip().startswith("sk-ant-api"):
                raise ValueError("that isn't an API key: they start sk-ant-api (console.anthropic.com, Settings, API keys)")
            p = Path.home() / ".dokkcast" / "anthropic_key"
            p.parent.mkdir(exist_ok=True)
            p.write_text(b["key"].strip())
            p.chmod(0o600)
            return {"ok": True}
        if path == "/api/new":
            video = Path(b["video"]).expanduser()
            if not video.exists():
                raise ValueError("can't find that video")
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
            if st.get("waiting") == "review":
                if not newest_saved(name):
                    raise ValueError("press Save my choices on the review page first (it goes to Downloads)")
                st["step"] = "finish"
            st["waiting"] = None
            save_state(name, st)
            start(name)
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
    print(f"Dokkcast Studio at {url}  (close this window to stop it)")
    if not os.environ.get("DOKK_NO_BROWSER"):
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
