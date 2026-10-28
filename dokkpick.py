#!/usr/bin/env python3
"""Pick an episode's social clips and write their copy with Claude, from the transcript.

    python dokkpick.py JohnDeanfield --guest "Prof. John Deanfield" --role "Cardiologist"

Reads output/<name>/transcript.txt (from dokkepisode.py prep) and asks Claude for the speakers'
names, the best 30-60 s clips (as word numbers, so they start and end on whole sentences), and
for each clip a title, captions for TikTok, Instagram and LinkedIn, and a YouTube Shorts title and
description. Writes them into plan.json (through dokkepisode.py suggest) and copy.json, ready for
dokkpost.py plan and review, where every clip and caption is checked before anything is made.

Needs the anthropic package and an API key in ANTHROPIC_API_KEY or ~/.dokkcast/anthropic_key.
Show details (host, how many clips, model) are in brand.json under "pick". Earlier episodes'
copy.json files are given to Claude as examples of the house style.
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from dokkclip import ROOT, load_brand

DEFAULTS = {"model": "claude-opus-5-5", "clips": 9, "min_seconds": 30, "max_seconds": 60,
            "host": "Dr. Nara Daubeney", "host_role": "Consultant ENT surgeon and entrepreneur",
            "show": "The Dokkcast"}


class Speaker(BaseModel):
    speaker: int = Field(description="speaker number as in the transcript (S0, S1 ...)")
    name: str
    role: str = Field(description='"Host" for the host, otherwise the guest\'s job title')


class Clip(BaseModel):
    first_word: int = Field(description="number of the clip's first word, as numbered in the transcript")
    last_word: int = Field(description="number of the clip's last word")
    title: str = Field(description="short internal title, a few words")
    why: str = Field(description="one sentence on why this works as a standalone clip")
    tiktok: str = Field(description="TikTok caption with 3-6 hashtags")
    instagram: str = Field(description="Instagram caption with hashtags")
    linkedin: str = Field(description="LinkedIn post, professional, a few short paragraphs, 3-5 hashtags")
    youtube_title: str = Field(description="YouTube Shorts title, under 90 characters, ends with #Shorts")
    youtube: str = Field(description="YouTube Shorts description with hashtags")


class Picks(BaseModel):
    speakers: list[Speaker]
    clips: list[Clip]


def strict(schema):
    """Structured output needs every object closed to extra keys."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
        for v in schema.values():
            strict(v)
    elif isinstance(schema, list):
        for v in schema:
            strict(v)
    return schema


def api_key():
    k = os.environ.get("ANTHROPIC_API_KEY")
    p = Path.home() / ".dokkcast" / "anthropic_key"
    return k or (p.read_text().strip() if p.exists() else None)


def examples(name, limit=2):
    """Copy from earlier episodes, newest first, as a style guide."""
    out = []
    for cj in sorted((ROOT / "output").glob("*/copy.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if cj.parent.name == name:
            continue
        copy = json.loads(cj.read_text())
        first = next(iter(copy.values()), None)
        if first:
            out.append(json.dumps(first, indent=1, ensure_ascii=False))
        if len(out) >= limit:
            break
    return out


def prompt(cfg, guest, role, transcript, styles):
    who = f"The guest is {guest}" + (f", {role}" if role else "") + "." if guest else \
        "Work out the guest's name and job from the conversation."
    style = ("\n\nCopy from earlier episodes, to match the tone and format:\n\n" + "\n\n".join(styles)) if styles else ""
    return f"""This is the transcript of an episode of {cfg['show']}, a health podcast hosted by {cfg['host']} \
({cfg['host_role']}). {who}

Each line is [number of its first word] mm:ss S<speaker>: text. Words are numbered one by one from the line's number.

Pick the {cfg['clips']} best clips for social media, {cfg['min_seconds']}-{cfg['max_seconds']} seconds each, in the \
order they appear. A good clip makes sense on its own with no setup, opens with a hook (a surprising fact, a strong \
claim, a question people ask), lands one clear idea, and ends on a complete sentence. Prefer the guest speaking; \
include the host's question only if the answer needs it. No two clips may overlap. Give each clip by the numbers of \
its first and last words.

Then write the copy for each clip. Keep it accurate to what was said: no medical claims beyond the clip, no \
promises, and spell names and terms correctly (the transcript is automatic and may mishear them). British English. \
Name the guest with their title. Captions should make people want to watch, then follow the episode.{style}

Transcript:

{transcript}"""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", help="the episode name (as in output/<name>)")
    p.add_argument("--guest", default="", help='e.g. "Prof. John Deanfield"')
    p.add_argument("--role", default="", help='e.g. "Consultant Cardiologist"')
    p.add_argument("--brand", default=str(ROOT / "brand.json"))
    a = p.parse_args()
    cfg = dict(DEFAULTS, **load_brand(a.brand).get("pick", {}))
    wd = ROOT / "output" / a.name
    transcript = (wd / "transcript.txt").read_text()
    words = json.loads((wd / "words.json").read_text())
    key = api_key()
    if not key:
        sys.exit("no Anthropic API key: put it in ~/.dokkcast/anthropic_key or ANTHROPIC_API_KEY")

    import anthropic
    client = anthropic.Anthropic(api_key=key)
    print(f"asking Claude for {cfg['clips']} clips and their copy ...", flush=True)
    try:  # streamed: nine clips of copy plus thinking needs room, and a long answer would time out otherwise
        with client.messages.stream(
                model=cfg["model"], max_tokens=64000, output_config={"effort": "high", "format": {
                    "type": "json_schema", "schema": strict(Picks.model_json_schema())}},
                messages=[{"role": "user", "content": prompt(cfg, a.guest, a.role, transcript, examples(a.name))}]) as stream:
            r = stream.get_final_message()
    except anthropic.AuthenticationError:
        sys.exit("the Anthropic API key was refused: check it at console.anthropic.com")
    except anthropic.RateLimitError:
        sys.exit("Anthropic API rate limit: wait a minute and try again")
    except anthropic.APIConnectionError:
        sys.exit("couldn't reach the Anthropic API: check the internet connection")
    if r.stop_reason == "refusal":
        sys.exit(f"Claude declined this transcript ({getattr(r.stop_details, 'category', None)}); pick the clips in chat instead")
    if r.stop_reason == "max_tokens":
        sys.exit("Claude's answer was cut short; run it again")
    try:
        picks = Picks.model_validate_json(next(b.text for b in r.content if b.type == "text"))
    except (StopIteration, ValidationError):
        sys.exit("Claude's answer didn't come back in the right shape; run it again")

    clips, copy = [], {}
    for n, c in enumerate(sorted(picks.clips, key=lambda c: c.first_word), 1):
        f, l = max(0, c.first_word), min(len(words) - 1, c.last_word)
        if l <= f:
            print(f"  skipping '{c.title}': bad word numbers"); continue
        dur = words[l]["e"] - words[f]["s"]
        flag = "" if cfg["min_seconds"] - 5 <= dur <= cfg["max_seconds"] + 10 else "  (check the length)"
        print(f"  clip {n}: {c.title} ({dur:.0f}s){flag}")
        clips.append({"id": n, "w": [f, l], "title": c.title, "note": c.why})
        copy[str(n)] = {k: getattr(c, k) for k in ("tiktok", "instagram", "linkedin", "youtube_title", "youtube")}
    if not clips:
        sys.exit("no usable clips came back; run it again")

    host = cfg["host"]
    speakers = {str(s.speaker): {"name": host if s.role.lower() == "host" else (a.guest or s.name),
                                 "role": cfg["host_role"] if s.role.lower() == "host" else (a.role or s.role)}
                for s in picks.speakers}
    sug = wd / "suggestions.json"
    sug.write_text(json.dumps({"speakers": speakers, "clips": clips}, indent=1, ensure_ascii=False))
    (wd / "copy.json").write_text(json.dumps(copy, indent=1, ensure_ascii=False))
    subprocess.run([sys.executable, str(ROOT / "dokkepisode.py"), "suggest", a.name, str(sug)], check=True)
    u = r.usage
    print(f"{len(clips)} clips and their copy -> {wd / 'copy.json'} ({u.input_tokens} in, {u.output_tokens} out tokens)")


if __name__ == "__main__":
    main()
