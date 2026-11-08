"""Live sky simulator: full-screen star field at a hidden, tumbling attitude.

Run:  .venv/bin/python -m sim.live            (full screen)
      .venv/bin/python -m sim.live --window   (windowed, for development)

Keys (window must have focus):
  space / f   solar flare: jump to a new random attitude and tumble axis
  + / -       tumble rate up / down by 1 deg/s        0   stop tumbling
  g           glare on/off                            c   calibration checkerboard on/off
  p           pause                                   s   save the current frame to recordings/
  q / esc     quit

The truth never goes on screen (the camera would see it). It is published as
one JSON line per frame over UDP to 127.0.0.1:5005 for the dashboard, and the
same packet to 127.0.0.1:5009 for camera.solve_live (one listener per port):
  {"frame", "t", "ra", "dec", "roll", "fov", "rate_dps", "omega", "exposure", "glare",
   "glare_corner", "flash", "mode", "w", "h"}
"mode" is "sky", "calib" or "paused". ra/dec/roll are tetra3's convention, at mid-exposure.
"omega" is the body-frame angular velocity in deg/s ([0,0,0] when paused); with
"glare_corner" it lets solve_live --follow-sim redraw exactly this frame. "flash" is the
flare whiteout added to every pixel (0..255): a flare blinds the sensor for
--flash-hold + --flash-fade s before the new sky shows through.

The same commands are accepted as text lines over UDP on 127.0.0.1:5006, so
the serial bridge can drive it:  flare | rate <deg/s> | glare 0|1 | calib 0|1 | pause 0|1

--planets: the spacecraft also has a hidden position in the Solar System (random, 0.7-2 AU from
the Sun near the ecliptic; JPL DE421 via sim.planets, data/de421.bsp). Mercury..Saturn and the
Moon are drawn at their apparent directions (light time included) in the stars' blob style; the
Sun is never drawn. Extra truth fields: "pos_km" (hidden heliocentric ICRF position, km), "jd"
(TDB Julian date), "planets" ([[name, ra, dec, mag], ...] as drawn, so --follow-sim and the
dashboard can redraw the screen). Extra commands (UDP and keys):
  point <ra> <dec> [roll]   hold this attitude (deg; tumble stops)    pos random | n key: new hidden position
  slew <ra> <dec> [roll] [deg/s]   turn there smoothly at that rate (default --slew-rate), then hold
                            (tumble stops; a 'rate', 'point' or flare cancels it). Truth "omega" is the
                            slew's body rate while turning, "slewing" true; "rate_dps" stays the tumble.
--flare-moves: a flare also picks a new hidden position (so the navigator has to find it again).
"""
import argparse
import json
import math
import pathlib
import socket
import time

import cv2
import numpy as np

from sim.sky import SkyRenderer, checkerboard, random_attitude, to_radecroll

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIN = "Lost in Space - sky"


class Sim:
    def __init__(self, args):
        self.a = args
        self.rng = np.random.default_rng(args.seed)
        self.sky = SkyRenderer(args.width, args.height, args.fov)
        self.calib_img, self.calib_corners = checkerboard(args.width, args.height)
        self.rate_dps = args.rate
        self.slew, self.omega_now = None, np.zeros(3)   # slew: (target attitude, rad/s) while turning
        self.glare = False
        self.calib = False
        self.paused = False
        self.frame = 0
        self.flash_t0 = None   # monotonic time of the last flare's whiteout
        self.flash = 0.0       # current whiteout level added to every pixel, 0..255
        self.eph = self.pos = self.jd = None
        self.planets, self.extra = [], None
        if args.planets:
            from sim import planets as pl
            self.eph, self.jd = pl.Ephemeris(), args.jd
            self.new_position()
        self.flare(blind=False)

    def new_position(self):
        from sim import planets as pl
        self.pos = pl.random_observer(self.rng, self.eph, self.jd)
        self.planets = pl.sky_list(self.eph.apparent(self.pos, self.jd), self.sky.mag_limit)
        self.extra = pl.extra_from_list(self.planets)
        print(f"[position] new hidden position, {np.linalg.norm(self.pos) / pl.AU_KM:.3f} AU from the Sun; "
              f"on screen when in view: {', '.join(p[0] for p in self.planets) or 'nothing'}")

    def point(self, ra, dec, roll=0.0):
        from sim.sky import from_radecroll
        self.att = from_radecroll(ra, dec, roll)
        self.rate_dps, self.slew = 0.0, None
        print(f"[point] RA {ra:.2f} Dec {dec:.2f} Roll {roll:.1f}")

    def start_slew(self, ra, dec, roll=0.0, dps=None):
        """Turn to (ra, dec, roll) about the shortest axis at dps deg/s, then hold (like reaction wheels)."""
        from sim.sky import from_radecroll
        dps = float(dps or self.a.slew_rate)
        self.slew, self.rate_dps = (from_radecroll(ra, dec, roll), math.radians(max(0.5, dps))), 0.0
        ang = math.degrees((self.att.inv() * self.slew[0]).magnitude())
        print(f"[slew] to RA {ra:.2f} Dec {dec:.2f} Roll {roll:.1f}: {ang:.1f} deg at {dps:.1f} deg/s")

    # ---- commands (keyboard and UDP share these) ----
    def flare(self, blind=True):
        """Jump to a new random attitude. blind: white out the sensor first (the flare saturates
        it: full white for --flash-hold s, then fading over --flash-fade s), so the tracker visibly
        loses lock before re-acquiring."""
        if blind and self.a.flash_hold + self.a.flash_fade > 0:
            self.flash_t0 = time.monotonic()
        self.att, self.slew = random_attitude(self.rng), None
        axis = self.rng.normal(size=3)
        self.axis = axis / np.linalg.norm(axis)
        self.glare_corner = self.rng.choice([0.0, 1.0], 2)
        print(f"[flare] new hidden attitude; tumble {self.rate_dps:.1f} deg/s")
        if blind and self.a.flare_moves and self.eph is not None:
            self.new_position()   # demo convention: the flare also leaves us somewhere unknown

    def set_rate(self, dps):
        self.slew = None   # the console knob wins over a slew in progress
        self.rate_dps = float(np.clip(dps, 0, 60))
        print(f"[rate] {self.rate_dps:.1f} deg/s")

    def command(self, line):
        parts = line.strip().lower().split()
        if not parts:
            return
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else None)
        on = (lambda cur: not cur if arg is None else arg in ("1", "on", "true"))
        if cmd == "flare":
            self.flare()
        elif cmd == "rate" and arg is not None:
            self.set_rate(float(arg))
        elif cmd == "glare":
            self.glare = on(self.glare)
        elif cmd == "calib":
            self.calib = on(self.calib)
        elif cmd == "pause":
            self.paused = on(self.paused)
        elif cmd == "point" and len(parts) >= 3:
            try:
                self.point(*(float(v) for v in parts[1:4]))
            except ValueError:
                print(f"[udp] bad point command: {line!r}")
        elif cmd == "slew" and len(parts) >= 3:
            try:
                v = [float(x) for x in parts[1:5]]
                self.start_slew(*v[:3], *(v[3:4] or [None]))
            except ValueError:
                print(f"[udp] bad slew command: {line!r}")
        elif cmd == "pos" and self.eph is not None:
            self.new_position()
        else:
            print(f"[udp] unknown command: {line!r}")

    # ---- per-frame ----
    def step(self, dt):
        omega = self.axis * math.radians(self.rate_dps)
        if self.slew is not None and not self.paused:
            target, rate = self.slew
            rv = (self.att.inv() * target).as_rotvec()   # body-frame rotation still to go
            ang = float(np.linalg.norm(rv))
            if ang <= rate * dt:
                self.att, self.slew, omega = target, None, np.zeros(3)
                print("[slew] arrived")
            else:
                omega = rv / ang * rate
                self.att = self.att * type(self.att).from_rotvec(omega * dt)
        elif not self.paused:
            self.att = self.att * type(self.att).from_rotvec(omega * dt)
            self.att = type(self.att).from_quat(self.att.as_quat())  # renormalise
        self.omega_now = omega   # body rate this frame (rad/s): tumble or slew
        self.flash = self.flash_level()
        if self.calib:
            return self.calib_img
        img = self.sky.render(self.att, omega if not self.paused else None, self.a.exposure, extra=self.extra)
        if self.glare:
            self.sky.glare(img, corner=self.glare_corner)
        if self.flash > 0:
            img += self.flash
        return SkyRenderer.to_u8(img, self.a.noise, self.rng)

    def flash_level(self):
        if self.flash_t0 is None:
            return 0.0
        t = time.monotonic() - self.flash_t0
        hold, fade = self.a.flash_hold, self.a.flash_fade
        if t < hold:
            return 255.0
        if fade > 0 and t < hold + fade:
            return 255.0 * (1 - (t - hold) / fade)
        self.flash_t0 = None
        return 0.0

    def truth(self):
        ra, dec, roll = to_radecroll(self.att)
        return {"frame": self.frame, "t": time.time(), "ra": ra, "dec": dec, "roll": roll,
                "fov": self.a.fov, "rate_dps": 0.0 if self.paused else self.rate_dps,
                "omega": [0.0, 0.0, 0.0] if self.paused else np.degrees(self.omega_now).tolist(),
                "slewing": self.slew is not None,
                "exposure": self.a.exposure, "glare": self.glare,
                "glare_corner": [float(v) for v in self.glare_corner],
                "flash": round(self.flash, 1),
                "mode": "calib" if self.calib else "paused" if self.paused else "sky",
                "w": self.a.width, "h": self.a.height,
                **({"pos_km": [round(float(v), 1) for v in self.pos], "jd": self.jd, "planets": self.planets}
                   if self.pos is not None else {})}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fov", type=float, default=50.0, help="horizontal field of view, deg (tetra3 db: 30-70)")
    ap.add_argument("--rate", type=float, default=0.0, help="initial tumble rate, deg/s")
    ap.add_argument("--exposure", type=float, default=0.1, help="simulated exposure for motion blur, s")
    ap.add_argument("--noise", type=float, default=0.0, help="Gaussian noise sigma (pure-sim mode only)")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--flash-hold", type=float, default=1.0, help="flare whiteout at full white, s (0 = none)")
    ap.add_argument("--flash-fade", type=float, default=0.5, help="flare whiteout fade back to the sky, s")
    ap.add_argument("--window", action="store_true", help="windowed instead of full screen")
    ap.add_argument("--truth-port", type=int, default=5005, help="truth for the dashboard")
    ap.add_argument("--solver-truth-port", type=int, default=5009,
                    help="same truth for camera.solve_live (0 = don't send)")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--slew-rate", type=float, default=15.0, help="default rate for the 'slew' command, deg/s")
    ap.add_argument("--share-frames", action=argparse.BooleanOptionalAction, default=True,
                    help="put each shown frame in shared memory for solve_live --follow-sim (no redraw)")
    ap.add_argument("--planets", action="store_true",
                    help="hidden position in the Solar System; draw the planets (needs data/de421.bsp)")
    ap.add_argument("--flare-moves", action="store_true",
                    help="--planets: a flare also moves the spacecraft to a new hidden position (demo convention; "
                         "a real flare only changes attitude)")
    ap.add_argument("--jd", type=float, default=2461352.5, help="--planets: epoch, TDB Julian date (2026-11-08)")
    args = ap.parse_args()

    sim = Sim(args)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inp.bind(("127.0.0.1", args.cmd_port))
    inp.setblocking(False)

    bus = None
    if args.share_frames:
        from sim.framebus import FrameWriter
        try:
            bus = FrameWriter(f"lis_{args.cmd_port}", args.width, args.height)
            print(f"[frames] sharing frames in shared memory '{bus.name}' (solve_live --follow-sim reads them)")
        except Exception as e:
            print(f"[frames] shared memory unavailable ({e}); solve_live will redraw from truth")
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    if args.window:
        cv2.resizeWindow(WIN, args.width // 2, args.height // 2)
    else:
        cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    period = 1.0 / args.fps
    last = time.perf_counter()
    fps_t, fps_n = last, 0
    while True:
        now = time.perf_counter()
        dt, last = now - last, now
        while True:
            try:
                sim.command(inp.recv(256).decode(errors="ignore"))
            except BlockingIOError:
                break

        img = sim.step(dt)
        cv2.imshow(WIN, img)
        if bus is not None:
            bus.write(sim.frame, img)   # before the truth packet, so the solver finds the frame
        tr = sim.truth()
        if bus is not None:
            tr["shm"] = bus.name
        pkt = json.dumps(tr).encode()
        out.sendto(pkt, ("127.0.0.1", args.truth_port))
        if args.solver_truth_port:
            out.sendto(pkt, ("127.0.0.1", args.solver_truth_port))
        sim.frame += 1

        fps_n += 1
        if now - fps_t > 5:
            t = sim.truth()
            print(f"[{fps_n / (now - fps_t):4.1f} fps] truth RA {t['ra']:6.2f} Dec {t['dec']:6.2f} "
                  f"Roll {t['roll']:6.1f}  rate {t['rate_dps']:.1f} deg/s")
            fps_t, fps_n = now, 0

        wait = max(1, int((period - (time.perf_counter() - now)) * 1000))
        key = cv2.waitKey(wait) & 0xFF
        if key in (ord("q"), 27):
            break
        elif key in (ord(" "), ord("f")):
            sim.flare()
        elif key in (ord("+"), ord("=")):
            sim.set_rate(sim.rate_dps + 1)
        elif key in (ord("-"), ord("_")):
            sim.set_rate(sim.rate_dps - 1)
        elif key == ord("0"):
            sim.set_rate(0)
        elif key == ord("g"):
            sim.glare = not sim.glare
        elif key == ord("c"):
            sim.calib = not sim.calib
        elif key == ord("p"):
            sim.paused = not sim.paused
        elif key == ord("n") and sim.eph is not None:
            sim.new_position()
        elif key == ord("s"):
            rec = ROOT / "recordings"
            rec.mkdir(exist_ok=True)
            path = rec / f"sky_{sim.frame:06d}.png"
            cv2.imwrite(str(path), img)
            (path.with_suffix(".json")).write_text(json.dumps(sim.truth()))
            print("saved", path)
    cv2.destroyAllWindows()
    if bus is not None:
        bus.close()


if __name__ == "__main__":
    main()
