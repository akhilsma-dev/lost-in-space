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

- `status`: `LOCK` / `SEARCH` / `LOST`. A frame is good if tetra3 solved with >= 8 matched stars and false-match probability `prob` <= 1e-6 (`--min-matches`, `--prob-max`). `SEARCH` while acquiring (see stages below). Once locked, the first bad frame gives `LOST`, and so does a solve that jumps more than 5 deg from the previous frame carried forward at the measured rate (`--jump-tol`; a flare without whiteout, a false match).
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
| `DETUMBLING r` | recovery: no stable lock for `--detumble-after` (3 s) while the sim tumbles faster than `--detumble-min` (1 deg/s): the rate is ramped down at `--detumble-accel` (4 deg/s^2) to 0, like reaction wheels braking. Once started it finishes (even if VERIFYING/LOCK happen on the way down) |
| `SEARCH SLEW` | recovery: no stable lock for `--recover-after` (8 s) at rest and no candidate being verified: `--slew-rate` (6 deg/s) for `--slew-time` (3 s) to a new patch of sky, then rate 0 and the clock restarts. Repeats while lost |

Recovery (`--recover`, default on; `--no-recover` removes it) sends only the sim's existing `rate <dps>`
command to UDP 5006 (`--sim-cmd-port`; with `--sim` it drives the internal sim). It never acts while LOCKed,
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
