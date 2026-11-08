"""Make the callout clips the dashboard plays (dashboard/static/voice/ + manifest.json).

  python -m voice.generate                  ElevenLabs text-to-speech (needs ELEVENLABS_API_KEY)
  python -m voice.generate --placeholder    macOS `say`, offline, no key: stand-in clips
  python -m voice.generate --list           print the callouts and exit

Options:
  --voice ID      ElevenLabs voice id (default: env ELEVENLABS_VOICE_ID, else a premade voice)
  --model ID      ElevenLabs model (default eleven_multilingual_v2)
  --only a,b      just these clip ids
  --force         regenerate clips that already exist with the same text

Clips whose text, engine and voice are unchanged are skipped, so re-running is cheap.
Run it before the venue Wi-Fi gives out; the clips are committed so the demo is offline.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from voice.lines import LINES

OUT = pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "static" / "voice"
MANIFEST = OUT / "manifest.json"
API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"   # premade "Adam": calm, low, mission-control-ish
DEFAULT_MODEL = "eleven_multilingual_v2"


def eleven(text, voice, model, key):
    body = json.dumps({"text": text, "model_id": model,
                       "voice_settings": {"stability": 0.55, "similarity_boost": 0.8, "style": 0.15}}).encode()
    req = urllib.request.Request(API.format(voice=voice), data=body, method="POST", headers={
        "xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        sys.exit(f"ElevenLabs error {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach ElevenLabs: {e.reason}")


def say(text, path):
    # `say` sometimes writes the file and then never exits: give it a deadline and keep a usable file
    path.unlink(missing_ok=True)
    cmd = ["say", "-r", "185", "-o", str(path), "--file-format=m4af", "--data-format=aac", text]
    for _ in range(2):
        try:
            subprocess.run(cmd, check=True, timeout=20, stdin=subprocess.DEVNULL)
            return
        except subprocess.TimeoutExpired:
            if path.is_file() and path.stat().st_size > 1000:
                return
    sys.exit(f"`say` failed for: {text}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--placeholder", action="store_true", help="use macOS `say` instead of ElevenLabs")
    ap.add_argument("--voice", default=os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--only", default="", help="comma-separated clip ids")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for k, v in LINES.items():
            print(f"{k:12s} {v}")
        return
    ids = [s for s in a.only.split(",") if s] or list(LINES)
    bad = [i for i in ids if i not in LINES]
    if bad:
        sys.exit(f"unknown clip ids: {', '.join(bad)} (see --list)")

    if a.placeholder:
        if not shutil.which("say"):
            sys.exit("--placeholder needs macOS `say`")
        engine, voice, ext, key = "say", "say", ".m4a", None
    else:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            sys.exit("set ELEVENLABS_API_KEY (or use --placeholder for offline stand-in clips)")
        engine, voice, ext = "elevenlabs", a.voice, ".mp3"

    OUT.mkdir(parents=True, exist_ok=True)
    try:
        man = json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        man = {"clips": {}}
    clips = man.setdefault("clips", {})

    made = skipped = 0
    for cid in ids:
        text, old = LINES[cid], clips.get(cid) or {}
        path = OUT / (cid + ext)
        if (not a.force and old.get("text") == text and old.get("engine") == engine
                and old.get("voice") == voice and (OUT / old.get("file", "")).is_file()):
            skipped += 1
            continue
        print(f"[{engine}] {cid}: {text}")
        if engine == "say":
            say(text, path)
        else:
            path.write_bytes(eleven(text, voice, a.model, key))
        if old.get("file") and old["file"] != path.name:
            (OUT / old["file"]).unlink(missing_ok=True)   # e.g. placeholder .m4a replaced by .mp3
        clips[cid] = {"file": path.name, "text": text, "engine": engine, "voice": voice}
        made += 1

    for cid in [c for c in clips if c not in LINES]:     # callouts removed from lines.py
        (OUT / clips.pop(cid)["file"]).unlink(missing_ok=True)
    MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    print(f"done: {made} made, {skipped} unchanged -> {OUT}")


if __name__ == "__main__":
    main()
