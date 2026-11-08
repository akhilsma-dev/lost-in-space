# Camera side: webcam -> attitude

```
sim.live (screen) --> webcam --> centroids --(homography)--> screen px --> tetra3 --> LOCK/LOST
      | truth UDP 5009 (dashboard: 5005)                                       | estimate UDP 5008 (dashboard) + 5007 (bridge)
      +-----------------------------> solve_live prints error vs truth <-------+
```

| file | what |
|---|---|
| `capture.py` | open the webcam (1080p preferred), try to lock exposure / gain / focus / WB, **read every control back** and report what stuck. Preview with live blob count. |
| `calibrate.py` | show the sim's checkerboard (`calib 1` over UDP 5006), find the 9x6 corners, fit camera->screen homography, write `data/calib.json` (gitignored) + overlay PNG in `recordings/`. |
| `solve_live.py` | live solve. `--sim` renders frames from `sim.sky` (no webcam); `--sim --sim-camera` also runs them through a synthetic webcam. |
| `fakecam.py` | synthetic "webcam filming the screen": keystone, defocus, LCD black glow, vignetting, noise, JPEG. |
| `common.py` | tetra3 loading, centroid convention fix, calib file, truth listener, error maths. |

## Run it

No hardware:

```bash
.venv/bin/python -m camera.solve_live --sim --rate 2 --frames 100
```

```bash
.venv/bin/python -m camera.solve_live --sim --sim-camera --frames 100 --save-shot
```

With the box (sim on the box display, webcam pointed at it):

```bash
.venv/bin/python -m sim.live
```

```bash
.venv/bin/python -m camera.capture --list
```

```bash
.venv/bin/python -m camera.capture --index 0 --probe
```

```bash
.venv/bin/python -m camera.calibrate --index 0
```

```bash
.venv/bin/python -m camera.solve_live --index 0 --show --save-shot
```

Re-run `calibrate` whenever the camera or screen moves. `solve_live` refuses a
calib file made at a different camera resolution.

On macOS the terminal / app running Python needs camera permission
(System Settings > Privacy & Security > Camera), otherwise OpenCV prints
`not authorized to capture video` and the camera does not open.

No camera at all: `--follow-sim` redraws exactly what the running `sim.live` shows (attitude, tumble blur,
glare) from its truth packets, so console flare / tumble reach the solver. `demo/fake_demo.sh` runs the whole
chain this way. `--detector starnet` uses our CNN (`ml/`) as the star finder. `--max-area 5000` keeps motion-blur streaks (holds LOCK to 20 deg/s; default 100 loses it near 5 deg/s).

## Output (UDP 127.0.0.1:5008 for the dashboard and 5007 for the bridge, one JSON per frame)

`{"t","ra","dec","roll","fov","status","confidence","matches","prob","rmse","solve_ms","n_centroids","centroids":[[x,y],...],"truth","err_arcsec","roll_err_deg"}`

- `status`: `LOCK` if tetra3 solved with >= 8 matched stars and false-match probability `prob` <= 1e-6, else `LOST` (both tunable: `--min-matches`, `--prob-max`).
- `confidence` 0..1 = `min(1, -log10(prob)/20) * max(0, 1 - rmse/900")`; 0 when LOST.
- `centroids`: screen pixels (x right, y down), brightest first, at most 100.
- `truth`: the sim packet paired by receive time (`--latency`, default 0.08 s), `null` without sim.

## Measured (2026-11-07, MacBook Air, headless)

1920x1080 screen, 50 deg FOV, exposure 0.1 s, 50 frames each, new random attitude every 5 frames, `--max-centroids 60`:

| source | tumble | LOCK | median / 95% / max pointing error | roll error (median) | solve |
|---|---|---|---|---|---|
| `--sim` | 0 deg/s | 50/50 | 6" / 24" / 24" | 0.002 deg | 2 ms |
| `--sim --sim-camera` | 0 deg/s | 50/50 | 5" / 33" / 61" | 0.002 deg | 2 ms |
| `--sim` | 2 deg/s | 41/50 | 4" / 34" / 36" | 0.003 deg | 4 ms |
| `--sim --sim-camera` | 2 deg/s | 50/50 | 7" / 32" / 39" | 0.002 deg | 3 ms |
| `--sim` | 5 deg/s | 12/50 | 11" / 34" / 34" | 0.007 deg | 500 ms (timeouts) |
| `--sim --sim-camera` | 5 deg/s | 42/50 | 16" / 52" / 55" | 0.007 deg | 10 ms |

No false locks: every LOCK frame was within 61" of truth. (The fake camera's
defocus blur helps at tumble: it rounds the streaks back into blobs.)
Calibration on the synthetic camera: reprojection RMS 0.12 screen px, and
mean 0.12 / max 0.39 screen px against the known true homography over the
whole screen. Screen pixel = 94" at 50 deg / 1920 px.

Blob finding (~60 ms on a 1080p frame) dominates the frame time, not tetra3.

## Real camera, first try (2026-11-07): iPhone Continuity Camera, open room, no box

`camera/box_test.sh [index]` runs the whole check (probe, sim full screen,
calibrate, solves) and logs to `recordings/box_test.log`. Run it from
**Terminal.app**: processes started by other apps (e.g. the Claude app) never get
the macOS camera prompt and stay `not authorized`.

- Index 0 = MacBook camera, index 1 = iPhone (1920x1080 @ 30 fps).
- **No control locks on macOS**: auto exposure, autofocus and auto WB were all
  refused (`set` returned False, readback -1) on both cameras. Plan for AE/AF.
- The iPhone takes ~1 s to deliver non-black frames; `Camera` now waits for one.
- Phone handheld-ish, ~20 cm from a glossy MacBook screen in a lit room:
  **no-go**. Checkerboard was cropped (camera too close) so calibration failed;
  the sky frame was dominated by ceiling-light reflections, smudges and dust:
  1528 blobs, no tetra3 solve (even uncalibrated, FOV 40-70).
- Next: the dark box (or a dark cloth hood over phone + screen), clean screen,
  camera far enough back to see the whole screen, then re-run.

## Gotchas found on the way

- **tetra3 centroids are +0.5 px off OpenCV's convention** (tetra3 puts pixel
  centres at i+0.5). Uncorrected, that was a constant ~70" pointing bias on
  perfect sim frames (the old 70" baseline). `common.centroids()` subtracts it:
  static error went from 69" to 6" median.
- **The checkerboard corners need the same half-pixel shift**: `checkerboard()`
  returns square edges as pixel indices; edges sit at index - 0.5.
- **tetra3 happily solves a mirrored image**, with the right boresight and
  roll = 180 - true roll (edge-ratio patterns are mirror invariant). OpenCV's
  corner order can make the homography a reflection, so `calibrate` rejects
  reflecting homographies and picks rot0 vs rot180 by the square colours
  (`--mirrored` if the camera really mirrors). Watch for this if roll error
  looks random while pointing error is small.
- Camera-control locking on macOS goes through AVFoundation, which ignores
  most OpenCV property writes; `capture.py` reports what really stuck rather
  than assuming. Fallback if nothing locks: screen at 100% brightness, let
  tetra3's local background subtraction absorb the auto-gain, `--avg N` for
  static scenes, or lock the camera with its vendor app / `uvc-util` first.

## Position from planets (`camera/navigate.py`, offline proof)

Stars give attitude only. Bodies in the Solar System are close enough to show parallax:
each identified planet at its known position X_i (JPL DE421) gives a line of sight u_i through
the spacecraft, so three or more give the position P.

```
# one-time: JPL ephemeris, ~17 MB, gitignored
.venv/bin/python -c "from skyfield.api import Loader; Loader('data')('de421.bsp')"

.venv/bin/python -m sim.planets                          # sanity check: the sky seen from Earth
.venv/bin/python -m camera.navigate --bench              # 24 positions, geometry + rendered, ~1 min
.venv/bin/python -m camera.navigate --bench --mode geometry --n 200
.venv/bin/python -m camera.navigate --bench --mode rendered --n 6 --save-frames
.venv/bin/python -m camera.navigate --bench --mode rendered --noise 3 --tag noise3
.venv/bin/python -m camera.navigate --bench --mode rendered --sim-camera --rendered-sigma 10 --tag simcam
```

Writes `camera/out/nav_bench.md` (summary + per-position table), `nav_bench.csv`, `nav_bench.png`
(`nav_bench_<tag>.*` with `--tag`).

Pieces:
- `sim/planets.py`: `Ephemeris.positions(jd)` (heliocentric ICRF km for the Sun, Mercury, Venus,
  Earth, Moon, Mars, and the Jupiter and Saturn barycentres), `Ephemeris.apparent(P, jd)` (unit direction
  from P with light time, distance, magnitude H + 5 log10(r Δ) + a crude phase term).
- `SkyRenderer.render(att, ..., extra=(dirs, mags))` draws extra point sources in the stars' blob style.
  Without `extra` the output is bit-identical to before.
- `camera/navigate.py`:
  - `pixel_rays`: pinhole camera, f = (w/2)/tan(fov/2), x right, y down, z boresight, then the solved attitude.
  - `refine_attitude`: least-squares (Kabsch) refit of tetra3's attitude on every blob near a
    catalogue star. tetra3's own attitude was off by 12″ median and up to ~130″ in roll; the refit
    brings that to about 2.6″, and the rays at the frame edge improve to match.
  - `position_fix` / `refine`: Σ wᵢ(I − uᵢuᵢᵀ)(Xᵢ − P) = 0, first unweighted, then wᵢ = 1/(σᵢrᵢ)².
    The Sun can be a sighting (X = 0).
  - `covariance` / `ellipsoid`: (Σ (I − uuᵀ)/(σᵢrᵢ)²)⁻¹ and its 1σ semi-axes.
  - `identify`: brute force over seeds of (3 candidates × 3 bodies). Each seed gives a P in
    0.3–6 AU, predicts every body from it and gathers the candidates that agree within 120″. It
    keeps the hypothesis with the most bodies, then the lowest residual.
  - `navigate`: identify, then the final fix with light time.
  - Unresolved Earth+Moon: from a few tenths of an AU the two are one blob. `identify` also tries
    the pseudo-body "Earth+Moon" (its flux-weighted photocentre). The flux-vs-magnitude curve comes
    from the display's blob model, which a real camera would calibrate on identified stars.

Benchmark scenario: hidden observer 0.7–2 AU from the Sun, ecliptic latitude ±3°, at epoch
2026-11-08. The nav scan is 8 pointings along the ecliptic at Sun elongations 40°–320°
(1920×1080, 50° FOV, 10° overlap). No pointing is within 35° of the Sun, and the Sun is never
rendered. Each frame is solved with the solve_live settings: centroids with max_area 100,
60 centroids, FOV 50 ± 2°, at least 8 matches, Prob ≤ 1e-6. A second centroid pass with
max_area 3000 finds the planets: anything brighter than about mag 1 renders larger than 100 px
and tetra3's star filter drops it. Blobs more than 4 px from every catalogue star are candidates.

Results (the same 24 hidden positions in every run, `--seed 1`; files in `camera/out/`):

| run | Sun | fixes | median err | 90% err | median 1σ ellipsoid | mean NEES | ID ok |
|---|---|---:|---:|---:|---|---:|---:|
| geometry, 20″ noise | no | 24/24 | 31,600 km (2.1e-4 AU) | 129,000 km | 75,600 / 13,100 / 12,500 km | 2.6 | 24/24 |
| geometry, 20″ noise | 20″ | 24/24 | 28,900 km | 82,000 km | 34,100 / 11,500 / 11,100 km | 3.8 | 24/24 |
| rendered, clean screen | no | 24/24 | 10,100 km (6.7e-5 AU) | 16,800 km | 20,600 / 3,800 / 3,600 km | 2.1 | 24/24 |
| rendered, clean screen | 20″ | 24/24 | 9,700 km | 25,300 km | 16,200 / 3,800 / 3,600 km | 2.3 | 24/24 |
| rendered, `--noise 3` | no | 24/24 | 7,600 km | 18,900 km | 20,600 / 3,800 / 3,600 km | 2.3 | 24/24 |
| rendered, `--noise 3` | 20″ | 24/24 | 11,900 km | 22,100 km | 16,200 / 3,800 / 3,600 km | 2.6 | 24/24 |
| rendered, `--sim-camera` | no | 24/24 | 22,900 km (1.5e-4 AU) | 38,200 km | 41,100 / 7,600 / 7,300 km | 40.7 | 23/24 |
| rendered, `--sim-camera` | 20″ | 24/24 | 15,100 km | 38,700 km | 23,300 / 7,300 / 6,900 km | 2.3 | 24/24 |

Planet direction errors after the attitude refit: 1.9″ median on the clean screen, 3.1″ with
noise 3, 8.0″ through the fake webcam (keystone, defocus, LCD glow, JPEG; the homography is
fitted to 0.12 px). Every one of the 192 scan frames locked in each rendered run.

- **Clean and noise 3:** about 8,000–12,000 km median.
- **Fake camera:** about 15,000–23,000 km.
- **Feasibility review:** both are inside or below its 10,000–30,000 km estimate. Geometry mode
  at 20″ is about 30,000 km, because those positions see a median of 5 bodies.
- **The ellipsoid is honest:** NEES ≈ 2–4. The ellipsoid σ was 5″ for the screen runs and 10″
  for the fake camera.
- **Wrong ID:** one fake-camera position (planets only) gave its partly merged Earth/Moon blob a
  334″ direction error. It was identified as Earth, and the fix was 700,000 km off; this outlier
  alone causes the NEES of 40.
- **Weak axis:** the long axis of the ellipsoid points out of the ecliptic, because all the
  planets lie near it (feasibility review point 5).
- **Earth/Moon ambiguity:** Earth and the Moon can swap and still give a self-consistent fix
  about 350,000 km off. Before the identifier also seeded on the Earth+Moon blend, this happened
  once per run.
- **Fake-camera fixes:** the edge of the screen's glow shows up as a row of blobs (dropped
  within 10 px of the edge). Noise blobs: only the 8 brightest unmatched blobs per frame are kept.

### Live (sim.live → solve_live → dashboard)

```
demo/fake_demo.sh --nav        # everything, no camera; dashboard POSITION panel
# or by hand:
.venv/bin/python -m sim.live --window --planets
.venv/bin/python -m camera.solve_live --follow-sim --nav --nav-scan 3
.venv/bin/python -m dashboard
```

- **`sim.live --planets`** gives the spacecraft a hidden random position: 0.7–2 AU from the Sun,
  within ±3° of the ecliptic, epoch `--jd`, default 2026-11-08. It draws Mercury to Saturn and the
  Moon where they appear from there; the Sun is never drawn.
  - New truth fields: `pos_km`, `jd`, and `planets` (as drawn, so `--follow-sim` and the
    dashboard redraw the same screen).
  - New commands on UDP 5006: `point <ra> <dec> [roll]` (hold this attitude; tumbling stops) and
    `pos random` (key `n`).
- **`solve_live --nav`**: after each LOCK it runs `CandidateFinder`: refit the attitude on all stars,
  then list the non-star blobs as inertial rays. `LiveNavigator` keeps these rays and forgets any
  not seen for `--nav-memory` s (default 60). A background thread re-solves the position whenever
  the list changes.
  - `--nav-scan 3` sends sim.live a `point` command every 3 s, for 8 pointings along the ecliptic.
    A full scan takes 24 s.
  - Every estimate packet carries a `nav` block: position, covariance, 1σ axes, bodies used,
    distance from the Sun and to Earth (± the 1σ along that line), and `err_km` against the
    hidden truth.
- **Dashboard**: a POSITION panel appears when `nav` data arrives (it replaces the tumble/glare
  strip).
  - Readouts: distance from the Sun, distance to Earth ± its 1σ, and the error against the truth.
  - Map: top view of the ecliptic, with orbits, planets, the sight lines used, the hidden truth
    (dashed ring) and the estimate (green).
  - Zoom inset: the 1σ and 3σ error ellipse in the ecliptic plane, the offset to the truth, a km
    scale bar and the out-of-plane 1σ.

**First live run** (`--seed 4`, hidden position 1.926 AU from the Sun):
- 400/400 frames locked.
- After one scan (~25 s): 7 bodies; Earth is 411.392 million km away ± 23,000 km; the error vs
  truth is 4,648 km.
- The 1σ ellipse is 23,000 × 8,800 km (σ = 10″, deliberately conservative).

Not done yet:
- A photometric check (blob brightness against predicted magnitude) to rule out Earth/Moon swaps.
- The scan ignores the Sun. sim.live never draws it, so no pointing is excluded around it. A real
  spacecraft would skip pointings near the Sun and add a sun sensor.
- Console/voice integration: the OLED, servos and voice callouts don't know about position yet.
- The real camera.
