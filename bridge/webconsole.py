"""Virtual console: the Arduino mission console as a web page, for when the board is not there.

The bridge treats it exactly like the real board. It sends the same serial lines
(STATUS / ATT / SERVO) and gets the same lines back (BTN FLARE / KNOB n). The page
(bridge/static/console.html) runs the firmware's behaviour: OLED layout, lock LED,
buzzer chirp/alarm, servo slew limit and the 3 s NO LINK timeout.

  python -m bridge.serial_bridge --fake --web          page on http://localhost:8081
  python -m bridge.serial_bridge --fake --web 9000     another port
  add --web-host 0.0.0.0 to reach it from a phone on the same network

The keyboard keys of --fake keep working alongside the page.

HTTP:
  GET  /          the console page (?embed = compact layout for the dashboard iframe)
  GET  /events    Server-Sent Events, one serial line per event:
                    "> STATUS LOCK"  bridge -> board (what the firmware would receive)
                    "< KNOB 512"     board -> bridge (so every open page shows the same knob)
                  on connect: "< KNOB n" and the latest STATUS / ATT / SERVO lines
  POST /input     body "BTN FLARE" or "KNOB <0-1023>" (what the firmware would send)
"""
import http.server
import pathlib
import queue
import re
import socketserver
import threading

from bridge.serial_bridge import FakeBoard, log

STATIC = pathlib.Path(__file__).resolve().parent / "static"
INPUT_RE = re.compile(r"^(BTN FLARE|KNOB (\d{1,4}))$")


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class WebBoard(FakeBoard):
    """FakeBoard (keyboard) plus an HTTP page that plays the board."""

    def __init__(self, rx, port=8081, host="127.0.0.1", verbose=False, keyboard=True):
        self.clients = set()
        self.clients_lock = threading.Lock()
        self.latest = {}             # kind -> last line sent to the board (STATUS / ATT / SERVO)
        super().__init__(rx, verbose, keyboard=keyboard)
        self.httpd = _Server((host, port), self._handler())
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="webconsole").start()
        shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
        log(f"[web] virtual console on http://{shown}:{port}/" + ("  (all interfaces)" if host == "0.0.0.0" else ""))

    # --- fan-out to open pages
    def _broadcast(self, ev):
        with self.clients_lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(ev)
                except queue.Full:   # a stuck page: drop it, it will reconnect
                    self.clients.discard(q)

    def emit(self, line):
        super().emit(line)
        if line.startswith("KNOB "):
            self._broadcast("< " + line)

    def write(self, line):
        super().write(line)
        self.latest[line.split(" ", 1)[0]] = line
        self._broadcast("> " + line)

    def press(self, line):
        """A line typed on the page, already validated."""
        if line.startswith("KNOB "):
            self.knob = max(0, min(1023, int(line.split()[1])))
            line = f"KNOB {self.knob}"
        self.emit(line)

    def close(self):
        self.httpd.shutdown()

    # --- HTTP
    def _handler(self):
        board = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "console.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/events":
                    return self._events()
                self._send(404, b"not found")

            def do_POST(self):
                if self.path.split("?")[0] != "/input":
                    return self._send(404, b"not found")
                n = min(int(self.headers.get("Content-Length") or 0), 64)
                line = self.rfile.read(n).decode(errors="ignore").strip().upper()
                if not INPUT_RE.match(line):
                    return self._send(400, b"expected 'BTN FLARE' or 'KNOB <0-1023>'")
                board.press(line)
                self._send(204, b"")

            def _events(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = queue.Queue(maxsize=500)
                for ev in [f"< KNOB {board.knob}"] + ["> " + board.latest[k] for k in ("STATUS", "ATT", "SERVO")
                                                     if k in board.latest]:
                    q.put(ev)
                with board.clients_lock:
                    board.clients.add(q)
                try:
                    while True:
                        try:
                            ev = q.get(timeout=10)
                        except queue.Empty:
                            ev = None
                        self.wfile.write(b": ping\n\n" if ev is None else b"data: " + ev.encode() + b"\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with board.clients_lock:
                        board.clients.discard(q)

        return H
