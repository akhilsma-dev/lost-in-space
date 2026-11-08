"""Position from planets: where in the Solar System is the spacecraft?

Stars give attitude only (they are effectively at infinity). Bodies inside the Solar System
are not: each sighted body i at known heliocentric position X_i (DE421) gives a line of sight
u_i through the spacecraft, so the spacecraft P lies on the line X_i - s u_i. Three or more
lines fix P.

Pipeline (one frame or a scan of frames):
  1. tetra3 locks the attitude from the stars (exactly as camera/solve_live.py does).
  2. Blobs that do not sit on a catalogue star once the attitude is known are planet candidates.
  3. pixel -> camera ray (pinhole, f = (w/2)/tan(fov/2); x right, y down, z boresight)
     -> inertial ray with the solved attitude.
  4. identify: brute force over (3 candidates x 3 bodies) seeds; each seed fixes P, predicts every
     body's direction from P and collects the other candidates that agree; the assignment with
     the most agreeing bodies (then the lowest residual) and a plausible P (0.3-6 AU) wins.
  5. position fix: linear least squares  sum_i W_i (I - u_i u_i^T)(X_i - P) = 0,
     first unweighted, then with W_i = 1/r_i^2 (r_i = range to body i), then with light-time
     corrected body positions. The Sun may be one of the bodies (X = 0), e.g. from a sun sensor.
  6. covariance  C ~= sigma^2 (sum_i (I - u_i u_i^T)/r_i^2)^-1  (sigma = direction error, rad);
     its eigenvectors / sqrt(eigenvalues) are the 1-sigma error ellipsoid.

Offline benchmark (no camera, no sim.live):
  .venv/bin/python -m camera.navigate --bench                 # geometry + rendered, 24 positions
  .venv/bin/python -m camera.navigate --bench --mode geometry --n 200
  .venv/bin/python -m camera.navigate --bench --mode rendered --n 12 --save-frames
  .venv/bin/python -m camera.navigate --bench --mode rendered --noise 3 --tag noise3
  .venv/bin/python -m camera.navigate --bench --mode rendered --sim-camera --tag simcam
Writes camera/out/nav_bench.md, nav_bench.csv and nav_bench.png (with --tag X: nav_bench_X.*).
--sim-camera: every screen frame is filmed by camera.fakecam.FakeCamera (keystone, defocus, LCD
glow, vignetting, sensor noise, JPEG 85); the camera->screen homography is fitted once on the fake
camera's view of the checkerboard and centroids are warped to screen pixels, as in
camera.solve_live --sim-camera.
"""
import argparse
import csv
import itertools
import math
import pathlib
import time

import numpy as np

from sim import planets as pl

ARCSEC = math.pi / 180 / 3600
OUT = pathlib.Path(__file__).resolve().parent / "out"
R_MIN_AU, R_MAX_AU = 0.3, 6.0


# ---------- geometry ----------
def pixel_rays(xy, w, h, fov_deg, att=None):
    """Pixels (N, 2) (OpenCV convention, x right, y down) -> unit rays (N, 3). Camera frame if att
    is None, else inertial: att is the sim.sky attitude (matrix columns = camera axes in ICRF)."""
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    f = (w / 2) / math.tan(math.radians(fov_deg / 2))
    v = np.column_stack([(xy[:, 0] - w / 2) / f, (xy[:, 1] - h / 2) / f, np.ones(len(xy))])
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v if att is None else v @ att.as_matrix().T


def _proj(u):
    """I - u u^T for each row of u: (..., 3) -> (..., 3, 3)."""
    return np.eye(3) - u[..., :, None] * u[..., None, :]


def position_fix(u, X, w=None):
    """Least-squares point closest to the lines {X_i - s u_i}: solves
    sum_i w_i (I - u_i u_i^T)(X_i - P) = 0. u: (N, 3) unit directions from P to body i,
    X: (N, 3) body positions (km), w: (N,) weights (default 1). Returns P (3,)."""
    A = _proj(np.asarray(u, np.float64))
    if w is not None:
        A = A * np.asarray(w, np.float64)[:, None, None]
    return np.linalg.solve(A.sum(0), np.einsum("nij,nj->i", A, X))


def covariance(P, X, sigma_rad):
    """(sum (I - u u^T)/(sigma_i^2 r_i^2))^-1 with u, r from P to each body (km^2); with one
    sigma for all sightings this is sigma^2 (sum (I - u u^T)/r^2)^-1. sigma_rad: scalar or (N,)."""
    d = np.asarray(X, np.float64) - P
    r = np.linalg.norm(d, axis=1)
    sig = np.broadcast_to(np.asarray(sigma_rad, np.float64), r.shape)
    info = (_proj(d / r[:, None]) / ((sig * r) ** 2)[:, None, None]).sum(0)
    return np.linalg.inv(info)


def ellipsoid(cov):
    """1-sigma semi-axes (km, largest first) and their directions (columns)."""
    val, vec = np.linalg.eigh(cov)
    o = np.argsort(val)[::-1]
    return np.sqrt(np.maximum(val[o], 0)), vec[:, o]


def residuals_arcsec(u, X, P):
    d = np.asarray(X, np.float64) - P
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    return np.degrees(np.arccos(np.clip(np.sum(d * u, 1), -1, 1))) * 3600


def refine(u, X, w=None, n_iter=2):
    """Unweighted fix, then reweighted by w_i/range_i^2 (w_i = 1/sigma_i^2; with angular noise
    sigma_i this is approximately the maximum-likelihood fix)."""
    w = np.ones(len(X)) if w is None else np.asarray(w, np.float64)
    P = position_fix(u, X)
    for _ in range(n_iter):
        r = np.linalg.norm(X - P, axis=1)
        P = position_fix(u, X, w / r ** 2)
    return P


# ---------- unresolved pairs ----------
# Seen from a few tenths of an AU, Earth and the Moon are a few pixels apart and the blob finder
# returns one centroid: the flux-weighted photocentre. "Earth+Moon" is a pseudo-body for that blob.
BLENDS = [("Earth", "Moon")]


def true_flux(mag):
    return 10 ** (-0.4 * np.asarray(mag, np.float64))


def targets(names, pos, P, flux=true_flux):
    """Points the sightings aim at: body positions, or for a blend "A+B" a point at A's range along
    the flux-weighted mean direction of A and B (depends on P, so callers iterate)."""
    X = []
    for n in names:
        if "+" not in n:
            X.append(pos[n])
            continue
        parts = n.split("+")
        d = np.array([pos[q] - P for q in parts])
        rng_ = np.linalg.norm(d, axis=1)
        w = flux([pl.magnitude(q, pos[q], P)["mag"] for q in parts])
        ub = (w[:, None] * d / rng_[:, None]).sum(0)
        X.append(P + rng_[0] * ub / np.linalg.norm(ub))
    return np.array(X).reshape(-1, 3)


def refine_targets(u, names, pos, P0, flux=true_flux, w=None, n_iter=3):
    P = P0
    for _ in range(n_iter if any("+" in n for n in names) else 1):
        P = refine(u, targets(names, pos, P, flux), w)
    return P


def solve_with_light_time(u, names, eph, jd, P0, flux=true_flux, w=None, n_iter=2):
    """Re-solve with each body at its retarded position as seen from the current estimate."""
    real = sorted({q for n in names for q in n.split("+")})
    P = P0
    for _ in range(n_iter):
        app = eph.apparent(P, jd, bodies=real)
        pos = {b: app[b]["pos_km"] for b in real}
        P = refine_targets(u, names, pos, P, flux, w)
    return P, targets(names, pos, P, flux)


# ---------- identification ----------
def identify(cand, body_pos, known=None, tol_arcsec=120.0, r_range_au=(R_MIN_AU, R_MAX_AU), flux=true_flux,
             known_weight=1.0):
    """Assign planet-candidate directions to bodies without knowing where we are.

    cand: (N, 3) unit directions of unidentified blobs. body_pos: {name: heliocentric X (km)}.
    known: optional {name: unit direction} of sightings whose identity is already certain (the
    Sun from a sun sensor); they take part in every fix.
    Brute force: every ordered seed of 3 sightings (candidates x bodies, minus the known ones) gives
    a fix; a seed survives if its own residuals are < tol and |P| is plausible. Each survivor then
    predicts every body (and the Earth+Moon photocentre) from its P, collects every candidate whose
    prediction is within tol (one body per candidate, nearest first; a blend uses up both bodies)
    and is re-solved. Best = most sightings, then lowest RMS residual.
    known_weight: weight of the known sightings relative to a candidate (sigma_cand^2 / sigma_known^2).
    Returns dict(assign={cand_index: name}, P, rms_arcsec, n_seeds, n_survivors) or None."""
    known = known or {}
    names = [b for b in body_pos if b not in known]
    blends = ["+".join(pr) for pr in BLENDS if all(q in names for q in pr)]
    ext = names + blends
    parts = [set(n.split("+")) for n in ext]
    Xb = np.array([body_pos[b] for b in names])
    kn_names = list(known)
    kn_u = np.array([known[b] for b in known]).reshape(-1, 3)
    cand = np.asarray(cand, np.float64).reshape(-1, 3)
    if len(cand) > 16:          # seeds grow as n^3; callers pass the brightest candidates first
        cand = cand[:16]
    k = max(1, 3 - len(kn_u))                       # sightings each seed needs from candidates
    if len(cand) < k:
        return None
    kn_X = np.array([body_pos[b] for b in known]).reshape(-1, 3)
    lo, hi = r_range_au[0] * pl.AU_KM, r_range_au[1] * pl.AU_KM
    tol = tol_arcsec * ARCSEC

    # seed bodies: the real bodies plus each blend, the latter at the flux-weighted mean position
    # (weights from H; exact enough for a seed, the survivors then model the photocentre properly)
    Xs = [body_pos[b] for b in names]
    for bl in blends:
        q = bl.split("+")
        w = flux([pl.BODIES[x][1] for x in q])
        Xs.append(sum(wi * body_pos[x] for wi, x in zip(w, q)) / w.sum())
    Xs = np.array(Xs)
    ci = np.array(list(itertools.combinations(range(len(cand)), k)), int).reshape(-1, k)
    bi = np.array([pm for pm in itertools.permutations(range(len(ext)), k)
                   if sum(len(parts[j]) for j in pm) == len(set().union(*[parts[j] for j in pm]))],
                  int).reshape(-1, k)
    S_c = np.repeat(ci, len(bi), 0)
    S_b = np.tile(bi, (len(ci), 1))
    U = np.concatenate([cand[S_c], np.broadcast_to(kn_u, (len(S_c),) + kn_u.shape)], 1)
    X = np.concatenate([Xs[S_b], np.broadcast_to(kn_X, (len(S_b),) + kn_X.shape)], 1)
    A = _proj(U)
    with np.errstate(all="ignore"):
        # tiny ridge so a degenerate seed (parallel lines) can't make the batched solve raise
        P = np.linalg.solve(A.sum(1) + 1e-9 * np.eye(3), np.einsum("snij,snj->si", A, X)[..., None])[..., 0]
        d = X - P[:, None]
        d /= np.linalg.norm(d, axis=2, keepdims=True)
        # seeds may hit a blended Earth/Moon blob: allow its offset (up to ~0.3 deg) here, the
        # survivors are checked against the real tolerance with the blend modelled
        res = np.arccos(np.clip(np.sum(d * U, 2), -1, 1)).max(1)
        rP = np.linalg.norm(P, axis=1)
    ok = np.isfinite(res) & (res < max(tol, 0.3 * math.pi / 180)) & (rP > lo) & (rP < hi)
    order = np.flatnonzero(ok)[np.argsort(res[ok])]
    best = None
    seen = set()
    for s in order:
        Ps = P[s]
        for _ in range(3):  # predict all bodies from the seed, gather agreeing candidates, re-solve
            pd = targets(ext, body_pos, Ps, flux) - Ps
            pd /= np.linalg.norm(pd, axis=1, keepdims=True)
            ang = np.arccos(np.clip(cand @ pd.T, -1, 1))          # (n_cand, n_ext)
            assign = {}
            used = set()
            for flat in np.argsort(ang, axis=None):
                c, b = divmod(int(flat), len(ext))
                if ang[c, b] > tol:
                    break
                if c in assign or used & parts[b]:
                    continue
                assign[c] = ext[b]
                used |= parts[b]
            if len(assign) + len(kn_u) < 3:
                break
            cs = sorted(assign)
            sn = [assign[c] for c in cs] + kn_names
            u_all = np.concatenate([cand[cs], kn_u])
            w_all = np.r_[np.ones(len(cs)), np.full(len(kn_u), known_weight)]
            Ps = refine_targets(u_all, sn, body_pos, Ps, flux, w_all)
        if len(assign) + len(kn_u) < 3:
            continue
        key = tuple(sorted(assign.items()))
        if key in seen:
            continue
        seen.add(key)
        r = residuals_arcsec(u_all, targets(sn, body_pos, Ps, flux), Ps)
        r[len(cs):] *= math.sqrt(known_weight)    # known sightings: judge in units of their own sigma
        if r.max() > tol_arcsec or not lo < np.linalg.norm(Ps) < hi:
            continue
        score = (sum(len(n.split("+")) for n in assign.values()), -float(np.sqrt(np.mean(r ** 2))))
        if best is None or score > best[0]:
            best = (score, dict(assign), Ps, -score[1])
        if len(seen) > 200:      # plenty of distinct hypotheses tried (seeds are sorted by residual)
            break
    if best is None:
        return None
    return {"assign": best[1], "P": best[2], "rms_arcsec": best[3], "n_seeds": len(S_c),
            "n_survivors": int(ok.sum())}


def navigate(cand, eph, jd, known=None, sigma_arcsec=20.0, known_sigma_arcsec=20.0, tol_arcsec=120.0,
             flux=true_flux):
    """Identify candidates and fix the position, light time included. sigma_arcsec: direction error
    of a camera sighting; known_sigma_arcsec: of a known sighting (sun sensor). flux(mag) -> relative
    blob flux, used only for the Earth+Moon photocentre (a camera calibrates it on identified stars).
    Returns dict(P, cov, axes_km, assign, names, resid_arcsec) or None."""
    known = known or {}
    pos = eph.positions(jd, bodies=pl.PLANETS + list(known))   # the Sun only as a known sighting
    kw = (sigma_arcsec / known_sigma_arcsec) ** 2
    ident = identify(cand, pos, known=known, tol_arcsec=tol_arcsec, flux=flux, known_weight=kw)
    if ident is None:
        return None
    cs = sorted(ident["assign"])
    names = [ident["assign"][c] for c in cs] + list(known)
    u = np.concatenate([np.asarray(cand)[cs].reshape(-1, 3), np.array([known[b] for b in known]).reshape(-1, 3)])
    sig = np.r_[np.full(len(cs), sigma_arcsec), np.full(len(known), known_sigma_arcsec)] * ARCSEC
    P, X = solve_with_light_time(u, names, eph, jd, ident["P"], flux, 1.0 / sig ** 2)
    cov = covariance(P, X, sig)
    axes, _ = ellipsoid(cov)
    return {"P": P, "cov": cov, "axes_km": axes, "assign": ident["assign"], "names": names,
            "resid_arcsec": residuals_arcsec(u, X, P), "n_survivors": ident["n_survivors"]}


# ---------- benchmark scenario ----------
W, H, FOV = 1920, 1080, 50.0
SCAN_ELONG = np.arange(1, 9) * 40.0      # 8 pointings along the ecliptic, Sun-relative, 50 x 29 deg each
SUN_EXCL_DEG = 35.0                        # no pointing whose boresight is this close to the Sun
VIS_MAG = 5.5                              # render / detection limit (same as the stars)


def scan_attitudes(sun_dir):
    """sim.sky attitudes (scipy Rotation) for the nav scan: boresights on the ecliptic at Sun
    elongations 40, 80, ..., 320 deg (frames overlap by 10 deg, so the scan sees everything more
    than ~15 deg from the Sun), image x along the ecliptic."""
    from scipy.spatial.transform import Rotation
    e = sun_dir @ pl.ecliptic_to_icrf(np.eye(3)).T  # ICRF -> ecliptic
    sun_lon = math.degrees(math.atan2(e[1], e[0]))
    out = []
    for lon in sun_lon + SCAN_ELONG:
        c = pl.ecliptic_unit(lon, 0.0)
        right = pl.ecliptic_unit(lon + 90.0, 0.0)
        out.append((lon, Rotation.from_matrix(np.column_stack([right, np.cross(c, right), c]))))
    return out


def visible_bodies(app, P, scan):
    """Planets bright enough and inside a usable scan frame (not one excluded by the Sun)."""
    sun = -P / np.linalg.norm(P)
    f = (W / 2) / math.tan(math.radians(FOV / 2))
    vis = []
    for b in pl.PLANETS:
        s = app[b]
        if s["mag"] > VIS_MAG:
            continue
        for _, att in scan:
            if att.as_matrix()[:, 2] @ sun > math.cos(math.radians(SUN_EXCL_DEG)):
                continue
            cam = att.as_matrix().T @ s["dir"]
            if cam[2] > 0 and abs(f * cam[0] / cam[2]) < W / 2 - 3 and abs(f * cam[1] / cam[2]) < H / 2 - 3:
                vis.append(b)
                break
    return vis


def perturb(u, sigma_rad, rng):
    """Rotate unit vectors by isotropic Gaussian angular noise (sigma per axis)."""
    u = np.atleast_2d(u)
    a = np.cross(u, rng.normal(size=u.shape))
    a /= np.linalg.norm(a, axis=1, keepdims=True)
    b = np.cross(u, a)
    e = rng.normal(0, sigma_rad, (len(u), 2))
    v = u + e[:, :1] * a + e[:, 1:] * b
    return v / np.linalg.norm(v, axis=1, keepdims=True)


class CandidateFinder:
    """After a star lock: refit the attitude on all stars, then list the blobs that are not
    catalogue stars (planet candidates) as inertial rays. Screen geometry w x h px, fov deg;
    H = camera->screen homography (None if the frame already is in screen pixels)."""

    def __init__(self, w, h, fov, H=None, max_blobs=300, cand_max_area=3000, refine=True, star_reject_px=4.0,
                 edge_px=10.0, max_cand=8):
        from sim.sky import SkyRenderer, load_stars
        self.w, self.h, self.fov, self.H = w, h, fov, H
        self.max_blobs, self.cand_max_area, self.refine = max_blobs, cand_max_area, refine
        self.star_reject_px, self.edge_px, self.max_cand = star_reject_px, edge_px, max_cand
        self.sky = SkyRenderer(w, h, fov)
        _, _, self.cat = load_stars(6.5)   # everything that could leave a blob that isn't a planet
        self.cat_drawn = self.sky.vec      # stars the screen actually shows (attitude refinement)

    def flux(self, mag):
        """Integrated blob flux vs magnitude for this display (peak * 2 pi sigma^2). A real camera
        would fit this curve on the identified stars; the sim knows it exactly."""
        peak, b = self.sky.blob_style(np.atleast_1d(mag))
        return peak * 2 * math.pi * self.sky.SIGMA_BINS[b] ** 2

    def blobs(self, gray, flux=False, **kw):
        """Centroids in screen pixels (warped from camera pixels when H is set), brightest first.
        flux=True also returns a brightness per blob (background-subtracted 9x9 sum around it)."""
        from camera.common import centroids, warp_points
        w, h = self.w, self.h
        cc = centroids(gray, **{**getattr(self, "centroid_kw", {}), **kw})
        c = cc if self.H is None else warp_points(cc, self.H)
        keep = (c[:, 0] > -0.5) & (c[:, 0] < w - 0.5) & (c[:, 1] > -0.5) & (c[:, 1] < h - 0.5)
        if not flux:
            return c[keep]
        g = gray.astype(np.float32)
        f = []
        for x, y in np.rint(cc).astype(int):
            patch = g[max(0, y - 4):y + 5, max(0, x - 4):x + 5]
            ring = g[max(0, y - 8):y + 9, max(0, x - 8):x + 9]
            f.append(float(patch.sum() - np.median(ring) * patch.size))
        f = np.array(f)
        o = np.argsort(-f[keep], kind="stable")
        return c[keep][o], f[keep][o]

    def find(self, gray, att):
        """gray: the frame tetra3 locked on, att: its attitude (sim.sky Rotation).
        Returns (refined attitude, n stars in the refit, rays (M, 3), screen px (M, 2), flux (M,))."""
        w, h = self.w, self.h
        # candidate pass: planets brighter than mag ~1 render as blobs bigger than tetra3's 100 px
        # star-area cap, so look again with the cap lifted
        c, fl = self.blobs(gray, flux=True, max_returned=self.max_blobs, max_area=self.cand_max_area)
        n_ref = 0
        if self.refine:
            att, n_ref = refine_attitude(att, c, self.cat_drawn, self.sky)
        # drop blobs on any catalogue star; what is left is a planet candidate
        px, py, front = self.sky.project(self.cat, att)
        on = front & (px > -10) & (px < w + 10) & (py > -10) & (py < h + 10)
        sp = np.column_stack([px[on], py[on]])
        if len(c) and len(sp):
            d2 = ((c[:, None, :] - sp[None]) ** 2).sum(2).min(1)
            c, fl = c[d2 > self.star_reject_px ** 2], fl[d2 > self.star_reject_px ** 2]
        # the screen border (LCD glow edge through a camera) shows up as a row of blobs; a planet
        # there is also in a neighbouring scan frame. Keep the brightest few: noise, JPEG blocks and
        # hot pixels leave many faint blobs that would blow up the brute-force identification.
        e = self.edge_px
        m = (c[:, 0] > e) & (c[:, 0] < w - 1 - e) & (c[:, 1] > e) & (c[:, 1] < h - 1 - e)
        c, fl = c[m][:self.max_cand], fl[m][:self.max_cand]
        return att, n_ref, pixel_rays(c, w, h, self.fov, att), c, fl


class FrameSolver:
    """Benchmark: rendered frames -> tetra3 attitude -> planet-candidate rays (inertial)."""

    def __init__(self, a):
        from camera.common import load_tetra3
        self.a = a
        self.t3 = load_tetra3()
        self.cam = H_ = None
        if a.sim_camera:
            from camera.calibrate import fit
            from camera.fakecam import FakeCamera
            from sim.sky import checkerboard
            self.cam = FakeCamera(W, H, seed=a.seed)
            r = fit(self.cam.view(checkerboard(W, H)[0]), W, H)
            H_ = r["H"]
            print(f"[sim-camera] homography fitted on the fake checkerboard: RMS {r['rms']:.3f} screen px")
        self.finder = CandidateFinder(W, H, FOV, H_, a.max_blobs, a.cand_max_area, a.refine, a.star_reject_px,
                                      a.edge_px, a.max_cand)
        self.sky, self.H, self.flux = self.finder.sky, H_, self.finder.flux

    def frame(self, att, extra, rng):
        from sim.sky import SkyRenderer
        img = self.sky.render(att, extra=extra)
        img = SkyRenderer.to_u8(img, noise=self.a.noise, rng=rng)
        return img if self.cam is None else self.cam.view(img)

    def solve(self, gray):
        """Returns (attitude or None, info dict, candidate rays (M, 3), candidate pixels (M, 2))."""
        from sim.sky import from_radecroll
        a = self.a
        c = self.finder.blobs(gray, max_returned=a.max_centroids, max_area=a.max_area)   # as solve_live
        res = (self.t3.solve_from_centroids(c[:, ::-1], (H, W), fov_estimate=FOV,
                                            fov_max_error=a.fov_max_error, solve_timeout=a.timeout_ms,
                                            return_matches=True)
               if len(c) >= 4 else {"RA": None})
        info = {"n_blobs": len(c), "matches": int(res.get("Matches") or 0)}
        if res.get("RA") is None or res["Matches"] < a.min_matches or res["Prob"] > a.prob_max:
            return None, info, np.zeros((0, 3)), np.zeros((0, 2))
        info["att_tetra3"] = from_radecroll(res["RA"], res["Dec"], res["Roll"])
        att, info["n_refine"], rays, pix, info["flux"] = self.finder.find(gray, info["att_tetra3"])
        return att, info, rays, pix


def refine_attitude(att, c, cat, sky, match_px=3.0, n_iter=3):
    """Least-squares attitude (Kabsch / Wahba) from every blob within match_px of a catalogue star
    projected with the current attitude. tetra3 reports an attitude from its few pattern stars;
    off-axis roll errors of ~1 arcmin are common and become ~1 arcmin ray errors at the frame edge."""
    from scipy.spatial.transform import Rotation
    n = 0
    for _ in range(n_iter):
        px, py, front = sky.project(cat, att)
        on = np.flatnonzero(front & (px > -5) & (px < sky.w + 5) & (py > -5) & (py < sky.h + 5))
        if not len(on) or not len(c):
            break
        sp = np.column_stack([px[on], py[on]])
        d2 = ((c[:, None, :] - sp[None]) ** 2).sum(2)
        j = d2.argmin(1)
        m = d2[np.arange(len(c)), j] < match_px ** 2
        n = int(m.sum())
        if n < 6:
            break
        v = pixel_rays(c[m], sky.w, sky.h, sky.fov)   # camera frame
        s_ = cat[on[j[m]]]                          # inertial
        U, _, Vt = np.linalg.svd(s_.T @ v)
        D = np.diag([1.0, 1.0, np.sign(np.linalg.det(U @ Vt))])
        att = Rotation.from_matrix(U @ D @ Vt)
    return att, n


# ---------- live navigator (camera.solve_live --nav) ----------
class LiveNavigator:
    """Keeps planet candidates seen in locked frames (inertial directions don't change as the
    spacecraft turns, so a scan or a tumble builds up the list), and re-solves the position in a
    background thread whenever the list changes. Candidates not seen for memory_s are dropped."""

    def __init__(self, finder, jd, sigma_arcsec=10.0, tol_arcsec=120.0, memory_s=60.0, merge_deg=0.05,
                 max_solve=12, eph=None):
        import threading
        self.finder, self.jd = finder, jd
        self.sigma, self.tol, self.memory, self.merge = sigma_arcsec, tol_arcsec, memory_s, merge_deg
        self.max_solve = max_solve
        self.eph = eph or pl.Ephemeris()
        self.buf = []            # dicts: dir (3,), flux, n, t
        self.lock = threading.Lock()
        self.dirty = threading.Event()
        self.fix = None          # last result (dict) or None
        self.fix_note = "collecting"
        self.last_att = None
        self.last_solve = 0.0
        threading.Thread(target=self._run, daemon=True, name="nav").start()

    def observe(self, gray, att, t=None):
        """Feed one locked frame. Returns the refined attitude."""
        t = time.time() if t is None else t
        att, _, rays, _, fl = self.finder.find(gray, att)
        self.last_att = att
        cos_m = math.cos(math.radians(self.merge))
        new = False
        with self.lock:
            for r, f in zip(rays, fl):
                for c in self.buf:
                    if c["dir"] @ r > cos_m:
                        c["dir"] = (c["dir"] * c["n"] + r) / (c["n"] + 1)
                        c["dir"] /= np.linalg.norm(c["dir"])
                        c["n"] += 1
                        c["flux"] = max(c["flux"], f)
                        c["t"] = t
                        break
                else:
                    self.buf.append({"dir": r, "flux": f, "n": 1, "t": t})
                    new = True
            n0 = len(self.buf)
            self.buf = [c for c in self.buf if t - c["t"] < self.memory]
            new |= len(self.buf) != n0
        # re-solve when the candidate list changed, else refresh slowly (averaged directions):
        # the fix runs in a thread that shares the GIL with the frame loop, so keep it rare
        if new or (len(rays) and time.time() - self.last_solve > 10.0):
            self.dirty.set()
        return att

    def reset(self):
        with self.lock:
            self.buf = []
        self.fix, self.fix_note = None, "collecting"

    def candidates(self):
        """Brightest first; one-off blobs (seen in a single frame) go after repeat sightings."""
        with self.lock:
            cs = sorted(self.buf, key=lambda c: (c["n"] < 2, -c["flux"]))
            return np.array([c["dir"] for c in cs]).reshape(-1, 3)

    def _run(self):
        while True:
            self.dirty.wait()
            self.dirty.clear()
            self.last_solve = time.time()
            cand = self.candidates()[:self.max_solve]
            if len(cand) < 3:
                self.fix_note = "collecting"
                continue
            t0 = time.time()
            try:
                nav = navigate(cand, self.eph, self.jd, sigma_arcsec=self.sigma, tol_arcsec=self.tol,
                               flux=self.finder.flux)
            except Exception as e:  # never kill the solver over a bad fix
                print("[nav]", repr(e))
                nav = None
            if nav is None:
                self.fix_note = "no fix"
                continue
            nav["t"], nav["solve_s"], nav["n_cand"] = time.time(), time.time() - t0, len(cand)
            self.fix, self.fix_note = nav, "fix"
            time.sleep(0.5)   # at most ~2 fixes / s

    def packet(self, truth=None):
        """The "nav" block of a solve_live estimate packet."""
        with self.lock:
            n_cand = len(self.buf)
        out = {"status": self.fix_note, "n_cand": n_cand, "jd": self.jd}
        f = self.fix
        if f is None:
            return out
        P, cov = f["P"], f["cov"]
        earth = self.eph.positions(self.jd, bodies=["Earth"])["Earth"]
        de = earth - P
        u = de / np.linalg.norm(de)
        out.update({
            "pos_km": [round(float(v), 1) for v in P], "cov_km2": np.round(cov, 1).tolist(),
            "axes_km": [round(float(v), 1) for v in f["axes_km"]],
            "bodies": f["names"], "resid_rms_arcsec": round(float(np.sqrt(np.mean(f["resid_arcsec"] ** 2))), 2),
            "resid_by_body": {n: round(float(r), 2) for n, r in zip(f["names"], f["resid_arcsec"])},
            "earth_pos_km": [round(float(v), 1) for v in earth],
            "r_au": round(float(np.linalg.norm(P) / pl.AU_KM), 6),
            "earth_km": round(float(np.linalg.norm(de)), 1),
            "earth_sigma_km": round(float(np.sqrt(u @ cov @ u)), 1),
            "age_s": round(time.time() - f["t"], 2), "solve_s": round(f["solve_s"], 3)})
        if truth and truth.get("pos_km"):
            out["err_km"] = round(float(np.linalg.norm(P - np.asarray(truth["pos_km"]))), 1)
        return out


def ecliptic_scan_radec(i, n=8):
    """(ra, dec, roll) of scan pointing i: boresight on the ecliptic, image x along it."""
    from sim.sky import to_radecroll
    from scipy.spatial.transform import Rotation
    lon = (np.arange(n) * 360.0 / n + 180.0 / n)[i % n]
    c, right = pl.ecliptic_unit(lon, 0.0), pl.ecliptic_unit(lon + 90.0, 0.0)
    return to_radecroll(Rotation.from_matrix(np.column_stack([right, np.cross(c, right), c])))


def merge_candidates(rays, flux=None, tol_deg=0.05):
    """Same object seen in two overlapping scan frames -> one candidate (mean direction).
    Returned brightest first (by the brightest sighting) when flux is given."""
    flux = np.zeros(len(rays)) if flux is None else np.asarray(flux)
    groups, gf = [], []
    for r, f in zip(rays, flux):
        for k, g in enumerate(groups):
            if np.degrees(np.arccos(np.clip(g[0] @ r, -1, 1))) < tol_deg:
                g.append(r)
                gf[k] = max(gf[k], f)
                break
        else:
            groups.append([r])
            gf.append(f)
    out = np.array([np.mean(g, 0) for g in groups]).reshape(-1, 3)
    out = out[np.argsort(-np.array(gf), kind="stable")] if len(out) else out
    return out / np.linalg.norm(out, axis=1, keepdims=True) if len(out) else out


def label_truth(cand, app, names, P, flux, tol_deg=0.1):
    """True identity of each candidate (nearest true apparent body, or the true Earth+Moon
    photocentre, within tol), else None."""
    dirs = {b: app[b]["dir"] for b in names}
    pos = {b: app[b]["pos_km"] for b in app}
    for pr in BLENDS:
        if all(q in names for q in pr):
            x = targets(["+".join(pr)], pos, P, flux)[0] - P
            dirs["+".join(pr)] = x / np.linalg.norm(x)
    lab = []
    for c in cand:
        ang = {b: math.degrees(math.acos(min(1.0, float(c @ d)))) for b, d in dirs.items()}
        b = min(ang, key=ang.get) if ang else None
        lab.append(b if b is not None and ang[b] < tol_deg else None)
    return lab


def score_case(nav, P_true, cand_truth, sigma_arcsec):
    if nav is None:
        return {"ok": False}
    e = nav["P"] - P_true
    err = float(np.linalg.norm(e))
    nees = float(e @ np.linalg.solve(nav["cov"], e))
    assigned = nav["assign"]
    n_lab = sum(1 for t in cand_truth if t is not None)
    def same(t, b):   # a blob labelled Earth/Moon/Earth+Moon assigned to the blend is right
        return t == b or (t is not None and "+" in b and set(t.split("+")) <= set(b.split("+")))
    n_right = sum(1 for i, t in enumerate(cand_truth) if t is not None and i in assigned and same(t, assigned[i]))
    n_wrong = sum(1 for i, b in assigned.items() if not same(cand_truth[i], b))
    return {"ok": True, "err_km": err, "err_au": err / pl.AU_KM, "axes_km": nav["axes_km"].tolist(),
            "nees": nees, "n_used": len(nav["names"]), "id_right": n_right, "id_labelled": n_lab,
            "id_wrong": n_wrong, "id_all_ok": n_wrong == 0, "resid_rms": float(np.sqrt(np.mean(nav["resid_arcsec"] ** 2))),
            "used": "+".join(nav["names"])}


def run_bench(a):
    eph = pl.Ephemeris()
    jd = a.jd
    rng = np.random.default_rng(a.seed)              # measurement noise, ID shuffles
    pos_rng = np.random.default_rng([a.seed, 7])     # hidden positions: same for every mode / option
    modes = ["geometry", "rendered"] if a.mode == "both" else [a.mode]
    fs = FrameSolver(a) if "rendered" in modes else None
    rows = []
    t_start = time.time()
    for i in range(a.n):
        P_true = pl.random_observer(pos_rng, eph, jd)
        app = eph.apparent(P_true, jd)
        sun_dir = -P_true / np.linalg.norm(P_true)   # light time to the Sun: < 1 km of solar motion
        scan = scan_attitudes(sun_dir)
        vis = visible_bodies(app, P_true, scan)
        base = {"case": i, "r_au": np.linalg.norm(P_true) / pl.AU_KM, "n_visible": len(vis), "visible": "+".join(vis)}

        if "geometry" in modes:
            truth_u = np.array([app[b]["dir"] for b in vis]).reshape(-1, 3)
            for sun in (False, True):
                u = perturb(truth_u, a.sigma * ARCSEC, rng) if len(vis) else truth_u
                order = rng.permutation(len(vis))        # the identifier must not rely on order
                cand, lab = u[order], [vis[j] for j in order]
                known = {"Sun": perturb(sun_dir, a.sun_sigma * ARCSEC, rng)[0]} if sun else None
                nav = navigate(cand, eph, jd, known=known, sigma_arcsec=a.sigma, known_sigma_arcsec=a.sun_sigma,
                               tol_arcsec=a.tol)
                rows.append({**base, "mode": "geometry", "sun": sun, "n_cand": len(cand),
                             **score_case(nav, P_true, lab, a.sigma)})

        if "rendered" in modes:
            ex = [b for b in pl.PLANETS if app[b]["mag"] <= VIS_MAG]
            extra = (np.array([app[b]["dir"] for b in ex]).reshape(-1, 3), np.array([app[b]["mag"] for b in ex]))
            rays, fluxes, n_frames, n_lock, att_err, att_err_t3 = [], [], 0, 0, [], []
            for k, (lon, att) in enumerate(scan):
                if att.as_matrix()[:, 2] @ sun_dir > math.cos(math.radians(SUN_EXCL_DEG)):
                    continue
                n_frames += 1
                gray = fs.frame(att, extra, rng)
                att_est, info, r, pix = fs.solve(gray)
                if att_est is None:
                    continue
                n_lock += 1
                att_err.append(math.degrees((att_est.inv() * att).magnitude()) * 3600)
                att_err_t3.append(math.degrees((info["att_tetra3"].inv() * att).magnitude()) * 3600)
                rays.append(r)
                fluxes.append(info["flux"])
                if a.save_frames and i < 3:
                    import cv2
                    OUT.mkdir(exist_ok=True)
                    vis_img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                    if fs.H is not None:
                        from camera.common import warp_points
                        pix = warp_points(pix, np.linalg.inv(fs.H))
                    for x, y in pix:
                        cv2.circle(vis_img, (int(round(x)), int(round(y))), 14, (0, 200, 255), 2)
                    cv2.imwrite(str(OUT / f"nav_frame{a.suffix}_{i}_{k}.png"), vis_img)
            cand = merge_candidates(np.concatenate(rays) if rays else np.zeros((0, 3)),
                                    np.concatenate(fluxes) if fluxes else None)
            lab = label_truth(cand, app, pl.PLANETS, P_true, fs.flux)
            # direction error of each real candidate vs its true (photocentre) direction
            tdir = {b: app[b]["dir"] for b in pl.PLANETS}
            tdir["Earth+Moon"] = targets(["Earth+Moon"], {b: app[b]["pos_km"] for b in app}, P_true, fs.flux)[0] - P_true
            ray_err = [math.degrees(math.acos(min(1.0, float(c @ tdir[t] / np.linalg.norm(tdir[t]))))) * 3600
                       for c, t in zip(cand, lab) if t is not None]
            for sun in (False, True):
                known = {"Sun": perturb(sun_dir, a.sun_sigma * ARCSEC, rng)[0]} if sun else None
                nav = navigate(cand, eph, jd, known=known, sigma_arcsec=a.rendered_sigma,
                               known_sigma_arcsec=a.sun_sigma, tol_arcsec=a.tol, flux=fs.flux)
                rows.append({**base, "mode": "rendered", "sun": sun, "n_cand": len(cand),
                             "n_frames": n_frames, "n_lock": n_lock,
                             "att_err_arcsec": float(np.median(att_err)) if att_err else float("nan"),
                             "att_err_tetra3_arcsec": float(np.median(att_err_t3)) if att_err_t3 else float("nan"),
                             "ray_err_arcsec": float(np.median(ray_err)) if ray_err else float("nan"),
                             "ray_errs": " ".join(f"{v:.1f}" for v in ray_err),
                             "n_true_cand": sum(t is not None for t in lab),
                             **score_case(nav, P_true, lab, a.sigma)})
        last = rows[-1]
        print(f"[{i + 1}/{a.n}] r {base['r_au']:.2f} AU, visible {base['visible'] or '-'}"
              + (f" | last: {last['mode']} sun={last['sun']} err {last['err_km']:,.0f} km, used {last['used']}"
                 if last.get("ok") else f" | last: {last['mode']} sun={last['sun']} no fix")
              + f" ({time.time() - t_start:.0f} s)", flush=True)
    return rows


# ---------- report ----------
def summarize(rows, a, wall_s):
    OUT.mkdir(exist_ok=True)
    keys = sorted({k for r in rows for k in r})
    with open(OUT / f"nav_bench{a.suffix}.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k, "") for k in keys})

    lines = [f"# Position from planets: offline benchmark{f" [{a.tag}]" if a.tag else ""} ({a.n} positions, {wall_s / 60:.1f} min wall)", "",
             f"Epoch TDB JD {a.jd}. Hidden observer: 0.7-2 AU from the Sun, ecliptic latitude +/-3 deg, "
             f"more than {pl.MIN_BODY_GAP_AU} AU from any planet. Bodies: Mercury, Venus, Earth, Moon, Mars, "
             f"Jupiter and Saturn barycentres (DE421), visible if mag <= {VIS_MAG} and inside one of "
             f"{len(SCAN_ELONG)} ecliptic scan frames ({W}x{H}, {FOV:.0f} deg FOV, boresights at Sun elongation "
             f"40, 80, ..., 320 deg; no pointing within {SUN_EXCL_DEG:.0f} deg of the Sun).", "",
             f"- **geometry**: exact light-time-corrected directions + Gaussian noise sigma = {a.sigma:g} arcsec "
             "per axis, shuffled, then identified and solved (checks the maths without the renderer).",
             "- **rendered**: each scan frame is rendered by sim.sky with the planets as extra blobs "
             f"(noise {a.noise:g} DN{', filmed by camera.fakecam with the fitted homography' if a.sim_camera else ''}), solved by tetra3 (same centroider/solver settings as solve_live), "
             f"blobs more than {a.star_reject_px:g} px from every catalogue star (mag <= 6.5) are planet "
             "candidates, merged across overlapping frames, identified and solved.",
             f"- **+Sun**: a Sun direction (sun sensor, sigma {a.sun_sigma:g} arcsec) is added as a known body at "
             "the origin.",
             "- fix = least squares weighted by 1/(sigma_i r_i)^2, light-time corrected. Ellipsoid = 1-sigma "
             "semi-axes of (sum (I - u u^T)/(sigma_i r_i)^2)^-1 with sigma = "
             f"{a.sigma:g} arcsec (geometry), {a.rendered_sigma:g} arcsec (rendered planets), {a.sun_sigma:g} "
             "arcsec (Sun). "
             "NEES = e^T C^-1 e (should average ~3 if the ellipsoid is honest).",
             "- ID ok = every assigned candidate got its true body (candidates labelled by the nearest true "
             "body within 0.1 deg).", ""]
    hdr = ("| mode | Sun | fixes | median err km | median err AU | 90% err km | median ellipsoid "
           "a/b/c km | mean NEES | ID ok | bodies used (median) | bodies visible (median) |")
    lines += [hdr, "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|"]
    groups = {}
    for r in rows:
        groups.setdefault((r["mode"], r["sun"]), []).append(r)
    summary = {}
    for (mode, sun), rs in groups.items():
        ok = [r for r in rs if r.get("ok")]
        e = np.array([r["err_km"] for r in ok])
        ax = np.array([r["axes_km"] for r in ok]).reshape(-1, 3)
        summary[(mode, sun)] = (rs, ok)
        lines.append(
            f"| {mode} | {'yes' if sun else 'no'} | {len(ok)}/{len(rs)} | "
            + (f"{np.median(e):,.0f} | {np.median(e) / pl.AU_KM:.2e} | {np.percentile(e, 90):,.0f} | "
               f"{' / '.join(f'{v:,.0f}' for v in np.median(ax, 0))} | {np.mean([r['nees'] for r in ok]):.1f} | "
               f"{sum(r['id_all_ok'] for r in ok)}/{len(ok)} | {np.median([r['n_used'] for r in ok]):.0f} | "
               if len(ok) else "- | - | - | - | - | - | - | ")
            + f"{np.median([r['n_visible'] for r in rs]):.0f} |")
    rend = [r for r in rows if r["mode"] == "rendered" and not r["sun"]]
    if rend:
        lines += ["", "Rendered pipeline: "
                  f"{sum(r['n_lock'] for r in rend)}/{sum(r['n_frames'] for r in rend)} scan frames locked, "
                  f"median attitude error {np.nanmedian([r['att_err_tetra3_arcsec'] for r in rend]):.0f} arcsec "
                  f"from tetra3, {np.nanmedian([r['att_err_arcsec'] for r in rend]):.1f} arcsec after the "
                  f"least-squares refit{'' if a.refine else ' (refit OFF: tetra3 attitude used)'} "
                  "(total rotation, roll included); median planet-candidate direction error "
                  f"{np.median([float(v) for r in rend for v in r['ray_errs'].split()] or [float('nan')]):.1f} arcsec; "
                  f"median {np.median([r['n_cand'] for r in rend]):.0f} planet candidates per scan, "
                  f"{np.median([r['n_true_cand'] for r in rend]):.0f} of them real planets."]
        fails = [r for r in rend if not r.get("ok")]
        if fails:
            lines.append(f"No fix in {len(fails)} rendered scans (visible bodies: "
                         + "; ".join(r["visible"] or "none" for r in fails) + ").")
    lines += ["", "## Per position (rendered, planets only)" if rend else "## Per position (geometry, planets only)", "",
              "| case | r AU | visible | used | err km | ellipsoid a/b/c km | NEES | resid RMS arcsec | ID ok |",
              "|---:|---:|---|---|---:|---|---:|---:|---|"]
    for r in (rend or [r for r in rows if r["mode"] == "geometry" and not r["sun"]]):
        if r.get("ok"):
            lines.append(f"| {r['case']} | {r['r_au']:.2f} | {r['visible']} | {r['used']} | {r['err_km']:,.0f} | "
                         f"{' / '.join(f'{v:,.0f}' for v in r['axes_km'])} | {r['nees']:.1f} | "
                         f"{r['resid_rms']:.0f} | {'yes' if r['id_all_ok'] else 'NO'} |")
        else:
            lines.append(f"| {r['case']} | {r['r_au']:.2f} | {r['visible']} | - | no fix | | | | |")
    (OUT / f"nav_bench{a.suffix}.md").write_text("\n".join(lines) + "\n")
    plot(summary, a)
    print("\n".join(lines[:lines.index(hdr) + 2 + len(groups)]))
    print(f"\nwrote {OUT / f'nav_bench{a.suffix}.md'}, .csv, .png")


def plot(summary, a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {("geometry", False): "#2a6fdb", ("geometry", True): "#7fa8ee",
              ("rendered", False): "#d9622b", ("rendered", True): "#eda57f"}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6))
    for key, (rs, ok) in summary.items():
        if not ok:
            continue
        lab = f"{key[0]}{' + Sun' if key[1] else ''} ({len(ok)}/{len(rs)} fixes)"
        e = np.sort([r["err_km"] for r in ok])
        ax1.step(e, np.arange(1, len(e) + 1) / len(rs), where="post", color=colors[key], lw=2, label=lab)
        ax2.scatter([r["axes_km"][0] for r in ok], [r["err_km"] for r in ok], s=18, color=colors[key], label=lab,
                    alpha=0.8)
    ax1.set_xscale("log")
    ax1.set_xlabel("position error (km)")
    ax1.set_ylabel("fraction of positions")
    ax1.set_title("Position error, CDF (fraction of all positions)")
    ax1.axvspan(1e4, 3e4, color="0.85", zorder=0, label="feasibility estimate 10-30k km")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.grid(alpha=0.3, which="both")
    lim = [1e2, 1e6]
    ax2.plot(lim, lim, color="0.5", lw=1, ls="--", label="error = 1-sigma major axis")
    ax2.plot(lim, [3 * v for v in lim], color="0.7", lw=1, ls=":", label="3 x major axis")
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlim(*lim)
    ax2.set_ylim(*lim)
    ax2.set_xlabel("predicted 1-sigma ellipsoid major semi-axis (km)")
    ax2.set_ylabel("actual position error (km)")
    ax2.set_title("Is the ellipsoid honest?")
    ax2.legend(fontsize=8, loc="upper left")
    ax2.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(OUT / f"nav_bench{a.suffix}.png", dpi=130)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", action="store_true", help="run the offline benchmark")
    ap.add_argument("--mode", choices=["both", "geometry", "rendered"], default="both")
    ap.add_argument("--n", type=int, default=24, help="hidden observer positions")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jd", type=float, default=pl.EPOCH_JD, help="epoch, TDB Julian date")
    ap.add_argument("--sigma", type=float, default=20.0,
                    help="geometry: direction noise per axis, arcsec (also its ellipsoid sigma)")
    ap.add_argument("--rendered-sigma", type=float, default=5.0,
                    help="rendered: assumed planet direction error for weights and ellipsoid, arcsec")
    ap.add_argument("--sun-sigma", type=float, default=20.0, help="+Sun rows: sun-sensor noise per axis, arcsec")
    ap.add_argument("--tol", type=float, default=120.0, help="identification residual tolerance, arcsec")
    ap.add_argument("--noise", type=float, default=0.0, help="rendered: Gaussian read noise, DN")
    ap.add_argument("--max-blobs", type=int, default=300, help="rendered: centroids kept, candidate pass")
    ap.add_argument("--max-centroids", type=int, default=60, help="rendered: centroids given to tetra3")
    ap.add_argument("--max-area", type=int, default=100, help="rendered: tetra3 star blob area cap (solve pass)")
    ap.add_argument("--cand-max-area", type=int, default=3000, help="rendered: blob area cap, candidate pass")
    ap.add_argument("--fov-max-error", type=float, default=2.0)
    ap.add_argument("--timeout-ms", type=int, default=500)
    ap.add_argument("--min-matches", type=int, default=8)
    ap.add_argument("--prob-max", type=float, default=1e-6)
    ap.add_argument("--star-reject-px", type=float, default=4.0, help="blob this close to a catalogue star = star")
    ap.add_argument("--no-refine", dest="refine", action="store_false",
                    help="rendered: use tetra3's attitude as is (no least-squares refit on all stars)")
    ap.add_argument("--save-frames", action="store_true", help="rendered: save annotated frames of the first 3 scans")
    ap.add_argument("--max-cand", type=int, default=8, help="rendered: brightest planet candidates kept per frame")
    ap.add_argument("--edge-px", type=float, default=10.0, help="rendered: ignore candidates this close to the screen edge")
    ap.add_argument("--sim-camera", action="store_true", help="rendered: film each frame with camera.fakecam")
    ap.add_argument("--tag", default="", help="output name suffix: camera/out/nav_bench_<tag>.*")
    a = ap.parse_args()
    a.suffix = f"_{a.tag}" if a.tag else ""
    if not a.bench:
        ap.print_help()
        return
    t0 = time.time()
    rows = run_bench(a)
    summarize(rows, a, time.time() - t0)


if __name__ == "__main__":
    main()
