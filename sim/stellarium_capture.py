"""Out-of-distribution test set: star fields drawn by Stellarium, not by our renderer.

Drives Stellarium (26.3, `brew install --cask stellarium`) through its built-in
scripting engine: we write a .ssc script with N random pointings, launch
Stellarium with a private user dir holding `sim/stellarium/config.ini` (the
user's own Stellarium settings are never touched), and it screenshots each
pointing and quits. (The Remote Control HTTP plugin would work too, but the
scripting route needs no local server.)

Camera model, verified with tetra3 on test frames (RA/Dec within 0.03 deg,
roll 0, FOV 50.01 deg):
  * Perspective (gnomonic) projection, 1920x1080 screenshots (custom size, so the
    window size does not matter), horizontal FOV 50 deg. Stellarium's FOV is the
    *vertical* one: 2*atan(tan(25 deg) * 1080/1920) = 29.40 deg.
  * Equatorial mount: image-up = celestial north, east = left -> tetra3 roll 0.
    Odd-numbered frames are then rotated 180 deg (an exact pixel permutation,
    no resampling) so the set also covers roll 180.
  * Fixed epoch JD 2451545.0 (2000-01-01 12:00), time frozen, aberration off, so
    "of date" ~= J2000. No landscape, atmosphere, labels, lines, grids, markers.
    Milky Way, nebula textures, Sun/Moon/planets left on (recorded per frame).
  * Star brightness/size: Stellarium defaults (relative/absolute scale 1.0),
    twinkle off, Bortle 1, luminance adaptation on (default).

  .venv/bin/python -m sim.stellarium_capture            # 100 frames, ~4 min
  .venv/bin/python -m sim.stellarium_capture --n 5      # quick look

Writes data/stellarium/images/*.png (gitignored) and data/stellarium/manifest.csv.
"""
import argparse
import csv
import math
import pathlib
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "sim" / "stellarium" / "config.ini"
OUT = ROOT / "data" / "stellarium"
APP = "/Applications/Stellarium.app/Contents/MacOS/stellarium"
W, H = 1920, 1080
HFOV = 50.0
VFOV = math.degrees(2 * math.atan(math.tan(math.radians(HFOV / 2)) * H / W))
EPOCH = "2000-01-01 12:00 (JD 2451545.0), time rate 0"
BODIES = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]


def pointings(n, seed):
    """Uniform on the sphere."""
    rng = np.random.default_rng(seed)
    ra = rng.uniform(0, 360, n)
    dec = np.degrees(np.arcsin(rng.uniform(-1, 1, n)))
    return ra, dec


def ssc(ra, dec, shot_dir):
    pts = ",\n".join(f"[{r:.6f}, {d:.6f}]" for r, d in zip(ra, dec))
    bodies = ", ".join(f'"{b}"' for b in BODIES)
    return f"""
core.setTimeRate(0);
core.setGuiVisible(false);
core.setMountMode("equatorial");
core.setProjectionMode("ProjectionPerspective");
core.wait(2);
core.output("JD " + core.getJDay());
var B = [{bodies}];
for (var k = 0; k < B.length; k++) {{
  var o = core.getObjectInfo(B[k]);
  core.output("BODY " + B[k] + " " + o["raJ2000"] + " " + o["decJ2000"] + " " + o["vmag"]);
}}
var P = [{pts}];
for (var i = 0; i < P.length; i++) {{
  core.moveToRaDecJ2000(P[i][0] + "d", P[i][1] + "d", 0);
  StelMovementMgr.zoomTo({VFOV:.6f}, 0);
  core.wait(1.5);
  core.output("FRAME " + i + " fov " + StelMovementMgr.getCurrentFov());
  core.screenshot("f" + ("000" + i).slice(-3), false, "{shot_dir}/", true, "png");
  core.wait(0.5);
}}
core.quitStellarium();
"""


def ang_sep(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(np.radians, (ra1, dec1, ra2, dec2))
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(r1 - r2)
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    img_dir = OUT / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    ra, dec = pointings(a.n, a.seed)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        user, shots = tmp / "user", tmp / "shots"
        (user / "scripts").mkdir(parents=True)
        shots.mkdir()
        shutil.copy(CONFIG, user / "config.ini")
        (user / "scripts" / "capture.ssc").write_text(ssc(ra, dec, shots))
        print(f"Stellarium: {a.n} pointings, vertical fov {VFOV:.4f} deg ...", flush=True)
        subprocess.run([APP, "--user-dir", str(user), "--screenshot-dir", str(shots),
                        "--startup-script", "capture.ssc"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log = (user / "output.txt").read_text().splitlines()
        bodies = {}
        for ln in log:
            if ln.startswith("BODY"):
                _, name, bra, bdec, mag = ln.split()
                bodies[name] = (float(bra) % 360, float(bdec), float(mag))
        jd = next(ln.split()[1] for ln in log if ln.startswith("JD"))

        rows = []
        for i in range(a.n):
            src = shots / f"f{i:03d}.png"
            img = Image.open(src).convert("RGB")
            assert img.size == (W, H), img.size
            roll = 180.0 if i % 2 else 0.0
            if roll:
                img = img.transpose(Image.Transpose.ROTATE_180)
            name = f"f{i:03d}.png"
            img.save(img_dir / name)
            # anything within the half-diagonal of the frame (generous "in view")
            half_diag = math.degrees(math.atan(math.tan(math.radians(HFOV / 2)) * math.hypot(W, H) / W))
            inview = [b for b, (bra, bdec, _) in bodies.items() if ang_sep(ra[i], dec[i], bra, bdec) < half_diag]
            rows.append(dict(file=name, ra_deg=round(ra[i], 6), dec_deg=round(dec[i], 6), roll_deg=roll,
                             hfov_deg=HFOV, vfov_deg=round(VFOV, 6), width=W, height=H,
                             bodies_in_view=";".join(inview)))

    with open(OUT / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(OUT / "bodies.csv", "w", newline="") as f:
        f.write(f"# Stellarium JD {jd} ({EPOCH}); J2000 positions\nbody,ra_deg,dec_deg,vmag\n")
        for b, (bra, bdec, mag) in bodies.items():
            f.write(f"{b},{bra:.4f},{bdec:.4f},{mag:.2f}\n")
    print(f"wrote {a.n} images to {img_dir} and {OUT / 'manifest.csv'}")
    print("bodies in view:", sum(bool(r['bodies_in_view']) for r in rows), "frames")


if __name__ == "__main__":
    main()
