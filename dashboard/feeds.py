"""Inputs for the dashboard: UDP JSON listeners and a mock that synthesises both feeds.

Truth    (sim.live, UDP 5005): {"frame","t","ra","dec","roll","fov","rate_dps","exposure","glare","mode","w","h"}
Estimate (camera.solve_live, UDP 5008):
    {"t","ra","dec","roll","fov","status":"LOCK"|"LOST","confidence","matches","prob","rmse","solve_ms",
     "n_centroids","centroids":[[x,y],...],"truth":{...}|null,"err_arcsec","roll_err_deg"}
"""
import json
import math
import socket
import threading
import time

import numpy as np
from scipy.spatial.transform import Rotation

from sim.sky import SkyRenderer, from_radecroll, random_attitude, to_radecroll


class Feed:
    """Latest packet from one source, plus receive bookkeeping. Thread-safe enough for one writer."""

    def __init__(self, name):
        self.name = name
        self.last = None      # latest decoded dict
        self.rx = 0.0         # wall time it arrived
        self.count = 0        # packets so far (used as a sequence number)
        self.error = None     # e.g. port already in use
        self._times = []      # recent arrival times, for the Hz readout
        self._lock = threading.Lock()

    def inject(self, msg):
        now = time.time()
        with self._lock:
            self.last, self.rx = msg, now
            self.count += 1
            self._times.append(now)
            if len(self._times) > 120:
                del self._times[:60]

    def age(self):
        return math.inf if self.last is None else time.time() - self.rx

    def hz(self, window=2.0):
        now = time.time()
        with self._lock:
            n = sum(1 for t in self._times if now - t <= window)
        return n / window


class UdpFeed(Feed):
    def __init__(self, name, port):
        super().__init__(name)
        self.port = port

    def start(self):
        threading.Thread(target=self._run, daemon=True, name=f"udp-{self.name}").start()

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", self.port))
        except OSError as e:
            self.error = f"UDP {self.port}: {e.strerror or e}"
            print(f"[{self.name}] cannot listen: {self.error}")
            return
        sock.settimeout(0.5)
        while True:
            try:
                data, _ = sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(0.2)
                continue
            try:
                self.inject(json.loads(data.decode(errors="ignore")))
            except ValueError:
                pass


# ---------------------------------------------------------------- mock
class MockTruth:
    """Stands in for sim.live: a tumbling hidden attitude with scripted flares, rate changes and glare."""

    RATES = [0.0, 1.0, 2.0, 4.0, 8.0, 15.0, 25.0]

    def __init__(self, w=1920, h=1080, fov=50.0, seed=None):
        self.rng = np.random.default_rng(seed)
        self.w, self.h, self.fov = w, h, fov
        self.frame = 0
        self.rate = 2.0
        self.glare = False
        self.t = time.time()
        self.next_event = self.t + 6
        self._flare()
        self.flash_until = 0.0     # no whiteout for the starting attitude

    def _flare(self):
        self.flash_until = time.time() + 1.2   # sensor whiteout, like sim.live's flare
        self.att = random_attitude(self.rng)
        ax = self.rng.normal(size=3)
        self.axis = ax / np.linalg.norm(ax)

    def step(self):
        now = time.time()
        dt, self.t = now - self.t, now
        if now >= self.next_event:
            r = self.rng.random()
            if r < 0.35:
                self._flare()
            elif r < 0.75:
                self.rate = float(self.rng.choice(self.RATES))
            else:
                self.glare = not self.glare
            self.next_event = now + self.rng.uniform(5, 11)
        self.att = self.att * Rotation.from_rotvec(self.axis * math.radians(self.rate) * dt)
        ra, dec, roll = to_radecroll(self.att)
        self.frame += 1
        return {"frame": self.frame, "t": now, "ra": ra, "dec": dec, "roll": roll, "fov": self.fov,
                "rate_dps": self.rate, "exposure": 0.1, "glare": self.glare,
                "flash": 255.0 if now < self.flash_until else 0.0, "mode": "sky",
                "w": self.w, "h": self.h}


class MockEstimator:
    """Turns a truth packet into a plausible camera-solve packet: noisy attitude, centroids,
    LOST after flares (attitude jumps), more often at high tumble rate or under glare."""

    def __init__(self, seed=None):
        self.rng = np.random.default_rng(None if seed is None else seed + 1)
        self.sky = None
        self.prev = None          # previous truth attitude, to detect flares
        self.outage_until = 0.0

    def _renderer(self, w, h, fov):
        if self.sky is None or (self.sky.w, self.sky.h, self.sky.fov) != (w, h, fov):
            self.sky = SkyRenderer(w, h, fov)
        return self.sky

    def make(self, truth):
        now = time.time()
        rng = self.rng
        w, h, fov = truth.get("w", 1920), truth.get("h", 1080), truth.get("fov", 50.0)
        att = from_radecroll(truth["ra"], truth["dec"], truth["roll"])
        rate = float(truth.get("rate_dps", 0.0))
        glare = bool(truth.get("glare"))

        if self.prev is not None and (self.prev.inv() * att).magnitude() > math.radians(3):
            self.outage_until = now + rng.uniform(1.2, 4.0)      # flare: re-acquire from scratch
        self.prev = att
        if now >= self.outage_until and rng.random() < 0.004 + 0.002 * rate + (0.01 if glare else 0):
            self.outage_until = now + rng.uniform(0.3, 1.5)      # random dropout episode

        # centroids: catalogue stars at the true attitude, brightest first, jittered; faint ones lost to blur
        sky = self._renderer(w, h, fov)
        px, py, ok = sky.project(sky.vec, att)
        ok &= (px >= 0) & (px < w) & (py >= 0) & (py < h) & (sky.mag <= 5.3 - 0.08 * rate - (0.6 if glare else 0))
        idx = np.flatnonzero(ok)[np.argsort(sky.mag[ok])][:60]
        c = np.stack([px[idx], py[idx]], 1) + rng.normal(0, 0.4 + 0.05 * rate, (len(idx), 2))
        n_fake = rng.integers(0, 3) + (rng.integers(3, 9) if glare else 0)
        fake = np.column_stack([rng.uniform(0, w, n_fake), rng.uniform(0, h, n_fake)])
        c = np.vstack([c, fake]) if len(c) else fake

        p_lost = 0.02 + max(0.0, rate - 8) * 0.03 + (0.12 if glare else 0)
        lost = (now < self.outage_until or rng.random() < p_lost or len(idx) < 8
                or truth.get("mode") == "calib")
        msg = {"t": now, "ra": None, "dec": None, "roll": None, "fov": None, "status": "LOST",
               "confidence": 0.0, "matches": 0, "prob": None, "rmse": None,
               "solve_ms": round(float(rng.uniform(25, 90) + (400 if lost else 0) * rng.random()), 1),
               "n_centroids": int(len(c)), "centroids": np.round(c[:100], 2).tolist(), "truth": truth,
               "err_arcsec": None, "roll_err_deg": None, "mock": True}
        if lost:
            return msg

        sp = math.radians((4 + 1.2 * rate + (6 if glare else 0)) / 3600)   # cross-boresight, rad
        sr = math.radians(0.01 + 0.004 * rate)                               # about boresight
        est = att * Rotation.from_rotvec(rng.normal(0, 1, 3) * [sp, sp, sr])
        ra, dec, roll = to_radecroll(est)
        matches = int(min(len(idx), rng.integers(10, 26)))
        prob = 10 ** -rng.uniform(9, 22)
        rmse = abs(rng.normal(12 + 2.5 * rate + (10 if glare else 0), 4))
        conf = min(1.0, -math.log10(prob) / 20) * max(0.0, 1 - rmse / 900)
        d_point = math.degrees(math.acos(np.clip(est.as_matrix()[:, 2] @ att.as_matrix()[:, 2], -1, 1))) * 3600
        msg.update(ra=ra, dec=dec, roll=roll, fov=fov * (1 + rng.normal(0, 2e-4)), status="LOCK",
                   confidence=round(conf, 3), matches=matches, prob=prob, rmse=rmse,
                   err_arcsec=round(d_point, 1), roll_err_deg=round((roll - truth["roll"] + 180) % 360 - 180, 3))
        return msg


class MockRunner(threading.Thread):
    """In-process mock. truth_feed=None -> also synthesise truth (MockTruth at 30 Hz);
    otherwise read truth from that feed (e.g. a real sim.live) and only synthesise estimates."""

    def __init__(self, est_feed, truth_feed=None, own_truth=None, est_hz=10.0, seed=None):
        super().__init__(daemon=True, name="mock")
        self.est_feed, self.truth_feed, self.own_truth = est_feed, truth_feed, own_truth
        self.mt = MockTruth(seed=seed) if own_truth is not None else None
        self.me = MockEstimator(seed)
        self.est_period = 1.0 / est_hz

    def run(self):
        next_est = 0.0
        while True:
            now = time.time()
            if self.mt is not None:
                self.own_truth.inject(self.mt.step())
            src = self.own_truth if self.mt is not None else self.truth_feed
            if now >= next_est and src.last is not None and src.age() < 1.0:
                self.est_feed.inject(self.me.make(dict(src.last)))
                next_est = now + self.est_period
            time.sleep(1 / 30)
