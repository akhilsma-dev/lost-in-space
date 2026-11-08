"""Live attitude solve: camera frame -> centroids -> screen coords -> tetra3 -> LOCK / LOST.

Real camera (sim.live full screen on the box display, data/calib.json from camera.calibrate):
      .venv/bin/python -m camera.solve_live --index 0 --show
No hardware, perfect screen pixels straight from sim.sky:
      .venv/bin/python -m camera.solve_live --sim --rate 2 --frames 100
No hardware, through a synthetic webcam (keystone, blur, noise, JPEG) + auto-calibration:
      .venv/bin/python -m camera.solve_live --sim --sim-camera --frames 100
No hardware, following the running sim.live (fake-mode demo: console flare/tumble reach the solver):
      .venv/bin/python -m camera.solve_live --follow-sim

Pipeline: tetra3's blob finder runs on the raw camera frame; only the centroids
are warped through the camera->screen homography (cheaper and more exact than
warping the image). In screen pixels the geometry is an ideal pinhole with the
sim's known FOV, so tetra3 gets a tight fov_estimate.

Status: LOCK when tetra3 solves with >= --min-matches stars and false-match
probability Prob <= --prob-max; otherwise LOST. confidence (0..1) =
min(1, -log10(Prob)/20) * max(0, 1 - RMSE/--rmse-max); 0 when LOST.

Truth: sim.live publishes truth on UDP 5009 (the dashboard has 5005); each frame is
paired with the truth packet received closest to (capture time - --latency) and the
pointing error is printed. In --sim and --follow-sim modes truth is exact.

Output: one JSON line per frame on UDP 127.0.0.1:5008 (dashboard), and the same
line on 5007 (bridge.serial_bridge -> console OLED/LED/servos; --bridge-port 0 = off)
  {"t","ra","dec","roll","fov","status","confidence","matches","prob","rmse","solve_ms",
   "n_centroids","centroids":[[x,y],...],"truth":{...}|null,"err_arcsec","roll_err_deg"}
  centroids are in screen pixels (x right, y down), brightest first, max 100.
"""
import argparse
import json
import math
import socket
import time

import cv2
import numpy as np

from camera.common import (BRIDGE_PORT, CALIB, ESTIMATE_PORT, RECORDINGS, TRUTH_PORT, TruthListener, centroids,
                           load_calib, load_tetra3, pointing_error_arcsec, roll_error_deg, warp_points)
from sim.sky import SkyRenderer, checkerboard, from_radecroll, random_attitude, to_radecroll


# ---------- frame sources: read() -> (t, gray, truth_dict_or_None) ----------
class SimSource:
    """Renders the hidden-attitude sky directly from sim.sky (optionally through FakeCamera)."""

    def __init__(self, a):
        self.a = a
        self.rng = np.random.default_rng(a.seed)
        self.sw, self.sh, self.fov = a.screen_w, a.screen_h, a.fov
        self.sky = SkyRenderer(self.sw, self.sh, self.fov)
        self.t = time.time()
        self.next_flare = self.t
        self.H = None
        self.cam = None
        if a.sim_camera:
            from camera.calibrate import fit
            from camera.fakecam import FakeCamera
            self.cam = FakeCamera(self.sw, self.sh, seed=a.seed or 0)
            r = fit(self.cam.view(checkerboard(self.sw, self.sh)[0]), self.sw, self.sh)
            self.H = r["H"]
            print(f"[sim-camera] auto-calibrated on fake checkerboard: RMS {r['rms']:.3f} screen px")

    def _flare(self):
        self.att = random_attitude(self.rng)
        ax = self.rng.normal(size=3)
        self.omega = ax / np.linalg.norm(ax) * math.radians(self.a.rate)

    def read(self):
        if self.t >= self.next_flare:
            self._flare()
            self.next_flare = self.t + (self.a.flare_every or 1e12)
        dt = 1.0 / self.a.fps
        self.t += dt
        self.att = self.att * type(self.att).from_rotvec(self.omega * dt)
        img = SkyRenderer.to_u8(self.sky.render(self.att, self.omega, self.a.exposure))
        if self.cam is not None:
            img = self.cam.view(img)
        ra, dec, roll = to_radecroll(self.att)
        return self.t, img, {"ra": ra, "dec": dec, "roll": roll, "fov": self.fov, "mode": "sky",
                             "rate_dps": self.a.rate, "w": self.sw, "h": self.sh}

    def close(self):
        pass


class FollowSource:
    """Redraws whatever sim.live is showing right now, from its truth packets (attitude,
    body rate, exposure, glare). No camera: the solver sees exactly the screen pixels, and
    console flare / tumble commands to sim.live reach it. Waits for each new sim frame."""

    def __init__(self, a):
        self.a = a
        self.truth = TruthListener(a.truth_port)
        print(f"[follow-sim] waiting for sim.live truth on UDP {a.truth_port} ...")
        first = self.truth.wait(timeout=1e9)
        self.sw, self.sh, self.fov = first["w"], first["h"], first["fov"]
        self.sky = SkyRenderer(self.sw, self.sh, self.fov)
        self.H = None
        self.last_frame = None
        print(f"[follow-sim] following sim.live: {self.sw}x{self.sh}, FOV {self.fov} deg")

    def read(self):
        while True:
            tr = self.truth.latest()
            if tr is not None and tr.get("frame") != self.last_frame:
                break
            time.sleep(0.005)
        self.last_frame = tr.get("frame")
        if tr.get("mode") == "calib":
            return time.time(), np.zeros((self.sh, self.sw), np.uint8), tr
        if (tr["w"], tr["h"], tr["fov"]) != (self.sw, self.sh, self.fov):
            self.sw, self.sh, self.fov = tr["w"], tr["h"], tr["fov"]
            self.sky = SkyRenderer(self.sw, self.sh, self.fov)
        att = from_radecroll(tr["ra"], tr["dec"], tr["roll"])
        omega = np.radians(tr.get("omega") or [0.0, 0.0, 0.0])
        img = self.sky.render(att, omega if np.any(omega) else None, tr.get("exposure", 0.0))
        if tr.get("glare"):
            self.sky.glare(img, corner=tuple(tr.get("glare_corner") or (1.0, 1.0)))
        return time.time(), SkyRenderer.to_u8(img), tr

    def close(self):
        pass


class CameraSource:
    def __init__(self, a):
        from camera.capture import Camera
        self.a = a
        self.cam = Camera(a.index, a.cam_w, a.cam_h)
        print(self.cam.summary())
        if a.no_calib:
            self.H, self.sw, self.sh = None, self.cam.w, self.cam.h
            print("[warn] --no-calib: solving in raw camera pixels with a loose FOV estimate")
        else:
            c = load_calib(a.calib)
            if (c["cam_w"], c["cam_h"]) != (self.cam.w, self.cam.h):
                raise SystemExit(f"calibration was made at {c['cam_w']}x{c['cam_h']} but the camera now gives "
                                 f"{self.cam.w}x{self.cam.h}; re-run camera.calibrate")
            self.H, self.sw, self.sh = c["H"], c["screen_w"], c["screen_h"]
            print(f"calibration {a.calib}: RMS {c['rms_screen_px']:.3f} screen px, screen {self.sw}x{self.sh}")
        self.truth = None if a.no_truth else TruthListener(a.truth_port)
        self.fov = a.fov

    def read(self):
        t, g = self.cam.read_avg(self.a.avg) if self.a.avg > 1 else self.cam.read()
        tr = self.truth.at(t - self.a.latency) if self.truth else None
        return t, g, tr

    def close(self):
        self.cam.release()


# ---------- solve ----------
class Solver:
    def __init__(self, a, src):
        self.a, self.src = a, src
        self.t3 = load_tetra3()

    def solve(self, gray, fov):
        c_cam = centroids(gray, max_returned=self.a.max_centroids, max_area=self.a.max_area)
        if self.src.H is not None:
            c = warp_points(c_cam, self.src.H)
            keep = (c[:, 0] > -0.5) & (c[:, 0] < self.src.sw - 0.5) & (c[:, 1] > -0.5) & (c[:, 1] < self.src.sh - 0.5)
            c, c_cam = c[keep], c_cam[keep]
            fov_kw = {"fov_estimate": fov, "fov_max_error": self.a.fov_max_error}
        else:
            c = c_cam
            fov_kw = {"fov_estimate": fov, "fov_max_error": 20} if fov else {}
        t0 = time.perf_counter()
        res = (self.t3.solve_from_centroids(c[:, ::-1], (self.src.sh, self.src.sw), return_matches=True,
                                            solve_timeout=self.a.timeout_ms, **fov_kw)
               if len(c) >= 4 else {"RA": None})
        ms = (time.perf_counter() - t0) * 1000
        return c, c_cam, res, ms

    def status(self, res):
        if res.get("RA") is None:
            return "LOST", 0.0
        prob, rmse, n = float(res["Prob"]), float(res["RMSE"]), int(res["Matches"])
        if n < self.a.min_matches or prob > self.a.prob_max:
            return "LOST", 0.0
        conf = min(1.0, -math.log10(max(prob, 1e-300)) / 20) * max(0.0, 1 - rmse / self.a.rmse_max)
        return "LOCK", round(conf, 3)


def annotate(gray, c_cam, res, H, status, line):
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for x, y in c_cam:
        cv2.circle(vis, (int(round(x)), int(round(y))), 9, (0, 200, 255), 1)
    if res.get("RA") is not None and H is not None and len(res.get("matched_centroids", [])):
        m = warp_points(np.asarray(res["matched_centroids"])[:, ::-1], np.linalg.inv(H))
        for x, y in m:
            cv2.circle(vis, (int(round(x)), int(round(y))), 16, (60, 230, 110), 2)
    col = (60, 230, 110) if status == "LOCK" else (80, 90, 240)
    cv2.rectangle(vis, (10, 10), (min(vis.shape[1] - 10, 1500), 60), (0, 0, 0), -1)
    cv2.putText(vis, line, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)
    return vis


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src_g = ap.add_argument_group("source")
    src_g.add_argument("--sim", action="store_true", help="render frames from sim.sky instead of a camera")
    src_g.add_argument("--sim-camera", action="store_true", help="with --sim: pass frames through camera.fakecam")
    src_g.add_argument("--follow-sim", action="store_true",
                       help="no camera: redraw the frames the running sim.live shows (from its truth packets)")
    src_g.add_argument("--index", type=int, default=0)
    src_g.add_argument("--cam-w", type=int, default=1920)
    src_g.add_argument("--cam-h", type=int, default=1080)
    src_g.add_argument("--avg", type=int, default=1, help="average N camera frames per solve (static scenes)")
    src_g.add_argument("--calib", default=str(CALIB))
    src_g.add_argument("--no-calib", action="store_true", help="skip the homography (expect worse results)")
    sim_g = ap.add_argument_group("--sim options")
    sim_g.add_argument("--screen-w", type=int, default=1920)
    sim_g.add_argument("--screen-h", type=int, default=1080)
    sim_g.add_argument("--rate", type=float, default=0.0, help="tumble rate, deg/s")
    sim_g.add_argument("--exposure", type=float, default=0.1)
    sim_g.add_argument("--fps", type=float, default=10.0, help="simulated frame rate (sets tumble per frame)")
    sim_g.add_argument("--flare-every", type=float, default=None, help="jump to a new attitude every N sim s")
    sim_g.add_argument("--seed", type=int, default=None)
    sol = ap.add_argument_group("solver")
    sol.add_argument("--fov", type=float, default=50.0, help="screen FOV, deg (overridden by sim truth packets)")
    sol.add_argument("--fov-max-error", type=float, default=2.0)
    sol.add_argument("--max-centroids", type=int, default=60)
    sol.add_argument("--max-area", type=int, default=100,
                     help="largest blob (px) kept as a star; tetra3 default 100. 5000 keeps motion-blur "
                          "streaks ('tetra3-tuned' in ml/out/benchmark.md: 100%% solves at 10 deg/s)")
    sol.add_argument("--timeout-ms", type=int, default=500)
    sol.add_argument("--min-matches", type=int, default=8)
    sol.add_argument("--prob-max", type=float, default=1e-6)
    sol.add_argument("--rmse-max", type=float, default=900.0, help="arcsec, for the confidence score")
    io = ap.add_argument_group("io")
    io.add_argument("--truth-port", type=int, default=TRUTH_PORT)
    io.add_argument("--no-truth", action="store_true")
    io.add_argument("--latency", type=float, default=0.08, help="camera latency for truth pairing, s")
    io.add_argument("--estimate-port", type=int, default=ESTIMATE_PORT, help="estimates for the dashboard")
    io.add_argument("--bridge-port", type=int, default=BRIDGE_PORT,
                    help="same estimates for bridge.serial_bridge (0 = don't send)")
    io.add_argument("--frames", type=int, default=0, help="stop after N frames (0 = run forever)")
    io.add_argument("--show", action="store_true", help="window with centroids / matches")
    io.add_argument("--save-shot", nargs="?", const="auto", default=None,
                    help="save an annotated frame (last LOCK, else last frame) to this path or recordings/")
    io.add_argument("--quiet", action="store_true", help="print only every 10th frame")
    a = ap.parse_args()

    src = FollowSource(a) if a.follow_sim else SimSource(a) if a.sim else CameraSource(a)
    solver = Solver(a, src)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stats = {"n": 0, "lock": 0, "err": [], "roll": [], "ms": [], "nc": [], "tp": 0}
    shot, shot_lock = None, False
    try:
        while not a.frames or stats["n"] < a.frames:
            t, gray, truth = src.read()
            if gray is None:
                print("frame grab failed")
                break
            if truth and truth.get("mode") == "calib":
                continue  # sim is showing the checkerboard
            fov = truth["fov"] if truth and "fov" in truth else a.fov
            c_scr, c_cam, res, ms = solver.solve(gray, fov)
            status, conf = solver.status(res)
            solved = res.get("RA") is not None
            msg = {"t": t, "ra": None, "dec": None, "roll": None, "fov": None, "status": status,
                   "confidence": conf, "matches": int(res.get("Matches") or 0),
                   "prob": float(res["Prob"]) if solved else None,
                   "rmse": float(res["RMSE"]) if solved else None, "solve_ms": round(ms, 1),
                   "n_centroids": len(c_scr), "centroids": np.round(c_scr[:100], 2).tolist(),
                   "truth": truth, "err_arcsec": None, "roll_err_deg": None}
            if solved:
                msg.update(ra=float(res["RA"]), dec=float(res["Dec"]), roll=float(res["Roll"]),
                           fov=float(res["FOV"]))
                if truth:
                    msg["err_arcsec"] = round(pointing_error_arcsec(msg["ra"], msg["dec"], truth["ra"], truth["dec"]), 1)
                    msg["roll_err_deg"] = round(roll_error_deg(msg["roll"], truth["roll"]), 3)
            pkt = json.dumps(msg).encode()
            out.sendto(pkt, ("127.0.0.1", a.estimate_port))
            if a.bridge_port:
                out.sendto(pkt, ("127.0.0.1", a.bridge_port))

            stats["n"] += 1
            stats["nc"].append(len(c_scr))
            stats["ms"].append(ms)
            if truth:
                stats["tp"] += 1
            if status == "LOCK":
                stats["lock"] += 1
                if msg["err_arcsec"] is not None:
                    stats["err"].append(msg["err_arcsec"])
                    stats["roll"].append(abs(msg["roll_err_deg"]))
            line = (f"{status:4s} conf {conf:4.2f} | {len(c_scr):3d} blobs {msg['matches']:3d} matched | "
                    + (f"RA {msg['ra']:7.2f} Dec {msg['dec']:6.2f} Roll {msg['roll']:6.1f} "
                       f"Prob {msg['prob']:.0e} RMSE {msg['rmse']:5.0f}\"" if solved else "no solution".ljust(62))
                    + f" | {ms:4.0f} ms"
                    + (f" | err {msg['err_arcsec']:6.0f}\" roll {msg['roll_err_deg']:+.2f} deg"
                       if msg["err_arcsec"] is not None else ""))
            if not a.quiet or stats["n"] % 10 == 1:
                print(line)
            if a.show or a.save_shot:
                if a.show or status == "LOCK" or not shot_lock:
                    vis = annotate(gray, c_cam, res, src.H, status, line)
                    if a.save_shot and (status == "LOCK" or not shot_lock):
                        shot, shot_lock = vis, status == "LOCK"
                if a.show:
                    cv2.imshow("solve_live", vis)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        break
    except KeyboardInterrupt:
        pass
    finally:
        src.close()

    n = stats["n"]
    if n:
        e = np.array(stats["err"])
        print(f"\n=== {n} frames: LOCK {stats['lock']}/{n} ({100 * stats['lock'] / n:.0f}%), "
              f"median {np.median(stats['nc']):.0f} blobs, median solve {np.median(stats['ms']):.0f} ms")
        if len(e):
            print(f"    pointing error vs truth (LOCK frames with truth): median {np.median(e):.0f}\", "
                  f"95% {np.percentile(e, 95):.0f}\", max {e.max():.0f}\"; "
                  f"roll median {np.median(stats['roll']):.3f} deg")
        elif not stats["tp"]:
            print("    no truth packets received (is sim.live running?)")
    if shot is not None:
        RECORDINGS.mkdir(exist_ok=True)
        path = a.save_shot if a.save_shot != "auto" else str(RECORDINGS / f"solve_{int(time.time())}.png")
        cv2.imwrite(path, shot)
        print("screenshot:", path)


if __name__ == "__main__":
    main()
