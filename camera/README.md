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

`{"t","ra","dec","roll","fov","status","stage","confidence","matches","prob","rmse","solve_ms","n_centroids","centroids":[[x,y],...],"truth","err_arcsec","roll_err_deg"}`

- `status`: `LOCK` / `SEARCH` / `LOST`. A frame is good if tetra3 solved with >= 8 matched stars and false-match probability `prob` <= 1e-6 (`--min-matches`, `--prob-max`). `SEARCH` while acquiring (see stages below). Once locked, a frame that does not solve, or whose solve jumps more than 5 deg from the last good attitude carried forward at the measured rate (`--jump-tol`; a flare without whiteout, a false match), starts `COASTING`: status stays `LOCK` and `ra`/`dec`/`roll` are the attitude carried forward (`"coasting": true`, `prob`/`rmse` from the rejected or missing solve are not used). No good solve within `--coast` (1.5 s), or a whited-out frame (mean brightness > 100 or fewer than 3 blobs: flare), gives `LOST`.
- `stage`: what the tracker is doing right now (see below). `LOCK` when locked, `LOST` on the frame lock is lost.
- `confidence` 0..1 = `min(1, -log10(prob)/20) * max(0, 1 - rmse/900")`; 0 unless LOCK.
- `centroids`: screen pixels (x right, y down), brightest first, at most 100.
- `truth`: the sim packet paired by receive time (`--latency`, default 0.08 s), `null` without sim.

## Acquisition stages and recovery maneuvers

Lost-in-space acquisition takes a believable 5-6 s and every step is real work:

| stage | meaning |
|---|---|
| `DETECTING n` | fewer than `--detect-min` (8) stars in the frame: flare whiteout, glare, heavy blur |
| `MATCHING` | enough stars, no trustworthy pattern match yet |
| `RATE HIGH r` | matches, but the rate measured from consecutive solves is above `--acq-max-rate` (8 deg/s): too fast to confirm a lost-in-space solution. Once LOCKed, tracking holds at higher rates (20 deg/s with `--max-area 5000`) |
| `VERIFYING k/N` | a candidate is being confirmed: every `--verify-spacing` (1 s) a new good solve must agree within `--verify-tol` (2 deg, +10% of the angle turned) with the previous verified attitude dead-reckoned forward at the measured body rate. Disagreement (a false match) restarts at check 1; no good solve for `--verify-timeout` (2.5 s) drops the candidate. The first match is check 1 of `--verify-n` (6) |
| `LOCK` | all N checks passed: ~(N-1) x spacing = 5 s after the first match |
| `COASTING s` | LOCKed, but the last good solve is s seconds old (blurred, unsolved or rejected frames): the attitude is carried forward at the measured body rate and reported with status `LOCK`, confidence fading to half. The next good solve that agrees resumes `LOCK`; none within `--coast` (1.5 s; `--coast 0` = the old LOST on the first bad frame) gives `LOST`. A flare whiteout (mean brightness > 100, or fewer than 3 blobs) is still `LOST` at once: StarNet finds "stars" in a whiteout, so the blob count alone is not enough. While coasting, the jump check's rate term stops growing after 0.5 s (at most 20 deg by default) |
| `DETUMBLING r` | recovery: no stable lock for `--detumble-after` (3 s) while the sim tumbles faster than `--detumble-min` (1 deg/s): the rate is ramped down at `--detumble-accel` (4 deg/s^2) to 0, like reaction wheels braking. Once started it finishes (even if VERIFYING/LOCK happen on the way down) |
| `SEARCH SLEW` | recovery: no stable lock for `--recover-after` (8 s) at rest and no candidate being verified: `--slew-rate` (6 deg/s) for `--slew-time` (3 s) to a new patch of sky, then rate 0 and the clock restarts. Repeats while lost |

Recovery (`--recover`, default on; `--no-recover` removes it) sends only the sim's existing `rate <dps>`
command to UDP 5006 (`--cmd-port`; with `--sim` it drives the internal sim). It works alongside `--nav --nav-scan`:
the scan only steps while LOCKed and not spinning, recovery only acts while not LOCKed, and each commanded
`point` is an expected jump (it does not count as LOST). While LOCKed, the jump check allows `--jump-tol` (5 deg)
plus `--jump-rate` (30 deg/s) times the frame gap, so a sudden knob turn at a low frame rate is not a jump. It never acts while LOCKed,
so tumble dialled in on the console knob during LOCK is left alone. The recovery clock runs from the last
*stable* lock (held `--stable-lock` 3 s) so a flapping lock still gets a maneuver. The knob on the console keeps
its position after a detumble (the sim then tumbles at 0 until the knob is moved again).

`--quiet` prints one `[stage] <t> s <stage>` line per stage change and `[acq] LOCK after N s`; the end
summary lists all acquisition times.

Measured headless (2026-11-08, `--sim --max-area 5000`, 10 fps simulated, times from start / loss of lock):

| scenario | stages | time to LOCK |
|---|---|---|
| at rest or 3-10 deg/s, start | VERIFYING 1/6 ... 5/6, LOCK | 5.5 s |
| 3 deg/s, attitude jump every 15 s | LOST (jump detected), VERIFYING ..., LOCK | 5.6 s, 5.6 s |
| 20 deg/s, start | RATE HIGH 20.1, DETUMBLING 20 (3.1 s), VERIFYING ... (from 6.4 s), LOCK | 11.8 s |
| 15 deg/s, classic blob filter (solves come and go) | a false match caught at check 2 (35.8 deg off), restart | 5.7-8.2 s |
| never solves (`--min-matches 500`) | MATCHING, SEARCH SLEW at 8.1 s, MATCHING, SEARCH SLEW at 19.3 s ... | - |

Live, the whole chain (`sim.live` at ~15 fps, `--follow-sim --max-area 5000`, virtual console): start 5.6 s;
console flare at rest 6.5 s from loss (7.0 s from the button, whiteout included); knob to 20 deg/s during LOCK
kept LOCK with no maneuver; flare at 20 deg/s: RATE HIGH, DETUMBLING 20 -> 0 in 5.1 s, LOCK 11.6 s from loss
(12.2 s from the button). Glare alone does not stop a solve (6.4 s).

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

### Star finders: switch live, film through the synthetic webcam

`--models` loads all three star finders and switches between them live (`detector classic|tuned|starnet` on
`--nav-cmd-port`, the dashboard's SOLVE RATE panel): **classic** = tetra3's blob finder as shipped (max area
100 px), **tuned** = streaks kept (`--max-area`, 5000), **starnet** = our CNN on the frame warped to screen px.
Each estimate carries `detector`, `detectors` and `good` (this frame alone solved trustworthily).
`--sim-camera` now also works with `--follow-sim`: each sim frame is filmed by `camera.fakecam` (keystone,
defocus, vignetting, noise, JPEG; ~50 ms) and the camera→screen homography is auto-fitted on its checkerboard,
so the live demo sees what the box's webcam would (`fake_demo.sh --webcam`; ~8-10 solves/s). With `--follow-sim`
it is also switchable live: `camera clean|webcam` on the command port (the dashboard's CLEAN / WEBCAM switch in
the camera view's header); the star finder background follows (fast for clean, tetra3's local filter for the
webcam), and a lock survives the switch. Estimates carry `camera` and `cameras`.

Measured live at 20 deg/s through the webcam (25 s per model, 2026-11-08): with tetra3 (classic or tuned) the
lock broke within seconds and recovery detumbled the craft; StarNet held LOCK for the full 25 s in one run and lost
it in sparse sky in another (25-42 stars found, confidence 0.3-0.6). StarNet's benchmark is 97% correct frames,
and one bad frame ends a LOCK, so at ~10 frames/s it cannot hold lock for long at 20 deg/s either: coasting
through single-frame dropouts would make the difference show reliably (see TODO).

### Frame rate (`--follow-sim`, 2026-11-08)

`solve_live` prints a `[perf]` line every 5 s: solves/s and ms per frame for read (and render), solve, nav, send.
Three changes took the `fake_demo.sh --nav` solver from about 2 to 15-18 solves/s (limited by sim.live's own
~15 fps), 11-15 during a WHERE AM I? sweep:
- **Shared frames.** sim.live puts each frame it shows in shared memory (`sim/framebus.py`, named in the truth
  packet's `"shm"`; `sim.live --no-share-frames` turns it off) and `--follow-sim` copies it instead of redrawing
  from truth: 50-100 ms → under 1 ms. `--no-shm` forces the old redraw.
- **Fast star finder for rendered skies** (`--centroid-mode auto|fast|robust`): a black sky needs no local
  background filter. tetra3 with `bg_sub_mode=global_mean, binary_open=False`: 43 vs 171 ms on 1080p, 12/12
  solved, median 7″ vs 6″. auto = fast for `--sim` / `--follow-sim`, robust (tetra3's default) for a camera.
- **Planet search off the frame loop.** The candidate pass (100+ ms) runs in a worker thread, not while spinning,
  and in button mode only during a sweep. It used to cost 110-250 ms per frame on average once locked.


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
    A full scan takes 24 s. Steps are only taken while LOCKed and while the spin measured from the solves
    is below `--nav-scan-max-rate` (0.5 deg/s): `point` stops the sim's tumble, so scanning while the console
    knob spins the craft used to cancel the knob within 3 s.
  - Scan steps are smooth `slew`s (sim.live command: turn to an attitude at a set rate, then hold) at
    `--nav-slew-rate` (20 deg/s), tracked all the way; the `--nav-scan` dwell (2 s in `fake_demo.sh --nav`)
    starts on arrival. A sweep starts at the scan pointing nearest the current view; a full sweep takes about
    35-40 s. `--nav-slew-rate 0` uses `point` teleports instead. A flare during a sweep restarts it.
  - Each locked frame with a fix carries `planets_px`: `[[x, y, name], ...]`, screen px where the fix's bodies
    should be, from the estimated position (light time included) and attitude. The dashboard marks them.
  - `--nav-scan-mode button` (what `fake_demo.sh --nav` uses with the virtual console): no automatic scan.
    WHERE AM I? on the console sends `whereami` (bridge → UDP 5011, `--nav-cmd-port`); the solver forgets
    old sightings and runs one 8-pointing sweep. Sightings are kept until the next press (or a flare with
    `--nav-reset-on-blind`). The `nav` block carries `scan`: `{mode, active, step, n, paused}`.
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

- **Flare = lost again** (`fake_demo.sh --nav` turns both on; a demo convention, since a real flare
  only turns the spacecraft):
  - `sim.live --flare-moves`: a flare also moves the spacecraft to a new hidden position.
  - `solve_live --nav-reset-on-blind`: the solver drops its sightings and fix when a frame is
    whited out (mean > 100; a white screen is ~160 through the synthetic webcam), which is what the camera itself sees during a flare.
  - Measured: reset after 1 s, a first new fix 14 s after the flare (3 bodies, 24,800 km off),
    then 6 bodies and 435 km off once the scan finished.
- **Distance-to-Earth working** on the dashboard: our position (from the photos), Earth's (from the
  ephemeris), the subtraction written out, light time, the truth comparison, and how closely
  Earth was seen where predicted. The map draws a labelled line to Earth.

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
