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
one JSON line per frame over UDP to 127.0.0.1:5005 for the dashboard:
  {"frame", "t", "ra", "dec", "roll", "fov", "rate_dps", "exposure", "glare", "mode", "w", "h"}
"mode" is "sky", "calib" or "paused". ra/dec/roll are tetra3's convention, at mid-exposure.

The same commands are accepted as text lines over UDP on 127.0.0.1:5006, so
the serial bridge can drive it:  flare | rate <deg/s> | glare 0|1 | calib 0|1 | pause 0|1
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
        self.glare = False
        self.calib = False
        self.paused = False
        self.frame = 0
        self.flare()

    # ---- commands (keyboard and UDP share these) ----
    def flare(self):
        self.att = random_attitude(self.rng)
        axis = self.rng.normal(size=3)
        self.axis = axis / np.linalg.norm(axis)
        self.glare_corner = self.rng.choice([0.0, 1.0], 2)
        print(f"[flare] new hidden attitude; tumble {self.rate_dps:.1f} deg/s")

    def set_rate(self, dps):
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
        else:
            print(f"[udp] unknown command: {line!r}")

    # ---- per-frame ----
    def step(self, dt):
        omega = self.axis * math.radians(self.rate_dps)
        if not self.paused:
            self.att = self.att * type(self.att).from_rotvec(omega * dt)
            self.att = type(self.att).from_quat(self.att.as_quat())  # renormalise
        if self.calib:
            return self.calib_img
        img = self.sky.render(self.att, omega if not self.paused else None, self.a.exposure)
        if self.glare:
            self.sky.glare(img, corner=self.glare_corner)
        return SkyRenderer.to_u8(img, self.a.noise, self.rng)

    def truth(self):
        ra, dec, roll = to_radecroll(self.att)
        return {"frame": self.frame, "t": time.time(), "ra": ra, "dec": dec, "roll": roll,
                "fov": self.a.fov, "rate_dps": 0.0 if self.paused else self.rate_dps,
                "exposure": self.a.exposure, "glare": self.glare,
                "mode": "calib" if self.calib else "paused" if self.paused else "sky",
                "w": self.a.width, "h": self.a.height}


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
    ap.add_argument("--window", action="store_true", help="windowed instead of full screen")
    ap.add_argument("--truth-port", type=int, default=5005)
    ap.add_argument("--cmd-port", type=int, default=5006)
    args = ap.parse_args()

    sim = Sim(args)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inp.bind(("127.0.0.1", args.cmd_port))
    inp.setblocking(False)

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
        out.sendto(json.dumps(sim.truth()).encode(), ("127.0.0.1", args.truth_port))
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
        elif key == ord("s"):
            rec = ROOT / "recordings"
            rec.mkdir(exist_ok=True)
            path = rec / f"sky_{sim.frame:06d}.png"
            cv2.imwrite(str(path), img)
            (path.with_suffix(".json")).write_text(json.dumps(sim.truth()))
            print("saved", path)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
