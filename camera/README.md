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
chain this way. `--max-area 5000` keeps motion-blur streaks (holds LOCK to 20 deg/s; default 100 loses it near 5 deg/s).

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
