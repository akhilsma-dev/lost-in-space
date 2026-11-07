"""tetra3 demo: render a fake camera frame of the sky at a hidden attitude,
hand only the pixels to tetra3, and show what it recovers.

Run:  .venv/bin/python demo/tetra3_demo.py
Out:  demo/out/demo.png  (one row per scenario)
"""
import json
import math
import pathlib

import numpy as np

np.math = math  # cedar-solve 0.5.1 still calls np.math, removed in NumPy 2

import cv2  # noqa: E402
import tetra3  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "demo" / "out"
W, H, FOV = 1280, 960, 50.0
RNG = np.random.default_rng(7)


def font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


# ---------- sky data (independent of tetra3's own database) ----------
def unit(ra_deg, dec_deg):
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], -1)


stars = json.loads((DATA / "stars.6.json").read_text())["features"]
S_HIP = np.array([f["id"] for f in stars])
S_MAG = np.array([f["properties"]["mag"] for f in stars])
S_VEC = unit(*np.array([f["geometry"]["coordinates"] for f in stars]).T)
NAMES = json.loads((DATA / "starnames.json").read_text())
LINES = json.loads((DATA / "constellations.lines.json").read_text())["features"]


def star_label(hip):
    n = NAMES.get(str(hip))
    if not n:
        return None
    if n["name"]:
        return n["name"]
    if n["bayer"]:
        return f'{n["bayer"]} {n["c"]}'
    return None


# ---------- camera model ----------
def basis(ra_deg, dec_deg, roll_deg):
    """Camera axes: boresight c, image-right r, image-up u (tetra3's convention)."""
    c = unit(ra_deg, dec_deg)
    east = np.cross([0, 0, 1], c)
    east /= np.linalg.norm(east)
    north = np.cross(c, east)
    t = -math.radians(roll_deg)  # tetra3 measures roll the other way
    right = -east * math.cos(t) + north * math.sin(t)
    up = north * math.cos(t) + east * math.sin(t)
    return c, right, up


def project(vecs, att):
    c, r, u = basis(*att)
    f = (W / 2) / math.tan(math.radians(FOV / 2))
    z = vecs @ c
    with np.errstate(divide="ignore", invalid="ignore"):
        px = W / 2 + f * (vecs @ r) / z
        py = H / 2 - f * (vecs @ u) / z
    return px, py, z > 0.05


def render(att):
    img = np.zeros((H, W), np.float32)
    px, py, front = project(S_VEC, att)
    yy, xx = np.mgrid[-6:7, -6:7]
    for x, y, m in zip(px[front], py[front], S_MAG[front]):
        if not (6 <= x < W - 7 and 6 <= y < H - 7):
            continue
        amp = 900 * 10 ** (-0.4 * m)
        xi, yi = int(x), int(y)
        g = amp * np.exp(-((xx - (x - xi)) ** 2 + (yy - (y - yi)) ** 2) / (2 * 1.3**2))
        img[yi - 6:yi + 7, xi - 6:xi + 7] += g
    return img


def finish(img):
    img = img + 6 + RNG.normal(0, 2.5, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


# ---------- scenarios ----------
def scenario_clean(att):
    return finish(render(att))


def scenario_messy(att):
    img = render(att)
    # a "planet": bright object that is not in the star catalogue
    yy, xx = np.mgrid[0:H, 0:W]
    img += 255 * np.exp(-((xx - 880) ** 2 + (yy - 300) ** 2) / (2 * 3.0**2))
    # sun/moon glare from one corner
    img += 160 * np.exp(-np.hypot(xx - W, yy - H) / 260)
    # hot pixels
    for _ in range(40):
        img[RNG.integers(0, H), RNG.integers(0, W)] = 255
    # part of the view blocked (solar panel / spacecraft body)
    img[:, :260] = 0
    return finish(img)


def scenario_tumble(att):
    img = render(att)
    k = np.zeros((61, 61), np.float32)
    cv2.line(k, (0, 10), (60, 50), 1, 1)  # ~70 px smear: spacecraft spinning during exposure
    img = cv2.filter2D(img, -1, k / k.sum()) * 6
    return finish(img)


SCENARIOS = [
    ("1. Clean frame", "Camera points somewhere unknown. Only the pixels go to tetra3.", scenario_clean,
     (83.8, -5.4, 20.0)),
    ("2. Messy but still solvable", "Planet, glare, 40 hot pixels, left 20% of view blocked.", scenario_messy,
     (201.3, -40.0, -35.0)),
    ("3. Tumbling (motion blur)", "Same sky as #1, but stars smear into streaks while spinning.", scenario_tumble,
     (83.8, -5.4, 20.0)),
]


# ---------- solve + draw ----------
def ang_err(a, b):
    return math.degrees(math.acos(np.clip(unit(a[0], a[1]) @ unit(b[0], b[1]), -1, 1)))


def panel(t3, title, sub, fn, truth):
    raw = fn(truth)
    res = t3.solve_from_image(Image.fromarray(raw), fov_estimate=FOV, fov_max_error=10,
                              return_matches=True)
    cents = tetra3.get_centroids_from_image(raw)
    solved = res.get("RA") is not None

    left = Image.fromarray(raw).convert("RGB")
    right = Image.fromarray((raw * 0.55).astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(right)
    f_sm, f_md = font(20), font(26)

    # everything tetra3's own blob finder picked up (yellow ticks)
    for y, x in cents[:200]:
        d.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(200, 170, 40), width=1)

    lines = [f"Truth : RA {truth[0]:7.2f}  Dec {truth[1]:6.2f}  Roll {truth[2] % 360:6.1f}"]
    if solved:
        est = (res["RA"], res["Dec"], res["Roll"])
        # constellation lines drawn from the SOLVED attitude: if they sit on the stars, it worked
        for feat in LINES:
            for seg in feat["geometry"]["coordinates"]:
                v = unit(*np.array(seg).T)
                px, py, ok = project(v, est)
                for i in range(len(seg) - 1):
                    if ok[i] and ok[i + 1]:
                        d.line([(px[i], py[i]), (px[i + 1], py[i + 1])], fill=(70, 130, 220), width=2)
        # matched stars: green rings + names
        idx = [np.where(t3.star_catalog_IDs == cid)[0][0] for cid in res["matched_catID"]]
        for (y, x), i in zip(res["matched_centroids"], idx):
            d.ellipse([x - 11, y - 11, x + 11, y + 11], outline=(60, 230, 110), width=3)
            v = t3.star_table[i, 2:5]
            j = int(np.argmax(S_VEC @ v))
            name = star_label(S_HIP[j]) if S_VEC[j] @ v > math.cos(math.radians(0.2)) else None
            if name and S_MAG[j] < 3.2:
                d.text((x + 14, y - 12), name, fill=(220, 255, 220), font=f_sm)
        lines.append(f"Solved: RA {est[0]:7.2f}  Dec {est[1]:6.2f}  Roll {est[2]:6.1f}")
        lines.append(f"Pointing error {ang_err(truth, est) * 3600:.0f} arcsec | {res['Matches']} stars matched "
                     f"| {res['T_solve']:.0f} ms")
    else:
        lines.append("Solved: FAILED - no star pattern matched")
        lines.append(f"tetra3 found {len(cents)} blobs but they are streaks, not points")

    # assemble row: header + two images
    gap, head = 24, 150
    row = Image.new("RGB", (W * 2 + gap * 3, H + head + gap), (14, 16, 24))
    row.paste(left, (gap, head))
    row.paste(right, (W + gap * 2, head))
    dr = ImageDraw.Draw(row)
    dr.text((gap, 18), title, fill=(255, 255, 255), font=font(40))
    dr.text((gap, 70), sub, fill=(170, 180, 200), font=f_md)
    dr.text((gap, 110), "What the camera sees  (input to tetra3)", fill=(140, 150, 170), font=f_md)
    dr.text((W + gap * 2, 110), "What tetra3 worked out", fill=(140, 150, 170), font=f_md)
    box_col = (60, 230, 110) if solved else (240, 90, 80)
    x0, y0 = W + gap * 2 + 16, head + H - 16 - 3 * 34
    dr.rectangle([x0 - 10, y0 - 10, x0 + 900, y0 + 3 * 34], fill=(0, 0, 0), outline=box_col, width=2)
    for k, s in enumerate(lines):
        dr.text((x0, y0 + k * 34), s, fill=box_col if k else (220, 220, 220), font=font(24))
    return row, solved, res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t3 = tetra3.Tetra3(load_database=None)
    t3.load_database((DATA / "tetra3_wide.npz").resolve())
    rows = []
    for sc in SCENARIOS:
        row, ok, res = panel(t3, *sc)
        print(f"{sc[0]:32s} solved={ok}  RA={res.get('RA')}  Dec={res.get('Dec')}  Roll={res.get('Roll')}")
        rows.append(row)
    legend = Image.new("RGB", (rows[0].width, 70), (14, 16, 24))
    ImageDraw.Draw(legend).text(
        (24, 18), "Legend:  yellow = blobs tetra3 detected   green ring = identified as a catalogue star   "
                  "blue = constellation lines drawn from tetra3's answer (they only line up if it's right)",
        fill=(200, 200, 210), font=font(24))
    out = Image.new("RGB", (rows[0].width, sum(r.height for r in rows) + legend.height), (14, 16, 24))
    y = 0
    for r in [legend, *rows]:
        out.paste(r, (0, y))
        y += r.height
    out = out.resize((out.width // 2, out.height // 2), Image.LANCZOS)
    out.save(OUT / "demo.png")
    print("wrote", OUT / "demo.png")


if __name__ == "__main__":
    main()
