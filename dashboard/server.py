"""Lost in Space mission-control dashboard.

Listens for truth (UDP 5005, sim.live), estimates (UDP 5008, camera.solve_live) and the solved camera
frames (UDP 5010, camera.solve_live), works out
errors / lock state / overlays, and serves a full-screen browser page on http://localhost:8080.

  .venv/bin/python -m dashboard                 real feeds
  .venv/bin/python -m dashboard --mock          synthesise both feeds (nothing else needed)
  .venv/bin/python -m dashboard --mock-est      truth from a running sim.live, estimates synthesised
  add --open to launch the page full screen (Chrome kiosk if installed, else default browser)
  add --console-url http://localhost:8081/ to show the virtual console (no-board demo) in a panel

Stdlib HTTP + Server-Sent Events; only numpy / scipy / opencv from the existing venv.
"""
import argparse
import http.server
import json
import math
import pathlib
import shutil
import socketserver
import subprocess
import threading
import time
import webbrowser

import cv2
import numpy as np
from scipy.spatial import cKDTree

from dashboard.feeds import Feed, MockRunner, UdpFeed, UdpFrameFeed
from sim.sky import DATA, SkyRenderer, checkerboard, from_radecroll, load_stars, unit

HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE / "static"

SIGNAL_TIMEOUT = 2.0     # s without packets -> "no signal"
LOST_TO_SEARCH = 3.0     # s without a lock: LOST becomes SEARCH
HISTORY_S = 90.0
VIEW_W = 960             # width the camera view is rendered at


# ---------------------------------------------------------------- sky data for overlays
class SkyData:
    def __init__(self):
        self.hip, self.mag, self.vec = load_stars(5.5)
        names = json.loads((DATA / "starnames.json").read_text())
        self.label = {}
        for hip in self.hip:
            n = names.get(str(hip))
            if n and (n["name"] or n["bayer"]):
                self.label[int(hip)] = n["name"] or f'{n["bayer"]} {n["c"]}'
        a, b, self.seg_con, self.con_names = [], [], [], []
        for k, feat in enumerate(json.loads((DATA / "constellations.lines.json").read_text())["features"]):
            self.con_names.append(feat["id"])
            for line in feat["geometry"]["coordinates"]:
                v = unit(*np.array(line, float).T)
                a.append(v[:-1]), b.append(v[1:])
                self.seg_con += [k] * (len(line) - 1)
        self.seg_a, self.seg_b = np.concatenate(a), np.concatenate(b)
        self.seg_con = np.array(self.seg_con)
        self._r = {}

    def renderer(self, w, h, fov):
        key = (int(w), int(h), float(fov))
        if key not in self._r:
            self._r = {key: SkyRenderer(*key)}
        return self._r[key]

    def overlay(self, att, w, h, fov, cents):
        """Constellation lines, identified stars and labels for the ESTIMATED attitude, in screen px."""
        sk = self.renderer(w, h, fov)
        pad = 0.15 * w
        ax, ay, af = sk.project(self.seg_a, att)
        bx, by, bf = sk.project(self.seg_b, att)

        def inside(x, y):
            return (x > -pad) & (x < w + pad) & (y > -pad) & (y < h + pad)

        ok = af & bf & (inside(ax, ay) | inside(bx, by))
        ok &= np.hypot(ax - bx, ay - by) < 1.5 * w
        lines = np.round(np.column_stack([ax[ok], ay[ok], bx[ok], by[ok]]), 1).tolist()
        cons = []
        for k in np.unique(self.seg_con[ok]):
            sel = ok & (self.seg_con == k)
            mx, my = np.concatenate([ax[sel], bx[sel]]).mean(), np.concatenate([ay[sel], by[sel]]).mean()
            if 0 < mx < w and 0 < my < h:
                cons.append([round(float(mx), 1), round(float(my), 1), self.con_names[k]])

        # identified stars = catalogue stars (projected with the estimate) that sit on a detected centroid
        px, py, front = sk.project(self.vec, att)
        vis = np.flatnonzero(front & (px > -20) & (px < w + 20) & (py > -20) & (py < h + 20))
        ident, labels = [], []
        if len(vis) and len(cents):
            tree = cKDTree(np.column_stack([px[vis], py[vis]]))
            d, j = tree.query(np.asarray(cents, float), distance_upper_bound=0.008 * w)
            seen = set()
            for (cx, cy), dist, jj in zip(cents, d, j):
                if not np.isfinite(dist) or jj in seen:
                    continue
                seen.add(jj)
                i = vis[jj]
                ident.append([round(float(px[i]), 1), round(float(py[i]), 1)])
                name = self.label.get(int(self.hip[i]))
                if name:
                    labels.append((float(self.mag[i]), [round(float(px[i]), 1), round(float(py[i]), 1), name]))
        labels = [lab for _, lab in sorted(labels, key=lambda m: m[0])[:14]]
        return {"lines": lines, "cons": cons, "ident": ident, "labels": labels}


def att_from(d):
    if not d or d.get("ra") is None:
        return None
    return from_radecroll(d["ra"], d["dec"], d["roll"])


# ---------------------------------------------------------------- frame renderer (the "camera view")
class FrameRenderer(threading.Thread):
    """Renders what's on the box screen (from truth) at reduced size, with motion blur estimated from
    consecutive truth packets and glare when the truth says it is on. Publishes JPEG bytes."""

    def __init__(self, hub):
        super().__init__(daemon=True, name="frames")
        self.hub = hub
        self.jpeg, self.id = None, 0
        self.prev = None

    def run(self):
        while True:
            t0 = time.time()
            try:
                self._one()
            except Exception as e:  # never let a bad packet kill the view
                print("[frames]", repr(e))
            time.sleep(max(0.01, 1 / 12 - (time.time() - t0)))

    def _one(self):
        truth = self.hub.frame_truth()
        if truth is None:
            return
        w, h, fov = truth.get("w", 1920), truth.get("h", 1080), truth.get("fov", 50.0)
        s = VIEW_W / w
        vw, vh = VIEW_W, int(round(h * s))
        if truth.get("mode") == "calib":
            img = checkerboard(vw, vh)[0]
        else:
            att = from_radecroll(truth["ra"], truth["dec"], truth["roll"])
            omega = None
            t = truth.get("t") or time.time()
            if self.prev is not None and 0 < t - self.prev[0] < 0.5 and truth.get("mode", "sky") == "sky":
                rv = (self.prev[1].inv() * att).as_rotvec()
                if np.linalg.norm(rv) < math.radians(5):
                    omega = rv / (t - self.prev[0])
            self.prev = (t, att)
            sk = self.hub.sky.renderer(vw, vh, fov)
            from sim.planets import extra_from_list   # planets on screen (sim.live --planets)
            img = sk.render(att, omega, float(truth.get("exposure", 0.1)) if omega is not None else 0.0,
                            extra=extra_from_list(truth.get("planets")))
            if truth.get("glare"):
                sk.glare(img, corner=tuple(truth.get("glare_corner") or (1.0, 1.0)))
            if truth.get("flash"):
                img += float(truth["flash"])   # flare whiteout, as on the sim screen
            img = SkyRenderer.to_u8(img)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            self.jpeg, self.id = buf.tobytes(), self.id + 1


# ---------------------------------------------------------------- position from planets
class NavMap:
    """Solar-system map data for the POSITION panel: estimate (solve_live --nav) vs hidden truth
    (sim.live --planets), planet positions, error ellipse projected on the ecliptic plane."""

    def __init__(self):
        self.eph, self.err, self._cache = None, None, (None, None)

    def _planets(self, jd):
        if self._cache[0] == jd:
            return self._cache[1]
        try:
            from sim import planets as pl
            if self.eph is None:
                self.eph = pl.Ephemeris()
            pos = self.eph.positions(jd, bodies=pl.PLANETS)
            out = {b: (pl.icrf_to_ecliptic(X) / pl.AU_KM).tolist() for b, X in pos.items()}
        except (SystemExit, Exception) as e:   # no data/de421.bsp: draw without planets
            if self.err is None:
                self.err = repr(e)
                print("[nav map] no ephemeris:", e)
            out = {}
        self._cache = (jd, out)
        return out

    def build(self, nav, truth):
        from sim import planets as pl
        if not nav and not (truth or {}).get("pos_km"):
            return None
        jd = (nav or {}).get("jd") or (truth or {}).get("jd") or pl.EPOCH_JD
        out = {"status": (nav or {}).get("status"), "n_cand": (nav or {}).get("n_cand"),
               "planets": self._planets(jd)}
        tp = (truth or {}).get("pos_km")
        if tp:
            out["truth_au"] = (pl.icrf_to_ecliptic(np.asarray(tp)) / pl.AU_KM).tolist()
        if nav and nav.get("pos_km"):
            P = np.asarray(nav["pos_km"])
            out["est_au"] = (pl.icrf_to_ecliptic(P) / pl.AU_KM).tolist()
            for k in ("axes_km", "bodies", "r_au", "earth_km", "earth_sigma_km", "err_km", "age_s",
                      "resid_rms_arcsec"):
                out[k] = nav.get(k)
            M = pl.icrf_to_ecliptic(np.eye(3))          # rows: ICRF basis in ecliptic coords
            C = M.T @ np.asarray(nav["cov_km2"]) @ M    # covariance in ecliptic coords
            val, vec = np.linalg.eigh(C[:2, :2])
            out["ellipse_km"] = [float(np.sqrt(max(val[1], 0))), float(np.sqrt(max(val[0], 0))),
                                 float(math.atan2(vec[1, 1], vec[0, 1]))]
            out["sigma_z_km"] = float(np.sqrt(max(C[2, 2], 0)))
            if tp:
                e = pl.icrf_to_ecliptic(np.asarray(tp) - P)
                out["err_ecl_km"] = e.tolist()
            # distance to Earth, step by step: |P - X_earth| with X_earth from the ephemeris
            if nav.get("earth_pos_km"):
                XE = np.asarray(nav["earth_pos_km"])
                res = nav.get("resid_by_body") or {}
                seen = next((b for b in res if "Earth" in b.split("+")), None)
                out["earth_calc"] = {
                    "us_au": (pl.icrf_to_ecliptic(P) / pl.AU_KM).tolist(),
                    "earth_au": (pl.icrf_to_ecliptic(XE) / pl.AU_KM).tolist(),
                    "km": float(np.linalg.norm(XE - P)), "sigma_km": nav.get("earth_sigma_km"),
                    "light_min": float(np.linalg.norm(XE - P) / pl.C_KM_S / 60),
                    "truth_km": float(np.linalg.norm(XE - np.asarray(tp))) if tp else None,
                    "seen_as": seen, "resid_arcsec": res.get(seen) if seen else None}
        return out


# ---------------------------------------------------------------- state hub
class Hub(threading.Thread):
    def __init__(self, truth, est, solve_rate_path, mode_label, cam=None):
        super().__init__(daemon=True, name="hub")
        self.truth, self.est = truth, est
        self.cam = cam or Feed("frames")   # real camera frames; empty feed = fall back to the truth render
        self._cam_cache = (-1, None)        # (cam.count, computed view) cache
        self.solve_rate_path = pathlib.Path(solve_rate_path)
        self.mode_label = mode_label
        self.sky = SkyData()
        self.navmap = NavMap()
        self.frames = FrameRenderer(self)
        self.cond = threading.Condition()
        self.snapshot, self.seq = "{}", 0
        self.hist = []               # [t, status(1 lock / 0 lost), err_arcsec|None, rate]
        self.est_seen = 0
        self.last_lock = -math.inf
        self.ever_lock = False
        self.session = {"n": 0, "lock": 0}
        self.solve_rate, self._sr_t = None, 0.0
        self.t0 = time.time()

    def frame_truth(self):
        """Truth to draw the view from: the one the solver paired with its frame if fresh, else latest truth."""
        e = self.est.last if self.est.age() < SIGNAL_TIMEOUT else None
        if e and isinstance(e.get("truth"), dict) and e["truth"].get("ra") is not None:
            return e["truth"]
        return self.truth.last if self.truth.age() < SIGNAL_TIMEOUT else None

    def run(self):
        self.frames.start()
        while True:
            t0 = time.time()
            try:
                snap = json.dumps(self._build(), separators=(",", ":"), allow_nan=False, default=_json_default)
            except Exception as e:
                print("[hub]", repr(e))
                snap = None
            if snap:
                with self.cond:
                    self.snapshot, self.seq = snap, self.seq + 1
                    self.cond.notify_all()
            time.sleep(max(0.005, 1 / 15 - (time.time() - t0)))

    def cam_frame(self):
        """Latest solved camera frame (header dict incl. "jpeg") if fresh, else None."""
        return self.cam.last if self.cam.age() < SIGNAL_TIMEOUT else None

    def _cam_view(self):
        """The camera panel: centroids and the overlay from the estimate solved on THIS frame, mapped from
        screen px into the JPEG's px (screen -> camera homography, then the downscale)."""
        f = self.cam_frame()
        if f is None:
            return None
        if self._cam_cache[0] == self.cam.count:
            return self._cam_cache[1]
        e = f.get("est") or {}
        s = float(f.get("scale", 1.0))
        Hs = f.get("screen_to_cam")
        Hs = None if Hs is None else np.asarray(Hs, float)

        def m(pts):
            p = np.asarray(pts, float).reshape(-1, 2)
            if len(p) and Hs is not None:
                p = cv2.perspectiveTransform(p.reshape(-1, 1, 2), Hs).reshape(-1, 2)
            return np.round(p * s, 1)

        overlay = None
        if e.get("status") == "LOCK" and e.get("ra") is not None:
            o = self.sky.overlay(att_from(e), f["sw"], f["sh"], e.get("fov") or 50.0, e.get("centroids") or [])
            ln = np.asarray(o["lines"], float).reshape(-1, 4)
            overlay = {"lines": np.hstack([m(ln[:, :2]), m(ln[:, 2:])]).tolist(),
                       "cons": [[*m([c[:2]])[0].tolist(), c[2]] for c in o["cons"]],
                       "ident": m(o["ident"]).tolist(),
                       "labels": [[*m([lb[:2]])[0].tolist(), lb[2]] for lb in o["labels"]],
                       "bore": m([[f["sw"] / 2, f["sh"] / 2]])[0].tolist()}
        view = {"w": f["w"], "h": f["h"], "frame_id": self.cam.count, "cents": f.get("cam_cents") or [],
                "overlay": overlay, "status": e.get("status"), "calibrated": Hs is not None}
        self._cam_cache = (self.cam.count, view)
        return view

    def _err(self, est, truth):
        ea, ta = att_from(est), att_from(truth)
        if ea is None or ta is None:
            return None
        full = math.degrees((ta.inv() * ea).magnitude()) * 3600
        c1, c2 = ea.as_matrix()[:, 2], ta.as_matrix()[:, 2]
        point = math.degrees(math.acos(float(np.clip(c1 @ c2, -1, 1)))) * 3600
        roll = (est["roll"] - truth["roll"] + 180) % 360 - 180
        return {"full": full, "point": point, "roll": roll}

    def _build(self):
        now = time.time()
        tr = self.truth.last if self.truth.age() < SIGNAL_TIMEOUT else None
        es = self.est.last if self.est.age() < SIGNAL_TIMEOUT else None
        paired = es.get("truth") if es and isinstance(es.get("truth"), dict) else tr

        # new estimate packets -> history / session stats
        if self.est.count != self.est_seen and self.est.last is not None:
            self.est_seen = self.est.count
            e = self.est.last
            lock = e.get("status") == "LOCK"
            if lock:
                self.last_lock, self.ever_lock = self.est.rx, True
            pt = e.get("truth") if isinstance(e.get("truth"), dict) else self.truth.last
            err = self._err(e, pt) if lock else None
            rate = (pt or {}).get("rate_dps")
            if not (pt or {}).get("mode") == "calib":
                self.hist.append([self.est.rx, 1 if lock else 0, err and err["full"], rate])
                self.session["n"] += 1
                self.session["lock"] += lock
        while self.hist and now - self.hist[0][0] > HISTORY_S:
            self.hist.pop(0)

        # lock state
        if es is None:
            state = "NO SIGNAL"
        elif (tr or {}).get("mode") == "calib":
            state = "CALIB"
        elif es.get("status") == "LOCK":
            state = "LOCK"
        elif now - self.last_lock < LOST_TO_SEARCH:
            state = "LOST"
        else:
            state = "SEARCH"

        err = self._err(es, paired) if es and es.get("status") == "LOCK" and paired else None

        # overlay on the camera view, from the estimated attitude
        geo = paired or tr or {}
        w, h = geo.get("w", 1920), geo.get("h", 1080)
        fov = geo.get("fov") or (es or {}).get("fov") or 50.0
        overlay = None
        cents = (es or {}).get("centroids") or []
        if es and es.get("status") == "LOCK" and es.get("ra") is not None:
            overlay = self.sky.overlay(att_from(es), w, h, fov, cents)

        # attitudes for the 3D view (matrix columns: right, down, boresight in J2000)
        ta, ea = att_from(paired), att_from(es) if es and es.get("status") == "LOCK" else None

        recent = [p for p in self.hist if now - p[0] <= 30]
        lock30 = (sum(p[1] for p in recent) / len(recent)) if recent else None
        errs = sorted(p[2] for p in recent if p[2] is not None)
        if now - self._sr_t > 2:
            self._sr_t = now
            try:
                self.solve_rate = json.loads(self.solve_rate_path.read_text())
            except (OSError, ValueError):
                self.solve_rate = None

        return {
            "now": now, "mode_label": self.mode_label,
            "feeds": {
                "truth": {"ok": tr is not None, "hz": self.truth.hz(), "age": _age(self.truth), "error": self.truth.error},
                "est": {"ok": es is not None, "hz": self.est.hz(), "age": _age(self.est), "error": self.est.error,
                        "mock": bool((es or {}).get("mock"))},
            },
            "truth": (tr or paired) and {k: (tr or paired).get(k) for k in ("ra", "dec", "roll", "fov", "rate_dps", "exposure", "glare", "flash", "mode", "w", "h", "frame")},
            "paired": paired and {k: paired.get(k) for k in ("ra", "dec", "roll")},
            "est": es and {k: es.get(k) for k in ("ra", "dec", "roll", "fov", "status", "confidence", "matches",
                                                  "prob", "rmse", "solve_ms", "n_centroids", "frontend")},
            "state": state, "since_lock": None if not self.ever_lock else now - self.last_lock,
            "err": err,
            "cam": self._cam_view(),
            "view": {"w": w, "h": h, "frame_id": self.frames.id, "has_frame": self.frames.jpeg is not None
                     and self.frame_truth() is not None,
                     "cents": cents[:100], "overlay": overlay},
            "att": {"truth": ta.as_matrix().tolist() if ta is not None else None,
                    "est": ea.as_matrix().tolist() if ea is not None else None},
            "hist": [[round(p[0] - now, 2), p[1], None if p[2] is None else round(p[2], 1), p[3]] for p in self.hist],
            "stats": {"lock30": lock30, "n30": len(recent), "med_err30": errs[len(errs) // 2] if errs else None,
                      "session_n": self.session["n"], "session_lock": self.session["lock"],
                      "uptime": now - self.t0},
            "solve_rate": self.solve_rate,
            "nav": self.navmap.build((es or {}).get("nav"), paired if (paired or {}).get("pos_km") else tr),
        }


def _age(feed):
    a = feed.age()
    return None if math.isinf(a) else round(a, 2)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


# ---------------------------------------------------------------- HTTP
def make_handler(hub, config):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                path = "/index.html"
            if path == "/events":
                return self._events()
            if path == "/state":
                with hub.cond:
                    return self._send(200, hub.snapshot.encode(), "application/json")
            if path == "/config":
                return self._send(200, json.dumps(config).encode(), "application/json")
            if path == "/cam.jpg":
                f = hub.cam.last
                return self._send(200, f["jpeg"], "image/jpeg") if f else self._send(404, b"", "text/plain")
            if path == "/frame.jpg":
                j = hub.frames.jpeg
                return self._send(200, j, "image/jpeg") if j else self._send(404, b"", "text/plain")
            f = (STATIC / path.lstrip("/")).resolve()
            if STATIC in f.parents and f.is_file():
                ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                         ".json": "application/json", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
                         ".wav": "audio/wav"}.get(
                    f.suffix, "application/octet-stream")
                return self._send(200, f.read_bytes(), ctype)
            self._send(404, b"not found", "text/plain")

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            seq = -1
            try:
                while True:
                    with hub.cond:
                        hub.cond.wait_for(lambda: hub.seq != seq, timeout=5)
                        seq, snap = hub.seq, hub.snapshot
                    self.wfile.write(b"data: " + snap.encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return H


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def open_fullscreen(url):
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if pathlib.Path(chrome).exists():
        prof = pathlib.Path.home() / ".cache" / "lis-dashboard-chrome"
        subprocess.Popen([chrome, "--kiosk", f"--app={url}", f"--user-data-dir={prof}",
                          "--no-first-run", "--disable-session-crashed-bubble",
                          "--autoplay-policy=no-user-gesture-required"],   # voice callouts without a click
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("chromium"):
        subprocess.Popen(["chromium", "--kiosk", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)  # press F in the page for full screen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080, help="HTTP port for the page")
    ap.add_argument("--truth-port", type=int, default=5005)
    ap.add_argument("--est-port", type=int, default=5008)
    ap.add_argument("--frame-port", type=int, default=5010, help="solved camera frames from camera.solve_live")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_true", help="synthesise truth and estimates (no UDP)")
    g.add_argument("--mock-est", action="store_true", help="real truth on UDP, synthesised estimates")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--solve-rate", default=str(HERE / "solve_rate.json"),
                    help="JSON with the tetra3-alone vs tetra3+CNN result (see solve_rate.example.json)")
    ap.add_argument("--open", action="store_true", help="open the page full screen")
    ap.add_argument("--console-url", default=None,
                    help="show the virtual console (bridge.serial_bridge --fake --web) in a panel, "
                         "e.g. http://localhost:8081/")
    a = ap.parse_args()

    cam = None
    if a.mock:
        truth, est = Feed("truth"), Feed("est")
        MockRunner(est, own_truth=truth, seed=a.seed).start()
        label = "MOCK"
    else:
        truth = UdpFeed("truth", a.truth_port)
        truth.start()
        if a.mock_est:
            est = Feed("est")
            MockRunner(est, truth_feed=truth, seed=a.seed).start()
            label = "SIM + MOCK EST"
        else:
            est = UdpFeed("est", a.est_port)
            est.start()
            cam = UdpFrameFeed(a.frame_port)
            cam.start()
            label = "LIVE"

    hub = Hub(truth, est, a.solve_rate, label, cam)
    hub.start()
    srv = Server(("127.0.0.1", a.port), make_handler(hub, {"console_url": a.console_url}))
    url = f"http://localhost:{a.port}/"
    print(f"[dashboard] {label}: {url}  (truth UDP {a.truth_port}, estimates UDP {a.est_port})")
    if a.open:
        threading.Timer(0.8, open_fullscreen, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
