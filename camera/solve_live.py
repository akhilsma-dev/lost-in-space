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

Status: a frame is good when tetra3 solves with >= --min-matches stars and
false-match probability Prob <= --prob-max. Acquisition (status SEARCH) goes through stages:
DETECTING n (too few stars) -> MATCHING -> VERIFYING k/N (every --verify-spacing s a new good solve
must agree with the previous verified attitude, dead-reckoned at the measured body rate) -> LOCK,
about (--verify-n - 1) * --verify-spacing s after the first match. Once locked, the first bad frame
gives LOST. confidence (0..1) = min(1, -log10(Prob)/20) * max(0, 1 - RMSE/--rmse-max); 0 unless LOCK.

Recovery (--recover, default on): after --recover-after s without a lock the tracker commands the
sim's tumble rate (UDP 5006 'rate <dps>', or the internal sim with --sim): DETUMBLING (ramp the
rate down) if tumbling, else SEARCH SLEW (a short rate burst to a new patch of sky). Never while
LOCKed. --no-recover turns it off.

Truth: sim.live publishes truth on UDP 5009 (the dashboard has 5005); each frame is
paired with the truth packet received closest to (capture time - --latency) and the
pointing error is printed. In --sim and --follow-sim modes truth is exact.

Position from planets (--nav; run sim.live --planets): after each LOCK, blobs that are not
catalogue stars become planet candidates (camera.navigate); their inertial directions build up
over frames and a background thread fixes the heliocentric position with an error ellipsoid.
--nav-scan S also drives the scan: every S seconds it sends sim.live "point <ra> <dec> <roll>"
(UDP 5006) for the next of 8 pointings along the ecliptic. Scan steps are only taken while LOCKed and
while the measured spin is below --nav-scan-max-rate (a tumble dialled in on the console knob is left
alone: 'point' would stop it). --nav-scan-mode button: no automatic scanning; a "whereami" datagram on
UDP --nav-cmd-port (5011; the console's WHERE AM I? button via the bridge) forgets old sightings and
runs one sweep of the 8 pointings; its sightings are kept until the next press. Each estimate packet then carries
  "nav": {"status": "collecting"|"no fix"|"fix", "n_cand", "jd", and with a fix: "pos_km",
          "cov_km2", "axes_km" (1-sigma), "bodies", "resid_rms_arcsec", "r_au", "earth_km",
          "earth_sigma_km", "age_s", "err_km" (vs the sim's hidden position, if known)}
      .venv/bin/python -m camera.solve_live --follow-sim --nav --nav-scan 3 [--nav-scan-mode button]
  "nav" also carries "scan": {"mode", "active", "step", "n", "paused"} (paused = spinning, waiting).

Output: one JSON line per frame on UDP 127.0.0.1:5008 (dashboard), and the same
line on 5007 (bridge.serial_bridge -> console OLED/LED/servos; --bridge-port 0 = off)
  {"t","ra","dec","roll","fov","status","stage","confidence","matches","prob","rmse","solve_ms",
   "n_centroids","centroids":[[x,y],...],"truth":{...}|null,"err_arcsec","roll_err_deg"}
  centroids are in screen pixels (x right, y down), brightest first, max 100.
Frames: the frame each estimate was solved from, downscaled to --frame-w and contrast-stretched for
display only, goes to UDP 127.0.0.1:5010 (dashboard camera panel; --frame-port 0 = off), at most
--frame-hz per second. One datagram: 4-byte big-endian header length, JSON header, JPEG bytes.
  header = {"t","w","h" (JPEG px),"scale" (JPEG px per camera px),"sw","sh" (screen px),
            "screen_to_cam": 3x3 or null (null = camera px are screen px),"cam_cents": [[x,y],...] (JPEG px),
            "est": {"status","ra","dec","roll","fov","centroids"}}
"""
import argparse
import json
import math
import socket
import struct
import time

import cv2
import numpy as np

from camera.common import (BRIDGE_PORT, CALIB, NAV_CMD_PORT, ESTIMATE_PORT, FRAME_PORT, RECORDINGS, TRUTH_PORT, TruthListener, centroids,
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
        self.axis = ax / np.linalg.norm(ax)
        self.omega = self.axis * math.radians(self.a.rate)

    def set_rate(self, dps):
        """Same as sim.live's 'rate <dps>' command (keeps the tumble axis)."""
        self.a.rate = float(dps)
        self.omega = self.axis * math.radians(self.a.rate)

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
    """Whatever sim.live is showing right now, frame by frame. No camera: the solver sees exactly the
    screen pixels, and console flare / tumble commands to sim.live reach it. The pixels come from the
    sim's shared-memory frame bus (sim/framebus.py, named in the truth packet's "shm") when there is one,
    else they are redrawn from the truth packet (attitude, body rate, exposure, glare, planets)."""

    def __init__(self, a):
        self.a = a
        self.truth = TruthListener(a.truth_port)
        print(f"[follow-sim] waiting for sim.live truth on UDP {a.truth_port} ...")
        first = self.truth.wait(timeout=1e9)
        self.sw, self.sh, self.fov = first["w"], first["h"], first["fov"]
        self.sky = SkyRenderer(self.sw, self.sh, self.fov)
        self.H = None
        self.last_frame = None
        self.bus, self.bus_name, self.render_ms = None, None, 0.0
        print(f"[follow-sim] following sim.live: {self.sw}x{self.sh}, FOV {self.fov} deg")

    def _bus_frame(self, tr):
        name = tr.get("shm") if not self.a.no_shm else None
        if name != self.bus_name:
            if self.bus is not None:
                self.bus.close()
            self.bus, self.bus_name = None, name
            if name:
                try:
                    from sim.framebus import FrameReader
                    self.bus = FrameReader(name)
                    print(f"[follow-sim] reading sim.live's frames from shared memory '{name}' (no redraw)")
                except Exception as e:
                    print(f"[follow-sim] shared memory '{name}' unusable ({e}); redrawing from truth")
        if self.bus is None or (self.bus.w, self.bus.h) != (self.sw, self.sh):
            return None
        return self.bus.read(int(tr["frame"]))

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
        _r0 = time.perf_counter()
        img = self._bus_frame(tr)
        if img is not None:
            self.render_ms = (time.perf_counter() - _r0) * 1000
            return time.time(), img, tr
        att = from_radecroll(tr["ra"], tr["dec"], tr["roll"])
        omega = np.radians(tr.get("omega") or [0.0, 0.0, 0.0])
        from sim.planets import extra_from_list   # planets on screen (sim.live --planets)
        img = self.sky.render(att, omega if np.any(omega) else None, tr.get("exposure", 0.0),
                              extra=extra_from_list(tr.get("planets")))
        if tr.get("glare"):
            self.sky.glare(img, corner=tuple(tr.get("glare_corner") or (1.0, 1.0)))
        if tr.get("flash"):
            img += float(tr["flash"])   # flare whiteout: the sensor is blinded
        out = SkyRenderer.to_u8(img)
        self.render_ms = (time.perf_counter() - _r0) * 1000
        return time.time(), out, tr

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
        self.acq = Acquisition(a)             # DETECTING -> MATCHING -> VERIFYING k/N -> LOCK
        # a rendered sky is black: a global background is enough and ~4x faster than tetra3's local
        # filter (43 vs 171 ms on 1080p, same accuracy). A real camera keeps the local filter (room light).
        fast = a.centroid_mode == "fast" or (a.centroid_mode == "auto" and (a.sim or a.follow_sim) and not a.sim_camera)
        self.centroid_kw = {"bg_sub_mode": "global_mean", "binary_open": False} if fast else {}
        print(f"[solver] star finder: {'fast (global background)' if fast else 'robust (local background)'}")
        self.t3 = load_tetra3()
        self.starnet = None
        if a.detector == "starnet":
            from ml import detect
            detect.load()
            detect.star_centroids(np.zeros((64, 64), np.uint8))   # warm-up: the first call is ~2 s on MPS
            self.starnet = detect
            print(f"[starnet] star finder on {detect._dev}")

    def starnet_centroids(self, gray):
        """StarNet works in screen pixels (that is what it was trained on): warp the camera frame to
        the screen first if there is a homography. Returns (screen xy, camera xy), brightest first."""
        H = self.src.H
        img = gray if H is None else cv2.warpPerspective(gray, H, (self.src.sw, self.src.sh))
        yx = self.starnet.star_centroids(img)
        c = (np.asarray(yx, np.float64).reshape(-1, 2) - 0.5)[:, ::-1].copy()  # tetra3 -> OpenCV convention
        return c, (c if H is None else warp_points(c, np.linalg.inv(H)))

    def solve(self, gray, fov):
        if self.starnet is not None:
            t0 = time.perf_counter()
            c, c_cam = self.starnet_centroids(gray)
            self.detect_ms = (time.perf_counter() - t0) * 1000
            fov_kw = {"fov_estimate": fov, "fov_max_error": self.a.fov_max_error}
            t0 = time.perf_counter()
            res = (self.t3.solve_from_centroids(c[:, ::-1], (self.src.sh, self.src.sw), return_matches=True,
                                                solve_timeout=self.a.timeout_ms, **fov_kw)
                   if len(c) >= 4 else {"RA": None})
            return c, c_cam, res, (time.perf_counter() - t0) * 1000
        c_cam = centroids(gray, max_returned=self.a.max_centroids, max_area=self.a.max_area, **self.centroid_kw)
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

    def good(self, res):
        if res.get("RA") is None:
            return None
        prob, rmse, n = float(res["Prob"]), float(res["RMSE"]), int(res["Matches"])
        if n < self.a.min_matches or prob > self.a.prob_max:
            return None
        return min(1.0, -math.log10(max(prob, 1e-300)) / 20) * max(0.0, 1 - rmse / self.a.rmse_max)

    def status(self, t, res, n_stars):
        """Acquisition state machine. Returns (status, confidence, stage).
        status is LOCK / SEARCH / LOST as before; stage says what the tracker is doing right now."""
        return self.acq.update(t, res, n_stars, self.good(res))


class Acquisition:
    """Lost-in-space acquisition with visible stages (times from the frame timestamps, so --sim runs
    at simulated speed):

      DETECTING n    fewer than --detect-min stars in the frame (whiteout, glare, blur)
      MATCHING       enough stars, no trustworthy pattern match yet
      RATE HIGH r    pattern matches, but the measured rate is above --acq-max-rate deg/s: too fast to
                     confirm a lost-in-space solution (tracking once LOCKed works at higher rates)
      VERIFYING k/N  a candidate attitude is being confirmed: every --verify-spacing s a new good solve
                     must agree within --verify-tol deg with the previous verified attitude dead-reckoned
                     forward at the body rate measured from consecutive solves. Disagreement restarts at
                     MATCHING; no good solve for --verify-timeout s also restarts.
      LOCK           N checks passed. Once locked, the first bad frame gives LOST (immediate), and so
                     does a solve that jumps more than --jump-tol deg from the previous frame's attitude
                     carried forward at the measured rate (a flare without whiteout, a false match).

    The first good solve is check 1, so LOCK comes (N-1) * spacing s after the first match."""

    def __init__(self, a):
        self.a = a
        self.locked = False
        self.k = 0                 # verification checks passed
        self.track = None          # dead-reckoned attitude, anchored at the last verified solve
        self.t_ver = None          # time of the last verified solve
        self.t_good = None         # time of the last good solve (any)
        self.prev = None           # (t, Rotation) of the previous good solve, for the rate estimate
        self.w = np.zeros(3)       # body rate estimate, rad/s
        self.t_prop = None         # time track was last propagated to
        self.lost_t = None         # when lock was last lost (or start), for recovery
        self.jump_ok_until = -1e18 # a commanded attitude change (nav-scan 'point') is expected until then

    def expect_jump(self, t, window=1.0):
        """We commanded the attitude (sim 'point'): the next jump is ours, not a lost track."""
        self.jump_ok_until = t + window

    def reset(self):
        self.k, self.track, self.t_ver, self.prev, self.t_prop = 0, None, None, None, None
        self.w = np.zeros(3)

    @staticmethod
    def angle_deg(r1, r2):
        return math.degrees((r1.inv() * r2).magnitude())

    def _propagate(self, t):
        if self.track is not None and self.t_prop is not None and t > self.t_prop:
            self.track = self.track * type(self.track).from_rotvec(self.w * (t - self.t_prop))
        self.t_prop = t

    def update(self, t, res, n_stars, conf):
        a = self.a
        if self.lost_t is None:
            self.lost_t = t
        att = from_radecroll(float(res["RA"]), float(res["Dec"]), float(res["Roll"])) if conf is not None else None

        jump, gap = 0.0, 0.0
        if att is not None:
            # body rate from consecutive good solves (frame-to-frame, so the tumble changing is followed);
            # jump = how far this solve is from the previous one carried forward at the old rate
            if t <= self.jump_ok_until:
                self.w = np.zeros(3)                          # commanded repoint: carry on from the new attitude
            elif self.prev is not None and 0.02 < t - self.prev[0] < 0.6:
                dt = gap = t - self.prev[0]
                jump = self.angle_deg(self.prev[1] * type(att).from_rotvec(self.w * dt), att)
                w = (self.prev[1].inv() * att).as_rotvec() / dt
                if np.linalg.norm(w) < math.radians(90):
                    self.w = w
            self.prev = (t, att)

        if self.locked:
            # the rate may change between frames (knob): allow --jump-rate deg/s of change over the gap
            tol = self.a.jump_tol + self.a.jump_rate * gap
            if conf is None or jump > tol:
                if conf is not None:
                    print(f"[track] attitude jumped {jump:.1f} deg in one frame (tol {tol:.1f}): LOST")
                self.locked, self.lost_t = False, t
                self.reset()
                return "LOST", 0.0, "LOST"
            return "LOCK", round(conf, 3), "LOCK"

        self._propagate(t)
        if att is not None:
            self.t_good = t
            rate = math.degrees(np.linalg.norm(self.w))
            if self.k == 0:                                   # first match: candidate, check 1
                self.k, self.track, self.t_ver = 1, att, t
            elif rate > a.acq_max_rate:                       # too fast to confirm: keep re-anchoring
                self.k, self.track, self.t_ver = 1, att, t
                return "SEARCH", 0.0, f"RATE HIGH {rate:.1f}"
            elif t - self.t_ver >= a.verify_spacing:          # time for the next check
                slop = a.verify_tol + 0.1 * math.degrees(np.linalg.norm(self.w)) * (t - self.t_ver)
                d = self.angle_deg(self.track, att)
                if d <= slop:
                    self.k += 1
                    self.track, self.t_ver = att, t           # re-anchor on the verified solve
                else:
                    print(f"[verify] check {self.k + 1} disagrees by {d:.1f} deg (tol {slop:.1f}): restart")
                    self.reset()
                    self.k, self.track, self.t_ver, self.prev, self.t_good = 1, att, t, (t, att), t
            if self.k >= a.verify_n:
                self.locked = True
                self.k, self.track, self.t_ver, self.t_prop = 0, None, None, None   # keep prev / w for tracking
                return "LOCK", round(conf, 3), "LOCK"
        elif self.k and t - (self.t_good or t) > a.verify_timeout:
            self.reset()                                      # candidate went stale

        if self.k:
            return "SEARCH", 0.0, f"VERIFYING {self.k}/{a.verify_n}"
        if n_stars < a.detect_min:
            return "SEARCH", 0.0, f"DETECTING {n_stars}"
        return "SEARCH", 0.0, "MATCHING"


class Recovery:
    """Lost-in-space recovery maneuvers, through the sim's existing 'rate <dps>' command (UDP 5006;
    in --sim mode the internal sim). Self-contained: --no-recover removes it entirely.

    Only acts while not LOCKed. The clock runs from the last stable lock (held --stable-lock s; a lock
    that keeps flapping does not count) or the end of the last maneuver:
      DETUMBLING r   after --detumble-after s, if tumbling faster than --detumble-min deg/s (rate from
                     truth): ramp the rate down at --detumble-accel deg/s^2 (reaction wheels) to 0.
                     Once started it finishes, even if the tracker locks on the way down.
      SEARCH SLEW    after --recover-after s at rest with no lock and no candidate being VERIFIED:
                     --slew-rate deg/s for --slew-time s (a new patch of sky), then back to 0 and the
                     clock restarts; repeats while lost.
    While LOCKed nothing is sent: knob tumble during LOCK is left alone."""

    CMD_HZ = 5.0

    def __init__(self, a, send_rate):
        self.a, self.send_rate = a, send_rate
        self.mode = None           # None | "detumble" | "slew"
        self.t0 = 0.0
        self.r0 = 0.0
        self.t_cmd = -1e9
        self.idle_since = None     # last time we had a stable lock (or a maneuver ended)
        self.lock_since = None     # start of the current LOCK
        self.cmd_rate = None       # last rate we commanded
        self.slews = 0

    def _cmd(self, t, r, force=False):
        r = max(0.0, round(r, 1))
        if force or (t - self.t_cmd >= 1.0 / self.CMD_HZ and r != self.cmd_rate):
            self.send_rate(r)
            self.cmd_rate, self.t_cmd = r, t

    def update(self, t, status, stage, rate_now):
        """rate_now: current tumble rate deg/s from truth (None if unknown). Returns the stage to show
        instead of the solver's, or None."""
        a = self.a
        if self.mode == "detumble":          # once braking has started it finishes, even if we lock on the way
            r = self.r0 - a.detumble_accel * (t - self.t0)
            if r > 0:
                self._cmd(t, r)
                # keep showing DETUMBLING over a fresh candidate (check 1 restarts while the rate still falls)
                return (None if status == "LOCK" or (stage.startswith("VERIFYING") and not stage.startswith("VERIFYING 1/"))
                        else f"DETUMBLING {r:.1f}")
            self._cmd(t, 0.0, force=True)
            self.mode, self.idle_since = None, t
            print(f"[recover] detumbled ({t - self.t0:.1f} s)")
        elif self.mode == "slew":
            if t - self.t0 < a.slew_time:
                return "SEARCH SLEW"
            self._cmd(t, 0.0, force=True)
            self.mode, self.idle_since = None, t
            print("[recover] slew done, settling")
        if self.idle_since is None:
            self.idle_since = t
        if status == "LOCK":
            self.lock_since = t if self.lock_since is None else self.lock_since
            if t - self.lock_since >= a.stable_lock:   # a lock that holds resets the clock;
                self.idle_since, self.slews = t, 0       # a flapping one does not
            return None
        self.lock_since = None
        waited = t - self.idle_since
        r = rate_now if rate_now is not None else (self.cmd_rate or 0.0)
        if r > a.detumble_min and waited >= a.detumble_after:
            self.mode, self.t0, self.r0 = "detumble", t, r
            print(f"[recover] no lock for {t - self.idle_since:.1f} s at {r:.1f} deg/s: detumbling")
            self._cmd(t, r - a.detumble_accel / self.CMD_HZ, force=True)
            return f"DETUMBLING {r:.1f}"
        if waited < a.recover_after or stage.startswith("VERIFYING"):
            return None                        # never slew away from a candidate being checked
        self.slews += 1
        self.mode, self.t0 = "slew", t
        print(f"[recover] no lock for {t - self.idle_since:.1f} s: search slew #{self.slews} "
              f"({a.slew_rate:g} deg/s for {a.slew_time:g} s)")
        self._cmd(t, a.slew_rate, force=True)
        return "SEARCH SLEW"


class FrameSender:
    """Sends the solved frame to the dashboard so it can show the real camera image next to the truth
    render. Display only: the solver never sees the stretched / downscaled copy."""
    MAX_BYTES = 60000

    def __init__(self, port, width, hz):
        self.port, self.width, self.period, self.next_t = port, width, 1.0 / hz if hz > 0 else 0.0, 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)   # macOS caps UDP sends at 9 KB otherwise

    @staticmethod
    def stretch(g):
        """Black level to 0, brightest stars to 255, so a dim real webcam frame is visible on a projector.
        A sensor whiteout stays white (the black level is capped)."""
        lo = min(float(np.percentile(g, 5)), 60.0)
        hi = max(float(np.percentile(g, 99.9)), lo + 64.0)
        return np.clip((g.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)

    def send(self, t, gray, H, sw, sh, c_cam, est):
        now = time.monotonic()
        if now < self.next_t:
            return
        self.next_t = now + self.period
        h0, w0 = gray.shape[:2]
        s = min(1.0, self.width / w0)
        small = cv2.resize(gray, (round(w0 * s), round(h0 * s)), interpolation=cv2.INTER_AREA) if s < 1 else gray
        small = self.stretch(small)
        for q in (80, 65, 50, 35):
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, q])
            if ok and len(buf) <= self.MAX_BYTES:
                break
        else:
            return
        hdr = {"t": t, "w": small.shape[1], "h": small.shape[0], "scale": s, "sw": sw, "sh": sh,
               "screen_to_cam": None if H is None else np.linalg.inv(H).tolist(),
               "cam_cents": np.round(np.asarray(c_cam[:100], float).reshape(-1, 2) * s, 1).tolist(),
               "est": {k: est.get(k) for k in ("status", "stage", "ra", "dec", "roll", "fov", "centroids", "planets_px")}}
        hb = json.dumps(hdr, separators=(",", ":")).encode()
        try:
            self.sock.sendto(struct.pack(">I", len(hb)) + hb + buf.tobytes(), ("127.0.0.1", self.port))
        except OSError:
            pass


class NavWorker:
    """Runs LiveNavigator.observe (the planet-candidate pass) off the frame loop. One job at a time;
    a frame offered while it is busy is skipped (planets barely move between frames)."""

    def __init__(self):
        import threading
        self.job, self.busy, self.ev = None, False, threading.Event()
        threading.Thread(target=self._run, daemon=True, name="nav-observe").start()

    def submit(self, navi, gray, att, t):
        if self.busy:
            return False
        self.busy, self.job = True, (navi, gray.copy(), att, t)
        self.ev.set()
        return True

    def _run(self):
        while True:
            self.ev.wait()
            self.ev.clear()
            navi, gray, att, t = self.job
            try:
                navi.observe(gray, att, t)
            except Exception as e:   # never kill the solver over a bad candidate pass
                print("[nav] observe:", repr(e))
            self.busy = False


class Perf:
    """Where each frame's time goes; printed every 5 s as one [perf] line (always, also with --quiet)."""
    NAMES = ("read", "solve", "misc", "nav", "send")

    def __init__(self):
        self.t0, self.n, self.acc, self.render = time.perf_counter(), 0, [0.0] * 5, 0.0

    def add(self, *dts):
        self.n += 1
        for i in range(5):
            self.acc[i] += dts[i]
        self.render += dts[5]
        now = time.perf_counter()
        if now - self.t0 >= 5.0:
            ms = [1000 * v / self.n for v in self.acc]
            print(f"[perf] {self.n / (now - self.t0):4.1f} solves/s | " + ", ".join(
                f"{k} {v:.0f}" + (f" (render {1000 * self.render / self.n:.0f})" if k == "read" else "")
                for k, v in zip(self.NAMES, ms)) + " ms/frame", flush=True)
            self.t0, self.n, self.acc, self.render = now, 0, [0.0] * 5, 0.0


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
    src_g.add_argument("--no-shm", action="store_true",
                       help="with --follow-sim: redraw from truth instead of reading sim.live's shared frames")
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
    sol.add_argument("--detector", choices=["tetra3", "starnet"], default="tetra3",
                     help="star finder before tetra3's pattern match: tetra3's blob finder, or StarNet (ml/)")
    sol.add_argument("--centroid-mode", choices=["auto", "fast", "robust"], default="auto",
                     help="star finder background: fast = global (black rendered sky, ~4x faster), robust = "
                          "tetra3's local filter (real camera). auto: fast for --sim / --follow-sim")
    sol.add_argument("--max-centroids", type=int, default=60)
    sol.add_argument("--max-area", type=int, default=100,
                     help="largest blob (px) kept as a star; tetra3 default 100. 5000 keeps motion-blur "
                          "streaks ('tetra3-tuned' in ml/out/benchmark.md: 100%% solves at 10 deg/s)")
    sol.add_argument("--timeout-ms", type=int, default=500)
    sol.add_argument("--min-matches", type=int, default=8)
    sol.add_argument("--prob-max", type=float, default=1e-6)
    acq = ap.add_argument_group("acquisition (stages before LOCK)")
    acq.add_argument("--detect-min", type=int, default=8,
                     help="stars needed in the frame to leave DETECTING for MATCHING")
    acq.add_argument("--verify-n", type=int, default=6,
                     help="verification checks before LOCK (the first match is check 1)")
    acq.add_argument("--verify-spacing", type=float, default=1.0,
                     help="s between verification checks; LOCK ~ (verify-n - 1) * spacing s after the first match")
    acq.add_argument("--verify-tol", type=float, default=2.0,
                     help="deg: a check must agree this well with the dead-reckoned previous verified attitude "
                          "(+10%% of the angle turned since)")
    acq.add_argument("--acq-max-rate", type=float, default=8.0,
                     help="deg/s: no verification above this measured rate (stage RATE HIGH); LOCK tracks faster")
    acq.add_argument("--jump-tol", type=float, default=5.0,
                     help="deg: while LOCKed, a frame-to-frame attitude jump larger than this gives LOST")
    acq.add_argument("--jump-rate", type=float, default=30.0,
                     help="deg/s: --jump-tol grows by this times the frame gap (a knob change between frames is "
                          "not a jump; flare / false-match jumps are tens of degrees)")
    acq.add_argument("--verify-timeout", type=float, default=2.5,
                     help="s without a good solve while VERIFYING before the candidate is dropped")
    rec = ap.add_argument_group("recovery maneuvers (while not locked)")
    rec.add_argument("--recover", action=argparse.BooleanOptionalAction, default=True,
                     help="command the sim's tumble rate to recover: detumble, then search slews")
    rec.add_argument("--recover-after", type=float, default=8.0,
                     help="s without a stable lock (at rest) before a search slew")
    rec.add_argument("--detumble-after", type=float, default=3.0,
                     help="s without a stable lock before detumbling, if tumbling faster than --detumble-min")
    rec.add_argument("--stable-lock", type=float, default=3.0,
                     help="s a LOCK must hold to reset the recovery clock")
    rec.add_argument("--detumble-min", type=float, default=1.0,
                     help="deg/s: tumbling faster than this gets a detumble, slower gets a search slew")
    rec.add_argument("--detumble-accel", type=float, default=4.0, help="detumble ramp, deg/s per s")
    rec.add_argument("--slew-rate", type=float, default=6.0, help="search slew rate, deg/s")
    rec.add_argument("--slew-time", type=float, default=3.0, help="search slew duration, s")
    sol.add_argument("--rmse-max", type=float, default=900.0, help="arcsec, for the confidence score")
    nav = ap.add_argument_group("position from planets")
    nav.add_argument("--nav", action="store_true", help="find planets after each lock and fix the position")
    nav.add_argument("--nav-scan", type=float, default=0.0,
                     help="drive sim.live through 8 ecliptic pointings, this many s each (0 = don't)")
    nav.add_argument("--nav-scan-mode", choices=["auto", "button"], default="auto",
                     help="auto: keep scanning; button: one sweep per 'whereami' on --nav-cmd-port")
    nav.add_argument("--nav-slew-rate", type=float, default=20.0,
                     help="deg/s: scan steps are smooth sim 'slew's at this rate, tracked throughout (0 = 'point' "
                          "teleports). The dwell (--nav-scan s) starts on arrival")
    nav.add_argument("--nav-scan-max-rate", type=float, default=0.5,
                     help="deg/s: no scan step while the measured spin is faster (keeps the knob's tumble)")
    nav.add_argument("--nav-max-rate", type=float, default=2.0,
                     help="deg/s: don't look for planets while spinning faster (streaked frames)")
    nav.add_argument("--nav-cmd-port", type=int, default=NAV_CMD_PORT,
                     help="UDP port for 'whereami' (with --nav-scan-mode button)")
    nav.add_argument("--nav-sigma", type=float, default=10.0, help="planet direction error, arcsec (ellipsoid)")
    nav.add_argument("--nav-memory", type=float, default=0.0,
                     help="forget candidates not seen for this long, s (0 = 1.5 scan cycles with --nav-scan, else 60)")
    nav.add_argument("--nav-jd", type=float, default=2461352.5,
                     help="epoch (TDB JD) if the truth packet carries none; sim.live --planets default")
    nav.add_argument("--nav-every", type=float, default=0.5,
                     help="look for planets in at most one locked frame per this many s (planets barely move)")
    nav.add_argument("--nav-reset-on-blind", action="store_true",
                     help="forget planet sightings and the fix when the sensor is blinded (whited-out frame, "
                          "e.g. a flare): pairs with sim.live --flare-moves")
    nav.add_argument("--cmd-port", type=int, default=5006,
                     help="sim.live command port (for --nav-scan and the recovery maneuvers)")
    io = ap.add_argument_group("io")
    io.add_argument("--truth-port", type=int, default=TRUTH_PORT)
    io.add_argument("--no-truth", action="store_true")
    io.add_argument("--latency", type=float, default=0.08, help="camera latency for truth pairing, s")
    io.add_argument("--estimate-port", type=int, default=ESTIMATE_PORT, help="estimates for the dashboard")
    io.add_argument("--bridge-port", type=int, default=BRIDGE_PORT,
                    help="same estimates for bridge.serial_bridge (0 = don't send)")
    io.add_argument("--frame-port", type=int, default=FRAME_PORT,
                    help="solved frames (small JPEG) for the dashboard camera panel (0 = don't send)")
    io.add_argument("--frame-w", type=int, default=640, help="width of the frames sent to the dashboard, px")
    io.add_argument("--frame-hz", type=float, default=15.0, help="max frames per second sent to the dashboard")
    io.add_argument("--frames", type=int, default=0, help="stop after N frames (0 = run forever)")
    io.add_argument("--show", action="store_true", help="window with centroids / matches")
    io.add_argument("--save-shot", nargs="?", const="auto", default=None,
                    help="save an annotated frame (last LOCK, else last frame) to this path or recordings/")
    io.add_argument("--quiet", action="store_true", help="print only every 10th frame")
    a = ap.parse_args()

    src = FollowSource(a) if a.follow_sim else SimSource(a) if a.sim else CameraSource(a)
    solver = Solver(a, src)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    recovery = None
    if a.recover:   # never overlaps the nav scan: recovery acts only while not LOCKed, the scan only while LOCKed
        if isinstance(src, SimSource):
            send_rate = src.set_rate
        else:
            def send_rate(r):
                out.sendto(f"rate {r:g}".encode(), ("127.0.0.1", a.cmd_port))
        recovery = Recovery(a, send_rate)
    t_start, t_unlock, stage_prev = None, None, None
    frames = FrameSender(a.frame_port, a.frame_w, a.frame_hz) if a.frame_port else None
    navi, scan_i, scan_t, nav_print, nav_t, was_blind = None, 0, 0.0, 0.0, 0.0, False
    nav_worker = None
    N_SCAN = 8
    button = a.nav and a.nav_scan > 0 and a.nav_scan_mode == "button"
    sweep_left, scan_paused, scan_base = 0, False, 0   # button mode: pointings left in the sweep, its first one
    slew_t0, slew_until = 0.0, 0.0             # our own scan slew in progress (until arrival or this deadline)
    nav_in = None
    if button:
        nav_in = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        nav_in.bind(("127.0.0.1", a.nav_cmd_port))
        nav_in.setblocking(False)
        print(f"[nav] scan on demand: 'whereami' on UDP {a.nav_cmd_port} (console WHERE AM I?)")
    stats = {"n": 0, "lock": 0, "err": [], "roll": [], "ms": [], "nc": [], "tp": 0, "acq": []}
    shot, shot_lock = None, False
    perf = Perf()
    try:
        while not a.frames or stats["n"] < a.frames:
            _p0 = time.perf_counter()
            t, gray, truth = src.read()
            _p1 = time.perf_counter()
            if gray is None:
                print("frame grab failed")
                break
            if truth and truth.get("mode") == "calib":
                continue  # sim is showing the checkerboard
            fov = truth["fov"] if truth and "fov" in truth else a.fov
            c_scr, c_cam, res, ms = solver.solve(gray, fov)
            _p2 = time.perf_counter()
            status, conf, stage = solver.status(t, res, len(c_scr))
            if recovery is not None:
                stage = recovery.update(t, status, stage, (truth or {}).get("rate_dps")) or stage
            # acquisition timing: from start / loss of lock to LOCK
            t_start = t if t_start is None else t_start
            if status != "LOCK" and t_unlock is None:
                t_unlock = t
            if status == "LOCK" and t_unlock is not None:
                print(f"[acq] LOCK after {t - t_unlock:.1f} s")
                stats["acq"].append(t - t_unlock)
                t_unlock = None
            key = stage if stage.startswith("VERIFYING") else stage.split()[0]   # not every star count / rate
            if key != stage_prev and a.quiet:
                print(f"[stage] {t - t_start:7.2f} s  {stage}")
            stage_prev = key
            solved = res.get("RA") is not None
            msg = {"t": t, "ra": None, "dec": None, "roll": None, "fov": None, "status": status, "stage": stage,
                   "confidence": conf, "matches": int(res.get("Matches") or 0),
                   "prob": float(res["Prob"]) if solved else None,
                   "rmse": float(res["RMSE"]) if solved else None, "solve_ms": round(ms, 1),
                   "n_centroids": len(c_scr), "centroids": np.round(c_scr[:100], 2).tolist(),
                   "truth": truth, "err_arcsec": None, "roll_err_deg": None, "detector": a.detector,
                   "detect_ms": round(getattr(solver, "detect_ms", 0.0), 1) if solver.starnet else None}
            if solved:
                msg.update(ra=float(res["RA"]), dec=float(res["Dec"]), roll=float(res["Roll"]),
                           fov=float(res["FOV"]))
                if truth:
                    msg["err_arcsec"] = round(pointing_error_arcsec(msg["ra"], msg["dec"], truth["ra"], truth["dec"]), 1)
                    msg["roll_err_deg"] = round(roll_error_deg(msg["roll"], truth["roll"]), 3)
            _p3 = time.perf_counter()
            if a.nav:
                # a whited-out frame (flare) is something the camera itself sees; with --nav-reset-on-blind
                # we assume it may have left us anywhere and start the position search again
                blind = float(gray.mean()) > 180.0
                if blind and a.nav_reset_on_blind and navi is not None and not was_blind:
                    navi.reset()
                    print("[nav] sensor blinded: forgetting sightings, searching for the position again")
                    if button and sweep_left > 0:   # a sweep in progress starts over (its sightings are gone)
                        sweep_left, scan_base, slew_until = N_SCAN, scan_i, 0.0
                was_blind = blind
                while nav_in is not None:
                    try:
                        cmd = nav_in.recv(256).decode(errors="ignore").strip().lower()
                    except BlockingIOError:
                        break
                    if cmd == "whereami" and sweep_left == 0:
                        if navi is not None:
                            navi.reset()
                        # start at the scan pointing nearest to where we already look: short first turn
                        i0 = 0
                        if solved:
                            from camera.navigate import ecliptic_scan_radec
                            from sim.sky import unit
                            here = unit(msg["ra"], msg["dec"])
                            i0 = int(np.argmax([unit(*ecliptic_scan_radec(i)[:2]) @ here for i in range(N_SCAN)]))
                        sweep_left, scan_i, scan_base, scan_t = N_SCAN, i0, i0, 0.0
                        print(f"[nav] WHERE AM I?: sweeping {N_SCAN} pointings along the ecliptic, from #{i0}")
                # a scan step turns the craft: a smooth 'slew' at --nav-slew-rate (tracked all the way), or with
                # --nav-slew-rate 0 a 'point' teleport. Either stops the sim's tumble, so steps are only taken
                # while locked (no scanning while lost) and not spinning (the knob's tumble stays).
                now_s = time.time()
                spin = math.degrees(np.linalg.norm(solver.acq.w))
                if slew_until and (now_s > slew_until or (now_s - slew_t0 > 0.6 and spin <= a.nav_scan_max_rate)):
                    slew_until, scan_t = 0.0, now_s      # arrived (or gave up waiting): the dwell starts now
                want = a.nav_scan > 0 and (not button or sweep_left > 0 or slew_until)
                wait = (None if not want else "slew" if slew_until else "lock" if status != "LOCK"
                        else "spin" if spin > a.nav_scan_max_rate else None)
                paused = wait == "spin"
                if paused != scan_paused:
                    scan_paused = paused
                    print(f"[nav] scan paused: spinning {spin:.1f} deg/s" if paused else "[nav] scan resumed")
                if want and wait is None and (not button or sweep_left > 0) and now_s - scan_t > a.nav_scan:
                    from camera.navigate import ecliptic_scan_radec
                    ra_, dec_, roll_ = ecliptic_scan_radec(scan_i)
                    if a.nav_slew_rate > 0:
                        turn = Acquisition.angle_deg(from_radecroll(msg["ra"], msg["dec"], msg["roll"]),
                                                     from_radecroll(ra_, dec_, roll_)) if solved else 90.0
                        out.sendto(f"slew {ra_:.4f} {dec_:.4f} {roll_:.3f} {a.nav_slew_rate:g}".encode(),
                                   ("127.0.0.1", a.cmd_port))
                        slew_t0, slew_until = now_s, now_s + 1.5 * turn / a.nav_slew_rate + 2.0
                    else:
                        solver.acq.expect_jump(t)
                        out.sendto(f"point {ra_:.4f} {dec_:.4f} {roll_:.3f}".encode(), ("127.0.0.1", a.cmd_port))
                    scan_i, scan_t = scan_i + 1, now_s
                    if button:
                        sweep_left -= 1
                # the last pointing still needs its slew and dwell before the sweep counts as done
                active = button and (sweep_left > 0 or bool(slew_until) or (scan_i > 0 and now_s - scan_t < a.nav_scan))
                if status == "LOCK":
                    if navi is None:
                        from camera.navigate import CandidateFinder, LiveNavigator
                        navi = LiveNavigator(CandidateFinder(src.sw, src.sh, fov, src.H), a.nav_jd,
                                             sigma_arcsec=a.nav_sigma,
                                             memory_s=a.nav_memory or (1e9 if button else
                                                                       12 * a.nav_scan if a.nav_scan > 0 else 60.0))
                        navi.finder.centroid_kw = solver.centroid_kw
                        nav_worker = NavWorker()      # one worker thread for the whole run
                        print(f"[nav] navigator ready ({src.sw}x{src.sh}, FOV {fov} deg)")
                    if truth and truth.get("jd"):
                        navi.jd = truth["jd"]   # the spacecraft clock; the sim's epoch
                    # the candidate pass costs 100+ ms: it runs in a worker so it never stalls the camera loop,
                    # not while spinning (streaked frames give junk candidates) and, in button mode, only
                    # during a WHERE AM I? sweep (no sightings are wanted between sweeps)
                    if (time.time() - nav_t >= a.nav_every and spin <= a.nav_max_rate and (active or not button)
                            and nav_worker.submit(navi, gray, from_radecroll(msg["ra"], msg["dec"], msg["roll"]), t)):
                        nav_t = time.time()
                msg["nav"] = navi.packet(truth) if navi else {"status": "collecting", "n_cand": 0}
                if navi is not None and status == "LOCK":   # identified planets in this frame, from the estimates
                    try:
                        msg["planets_px"] = navi.planets_in_view(from_radecroll(msg["ra"], msg["dec"], msg["roll"]))
                    except Exception as e:   # display only: never let it stop the solver
                        msg["planets_px"] = []
                        if not getattr(navi, "_piv_warned", False):
                            navi._piv_warned = True
                            print("[nav] planets_in_view:", repr(e))
                if a.nav_scan > 0:
                    msg["nav"]["scan"] = {"mode": a.nav_scan_mode, "active": bool(active) if button else True,
                                          "step": min(scan_i - scan_base, N_SCAN) if button else scan_i % N_SCAN,
                                          "n": N_SCAN, "paused": scan_paused, "wait": wait}
                nv = msg["nav"]
                if time.time() - nav_print > 5:
                    nav_print = time.time()
                    print(f"[nav] {nv['status']}, {nv['n_cand']} candidates"
                          + (f" | {nv['r_au']:.4f} AU from the Sun, Earth {nv['earth_km'] / 1e6:.3f} Mkm "
                             f"+/- {nv['earth_sigma_km']:,.0f} km, 1-sigma {nv['axes_km'][0]:,.0f} km, "
                             f"bodies {'+'.join(nv['bodies'])}"
                             + (f", ERROR {nv['err_km']:,.0f} km" if 'err_km' in nv else "")
                             if nv.get("pos_km") else ""))
            _p4 = time.perf_counter()
            pkt = json.dumps(msg).encode()
            out.sendto(pkt, ("127.0.0.1", a.estimate_port))
            if a.bridge_port:
                out.sendto(pkt, ("127.0.0.1", a.bridge_port))
            if frames is not None:
                frames.send(t, gray, src.H, src.sw, src.sh, c_cam, msg)

            _p5 = time.perf_counter()
            perf.add(_p1 - _p0, _p2 - _p1, _p3 - _p2, _p4 - _p3, _p5 - _p4, getattr(src, "render_ms", 0.0) / 1000)
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
            line = (f"{status:6s} {stage:16s} conf {conf:4.2f} | {len(c_scr):3d} blobs {msg['matches']:3d} matched | "
                    + (f"RA {msg['ra']:7.2f} Dec {msg['dec']:6.2f} Roll {msg['roll']:6.1f} "
                       f"Prob {msg['prob']:.0e} RMSE {msg['rmse']:5.0f}\"" if solved else "no solution".ljust(62))
                    + f" | {ms:4.0f} ms"
                    + (f" (+StarNet {solver.detect_ms:4.0f} ms)" if solver.starnet else "")
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
        if stats["acq"]:
            print("    acquisition times (start/loss -> LOCK): " + ", ".join(f"{x:.1f} s" for x in stats["acq"]))
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
