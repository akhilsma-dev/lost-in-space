"""Side-by-side picture of tetra3's blob finder vs StarNet on one hard frame.

  .venv/bin/python -m ml.viz --rate 10 --glare      -> ml/out/detect_example.png
"""
import argparse
import math
import pathlib

import cv2
import numpy as np

np.math = math

import tetra3  # noqa: E402

from ml.detect import detect_full  # noqa: E402
from ml.patches import CLASSES, GLARE, HOT, PLANET, STAR, detectable_stars, make_frame  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=10)
    ap.add_argument("--glare", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    fr = make_frame(np.random.default_rng(a.seed), rate_dps=a.rate, glare=a.glare, n_hot=20, planet=True,
                    noise=2.0, bg=4.0)
    base = cv2.cvtColor(np.clip(fr.img.astype(np.float32) * 2.5, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    left, right = base.copy(), base.copy()
    true, _ = detectable_stars(fr)
    for im in (left, right):
        for x, y in true:
            cv2.circle(im, (int(round(x)), int(round(y))), 14, (90, 90, 90), 1)

    for y, x in np.asarray(tetra3.get_centroids_from_image(fr.img)).reshape(-1, 2):
        cv2.circle(left, (int(x), int(y)), 7, (60, 200, 255), 2)
    r = detect_full(fr.img)
    col = {STAR: (80, 230, 90), PLANET: (255, 140, 60), HOT: (220, 80, 220), GLARE: (60, 60, 255)}
    for (y, x), c in zip(r["yx"], r["cls"]):
        cv2.circle(right, (int(x), int(y)), 7 if c != GLARE else 40, col[int(c)], 2)
        if c != STAR:
            cv2.putText(right, CLASSES[c], (int(x) + 10, int(y) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col[int(c)], 2)
    n_cnn = int((r["cls"] == STAR).sum())
    cv2.putText(left, f"tetra3 blob finder: {len(tetra3.get_centroids_from_image(fr.img))} blobs",
                (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (60, 200, 255), 3)
    cv2.putText(right, f"StarNet: {n_cnn} stars (+ planet / hot px / glare flagged)",
                (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (80, 230, 90), 3)
    for im in (left, right):
        cv2.putText(im, f"{a.rate:g} deg/s tumble, glare={a.glare}; grey rings = true stars", (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (200, 200, 200), 2)
    out = np.hstack([left, np.zeros((fr.img.shape[0], 20, 3), np.uint8), right])
    out = cv2.resize(out, (out.shape[1] // 2, out.shape[0] // 2), interpolation=cv2.INTER_AREA)
    OUT.mkdir(exist_ok=True)
    cv2.imwrite(str(OUT / "detect_example.png"), out)
    print("wrote", OUT / "detect_example.png")


if __name__ == "__main__":
    main()
