"""Does the learned front-end matter? tetra3 solve rate vs tumble rate and glare.

Every frame is solved three ways, on the *same* pixels:
  tetra3        tetra3.solve_from_image (its own centroider, default settings)
  tetra3-tuned  tetra3 centroider with the blob-area cap lifted (max_area 100 -> 5000)
                so streaks aren't thrown away: the strongest classical baseline we
                could set up in one line, to keep the comparison honest
  StarNet       our CNN centroids -> tetra3.solve_from_centroids
All three use the same solver settings (fov 50 +/- 10 deg, 2 s timeout).

Frames: 1920x1080, 50 deg FOV, 0.1 s exposure, read noise 2 DN, background 4 DN,
20 hot pixels and one fake planet in every frame; glare on/off; tumble about a
random axis at 0, 2, 5, 10, 15, 20 deg/s. Frame seeds are fixed and the test
seed range is disjoint from training (training draws fresh random frames).

  .venv/bin/python -m ml.benchmark            # 200 frames per cell (2400 frames, ~15-25 min)
  .venv/bin/python -m ml.benchmark --n 20     # quick look
  .venv/bin/python -m ml.benchmark --sim-camera --n 50

Writes ml/out/bench_frames.csv, ml/out/benchmark.md, ml/out/solve_rate.png
(with --sim-camera: the same names with a _simcam suffix).

--sim-camera: every rendered frame is filmed by camera.fakecam.FakeCamera (keystone,
defocus, LCD black glow, vignetting, sensor noise, JPEG 85), none of which StarNet saw
in training. The camera->screen homography is auto-fitted once on the fake camera's view
of the checkerboard (camera.calibrate.fit), exactly as camera.solve_live --sim-camera does.
All three methods find centroids in camera pixels; the centroids are then warped to
screen pixels (dropping any that land off screen) and solved there with the sim's known
FOV. Plain tetra3 therefore runs as get_centroids_from_image (default settings) +
solve_from_centroids instead of solve_from_image, which is the same thing internally.
Recall / precision / centroid error are measured in screen pixels against the sim's
ground truth, so they include calibration error. Two extra rows, tetra3-tuned-warp and
StarNet-warp, instead warp the whole camera image back to screen pixels first and detect
there (so stars have the on-screen size StarNet was trained on).
"""
import argparse
import math
import os
import pathlib
import time
from multiprocessing import Pool

import numpy as np

np.math = math  # cedar-solve 0.5.1 still calls np.math, removed in NumPy 2

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "out"
DB = (ROOT.parent / "data" / "tetra3_wide.npz").resolve()
RATES = [0, 2, 5, 10, 15, 20]
GLARES = [False, True]
METHODS = ["tetra3", "tetra3-tuned", "StarNet"]
# --sim-camera only: detect on the camera frame warped back to screen pixels (cv2.warpPerspective with
# the fitted homography) instead of warping the centroids. This is the input ml/detect.py documents.
WARP_METHODS = ["tetra3-tuned-warp", "StarNet-warp"]
ALL_METHODS = METHODS + WARP_METHODS
CORRECT_DEG = 1.0  # a "solve" further than this from truth is a false match, not a success
MATCH_PX = 3.0
SOLVE = dict(fov_estimate=50.0, fov_max_error=10.0, solve_timeout=2000)

_t3 = None
_cam = None  # (FakeCamera, H_cam2screen, screen_w, screen_h) with --sim-camera


def _init(sim_camera=False):
    global _t3, _cam
    import torch
    torch.set_num_threads(1)
    os.environ["OMP_NUM_THREADS"] = "1"
    import cv2
    cv2.setNumThreads(1)
    import tetra3
    _t3 = tetra3.Tetra3(load_database=None)
    _t3.load_database(DB)
    from ml import detect
    detect.load(device="cpu")  # one single-threaded CPU model per worker
    if sim_camera:
        from camera.calibrate import fit
        from camera.fakecam import FakeCamera
        from ml.patches import H, W
        from sim.sky import checkerboard
        cam = FakeCamera(W, H, seed=0)
        r = fit(cam.view(checkerboard(W, H)[0]), W, H)
        _cam = (cam, r["H"], W, H)


def _to_screen(cents_yx):
    """Camera-pixel centroids (tetra3 convention) -> screen pixels (tetra3 convention), off-screen dropped."""
    from camera.common import warp_points
    _, Hc, sw, sh = _cam
    c = np.asarray(cents_yx, np.float64).reshape(-1, 2)
    xy = warp_points(c[:, ::-1] - 0.5, Hc)  # homography maps pixel-centre (OpenCV) coordinates
    keep = (xy[:, 0] > -0.5) & (xy[:, 0] < sw - 0.5) & (xy[:, 1] > -0.5) & (xy[:, 1] < sh - 0.5)
    return (xy[keep] + 0.5)[:, ::-1].copy()


def _att_err_deg(truth, res):
    from sim.sky import from_radecroll
    if res.get("RA") is None:
        return None
    est = from_radecroll(res["RA"], res["Dec"], res["Roll"])
    return math.degrees((truth.inv() * est).magnitude())


def _centroid_stats(cents_yx, fr):
    """cents in tetra3 convention. -> (recall, precision, median err px, n_false_on_planet_or_hot)."""
    from ml.patches import HOT, PLANET, detectable_stars
    true, _ = detectable_stars(fr)
    if len(cents_yx) == 0 or len(true) == 0:
        return 0.0, float("nan"), float("nan"), 0
    c = cents_yx[:, ::-1] - 0.5  # -> (x, y) pixel-centre convention
    d = np.linalg.norm(c[:, None] - true[None], axis=-1)
    hit_true = d.min(0) < MATCH_PX
    hit_det = d.min(1) < MATCH_PX
    junk = fr.xy[(fr.cls == PLANET) | (fr.cls == HOT)]
    n_junk = int((np.linalg.norm(c[:, None] - junk[None], axis=-1).min(1) < MATCH_PX).sum()) if len(junk) else 0
    err = np.median(d.min(1)[hit_det]) if hit_det.any() else float("nan")
    return float(hit_true.mean()), float(hit_det.mean()), float(err), n_junk


def run_one(job):
    rate, glare, i = job
    import tetra3
    from PIL import Image
    from ml.detect import star_centroids
    from ml.patches import make_frame

    seed = 1_000_000 + 10_000 * rate + 5_000 * glare + i
    fr = make_frame(np.random.default_rng(seed), rate_dps=rate, glare=glare, n_hot=20, planet=True,
                    noise=2.0, bg=4.0)
    h, w = fr.img.shape  # screen size: the solve always happens in screen pixels
    img = fr.img
    if _cam is not None:
        _cam[0].rng = np.random.default_rng(seed)  # reproducible camera noise per frame
        img = _cam[0].view(fr.img)
    rows = []

    def record(method, res, cents, dt):
        err = _att_err_deg(fr.att, res)
        rec, prec, cerr, junk = _centroid_stats(cents, fr)
        rows.append(dict(rate=rate, glare=int(glare), i=i, method=method, solved=int(err is not None),
                         correct=int(err is not None and err < CORRECT_DEG),
                         att_err_arcsec=(err * 3600 if err is not None else float("nan")),
                         n_cent=len(cents), recall=rec, precision=prec, cent_err_px=cerr, junk=junk,
                         sec=dt))

    def solve(cents):
        return _t3.solve_from_centroids(cents, (h, w), **SOLVE) if len(cents) >= 4 else {"RA": None}

    if _cam is None:
        t = time.time()
        cents = tetra3.get_centroids_from_image(img)
        res = _t3.solve_from_image(Image.fromarray(img), **SOLVE)
        record("tetra3", res, np.asarray(cents).reshape(-1, 2), time.time() - t)
    else:
        t = time.time()
        cents = _to_screen(tetra3.get_centroids_from_image(img))
        record("tetra3", solve(cents), cents, time.time() - t)

    t = time.time()
    cents = np.asarray(tetra3.get_centroids_from_image(img, max_area=5000)).reshape(-1, 2)
    if _cam is not None:
        cents = _to_screen(cents)
    res = _t3.solve_from_centroids(cents, (h, w), **SOLVE) if _cam is None else solve(cents)
    record("tetra3-tuned", res, cents, time.time() - t)

    t = time.time()
    cents = star_centroids(img)
    if _cam is not None:
        cents = _to_screen(cents)
    res = _t3.solve_from_centroids(cents, (h, w), **SOLVE) if _cam is None else solve(cents)
    record("StarNet", res, cents, time.time() - t)

    if _cam is not None:
        import cv2
        t = time.time()
        scr = cv2.warpPerspective(img, _cam[1], (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        t_warp = time.time() - t
        t = time.time()
        cents = np.asarray(tetra3.get_centroids_from_image(scr, max_area=5000)).reshape(-1, 2)
        record("tetra3-tuned-warp", solve(cents), cents, t_warp + time.time() - t)
        t = time.time()
        cents = star_centroids(scr)
        record("StarNet-warp", solve(cents), cents, t_warp + time.time() - t)
    return rows


def summarise(df):
    g = df.groupby(["glare", "rate", "method"])
    s = g.agg(frames=("solved", "size"), solve_rate=("solved", "mean"), correct_rate=("correct", "mean"),
              recall=("recall", "mean"), precision=("precision", "mean"), cent_err_px=("cent_err_px", "median"),
              junk_per_frame=("junk", "mean"), sec_per_frame=("sec", "mean"))
    s["median_att_err_arcsec"] = df[df.correct == 1].groupby(["glare", "rate", "method"])["att_err_arcsec"].median()
    return s.reset_index()


def plot(s, path, note=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    col = {"tetra3": "#c0504d", "tetra3-tuned": "#e3a33b", "StarNet": "#2f6fd6",
           "tetra3-tuned-warp": "#8a6d1f", "StarNet-warp": "#17a589"}
    methods = [m for m in ALL_METHODS if m in set(s.method)]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    for ax, gl in zip(axes, GLARES):
        for m in methods:
            d = s[(s.glare == int(gl)) & (s.method == m)].sort_values("rate")
            ax.plot(d.rate, 100 * d.correct_rate, "o-", color=col[m], lw=2.4 if m.startswith("StarNet") else 1.8,
                    ls="--" if m.endswith("-warp") else "-",
                    label=m + (" (ours)" if m.startswith("StarNet") else ""))
        ax.set_title("with sun glare" if gl else "no glare")
        ax.set_xlabel("tumble rate (deg/s), 0.1 s exposure")
        ax.set_xticks(RATES)
        ax.set_ylim(-3, 103)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel(f"correct attitude solves (%)\n(error < {CORRECT_DEG:g} deg)")
    axes[0].legend(loc="lower left")
    n = int(s.frames.max())
    fig.suptitle(f"tetra3 solve rate vs tumble: classical centroiding vs StarNet front-end "
                 f"({n} frames per point, 20 hot px + planet + noise){note}", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def markdown(s, path, n, minutes, title="StarNet benchmark"):
    lines = [f"# {title} ({n} frames per cell, {minutes:.0f} min wall)\n",
             "correct = tetra3 returned an attitude within "
             f"{CORRECT_DEG:g} deg of truth. recall / precision: detected centroids within {MATCH_PX:g} px "
             "of a detectable star (matched-filter SNR >= 5). junk = centroids sitting on the planet or a hot pixel. "
             "att err = median total rotation error of correct solves.\n"]
    for gl in GLARES:
        lines.append(f"\n## {'With' if gl else 'No'} glare\n")
        lines.append("| rate deg/s | method | correct % | any solve % | att err (arcsec) | recall | precision | "
                     "centroid err px | junk/frame | s/frame |")
        lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        d = s[s.glare == int(gl)].sort_values(["rate", "method"], key=lambda c: c.map(
            {m: k for k, m in enumerate(ALL_METHODS)}) if c.name == "method" else c)
        for _, r in d.iterrows():
            lines.append(f"| {r.rate} | {r.method} | {100 * r.correct_rate:.1f} | {100 * r.solve_rate:.1f} | "
                         f"{r.median_att_err_arcsec:.0f} | {r.recall:.2f} | {r.precision:.2f} | "
                         f"{r.cent_err_px:.2f} | {r.junk_per_frame:.1f} | {r.sec_per_frame:.2f} |")
    path.write_text("\n".join(lines) + "\n")


def main():
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--sim-camera", action="store_true",
                    help="film every frame through camera.fakecam (keystone, blur, vignette, JPEG) + auto-calibration")
    a = ap.parse_args()
    sfx = "_simcam" if a.sim_camera else ""
    OUT.mkdir(exist_ok=True)
    jobs = [(r, g, i) for i in range(a.n) for g in GLARES for r in RATES]
    t0 = time.time()
    rows = []
    with Pool(a.workers, initializer=_init, initargs=(a.sim_camera,)) as pool:
        for k, rr in enumerate(pool.imap_unordered(run_one, jobs, chunksize=2), 1):
            rows += rr
            if k % 100 == 0 or k == len(jobs):
                print(f"{k}/{len(jobs)} frames  {(time.time() - t0) / 60:.1f} min", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / f"bench_frames{sfx}.csv", index=False)
    s = summarise(df)
    minutes = (time.time() - t0) / 60
    md = OUT / f"benchmark{sfx}.md"
    markdown(s, md, a.n, minutes,
             title="StarNet benchmark through the fake webcam" if a.sim_camera else "StarNet benchmark")
    plot(s, OUT / f"solve_rate{sfx}.png", "  [through fake webcam]" if a.sim_camera else "")
    print(md.read_text())
    print("wrote", OUT / f"solve_rate{sfx}.png")


if __name__ == "__main__":
    main()
