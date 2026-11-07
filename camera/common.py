"""Shared bits for the camera side: tetra3 loading, calibration file, truth listener, errors."""
import collections
import json
import math
import pathlib
import socket
import threading
import time

import numpy as np

np.math = math  # cedar-solve 0.5.1 still calls np.math, removed in NumPy 2

import cv2  # noqa: E402
import tetra3  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CALIB = DATA / "calib.json"
RECORDINGS = ROOT / "recordings"

SIM_CMD = ("127.0.0.1", 5006)
TRUTH_PORT = 5005
ESTIMATE_PORT = 5008


def load_tetra3():
    t3 = tetra3.Tetra3(load_database=None)
    t3.load_database((DATA / "tetra3_wide.npz").resolve())
    return t3


def centroids(gray, **kw):
    """tetra3's blob finder. Returns (N, 2) float array of (x, y), brightest first, in OpenCV's
    convention (pixel centres at integer coords). tetra3 itself reports pixel centres at +0.5, which
    the solver doesn't expect for the sim's renderer: left uncorrected it is a constant 0.7 px
    (~70 arcsec) pointing bias."""
    yx = tetra3.get_centroids_from_image(gray, **kw)
    yx = np.asarray(yx, np.float64).reshape(-1, 2) - 0.5
    return yx[:, ::-1].copy()


# ---------- calibration ----------
def save_calib(d, path=CALIB):
    path.write_text(json.dumps(d, indent=2))


def load_calib(path=CALIB):
    d = json.loads(pathlib.Path(path).read_text())
    d["H"] = np.array(d["H_cam2screen"], np.float64)
    return d


def warp_points(pts_xy, H):
    if len(pts_xy) == 0:
        return np.zeros((0, 2))
    return cv2.perspectiveTransform(np.asarray(pts_xy, np.float64).reshape(-1, 1, 2), H).reshape(-1, 2)


# ---------- sim truth over UDP ----------
class TruthListener:
    """Background thread keeping the last ~2 s of sim truth packets (by receive time)."""

    def __init__(self, port=TRUTH_PORT):
        self.buf = collections.deque(maxlen=200)
        self.lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.5)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                pkt = json.loads(data)
            except ValueError:
                continue
            with self.lock:
                self.buf.append((time.time(), pkt))

    def latest(self):
        with self.lock:
            return self.buf[-1][1] if self.buf else None

    def at(self, t):
        """Truth packet whose receive time is closest to t (None if nothing within 0.5 s)."""
        with self.lock:
            if not self.buf:
                return None
            rt, pkt = min(self.buf, key=lambda e: abs(e[0] - t))
        return pkt if abs(rt - t) < 0.5 else None

    def wait(self, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end and not self.buf:
            time.sleep(0.05)
        return self.latest()


def send_sim(cmd):
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(cmd.encode(), SIM_CMD)


# ---------- attitude error ----------
def _unit(ra, dec):
    ra, dec = math.radians(ra), math.radians(dec)
    return np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])


def pointing_error_arcsec(ra1, dec1, ra2, dec2):
    return math.degrees(math.acos(float(np.clip(_unit(ra1, dec1) @ _unit(ra2, dec2), -1, 1)))) * 3600


def roll_error_deg(r1, r2):
    return (r1 - r2 + 180) % 360 - 180
