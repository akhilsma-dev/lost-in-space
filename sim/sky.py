"""Sky renderer: attitude -> 8-bit image of the star field the camera should see.

Attitude is a scipy Rotation whose matrix columns are the camera axes
[image-right, image-down, boresight] (right-handed, OpenCV-style) expressed in
J2000 inertial coordinates.
`to_radecroll` converts it to tetra3's (RA, Dec, Roll) convention so truth and
tetra3's estimate can be compared directly.
"""
import json
import math
import pathlib

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
MAG_LIMIT = 5.5  # matches the tetra3 database's star_max_magnitude


def unit(ra_deg, dec_deg):
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], -1)


def load_stars(mag_limit=MAG_LIMIT):
    feats = json.loads((DATA / "stars.6.json").read_text())["features"]
    hip = np.array([f["id"] for f in feats])
    mag = np.array([f["properties"]["mag"] for f in feats], np.float32)
    radec = np.array([f["geometry"]["coordinates"] for f in feats], np.float64)
    keep = mag <= mag_limit
    return hip[keep], mag[keep], unit(*radec[keep].T)


# ---------- attitude <-> tetra3 (RA, Dec, Roll) ----------
def _east_north(c):
    east = np.cross([0.0, 0.0, 1.0], c)
    n = np.linalg.norm(east)
    east = np.array([0.0, 1.0, 0.0]) if n < 1e-9 else east / n  # pole: pick any east
    return east, np.cross(c, east)


def from_radecroll(ra, dec, roll):
    c = unit(ra, dec)
    east, north = _east_north(c)
    t = -math.radians(roll)  # tetra3 measures roll the other way
    right = -east * math.cos(t) + north * math.sin(t)
    up = north * math.cos(t) + east * math.sin(t)
    return Rotation.from_matrix(np.column_stack([right, -up, c]))


def to_radecroll(att):
    right, _, c = att.as_matrix().T
    ra = math.degrees(math.atan2(c[1], c[0])) % 360
    dec = math.degrees(math.asin(np.clip(c[2], -1, 1)))
    east, north = _east_north(c)
    t = math.atan2(right @ north, -(right @ east))
    return ra, dec, math.degrees(-t) % 360


def random_attitude(rng):
    return Rotation.random(random_state=rng)


# ---------- renderer ----------
class SkyRenderer:
    """Renders stars as Gaussian blobs (size and brightness both scale with
    magnitude, so faint stars survive an 8-bit screen + webcam). Motion blur is
    done by splatting the star at many sub-steps across the exposure."""

    SIGMA_BINS = np.array([1.6, 2.0, 2.5, 3.0, 3.6])  # screen px

    def __init__(self, width, height, fov_deg, mag_limit=MAG_LIMIT):
        self.w, self.h, self.fov = width, height, fov_deg
        self.f = (width / 2) / math.tan(math.radians(fov_deg / 2))
        self.hip, self.mag, self.vec = load_stars(mag_limit)
        self.mag_limit = mag_limit
        self.peak, self.bin = self.blob_style(self.mag)
        # half-diagonal FOV + margin, for cheap culling
        self.cos_cull = math.cos(math.atan(math.hypot(width, height) / 2 / self.f) + math.radians(3))

    def blob_style(self, mag):
        """Magnitudes -> (peak brightness, sigma bin index). Stars and extra sources share it."""
        m = np.asarray(mag, np.float32)
        # peak brightness: compressed magnitude scale, floor so mag 5.5 is still visible
        peak = np.clip(255 * 10 ** (-0.2 * (m - 1.0)), 55, 255).astype(np.float32)
        sigma = np.clip(1.6 + 0.4 * (self.mag_limit - m), 1.6, 3.6)
        return peak, np.abs(sigma[:, None] - self.SIGMA_BINS[None]).argmin(1)

    def project(self, vecs, att):
        """Inertial unit vectors -> (px, py, in_front). Works for any vectors."""
        cam = vecs @ att.as_matrix()  # rows: (x right, y down, z boresight)
        z = cam[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            px = self.w / 2 + self.f * cam[:, 0] / z
            py = self.h / 2 + self.f * cam[:, 1] / z
        return px, py, z > 0.05

    def render(self, att, omega=None, exposure=0.0, extra=None):
        """att: attitude at mid-exposure. omega: body-frame angular velocity
        (rad/s, 3-vector). exposure: seconds. extra: optional (unit vectors (N, 3), magnitudes
        (N,)) of additional point sources (planets) drawn in the same blob style as the stars;
        sources fainter than the renderer's mag limit are skipped. Returns float32 image (0..255+)."""
        vec_all, peak_all, bin_all = self.vec, self.peak, self.bin
        if extra is not None and len(extra[0]):
            ev, em = np.asarray(extra[0], np.float64).reshape(-1, 3), np.asarray(extra[1], np.float32)
            ok = em <= self.mag_limit
            ep, eb = self.blob_style(em[ok])
            vec_all = np.concatenate([vec_all, ev[ok]])
            peak_all, bin_all = np.concatenate([peak_all, ep]), np.concatenate([bin_all, eb])
        bore = att.as_matrix()[:, 2]
        near = np.flatnonzero(vec_all @ bore > self.cos_cull)
        vec, peak, bins = vec_all[near], peak_all[near], bin_all[near]

        rate = 0.0 if omega is None else float(np.linalg.norm(omega))
        smear_px = rate * exposure * self.f
        n_sub = int(np.clip(math.ceil(smear_px / 0.7), 1, 160))
        offsets = np.linspace(-0.5, 0.5, n_sub) * exposure if n_sub > 1 else [0.0]

        P, W8, B = [], [], []
        for dt in offsets:
            a = att if dt == 0.0 else att * Rotation.from_rotvec(np.asarray(omega) * dt)
            px, py, ok = self.project(vec, a)
            ok &= (px >= 0) & (px < self.w - 1) & (py >= 0) & (py < self.h - 1)
            P.append(np.stack([px[ok], py[ok]], 1)); W8.append(peak[ok] / n_sub); B.append(bins[ok])
        pts, w, b = np.concatenate(P), np.concatenate(W8), np.concatenate(B)

        img = np.zeros((self.h, self.w), np.float32)
        for k in np.unique(b):
            s = self.SIGMA_BINS[k]
            sel = b == k
            # impulse weight peak*2*pi*s^2 -> Gaussian with that peak after blur
            lay = self._splat(pts[sel], w[sel] * (2 * math.pi * s * s))
            cv2.add(img, cv2.GaussianBlur(lay, (0, 0), s), dst=img)
        return img

    def _splat(self, pts, w):
        """Bilinear impulse splat (sub-pixel accurate) -> float32 (h, w) image."""
        x0, y0 = np.floor(pts[:, 0]).astype(np.int64), np.floor(pts[:, 1]).astype(np.int64)
        fx, fy = pts[:, 0] - x0, pts[:, 1] - y0
        base = y0 * self.w + x0
        idx = np.concatenate([base, base + 1, base + self.w, base + self.w + 1])
        wt = np.concatenate([w * (1 - fx) * (1 - fy), w * fx * (1 - fy), w * (1 - fx) * fy, w * fx * fy])
        flat = np.bincount(idx, weights=wt, minlength=self.h * self.w)
        return flat.astype(np.float32).reshape(self.h, self.w)

    # ---------- extra effects ----------
    def glare(self, img, corner=(1.0, 1.0), strength=170, scale=0.22):
        key = (tuple(corner), strength, scale)
        if getattr(self, "_glare_key", None) != key:  # cache: the map only changes on flare
            yy, xx = np.ogrid[:self.h, :self.w]
            cx, cy = corner[0] * self.w, corner[1] * self.h
            self._glare = (strength * np.exp(-np.hypot(xx - cx, yy - cy) / (scale * self.w))).astype(np.float32)
            self._glare_key = key
        cv2.add(img, self._glare, dst=img)
        return img

    @staticmethod
    def to_u8(img, noise=0.0, rng=None):
        if noise > 0:
            img = img + (rng or np.random.default_rng()).normal(0, noise, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)


def checkerboard(width, height, cols=9, rows=6):
    """Calibration pattern: (cols x rows) inner corners, centered, white margin.
    Returns (image, inner-corner screen coords as (N, 2) float32 in row-major order)."""
    sq = int(min(width / (cols + 3), height / (rows + 3)))
    bw, bh = sq * (cols + 1), sq * (rows + 1)
    x0, y0 = (width - bw) // 2, (height - bh) // 2
    img = np.full((height, width), 255, np.uint8)
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                img[y0 + r * sq:y0 + (r + 1) * sq, x0 + c * sq:x0 + (c + 1) * sq] = 0
    gx, gy = np.meshgrid(np.arange(1, cols + 1), np.arange(1, rows + 1))
    corners = np.stack([x0 + gx * sq, y0 + gy * sq], -1).reshape(-1, 2).astype(np.float32)
    return img, corners
