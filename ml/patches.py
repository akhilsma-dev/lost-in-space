"""Synthetic training / test frames for the learned star front-end.

Everything is rendered by `sim.sky.SkyRenderer` (same renderer the live demo
puts on screen), then dirtied: tumble motion blur, sun/moon glare, hot pixels,
a fake planet, background offset and read noise.

Labels come from geometry, not from any detector:
  * star / planet  -> mid-exposure position from `SkyRenderer.project` at the
                      mid-exposure attitude (the renderer smears symmetrically
                      about it, so this is the centroid we want tetra3 to get)
  * hot pixel      -> the pixel it was stamped on (sensor-fixed, never smeared)
  * glare          -> per-pixel mask where the glare map exceeds GLARE_TH DN
  * background     -> everything else (implicit: no object there)

Pixel convention in this module: pixel i covers [i-0.5, i+0.5), i.e. its centre
is the integer i (that is what `project` + the renderer's bilinear splat use).
tetra3 uses pixel-corner origin, so add 0.5 before handing positions to it.

Run `python -m ml.patches` to write a preview image to ml/out/patches_preview.png.
"""
import math
import pathlib
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from sim.sky import SkyRenderer, random_attitude

W, H, FOV = 1920, 1080, 50.0
EXPOSURE = 0.1
GLARE_TH = 25.0  # DN; glare brighter than this counts as "glare" pixels

CLASSES = ["star", "planet", "glare", "hot_pixel", "background"]
STAR, PLANET, GLARE, HOT, BACKGROUND = range(5)

# matched-filter SNR thresholds for "a detector could see this star"
SNR_POS = 5.0     # >= : positive label
SNR_IGNORE = 2.5  # [IGNORE, POS): don't-care (neither rewarded nor punished)


@dataclass
class Frame:
    img: np.ndarray                     # (H, W) uint8 — what the camera sees
    att: Rotation                       # truth attitude at mid-exposure
    omega: np.ndarray                   # body-frame rate, rad/s
    xy: np.ndarray                      # (N, 2) object positions (x, y), pixel-centre convention
    cls: np.ndarray                     # (N,) class id (STAR / PLANET / HOT)
    mag: np.ndarray                     # (N,) magnitude (stars, planet), 0 for hot pixels
    snr: np.ndarray                     # (N,) matched-filter SNR estimate (inf for hot px / planet)
    glare_mask: np.ndarray              # (H, W) bool
    meta: dict = field(default_factory=dict)


_RENDERERS = {}


def renderer(w=W, h=H, fov=FOV):
    key = (w, h, fov)
    if key not in _RENDERERS:
        _RENDERERS[key] = SkyRenderer(w, h, fov)
    return _RENDERERS[key]


def random_axis(rng):
    a = rng.normal(size=3)
    return a / np.linalg.norm(a)


def _path(r, vecs, att, omega, exposure, n=9):
    """Positions of `vecs` at n instants across the exposure -> (n, N, 2), ok (N,)."""
    xs, ys, oks = [], [], []
    for dt in np.linspace(-0.5, 0.5, n) * exposure:
        a = att * Rotation.from_rotvec(omega * dt)
        px, py, ok = r.project(vecs, a)
        xs.append(px); ys.append(py); oks.append(ok)
    return np.stack([np.stack(xs), np.stack(ys)], -1), np.all(oks, 0)


def _add_planet(r, img, att, omega, exposure, rng):
    """A bright, extended (sigma 4-6 px) object that is NOT in the catalogue,
    smeared by the same tumble as the stars. Returns (x, y) or None."""
    # random direction inside the field of view (at mid-exposure)
    x, y = rng.uniform(40, r.w - 40), rng.uniform(40, r.h - 40)
    d = att.as_matrix() @ np.array([(x - r.w / 2) / r.f, (y - r.h / 2) / r.f, 1.0])
    d /= np.linalg.norm(d)
    sigma = rng.uniform(4.0, 6.0)
    peak = rng.uniform(150, 255)
    rate = float(np.linalg.norm(omega))
    n_sub = int(np.clip(math.ceil(rate * exposure * r.f / 0.7), 1, 160))
    pts = []
    for dt in (np.linspace(-0.5, 0.5, n_sub) * exposure if n_sub > 1 else [0.0]):
        px, py, _ = r.project(d[None], att * Rotation.from_rotvec(omega * dt))
        pts.append((px[0], py[0]))
    pts = np.array(pts)
    keep = (pts[:, 0] >= 0) & (pts[:, 0] < r.w - 1) & (pts[:, 1] >= 0) & (pts[:, 1] < r.h - 1)
    if not keep.any():
        return None
    lay = r._splat(pts[keep], np.full(keep.sum(), peak * 2 * math.pi * sigma ** 2 / n_sub))
    img += cv2.GaussianBlur(lay, (0, 0), sigma)
    return x, y


def make_frame(rng, rate_dps=None, glare=None, exposure=EXPOSURE, n_hot=None, planet=None,
               noise=None, bg=None, w=W, h=H, fov=FOV):
    """Render one labelled frame. Any argument left None is randomised (training mode)."""
    r = renderer(w, h, fov)
    if rate_dps is None:
        # bias toward the hard end: half the frames at >= 8 deg/s
        rate_dps = rng.uniform(0, 20) if rng.random() < 0.6 else rng.uniform(8, 20)
    glare = rng.random() < 0.5 if glare is None else glare
    n_hot = int(rng.integers(0, 40)) if n_hot is None else n_hot
    planet = rng.random() < 0.5 if planet is None else planet
    noise = rng.uniform(1.0, 4.0) if noise is None else noise
    bg = rng.uniform(0, 12) if bg is None else bg

    att = random_attitude(rng)
    omega = random_axis(rng) * math.radians(rate_dps)
    img = r.render(att, omega, exposure)

    # --- star labels ---
    bore = att.as_matrix()[:, 2]
    near = np.flatnonzero(r.vec @ bore > r.cos_cull)
    path, ok_front = _path(r, r.vec[near], att, omega, exposure)
    mid = path[path.shape[0] // 2]
    inside = ok_front & (mid[:, 0] >= 0) & (mid[:, 0] <= w - 1) & (mid[:, 1] >= 0) & (mid[:, 1] <= h - 1)
    seg = np.linalg.norm(np.diff(path, axis=0), axis=-1).sum(0)  # streak length, px
    sig = r.SIGMA_BINS[r.bin[near]]
    flux = r.peak[near] * 2 * math.pi * sig ** 2
    area = (seg + 2.5 * sig) * 2.5 * sig  # pixels a matched filter would integrate over
    snr = flux / (max(noise, 0.5) * np.sqrt(area))
    s_xy, s_mag, s_snr = mid[inside], r.mag[near][inside], snr[inside]
    # peak DN of the smeared streak (used below to account for 8-bit saturation under glare)
    s_peak = (r.peak[near] * np.minimum(1.0, 2.5 * sig / (seg + 2.5 * sig)))[inside]

    xy, cls, mag, snrs = [s_xy], [np.full(len(s_xy), STAR)], [s_mag], [s_snr]

    # --- planet ---
    if planet:
        p = _add_planet(r, img, att, omega, exposure, rng)
        if p is not None:
            xy.append(np.array([p])); cls.append([PLANET]); mag.append([-1.0]); snrs.append([np.inf])

    # --- glare ---
    glare_mask = np.zeros((h, w), bool)
    if glare:
        corner = (rng.choice([0.0, 1.0]) + rng.uniform(-0.15, 0.15), rng.choice([0.0, 1.0]) + rng.uniform(-0.15, 0.15))
        g = np.zeros((h, w), np.float32)
        r.glare(g, corner=corner, strength=float(rng.uniform(110, 230)), scale=float(rng.uniform(0.12, 0.3)))
        img += g
        glare_mask = g > GLARE_TH
        # a star sitting on bright glare clips at 255: only the headroom above the
        # glare survives, so scale its SNR down (fully saturated -> invisible)
        ix = np.clip(np.round(s_xy).astype(int), 0, [w - 1, h - 1])
        head = np.clip(255.0 - bg - g[ix[:, 1], ix[:, 0]], 0, None)
        s_snr = s_snr * np.minimum(1.0, head / np.maximum(s_peak, 1e-3))
        snrs[0] = s_snr

    img += bg
    u8 = r.to_u8(img, noise=noise, rng=rng)

    # --- hot pixels (sensor, after everything; never smeared) ---
    if n_hot:
        hx, hy = rng.integers(2, w - 2, n_hot), rng.integers(2, h - 2, n_hot)
        u8[hy, hx] = rng.integers(140, 256, n_hot).astype(np.uint8)
        xy.append(np.stack([hx, hy], 1).astype(np.float64)); cls.append(np.full(n_hot, HOT))
        mag.append(np.zeros(n_hot)); snrs.append(np.full(n_hot, np.inf))

    return Frame(u8, att, omega, np.concatenate(xy).astype(np.float32), np.concatenate(cls).astype(np.int64),
                 np.concatenate(mag).astype(np.float32), np.concatenate(snrs).astype(np.float32), glare_mask,
                 dict(rate_dps=rate_dps, glare=glare, noise=noise, bg=bg, n_hot=n_hot, planet=planet))


def detectable_stars(fr, snr=SNR_POS):
    """(x, y) of the stars a good detector should find in this frame."""
    m = (fr.cls == STAR) & (fr.snr >= snr)
    return fr.xy[m], fr.mag[m]


# ---------------- training targets ----------------
STRIDE = 4
HEAT_CH = [STAR, PLANET, HOT]  # classes that get a point heatmap channel


def targets(fr, x0, y0, size, stride=STRIDE):
    """Dense targets for a size x size crop at (x0, y0), at output stride `stride`.
    Returns dict of float32 arrays:
      heat (3, s, s)  CenterNet-style Gaussian peaks for star / planet / hot pixel
      ignore (s, s)   1 where the star channel is don't-care (marginal-SNR stars)
      off (2, s, s)   sub-cell offset of the centre (x, y) in [0, 1) at peak cells
      mag (s, s)      star magnitude at peak cells
      reg (s, s)      1 at peak cells (where off / mag are supervised)
      glare (s, s)    glare mask, average-pooled
    """
    s = size // stride
    heat = np.zeros((3, s, s), np.float32)
    ignore = np.zeros((s, s), np.float32)
    off = np.zeros((2, s, s), np.float32)
    magt = np.zeros((s, s), np.float32)
    reg = np.zeros((s, s), np.float32)
    yy, xx = np.mgrid[:s, :s]
    for (x, y), c, m, q in zip(fr.xy, fr.cls, fr.mag, fr.snr):
        u, v = (x - x0) / stride, (y - y0) / stride
        if not (-2 <= u < s + 2 and -2 <= v < s + 2):
            continue
        ci, cj = int(math.floor(u)), int(math.floor(v))
        g = np.exp(-((xx - ci) ** 2 + (yy - cj) ** 2) / (2 * 0.8 ** 2))
        if c == STAR and q < SNR_POS:
            if q >= SNR_IGNORE:
                ignore = np.maximum(ignore, (g > 0.05).astype(np.float32))
            continue
        ch = HEAT_CH.index(c)
        heat[ch] = np.maximum(heat[ch], g)
        if 0 <= ci < s and 0 <= cj < s:
            heat[ch, cj, ci] = 1.0
            off[:, cj, ci] = (u - ci, v - cj)
            magt[cj, ci] = m
            reg[cj, ci] = 1.0
    ignore *= heat[0] < 1.0  # a real positive always wins
    gl = fr.glare_mask[y0:y0 + size, x0:x0 + size].reshape(s, stride, s, stride).mean((1, 3))
    return dict(heat=heat, ignore=ignore, off=off, mag=magt, reg=reg, glare=gl.astype(np.float32))


def random_crops(fr, rng, size=256, n=8):
    """n crops, half of them centred near a labelled object (stars are sparse)."""
    out = []
    for k in range(n):
        if k % 2 == 0 and len(fr.xy):
            x, y = fr.xy[rng.integers(len(fr.xy))]
            x0 = int(np.clip(x - rng.uniform(16, size - 16), 0, W - size))
            y0 = int(np.clip(y - rng.uniform(16, size - 16), 0, H - size))
        else:
            x0, y0 = int(rng.integers(0, W - size + 1)), int(rng.integers(0, H - size + 1))
        out.append((fr.img[y0:y0 + size, x0:x0 + size], targets(fr, x0, y0, size)))
    return out


def preview(path):
    rng = np.random.default_rng(3)
    tiles = []
    for rate, gl in [(0, False), (5, True), (12, False), (20, True)]:
        fr = make_frame(rng, rate_dps=rate, glare=gl, n_hot=20, planet=True, noise=2.0, bg=4)
        rgb = cv2.cvtColor(fr.img, cv2.COLOR_GRAY2BGR)
        col = {STAR: (80, 220, 80), PLANET: (60, 140, 255), HOT: (255, 80, 200)}
        for (x, y), c, q in zip(fr.xy, fr.cls, fr.snr):
            if c == STAR and q < SNR_POS:
                cv2.circle(rgb, (int(x), int(y)), 9, (90, 90, 90), 1)
            else:
                cv2.circle(rgb, (int(x), int(y)), 9, col[int(c)], 2)
        cv2.putText(rgb, f"{rate} deg/s  glare={gl}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)
        tiles.append(cv2.resize(rgb, (W // 2, H // 2), interpolation=cv2.INTER_AREA))
    grid = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
    cv2.imwrite(str(path), grid)
    print("wrote", path)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent / "out"
    out.mkdir(exist_ok=True)
    preview(out / "patches_preview.png")
