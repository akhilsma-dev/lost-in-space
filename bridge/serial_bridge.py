"""Serial bridge: Arduino mission console <-> sky simulator / solver.

  board  BTN FLARE        ->  UDP "flare"            -> sim/live.py (127.0.0.1:5006)
  board  KNOB <0-1023>    ->  UDP "rate <0..20>"     -> sim/live.py (only on meaningful change)
  board  BTN NAV          ->  UDP "whereami"         -> camera.solve_live (127.0.0.1:5011, --nav-scan-mode button)
  solver JSON on UDP 5007 ->  STATUS / STAGE / ATT / SERVO -> board (throttled to 10 Hz)

Solver JSON (one object per datagram or per line):
  {"status": "LOCK"|"SEARCH"|"LOST", "ra": deg, "dec": deg, "roll": deg}
optional "pan"/"tilt" (deg) override the servo mapping; optional "nav" (solve_live --nav) gives
"NAV <state>" lines for the console's WHERE AM I? button (IDLE, SCAN k/8, HOLD (spinning), WAIT (no lock),
FIX, NO FIX);
optional "stage" (e.g. "VERIFYING 3/6",
"DETUMBLING 12.0") goes to the board as "STAGE <text>". The virtual console shows it; the real
firmware does not know STAGE yet, so it is only sent to a real board with --stage-lines.

Servo mapping (--servo-mode):
  relative (default)  on entering LOCK the model centres (90, 90) and remembers that pointing;
                      afterwards pan/tilt show how far the boresight has turned since then
                      (pan = left/right along RA, tilt = up/down along Dec, roll ignored).
                      Good to about +-90 deg, then the servos sit at their limits. A flare or
                      a new LOCK re-centres. While SEARCH/LOST the model holds still.
  absolute            pan = ra / 2 (0..360 -> 0..180), tilt = 90 + dec. Swings end to end
                      whenever RA wraps through 0.

Protocol details: firmware/PROTOCOL.md

Usage:
  python -m bridge.serial_bridge                 # auto-detect /dev/cu.usbserial-* / usbmodem*
  python -m bridge.serial_bridge --port /dev/cu.usbserial-1410
  python -m bridge.serial_bridge --fake          # no hardware: keyboard plays the board
  python -m bridge.serial_bridge --fake --web    # no hardware: virtual console page on :8081 (+ keyboard)
  python -m bridge.serial_bridge --fake --truth-demo   # also feed sim truth (UDP 5005) back as a fake LOCK

Fake-mode keys (type then Enter):
  f / Enter   BTN FLARE         <n>  KNOB n (0-1023)      + / -  knob +/-100
  0           knob to 0          q   quit
"""
import argparse
import glob
import json
import math
import queue
import socket
import sys
import threading
import time

BAUD = 115200
RESET_WAIT_S = 2.0
SEND_HZ = 10.0
KEEPALIVE_S = 1.0
SOLVER_STALE_S = 3.0
FLARE_SETTLE_S = 0.3   # ignore solver data this long after a flare before re-centring


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def relative_pan_tilt(ra0, dec0, ra, dec):
    """Angles (deg) of pointing (ra, dec) seen from a frame looking at (ra0, dec0):
    pan = turn towards +RA (east), tilt = turn towards +Dec (north). No wrap-around at RA 0."""
    def vec(r, d):
        r, d = math.radians(r), math.radians(d)
        return (math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d))
    b, fwd = vec(ra, dec), vec(ra0, dec0)
    r0, d0 = math.radians(ra0), math.radians(dec0)
    east = (-math.sin(r0), math.cos(r0), 0.0)
    north = (-math.sin(d0) * math.cos(r0), -math.sin(d0) * math.sin(r0), math.cos(d0))
    dot = lambda u, v: u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    pan = math.degrees(math.atan2(dot(b, east), dot(b, fwd)))
    tilt = math.degrees(math.asin(max(-1.0, min(1.0, dot(b, north)))))
    return pan, tilt


# ------------------------------------------------------------------ boards
class SerialBoard:
    """Real board over pyserial. Reader thread puts received lines on `rx`; reconnects on error."""

    def __init__(self, port, rx):
        self.port_arg, self.rx = port, rx
        self.ser = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="serial").start()

    @staticmethod
    def detect():
        pats = ["/dev/cu.usbserial-*", "/dev/cu.usbmodem*", "/dev/ttyUSB*", "/dev/ttyACM*"]
        found = sorted(p for pat in pats for p in glob.glob(pat))
        if not found:
            try:
                from serial.tools import list_ports
                found = [p.device for p in list_ports.comports() if p.vid is not None]
            except Exception:
                pass
        return found[0] if found else None

    def _open(self):
        import serial
        port = self.port_arg or self.detect()
        if not port:
            return False
        ser = serial.Serial(port, BAUD, timeout=0.2)
        log(f"[serial] opened {port}; waiting {RESET_WAIT_S:.0f}s for board reset")
        time.sleep(RESET_WAIT_S)
        ser.reset_input_buffer()
        with self.lock:
            self.ser = ser
        self.write("PING")
        return True

    def _run(self):
        buf = b""
        while not self.stop.is_set():
            if self.ser is None:
                try:
                    if not self._open():
                        log("[serial] no board found (looking for /dev/cu.usbserial-* / usbmodem*); retrying")
                        time.sleep(2)
                        continue
                except Exception as e:
                    log(f"[serial] open failed: {e}; retrying")
                    time.sleep(2)
                    continue
            try:
                buf += self.ser.read(256)
            except Exception as e:
                log(f"[serial] read error: {e}; reconnecting")
                self._close()
                time.sleep(1)
                continue
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="ignore").strip()
                if line:
                    self.rx.put(line)

    def _close(self):
        with self.lock:
            try:
                if self.ser:
                    self.ser.close()
            except Exception:
                pass
            self.ser = None

    def write(self, line):
        with self.lock:
            if self.ser is None:
                return
            try:
                self.ser.write((line + "\n").encode())
            except Exception as e:
                log(f"[serial] write error: {e}")

    def close(self):
        self.stop.set()
        self._close()


class FakeBoard:
    """Keyboard pretends to be the board; prints what the board would receive."""

    def __init__(self, rx, verbose=False, keyboard=True):
        self.rx, self.verbose = rx, verbose
        self.knob = 0
        self.last_print = {}
        self.status = None
        self.emit("HELLO console 1 (fake)")
        self.emit("KNOB 0")
        if keyboard:
            threading.Thread(target=self._run, daemon=True, name="keyboard").start()

    def emit(self, line):
        """A line the board sends to the laptop."""
        self.rx.put(line)

    def _run(self):
        log("[fake] keys: f/Enter=FLARE  <0-1023>=knob  +/-=knob +-100  0=stop  q=quit")
        for raw in sys.stdin:
            s = raw.strip().lower()
            if s in ("", "f", "flare"):
                self.emit("BTN FLARE")
            elif s in ("+", "-"):
                self.knob = max(0, min(1023, self.knob + (100 if s == "+" else -100)))
                self.emit(f"KNOB {self.knob}")
            elif s.lstrip("k ").isdigit():
                self.knob = max(0, min(1023, int(s.lstrip("k "))))
                self.emit(f"KNOB {self.knob}")
            elif s in ("q", "quit", "exit"):
                break
            else:
                log(f"[fake] ? {s!r}")
        self.rx.put(None)   # stdin closed or quit -> stop the bridge

    def write(self, line):
        kind = line.split(" ", 1)[0]
        if kind == "STATUS":
            if line != self.status or self.verbose:
                self.status = line
                log(f"[board <] {line}")
            return
        now = time.monotonic()
        if self.verbose or now - self.last_print.get(kind, 0) >= 1.0:   # ATT/SERVO arrive at 10 Hz
            self.last_print[kind] = now
            log(f"[board <] {line}")

    def close(self):
        pass


# ------------------------------------------------------------------ bridge
class Bridge:
    def __init__(self, args, board, rx):
        self.args, self.board, self.rx = args, board, rx
        self.sim_addr = ("127.0.0.1", args.sim_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.last_rate = None
        self.state_lock = threading.Lock()
        self.solver = None          # latest solver dict
        self.solver_t = 0.0
        self.solver_new = False
        self.sent_status = None
        self.sent_status_t = 0.0
        self.sent_stage = None
        self.sent_nav = None
        self.servo_ref = None       # (ra, dec) the model was centred on
        self.ref_status = None      # last solver status seen by servo_target
        self.flare_t = -1e9

    # --- board -> sim
    def to_sim(self, msg):
        self.sock.sendto(msg.encode(), self.sim_addr)
        log(f"[sim >] {msg}")

    def knob_to_rate(self, n):
        n = max(0, min(1023, n))
        if n <= 8:                       # dead zone so "fully left" really means stopped
            return 0.0
        return round(n * self.args.max_rate / 1023.0, 1)

    def on_board_line(self, line):
        if line == "BTN FLARE":
            log("[board >] BTN FLARE")
            self.to_sim("flare")
            self.servo_ref = None       # re-centre the model on the next LOCK
            self.ref_status = None
            self.flare_t = time.monotonic()
        elif line == "BTN NAV":
            log("[board >] BTN NAV")
            self.sock.sendto(b"whereami", ("127.0.0.1", self.args.nav_port))
            log(f"[solver >] whereami (udp:{self.args.nav_port})")
        elif line.startswith("KNOB "):
            try:
                n = int(line.split()[1])
            except (IndexError, ValueError):
                return
            rate = self.knob_to_rate(n)
            last = self.last_rate
            if last is None or abs(rate - last) >= self.args.rate_step or (rate != last and rate in (0.0, self.args.max_rate)):
                self.last_rate = rate
                self.to_sim(f"rate {rate:g}")
        elif line.startswith(("HELLO", "PONG", "ERR")):
            log(f"[board >] {line}")

    # --- solver -> board
    def on_solver(self, d):
        if not isinstance(d, dict):
            return
        with self.state_lock:
            self.solver = d
            self.solver_t = time.monotonic()
            self.solver_new = True

    @staticmethod
    def norm_status(s):
        s = str(s or "").upper()
        if s.startswith("SEARCH"):
            return "SEARCH"
        return s if s in ("LOCK", "LOST") else None

    def tick(self):
        now = time.monotonic()
        with self.state_lock:
            d, fresh, new = self.solver, now - self.solver_t < SOLVER_STALE_S, self.solver_new
            self.solver_new = False
        if d is None or not fresh:
            return   # no solver -> board drops to NO LINK after its own timeout
        st = self.norm_status(d.get("status"))
        if st and (st != self.sent_status or now - self.sent_status_t >= KEEPALIVE_S):
            self.board.write(f"STATUS {st}")
            self.sent_status, self.sent_status_t = st, now
        stage = self.stage_line(d.get("stage"))
        if stage and self.args.stage_lines and stage != self.sent_stage:
            self.board.write(stage)
            self.sent_stage = stage
        nav = self.nav_line(d.get("nav"))
        if nav and self.args.stage_lines and nav != self.sent_nav:
            self.board.write(nav)
            self.sent_nav = nav
        if not new:
            return
        try:
            ra, dec, roll = float(d["ra"]), float(d["dec"]), float(d["roll"])
        except (KeyError, TypeError, ValueError):
            return
        self.board.write(f"ATT {ra:.2f} {dec:.2f} {roll:.1f}")
        if self.args.no_servo:
            return
        servo = self.servo_target(st, ra, dec, d)
        if servo:
            self.board.write(f"SERVO {servo[0]:.1f} {servo[1]:.1f}")

    @staticmethod
    def stage_line(stage):
        """'STAGE <text>' for the board: printable ASCII, upper case, fits the 63-char line limit."""
        if not stage:
            return None
        text = "".join(c for c in str(stage).upper() if 32 <= ord(c) < 127).strip()[:40]
        if text.startswith("COASTING"):
            text = "COASTING"   # not the seconds: one line per dropout, not one per frame
        return f"STAGE {text}" if text else None

    @staticmethod
    def nav_line(nav):
        """'NAV <state>' for the console's WHERE AM I? button, from solve_live's "nav" block."""
        if not isinstance(nav, dict) or not isinstance(nav.get("scan"), dict):
            return None
        sc = nav["scan"]
        if sc.get("active"):
            w = sc.get("wait") or ("spin" if sc.get("paused") else None)
            return {"spin": "NAV HOLD", "lock": "NAV WAIT"}.get(w) or f"NAV SCAN {sc.get('step', 0)}/{sc.get('n', 8)}"
        if nav.get("pos_km"):
            return "NAV FIX"
        return "NAV NO FIX" if nav.get("status") == "no fix" and sc.get("step") else "NAV IDLE"

    def servo_target(self, st, ra, dec, d):
        if "pan" in d and "tilt" in d:
            return float(d["pan"]), float(d["tilt"])
        if self.args.servo_mode == "absolute":
            return (ra % 360.0) / 2.0, 90.0 + dec
        # relative: reference = pointing when we (re-)entered LOCK
        if time.monotonic() - self.flare_t < FLARE_SETTLE_S:
            return None             # solver data may still be from before the flare
        entering_lock = st == "LOCK" and self.ref_status != "LOCK"
        if st:
            self.ref_status = st
        if entering_lock or (st == "LOCK" and self.servo_ref is None):
            self.servo_ref = (ra, dec)
            log(f"[servo] new reference RA {ra:.2f} Dec {dec:.2f} -> centre")
        if st != "LOCK" or self.servo_ref is None:
            return None             # SEARCH / LOST: hold the model where it is
        pan, tilt = relative_pan_tilt(*self.servo_ref, ra, dec)
        sp = -1.0 if self.args.flip_pan else 1.0
        stl = -1.0 if self.args.flip_tilt else 1.0
        return 90.0 + sp * pan, 90.0 + stl * tilt

    def run(self):
        period = 1.0 / SEND_HZ
        next_t = time.monotonic()
        while True:
            timeout = max(0.0, next_t - time.monotonic())
            try:
                line = self.rx.get(timeout=timeout)
                if line is None:
                    return
                self.on_board_line(line)
                continue
            except queue.Empty:
                pass
            self.tick()
            next_t += period
            if next_t < time.monotonic():
                next_t = time.monotonic() + period


def udp_json_listener(port, callback, transform=None):
    """Thread: receive JSON datagrams (one or more newline-separated objects) on 127.0.0.1:port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))

    def run():
        while True:
            data = s.recv(65536)
            for part in data.decode(errors="ignore").splitlines():
                part = part.strip()
                if not part:
                    continue
                try:
                    obj = json.loads(part)
                except json.JSONDecodeError:
                    log(f"[udp:{port}] bad JSON: {part[:80]!r}")
                    continue
                callback(transform(obj) if transform else obj)

    threading.Thread(target=run, daemon=True, name=f"udp{port}").start()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--fake", action="store_true", help="simulate the board from the keyboard")
    ap.add_argument("--web", nargs="?", type=int, const=8081, default=None, metavar="PORT",
                    help="with --fake: serve the virtual console page (default port 8081; see bridge/webconsole.py)")
    ap.add_argument("--web-host", default="127.0.0.1", help="interface for --web (0.0.0.0 = reachable from a phone)")
    ap.add_argument("--sim-port", type=int, default=5006, help="sim command port")
    ap.add_argument("--solver-port", type=int, default=5007, help="UDP port for solver status JSON")
    ap.add_argument("--max-rate", type=float, default=20.0, help="tumble rate at full knob, deg/s")
    ap.add_argument("--rate-step", type=float, default=0.5, help="min rate change worth sending, deg/s")
    ap.add_argument("--no-servo", action="store_true", help="don't send SERVO lines")
    ap.add_argument("--servo-mode", choices=["relative", "absolute"], default="relative",
                    help="how attitude maps to the model (see top of file)")
    ap.add_argument("--flip-pan", action="store_true", help="reverse pan direction (relative mode)")
    ap.add_argument("--flip-tilt", action="store_true", help="reverse tilt direction (relative mode)")
    ap.add_argument("--truth-demo", action="store_true",
                    help="testing only: listen to sim truth on UDP 5005 and treat it as a LOCKed solver "
                         "(conflicts with anything else bound to 5005, e.g. the dashboard)")
    ap.add_argument("--nav-port", type=int, default=5011,
                    help="solve_live command port: BTN NAV (WHERE AM I?) sends 'whereami' here")
    ap.add_argument("--stage-lines", action=argparse.BooleanOptionalAction, default=None,
                    help="send 'STAGE <text>' lines (acquisition stage) to the board. Default: on for --fake / "
                         "--web, off for a real board (its firmware answers 'ERR unknown STAGE')")
    ap.add_argument("--verbose", action="store_true", help="fake mode: print every line sent to the board")
    args = ap.parse_args()

    if args.stage_lines is None:
        args.stage_lines = args.fake
    rx = queue.Queue()
    if args.web is not None and not args.fake:
        ap.error("--web needs --fake (the virtual console replaces the board)")
    if args.web is not None:
        from bridge.webconsole import WebBoard
        board = WebBoard(rx, args.web, args.web_host, args.verbose, keyboard=sys.stdin.isatty())
    elif args.fake:
        board = FakeBoard(rx, args.verbose)
    else:
        board = SerialBoard(args.port, rx)
    bridge = Bridge(args, board, rx)
    udp_json_listener(args.solver_port, bridge.on_solver)
    log(f"[bridge] solver JSON on udp:{args.solver_port}, sim commands -> udp:{args.sim_port}")
    if args.truth_demo:
        udp_json_listener(5005, bridge.on_solver, lambda t: {**t, "status": "LOCK"})
        log("[bridge] truth-demo: forwarding sim truth from udp:5005 as LOCK")
    try:
        bridge.run()
    except KeyboardInterrupt:
        pass
    finally:
        board.close()
        log("[bridge] bye")


if __name__ == "__main__":
    main()
