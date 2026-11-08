"""Solar-system bodies from JPL DE421: positions, apparent directions, rough magnitudes.

Frame: heliocentric ICRF/J2000 equatorial, km (the Sun at the origin at time t). Time: TDB
Julian date (float). The ephemeris is data/de421.bsp (~17 MB, gitignored), fetched once with:
    .venv/bin/python -c "from skyfield.api import Loader; Loader('data')('de421.bsp')"

    eph = Ephemeris()
    t = EPOCH_JD
    eph.positions(t)                   # {"Sun": (0,0,0), "Venus": (x,y,z) km, ...}
    eph.apparent(P, t)                 # seen from heliocentric P (km): unit dir with light time,
                                       # distance, magnitude, retarded heliocentric position

Light time: a body is seen where it was at t - |X - P|/c. Stellar aberration (observer
velocity) is ignored: the simulated spacecraft is at rest in the heliocentric frame, and the
same would apply to the stars anyway.

Magnitudes: m = H + 5 log10(r * D) + beta * phase_deg (r, D in AU), with a crude linear phase
coefficient. Good to a few tenths of a magnitude, which is all the renderer needs.
"""
import math
import pathlib

import numpy as np

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
BSP = DATA / "de421.bsp"

AU_KM = 149_597_870.7
C_KM_S = 299_792.458
OBLIQUITY_DEG = 23.4392911   # J2000 mean obliquity: ecliptic -> ICRF
EPOCH_JD = 2461352.5          # 2026-11-08 00:00 TDB, the default benchmark epoch

# name -> (DE421 target, H = V(1,0), linear phase coefficient mag/deg)
BODIES = {
    "Sun":     ("sun", None, 0.0),
    "Mercury": ("mercury", -0.60, 0.038),
    "Venus":   ("venus", -4.47, 0.013),
    "Earth":   ("earth", -3.86, 0.020),
    "Moon":    ("moon", 0.28, 0.026),
    "Mars":    ("mars", -1.52, 0.016),
    "Jupiter": ("jupiter barycenter", -9.40, 0.005),
    "Saturn":  ("saturn barycenter", -8.88, 0.044),
}
SUN_MAG_1AU = -26.74
PLANETS = [b for b in BODIES if b != "Sun"]


def ecliptic_to_icrf(v):
    e = math.radians(OBLIQUITY_DEG)
    ce, se = math.cos(e), math.sin(e)
    M = np.array([[1, 0, 0], [0, ce, -se], [0, se, ce]])
    return np.asarray(v, np.float64) @ M.T


def ecliptic_unit(lon_deg, lat_deg):
    lon, lat = np.radians(lon_deg), np.radians(lat_deg)
    return ecliptic_to_icrf(np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], -1))


class Ephemeris:
    def __init__(self, path=BSP):
        from skyfield.api import Loader
        path = pathlib.Path(path)
        if not path.exists():
            raise SystemExit(f"{path} missing: .venv/bin/python -c \"from skyfield.api import Loader; "
                             f"Loader('{path.parent}')('{path.name}')\"")
        ld = Loader(str(path.parent))
        self.ts = ld.timescale(builtin=True)
        self.eph = ld(path.name)
        self._seg = {n: self.eph[tgt] for n, (tgt, _, _) in BODIES.items()}

    def _bary(self, name, jd):
        return self._seg[name].at(self.ts.tdb_jd(jd)).position.km

    def positions(self, jd, bodies=None):
        """Heliocentric ICRF positions (km) at TDB Julian date jd (geometric, no light time)."""
        sun = self._bary("Sun", jd)
        return {b: self._bary(b, jd) - sun for b in (bodies or BODIES)}

    def apparent(self, P, jd, bodies=None, light_time=True):
        """What an observer at heliocentric P (km) sees at jd. Returns {name: dict(dir, dist_km,
        r_km, pos_km, mag, phase_deg)}; pos_km is the body's retarded position, expressed in the
        Sun-at-time-jd frame, so dir == unit(pos_km - P) exactly."""
        P = np.asarray(P, np.float64)
        sun_now = self._bary("Sun", jd)
        out = {}
        for b in (bodies or BODIES):
            X = self._bary(b, jd) - sun_now
            if light_time:
                for _ in range(3):
                    tau = np.linalg.norm(X - P) / C_KM_S / 86400.0
                    X = self._bary(b, jd - tau) - sun_now
            d = X - P
            dist = float(np.linalg.norm(d))
            r = float(np.linalg.norm(X))
            out[b] = {"dir": d / dist, "dist_km": dist, "r_km": r, "pos_km": X,
                      **magnitude(b, X, P)}
        return out


def magnitude(name, X, P):
    """Approximate visual magnitude and phase angle of body `name` at heliocentric X seen from P."""
    d = np.asarray(X) - np.asarray(P)
    D = np.linalg.norm(d) / AU_KM
    if name == "Sun":
        return {"mag": SUN_MAG_1AU + 5 * math.log10(D), "phase_deg": 0.0}
    r = np.linalg.norm(X) / AU_KM
    # phase angle: Sun-body-observer
    cosa = float(np.dot(-np.asarray(X), -d) / (np.linalg.norm(X) * np.linalg.norm(d)))
    phase = math.degrees(math.acos(max(-1.0, min(1.0, cosa))))
    _, H, beta = BODIES[name]
    return {"mag": H + 5 * math.log10(r * D) + beta * phase, "phase_deg": phase}


if __name__ == "__main__":
    eph = Ephemeris()
    pos = eph.positions(EPOCH_JD)
    earth = pos["Earth"]
    print(f"epoch TDB JD {EPOCH_JD}: heliocentric distances (AU) and view from Earth")
    for b, s in eph.apparent(earth + 1.0, EPOCH_JD).items():  # nudge P off Earth's centre
        if b == "Earth":
            continue
        ra = math.degrees(math.atan2(s["dir"][1], s["dir"][0])) % 360
        dec = math.degrees(math.asin(s["dir"][2]))
        print(f"  {b:8s} r {s['r_km'] / AU_KM:6.3f}  D {s['dist_km'] / AU_KM:7.4f}  "
              f"RA {ra:7.3f} Dec {dec:+7.3f}  mag {s['mag']:+6.2f}")
