"""Inference API for the live pipeline.

    from ml.detect import detect
    centroids_yx, classes, scores = detect(img_u8)

* `img_u8`: (H, W) uint8 grayscale frame in screen-pixel coordinates (after the
  camera->screen homography warp in the live pipeline). Any size works.
* `centroids_yx`: (N, 2) float, **tetra3 convention** (pixel-corner origin), so
  `t3.solve_from_centroids(centroids_yx[classes == STAR], (H, W), ...)` works as is.
* `classes`: (N,) int, index into `CLASSES` = star, planet, glare, hot_pixel, background.
  Stars come first, sorted brightest-first (by predicted magnitude), which is the
  order tetra3 expects. A single "glare" entry (centroid of the glare region) is
  appended when the net sees glare in the frame.
* `scores`: (N,) float in [0, 1] (for glare: fraction of the frame flagged as glare).

`detect_full` additionally returns predicted magnitudes and the raw maps.
"""
import pathlib

import numpy as np
import torch
import torch.nn.functional as F

from ml.model import STRIDE, StarNet
from ml.patches import CLASSES, GLARE, HOT, PLANET, STAR

WEIGHTS = pathlib.Path(__file__).resolve().parent / "weights" / "starnet.pt"
HEAT_CLASS = [STAR, PLANET, HOT]
THRESH = 0.3

_model = None
_dev = None


def load(weights=WEIGHTS, device=None):
    """device None -> CUDA / Apple MPS if available (~10x faster), else CPU."""
    global _model, _dev
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    _dev = torch.device(device)
    m = StarNet()
    m.load_state_dict(torch.load(weights, map_location="cpu")["state_dict"])
    _model = m.to(_dev).eval()
    return _model


@torch.no_grad()
def detect_full(img_u8, thresh=THRESH, max_stars=60):
    if _model is None:
        load()
    h, w = img_u8.shape
    ph, pw = (-h) % 16, (-w) % 16
    x = torch.from_numpy(np.ascontiguousarray(img_u8, dtype=np.float32) / 255.0)[None, None]
    if ph or pw:
        x = F.pad(x, (0, pw, 0, ph), mode="replicate")  # zero pad = fake edge = false stars
    out = _model(x.to(_dev))
    heat = torch.sigmoid(out["heat"])
    peaks = heat * (heat == F.max_pool2d(heat, 3, 1, 1))
    # one class per location: keep the strongest channel
    best = peaks.max(1, keepdim=True).values
    peaks = peaks * (peaks == best)
    c, j, i = torch.nonzero(peaks[0] > thresh, as_tuple=True)
    sc = peaks[0, c, j, i]
    off = out["off"][0, :, j, i]
    xs = STRIDE * (i.float() + off[0])
    ys = STRIDE * (j.float() + off[1])
    mag = out["mag"][0, 0, j, i]
    keep = (xs <= w - 1) & (ys <= h - 1)
    cls = torch.tensor(HEAT_CLASS, device=c.device)[c]
    yx = torch.stack([ys, xs], 1)[keep].cpu().numpy() + 0.5  # -> tetra3 pixel-corner convention
    cls, sc, mag = cls[keep].cpu().numpy(), sc[keep].cpu().numpy(), mag[keep].cpu().numpy()

    # order: stars brightest-first (cap at max_stars), then planets, then hot pixels
    st = np.flatnonzero(cls == STAR)
    st = st[np.argsort(mag[st])][:max_stars]
    other = np.flatnonzero(cls != STAR)
    other = other[np.lexsort((-sc[other], cls[other]))]
    order = np.concatenate([st, other]).astype(int)
    yx, cls, sc, mag = yx[order], cls[order], sc[order], mag[order]

    g = torch.sigmoid(out["glare"][0, 0, : (h + 3) // STRIDE, : (w + 3) // STRIDE]).cpu().numpy()
    gm = g > 0.5
    if gm.mean() > 0.01:
        gy, gx = np.nonzero(gm)
        yx = np.vstack([yx, [[STRIDE * gy.mean() + 2.0, STRIDE * gx.mean() + 2.0]]])
        cls = np.append(cls, GLARE)
        sc = np.append(sc, gm.mean())
        mag = np.append(mag, np.nan)
    return dict(yx=yx.astype(np.float64), cls=cls.astype(np.int64), score=sc.astype(np.float64),
                mag=mag, glare_map=g)


def detect(img_u8):
    """-> (centroids_yx [tetra3 convention], classes, scores). See module docstring."""
    r = detect_full(img_u8)
    return r["yx"], r["cls"], r["score"]


def star_centroids(img_u8, **kw):
    """Just the star centroids, brightest first, ready for tetra3.solve_from_centroids."""
    r = detect_full(img_u8, **kw)
    return r["yx"][r["cls"] == STAR]


__all__ = ["detect", "detect_full", "star_centroids", "load", "CLASSES", "STAR", "PLANET", "GLARE", "HOT"]
