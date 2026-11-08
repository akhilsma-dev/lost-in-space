"""HTTP helper for the dashboard's voice panel (dashboard/voice.js).

It keeps the ElevenLabs key on the server, turns speech or typed text into sim commands with
voice/commands.py, and forwards ONLY those whitelisted commands to the sim's UDP command port.

Run (from the project root):
  .venv/bin/python -m voice.server                    listens on 127.0.0.1:5010
  .venv/bin/python -m voice.server --host 0.0.0.0     reachable from other devices on your network
  .venv/bin/python -m voice.server --dry-run          parse only, do not send anything to the sim

Needs for speech:  pip install elevenlabs sounddevice numpy   and   export ELEVENLABS_API_KEY=...
Typed commands work without any of that.

Endpoints (JSON out, CORS open so the dashboard page can call it from its own port):
  GET  /voice/status                  {"stt": bool, "server_mic": bool, "sim_port": int, "dry_run": bool}
  POST /voice/text   {"text": "..."}  a typed command
  POST /voice/talk   {"seconds": 4}   record on THIS computer's microphone, then transcribe
  POST /voice/audio  <audio bytes>    audio recorded in the browser (16 kHz WAV preferred)
Every POST answers {"ok", "heard", "commands", "reply", "sent", "error"}.
"""
import argparse
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from voice import listen
from voice.commands import parse

CFG = {"port": 5006, "solver_port": 5011, "dry": False}
SOLVER_CMDS = ("whereami", "detector", "camera")   # camera.solve_live's command port, not the sim's
_client = None
_mic_lock = threading.Lock()
MAX_BODY = 12 * 1024 * 1024
EXT = {"wav": "wav", "webm": "webm", "ogg": "ogg", "mp4": "mp4", "mpeg": "mp3", "mp3": "mp3", "aac": "aac"}


def get_client():
    global _client
    if _client is None:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set on the voice server (typed commands still work)")
        from elevenlabs.client import ElevenLabs
        _client = ElevenLabs(api_key=key)
    return _client


def server_mic_available():
    try:
        import sounddevice as sd
        sd.query_devices(kind="input")
        return True
    except Exception:
        return False


def execute(text):
    """Parse text and send the resulting commands to the sim. Nothing else can reach the sim."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "heard": "", "commands": [], "reply": "", "sent": False,
                "error": "I did not hear anything. Try again, a little closer to the microphone."}
    p = parse(text)
    sent = False
    if p.commands and not CFG["dry"]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for c in p.commands:
            port = CFG["solver_port"] if c.split()[0] in SOLVER_CMDS else CFG["port"]
            sock.sendto(c.encode(), ("127.0.0.1", port))
        sock.close()
        sent = True
    print(f'[voice] "{text}" -> {p.commands or "nothing"}' + ("  (dry run)" if CFG["dry"] else ""))
    return {"ok": bool(p.commands), "heard": text, "commands": p.commands, "reply": p.reply, "sent": sent,
            "error": None if p.commands else p.reply}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the terminal quiet
        pass

    def _send(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, msg, code=200):
        self._send(code, {"ok": False, "heard": "", "commands": [], "reply": "", "sent": False, "error": msg})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("audio too large")
        return self.rfile.read(n) if n else b""

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        if self.path.split("?")[0] == "/voice/status":
            self._send(200, {"stt": bool(os.environ.get("ELEVENLABS_API_KEY")), "server_mic": server_mic_available(),
                             "sim_port": CFG["port"], "dry_run": CFG["dry"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            data = self._body()
            if path == "/voice/text":
                self._send(200, execute(json.loads(data or b"{}").get("text", "")))
            elif path == "/voice/talk":
                secs = min(10.0, max(1.0, float(json.loads(data or b"{}").get("seconds", 4))))
                client = get_client()
                if not _mic_lock.acquire(blocking=False):
                    return self._fail("the microphone is already recording")
                try:
                    wav = listen.record_wav(secs)
                finally:
                    _mic_lock.release()
                self._send(200, execute(listen.transcribe(client, wav)))
            elif path == "/voice/audio":
                if not data:
                    return self._fail("no audio received")
                kind = (self.headers.get("Content-Type") or "audio/wav").split(";")[0].split("/")[-1].lower()
                name = "speech." + EXT.get(kind, "wav")
                self._send(200, execute(listen.transcribe(get_client(), data, name)))
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            print(f"[voice] error on {path}: {type(e).__name__}: {e}")
            self._fail(str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from other devices")
    ap.add_argument("--port", type=int, default=5010)
    ap.add_argument("--sim-port", type=int, default=5006, help="the sim's UDP command port")
    ap.add_argument("--solver-port", type=int, default=5011,
                    help="camera.solve_live's UDP command port (where am I?, star finder, camera)")
    ap.add_argument("--dry-run", action="store_true", help="parse only, do not send to the sim")
    args = ap.parse_args()
    CFG["port"], CFG["solver_port"], CFG["dry"] = args.sim_port, args.solver_port, args.dry_run
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"voice helper on http://{args.host}:{args.port}  ->  sim UDP 127.0.0.1:{args.sim_port}"
          + ("  (dry run)" if args.dry_run else ""))
    print("speech-to-text key:", "found" if os.environ.get("ELEVENLABS_API_KEY") else "NOT set (typed commands only)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()