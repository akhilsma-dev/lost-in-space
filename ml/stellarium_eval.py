"""Out-of-distribution check: the three star-ID front-ends on Stellarium-rendered frames.

StarNet was trained only on our own renderer (sim/sky.py), and ml/benchmark.py
tests it on the same renderer. Here every frame comes from Stellarium (real
catalogues, its own star sizes/colours/halos, Milky Way, nebula textures,
planets), captured by sim/stellarium_capture.py with known RA/Dec/roll/FOV.

Methods exactly as in ml/benchmark.py, same solver settings (fov 50 +/- 10 deg,
2 s timeout), same tetra3 database (data/tetra3_wide.npz):
  tetra3        tetra3.solve_from_image (its own default centroider)
  tetra3-tuned  get_centroids_from_image(max_area=5000) -> solve_from_centroids
  StarNet       ml.detect.star_centroids -> solve_from_centroids
Frames are converted to 8-bit grayscale (PIL "L") once; all methods see the same pixels.

No per-pixel ground truth exists for Stellarium frames, so the centroid metrics
use the tetra3 catalogue (BSC5, mag <= 5.5) projected with the true attitude:
  cat recall  = fraction of in-frame catalogue stars with a centroid within 3 px
  top-20 hit  = fraction of the 20 brightest centroids (the ones tetra3 builds
                patterns from first) that sit within 3 px of a catalogue star
                (Stellarium also draws fainter stars, so this is a purity proxy
                for "does the brightness ordering pick real bright stars").

  .venv/bin/python -m ml.stellarium_eval

Writes ml/out/stellarium_frames.csv, ml/out/stellarium_eval_results.md (table only)
and ml/out/stellarium_examples/*.png.
"""
import argparse
import csv
import math
import os
import pathlib
import time
from multiprocessing import Pool

import numpy as np

np.math = math  # cedar-solve 0.5.1 still calls np.math, removed in NumPy 2

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "out"
DATA = (ROOT.parent / "data" / "stellarium").resolve()
DB = (ROOT.parent / "data" / "tetra3_wide.npz").resolve()
METHODS = ["tetra3", "tetra3-tuned", "StarNet"]
# Diagnostic only (not a tuned method): StarNet with its "planet" class counted as stars,
# to measure how much of the failure is bright Stellarium stars being called planets.
DIAG = "StarNet star+planet (diag)"
ALL = METHODS + [DIAG]
CORRECT_DEG = 1.0
MATCH_PX = 3.0
TOPK = 20
SOLVE = dict(fov_estimate=50.0, fov_max_error=10.0, solve_timeout=2000)

_t3 = None
_cat = None


def _load_cat():
    d = np.load(DB)
    st = d["star_table"]  # ra, dec (rad), x, y, z, mag
    return st[:, 2:5].astype(float), st[:, 5].astype(float)


def _init():
    global _t3, _cat
    import torch
    torch.set_num_threads(1)
    os.environ["OMP_NUM_THREADS"] = "1"
    import cv2
    cv2.setNumThreads(1)
    import tetra3
    _t3 = tetra3.Tetra3(load_database=None)
    _t3.load_database(DB)
    _cat = _load_cat()
    from ml import detect
    detect.load(device="cpu")


def load_gray(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("L"))


def project_catalogue(row, cat, with_mag=False):
    """Catalogue stars in frame, (N, 2) in tetra3 (y, x) pixel-corner convention."""
    from sim.sky import from_radecroll
    xyz, mags = cat
    m = from_radecroll(float(row["ra_deg"]), float(row["dec_deg"]), float(row["roll_deg"])).as_matrix()
    cam = xyz @ m  # columns: image right, image down, boresight
    w, h = int(row["width"]), int(row["height"])
    f = (w / 2) / math.tan(math.radians(float(row["hfov_deg"]) / 2))
    front = cam[:, 2] > 0
    x = w / 2 + f * cam[front, 0] / cam[front, 2]
    y = h / 2 + f * cam[front, 1] / cam[front, 2]
    ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    yx = np.column_stack([y[ok], x[ok]])
    return (yx, mags[front][ok]) if with_mag else yx


def cent_stats(cents, cat_yx):
    if len(cat_yx) == 0:
        return float("nan"), float("nan")
    if len(cents) == 0:
        return 0.0, float("nan")
    d = np.linalg.norm(cents[:, None] - cat_yx[None], axis=-1)
    recall = float((d.min(0) < MATCH_PX).mean())
    top = d[:TOPK].min(1) < MATCH_PX
    return recall, float(top.mean())


def att_err_deg(row, res):
    from sim.sky import from_radecroll
    if res.get("RA") is None:
        return None
    truth = from_radecroll(float(row["ra_deg"]), float(row["dec_deg"]), float(row["roll_deg"]))
    est = from_radecroll(res["RA"], res["Dec"], res["Roll"])
    return math.degrees((truth.inv() * est).magnitude())


def centroids(method, g):
    import tetra3
    from ml.detect import star_centroids
    if method == "tetra3":
        return np.asarray(tetra3.get_centroids_from_image(g)).reshape(-1, 2)
    if method == "tetra3-tuned":
        return np.asarray(tetra3.get_centroids_from_image(g, max_area=5000)).reshape(-1, 2)
    if method == DIAG:
        from ml.detect import detect_full
        from ml.patches import PLANET, STAR
        r = detect_full(g)
        k = np.flatnonzero((r["cls"] == STAR) | (r["cls"] == PLANET))
        return r["yx"][k[np.argsort(r["mag"][k])]]
    return star_centroids(g)


def run_one(row):
    from PIL import Image
    g = load_gray(DATA / "images" / row["file"])
    h, w = g.shape
    cat_yx, cat_mag = project_catalogue(row, _cat, with_mag=True)
    solve = dict(SOLVE, fov_estimate=float(row["hfov_deg"]))
    out = []
    for method in ALL:
        t = time.time()
        cents = centroids(method, g)
        if method == "tetra3":  # as in ml/benchmark.py: tetra3's own pipeline end to end
            res = _t3.solve_from_image(Image.fromarray(g), **solve)
        else:
            res = _t3.solve_from_centroids(cents, (h, w), **solve)
        dt = time.time() - t
        err = att_err_deg(row, res)
        rec, top = cent_stats(cents, cat_yx)
        out.append(dict(file=row["file"], ra=row["ra_deg"], dec=row["dec_deg"], roll=row["roll_deg"],
                        bodies=row["bodies_in_view"], method=method, solved=int(err is not None),
                        correct=int(err is not None and err < CORRECT_DEG),
                        att_err_arcsec=(err * 3600 if err is not None else float("nan")),
                        n_cent=len(cents), n_cat=len(cat_yx), cat_recall=rec, top20_hit=top, sec=dt))
    # what StarNet calls each catalogue star (nearest detection of any class within 3 px)
    from ml.detect import detect_full
    from ml.patches import CLASSES
    r = detect_full(g)
    cls_rows = []
    for (cy, cx), mag in zip(cat_yx, cat_mag):
        d = np.hypot(r["yx"][:, 0] - cy, r["yx"][:, 1] - cx) if len(r["yx"]) else np.array([np.inf])
        j = int(d.argmin())
        cls_rows.append(dict(file=row["file"], mag=mag, cls=CLASSES[r["cls"][j]] if d[j] < MATCH_PX else "missed"))
    glare = int((r["cls"] == CLASSES.index("glare")).any())
    for o in out:
        o["starnet_glare_flag"] = glare
    return out, cls_rows


def summarise(df):
    g = df.groupby("method", sort=False)
    s = g.agg(frames=("solved", "size"), correct=("correct", "mean"), solved=("solved", "mean"),
              n_cent=("n_cent", "median"), cat_recall=("cat_recall", "mean"), top20_hit=("top20_hit", "mean"),
              sec=("sec", "mean"))
    s["att_err_arcsec"] = df[df.correct == 1].groupby("method")["att_err_arcsec"].median()
    return s.reindex(ALL)


def md_table(ct):
    head = "| " + " | ".join([ct.index.name or ""] + [str(c) for c in ct.columns]) + " |"
    sep = "|---|" + "---:|" * len(ct.columns)
    body = ["| " + " | ".join([str(i)] + [f"{v:g}" for v in r]) + " |" for i, r in ct.iterrows()]
    return "\n".join([head, sep] + body)


def table(s):
    lines = ["| method | correct % | any solve % | median att err (arcsec) | centroids/frame (median) | "
             "cat recall (mag<=5.5) | top-20 hit | s/frame |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for m, r in s.iterrows():
        lines.append(f"| {m} | {100 * r.correct:.0f} | {100 * r.solved:.0f} | {r.att_err_arcsec:.0f} | "
                     f"{r.n_cent:.0f} | {r.cat_recall:.2f} | {r.top20_hit:.2f} | {r.sec:.2f} |")
    return "\n".join(lines)


def annotate(rows_by_file, df, files, outdir):
    """One figure per frame: the image with each method's centroids and the catalogue stars."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cat = _load_cat()
    outdir.mkdir(parents=True, exist_ok=True)
    col = {"tetra3": "#e04040", "tetra3-tuned": "#f0a020", "StarNet": "#40a0ff", DIAG: "#c060ff"}
    for tag, f in files:
        row = rows_by_file[f]
        g = load_gray(DATA / "images" / f)
        cat_yx = project_catalogue(row, cat)
        fig, axes = plt.subplots(2, 2, figsize=(15, 9.2))
        for ax, m in zip(axes.ravel(), ALL):
            c = centroids(m, g)
            r = df[(df.file == f) & (df.method == m)].iloc[0]
            ax.imshow(g, cmap="gray", vmin=0, vmax=160)
            ax.scatter(cat_yx[:, 1], cat_yx[:, 0], s=90, facecolors="none", edgecolors="#30d060", lw=0.8)
            if len(c):
                ax.scatter(c[TOPK:, 1], c[TOPK:, 0], s=4, c=col[m], alpha=0.5, lw=0)
                ax.scatter(c[:TOPK, 1], c[:TOPK, 0], s=40, marker="x", c=col[m], lw=1.2)
            verdict = ("correct" if r.correct else f"WRONG ({r.att_err_arcsec / 3600:.0f} deg off)") \
                if r.solved else "no solve"
            ax.set_title(f"{m}: {verdict}, {int(r.n_cent)} centroids, top-20 hit {r.top20_hit:.2f}", fontsize=10)
            ax.set_xlim(0, g.shape[1])
            ax.set_ylim(g.shape[0], 0)
            ax.set_xticks([])
            ax.set_yticks([])
        fig.suptitle(f"Stellarium {f} ({tag}): RA {float(row['ra_deg']):.1f}, Dec {float(row['dec_deg']):.1f}, "
                     f"roll {float(row['roll_deg']):.0f}. green circles = catalogue stars mag<=5.5 at true attitude; "
                     f"x = 20 brightest centroids, dots = the rest. {row['bodies_in_view'] or ''}", fontsize=10)
        fig.tight_layout()
        fig.savefig(outdir / f"{f[:-4]}_{tag}.png", dpi=80)
        plt.close(fig)


def pick_examples(df):
    p = df.pivot(index="file", columns="method", values="correct")
    picks = []

    def first(mask, tag):
        idx = [f for f in p.index[mask] if f not in [x[1] for x in picks]]
        if idx:
            picks.append((tag, idx[0]))

    first((p["StarNet"] == 1) & (p["tetra3-tuned"] == 1) & (p["tetra3"] == 0), "starnet-ok_tetra3-default-fails")
    first((p["StarNet"] == 0) & (p["tetra3-tuned"] == 1), "starnet-fails")
    first((p["StarNet"] == 1) & (p["tetra3-tuned"] == 0), "tuned-fails")
    first((p["StarNet"] == 0) & (p["tetra3-tuned"] == 0), "all-fail")
    first((p["StarNet"] == 1) & (p["tetra3-tuned"] == 1) & (p["tetra3"] == 1), "all-ok")
    body = df.groupby("file").bodies.first().fillna("")
    first(body.reindex(p.index).str.len() > 0, "body-in-view")
    return picks[:5]


def main():
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--examples-only", action="store_true", help="redraw figures from stellarium_frames.csv")
    a = ap.parse_args()
    with open(DATA / "manifest.csv") as f:
        rows = list(csv.DictReader(f))
    if a.examples_only:
        df = pd.read_csv(OUT / "stellarium_frames.csv", keep_default_na=False, na_values=[""])
        df["bodies"] = df.bodies.fillna("")
        annotate({r["file"]: r for r in rows}, df, pick_examples(df), OUT / "stellarium_examples")
        return
    t0 = time.time()
    out, cls_rows = [], []
    with Pool(a.workers, initializer=_init) as pool:
        for k, (rr, cr) in enumerate(pool.imap_unordered(run_one, rows), 1):
            out += rr
            cls_rows += cr
            if k % 20 == 0 or k == len(rows):
                print(f"{k}/{len(rows)} frames  {(time.time() - t0) / 60:.1f} min", flush=True)
    df = pd.DataFrame(out).sort_values(["file", "method"])
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "stellarium_frames.csv", index=False)
    s = summarise(df)
    c = pd.DataFrame(cls_rows)
    c["mag bin"] = pd.cut(c.mag, [-2, 1, 2, 3, 4, 4.5, 5, 5.5]).astype(str)
    ct = pd.crosstab(c["mag bin"], c.cls)
    ct.insert(0, "n", ct.sum(axis=1))
    for k in ct.columns[1:]:
        ct[k] = (ct[k] / ct["n"]).round(2)
    glare = df.groupby("file").starnet_glare_flag.first().mean()
    txt = (f"{len(rows)} Stellarium frames, {(time.time() - t0) / 60:.1f} min wall\n\n" + table(s) + "\n\n"
           "What StarNet calls each in-frame catalogue star (fraction per magnitude bin; nearest detection "
           f"within {MATCH_PX:g} px, else missed):\n\n" + md_table(ct) + "\n\n"
           f"StarNet flags glare in {100 * glare:.0f}% of frames (there is no glare in these images).\n")
    (OUT / "stellarium_eval_results.md").write_text(txt)
    print(txt)
    picks = pick_examples(df)
    annotate({r["file"]: r for r in rows}, df, picks, OUT / "stellarium_examples")
    print("examples:", picks)


if __name__ == "__main__":
    main()
