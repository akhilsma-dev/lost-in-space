# StarNet benchmark through the fake webcam (100 frames per cell, 36 min wall)

Frames from `ml.patches.make_frame` (same settings and seeds as `benchmark.md`), then filmed by
`camera.fakecam.FakeCamera`: keystone, defocus, LCD black glow, vignetting, sensor noise and JPEG 85.
StarNet never saw any of these in training. The camera->screen homography is auto-fitted once on the
fake camera's view of the checkerboard (`camera.calibrate.fit`). Every method solves in screen pixels
with the sim's known FOV, and all metrics are measured in screen pixels against the sim's ground truth.
Command: `.venv/bin/python -m ml.benchmark --sim-camera --n 100` (CSV in `bench_frames_simcam.csv`,
plot in `solve_rate_simcam.png`).

Two ways to get from camera pixels to screen pixels:
* **centroid warp** (`tetra3`, `tetra3-tuned`, `StarNet`): detect on the raw camera frame, then warp
  the centroids through the homography. This is what `camera/solve_live.py` does today.
* **image warp** (`tetra3-tuned-warp`, `StarNet-warp`): `cv2.warpPerspective` the camera frame back to
  screen pixels first, then detect. This is the input `ml/detect.py` documents for StarNet.

## Summary: correct solves %, original renderer vs through the fake webcam

| | rate deg/s | 0 | 2 | 5 | 10 | 15 | 20 |
|---|---|---:|---:|---:|---:|---:|---:|
| no glare | tetra3-tuned, original (`benchmark.md`) | 100 | 100 | 100 | 100 | 89 | 67 |
| | tetra3-tuned, webcam, centroid warp | 100 | 100 | 100 | 99 | 74 | 39 |
| | tetra3-tuned, webcam, image warp | 100 | 100 | 100 | 100 | 84 | 51 |
| | StarNet, original (`benchmark.md`) | 100 | 100 | 100 | 100 | 100 | 100 |
| | StarNet, webcam, centroid warp | 93 | 95 | 87 | 84 | 79 | 68 |
| | **StarNet, webcam, image warp** | **100** | **100** | **100** | **100** | **98** | **97** |
| glare | tetra3-tuned, original | 100 | 100 | 100 | 99.5 | 85 | 72 |
| | tetra3-tuned, webcam, centroid warp | 98 | 99 | 98 | 88 | 45 | 31 |
| | tetra3-tuned, webcam, image warp | 100 | 100 | 100 | 98 | 72 | 61 |
| | StarNet, original | 100 | 100 | 100 | 100 | 100 | 100 |
| | StarNet, webcam, centroid warp | **16** | **16** | **8** | **18** | **8** | **8** |
| | **StarNet, webcam, image warp** | **99** | **100** | **100** | **99** | **100** | **96** |

## What changed

1. **StarNet on raw camera pixels breaks down.** Fed the camera frame directly, StarNet's recall falls
   from 0.68 to 0.07-0.20 and precision from 0.99 to 0.1-0.3. It still solves 68-95% of no-glare frames
   but only 8-18% of glare frames, even at 0 deg/s. Tuned tetra3 beats it at every rate up to 10 deg/s.
   A small ablation (6 frames per setting, glare on) showed the cause is geometry, not image quality:
   keystone plus resampling alone (no blur, noise, glow, vignette or JPEG) already drops recall to 0.12
   at 0 deg/s. Turning off any single photometric effect does not fix it. In camera pixels the screen
   is shrunk to about 85% and stars get a resampled PSF that StarNet never saw. StarNet also fires on the
   bright screen edge against the black surround: in 3 checked frames, 44-59 of its top-60 "stars" were
   within 15 px of the screen border. Masking the border alone did not fix recall.
2. **Warping the image back to screen pixels first fixes most of it.** `StarNet-warp` gets 96-100% correct
   at every rate, with and without glare, compared with 100% on the original renderer. It loses 0-4 points.
   Recall drops from about 0.65 to 0.30-0.51 and precision from about 0.9-1.0 to 0.44-0.74, but what it
   still finds is enough for tetra3. Attitude error is unchanged when static (69 vs 69 arcsec) and grows
   at speed (164-179 vs about 101 arcsec at 20 deg/s).
3. **Tuned tetra3 is unaffected below 10 deg/s but drops sharply during fast tumble.** At 15-20 deg/s it
   falls to 74/39% (no glare) and 45/31% (glare) with centroid warp, compared with 89/67 and 85/72 in
   the original benchmark. Image warp recovers part of that loss (84/51 and 72/61).
   Hot pixels and the planet get defocused into blobs, and tetra3's centroider picks them up: junk rises
   from about 1 to 12-21 per frame and precision falls to 0.1-0.7. StarNet still rejects them
   (junk 0.1-0.7 per frame).
4. **Does StarNet still beat tuned tetra3 on frames it was not trained on?** Yes, but only on the
   image-warp path, and only during fast tumble. Up to 10 deg/s, both methods are at 98-100%.
   At 15 deg/s, StarNet-warp is ahead of the best tetra3 variant by 14 points (no glare: 98 vs 84) and
   28 points (glare: 100 vs 72). At 20 deg/s it is ahead by 46 points (97 vs 51) and 35 points (96 vs 61).
   On the raw-camera path, StarNet is worse than tuned tetra3.

## Caveats for the pitch

* The fake webcam is still synthetic, and the stars underneath come from the same renderer StarNet was
  trained on. This test covers the camera effects only. Real camera frames are a separate test that
  has not been run yet.
* Any live pipeline that uses StarNet **must warp the image** to screen pixels before detection. Warping
  only the centroids, as `camera/solve_live.py` does for tetra3, makes StarNet worse than tetra3.
* s/frame is wall time per frame on 9 worker processes sharing 10 cores, single-threaded CPU torch.
  With 5 methods per frame instead of 3, contention is higher than in `benchmark.md`, so treat
  timings as relative only. StarNet takes about 5 s/frame here, against about 2.5 s in the original run.
* 100 frames per cell (vs 200 in the original), so a single cell's correct % has a standard error of up
  to about 5 points.

# Full tables

correct = tetra3 returned an attitude within 1 deg of truth. recall / precision: detected centroids within 3 px of a detectable star (matched-filter SNR >= 5). junk = centroids sitting on the planet or a hot pixel. att err = median total rotation error of correct solves.


### No glare

| rate deg/s | method | correct % | any solve % | att err (arcsec) | recall | precision | centroid err px | junk/frame | s/frame |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | tetra3 | 100.0 | 100.0 | 66 | 0.97 | 0.36 | 0.18 | 17.6 | 0.42 |
| 0 | tetra3-tuned | 100.0 | 100.0 | 66 | 0.99 | 0.36 | 0.17 | 18.6 | 0.46 |
| 0 | StarNet | 93.0 | 93.0 | 85 | 0.20 | 0.31 | 0.19 | 0.6 | 4.95 |
| 0 | tetra3-tuned-warp | 100.0 | 100.0 | 65 | 0.99 | 0.73 | 0.17 | 20.2 | 0.59 |
| 0 | StarNet-warp | 100.0 | 100.0 | 69 | 0.49 | 0.72 | 0.19 | 0.2 | 5.32 |
| 2 | tetra3 | 100.0 | 100.0 | 67 | 0.95 | 0.32 | 0.23 | 17.8 | 0.47 |
| 2 | tetra3-tuned | 100.0 | 100.0 | 68 | 0.99 | 0.32 | 0.22 | 18.8 | 0.47 |
| 2 | StarNet | 95.0 | 95.0 | 81 | 0.20 | 0.30 | 0.22 | 0.5 | 5.30 |
| 2 | tetra3-tuned-warp | 100.0 | 100.0 | 68 | 0.99 | 0.67 | 0.21 | 20.3 | 0.59 |
| 2 | StarNet-warp | 100.0 | 100.0 | 70 | 0.51 | 0.74 | 0.24 | 0.1 | 4.96 |
| 5 | tetra3 | 82.0 | 82.0 | 77 | 0.79 | 0.22 | 0.52 | 18.1 | 0.93 |
| 5 | tetra3-tuned | 100.0 | 100.0 | 74 | 0.88 | 0.24 | 0.46 | 19.1 | 0.51 |
| 5 | StarNet | 87.0 | 87.0 | 89 | 0.18 | 0.30 | 0.31 | 0.3 | 5.47 |
| 5 | tetra3-tuned-warp | 100.0 | 100.0 | 75 | 0.91 | 0.50 | 0.42 | 20.5 | 0.74 |
| 5 | StarNet-warp | 100.0 | 100.0 | 76 | 0.47 | 0.72 | 0.37 | 0.1 | 5.56 |
| 10 | tetra3 | 24.0 | 24.0 | 167 | 0.34 | 0.08 | 1.46 | 18.6 | 2.02 |
| 10 | tetra3-tuned | 99.0 | 99.0 | 182 | 0.45 | 0.10 | 0.99 | 19.6 | 0.60 |
| 10 | StarNet | 84.0 | 84.0 | 170 | 0.16 | 0.26 | 0.44 | 0.5 | 5.51 |
| 10 | tetra3-tuned-warp | 100.0 | 100.0 | 166 | 0.55 | 0.21 | 1.09 | 20.7 | 0.65 |
| 10 | StarNet-warp | 100.0 | 100.0 | 112 | 0.42 | 0.62 | 0.60 | 0.3 | 5.16 |
| 15 | tetra3 | 15.0 | 15.0 | 202 | 0.19 | 0.04 | 1.52 | 18.6 | 2.18 |
| 15 | tetra3-tuned | 74.0 | 74.0 | 385 | 0.26 | 0.06 | 1.16 | 19.6 | 1.14 |
| 15 | StarNet | 79.0 | 79.0 | 235 | 0.14 | 0.23 | 0.57 | 0.7 | 6.35 |
| 15 | tetra3-tuned-warp | 84.0 | 84.0 | 235 | 0.34 | 0.12 | 1.28 | 20.7 | 1.03 |
| 15 | StarNet-warp | 98.0 | 98.0 | 136 | 0.37 | 0.56 | 0.84 | 0.5 | 5.09 |
| 20 | tetra3 | 7.0 | 7.0 | 302 | 0.12 | 0.03 | 1.71 | 18.5 | 2.32 |
| 20 | tetra3-tuned | 39.0 | 39.0 | 280 | 0.16 | 0.03 | 1.23 | 19.5 | 1.77 |
| 20 | StarNet | 68.0 | 68.0 | 214 | 0.14 | 0.21 | 0.70 | 0.8 | 6.11 |
| 20 | tetra3-tuned-warp | 51.0 | 51.0 | 302 | 0.23 | 0.08 | 1.41 | 20.8 | 1.65 |
| 20 | StarNet-warp | 97.0 | 97.0 | 164 | 0.30 | 0.46 | 1.17 | 0.7 | 5.65 |

### With glare

| rate deg/s | method | correct % | any solve % | att err (arcsec) | recall | precision | centroid err px | junk/frame | s/frame |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | tetra3 | 100.0 | 100.0 | 67 | 0.96 | 0.42 | 0.17 | 11.8 | 0.47 |
| 0 | tetra3-tuned | 98.0 | 98.0 | 67 | 0.98 | 0.41 | 0.17 | 12.8 | 0.58 |
| 0 | StarNet | 16.0 | 16.0 | 258 | 0.07 | 0.11 | 0.19 | 0.6 | 6.90 |
| 0 | tetra3-tuned-warp | 100.0 | 100.0 | 67 | 0.99 | 0.64 | 0.17 | 19.0 | 0.75 |
| 0 | StarNet-warp | 99.0 | 99.0 | 78 | 0.40 | 0.59 | 0.19 | 0.2 | 5.38 |
| 2 | tetra3 | 100.0 | 100.0 | 68 | 0.96 | 0.42 | 0.24 | 12.0 | 0.49 |
| 2 | tetra3-tuned | 99.0 | 99.0 | 67 | 0.98 | 0.41 | 0.23 | 13.0 | 0.56 |
| 2 | StarNet | 16.0 | 16.0 | 80 | 0.07 | 0.11 | 0.23 | 0.6 | 6.70 |
| 2 | tetra3-tuned-warp | 100.0 | 100.0 | 66 | 0.99 | 0.59 | 0.20 | 19.2 | 0.74 |
| 2 | StarNet-warp | 100.0 | 100.0 | 74 | 0.41 | 0.61 | 0.24 | 0.1 | 5.37 |
| 5 | tetra3 | 96.0 | 96.0 | 101 | 0.67 | 0.27 | 0.71 | 12.6 | 0.57 |
| 5 | tetra3-tuned | 98.0 | 98.0 | 95 | 0.72 | 0.27 | 0.65 | 13.6 | 0.64 |
| 5 | StarNet | 8.0 | 8.0 | 138 | 0.06 | 0.09 | 0.31 | 0.4 | 6.94 |
| 5 | tetra3-tuned-warp | 100.0 | 100.0 | 80 | 0.89 | 0.39 | 0.48 | 19.5 | 0.71 |
| 5 | StarNet-warp | 100.0 | 100.0 | 98 | 0.40 | 0.58 | 0.37 | 0.1 | 5.47 |
| 10 | tetra3 | 36.0 | 36.0 | 143 | 0.26 | 0.11 | 1.10 | 13.2 | 1.73 |
| 10 | tetra3-tuned | 88.0 | 88.0 | 173 | 0.33 | 0.14 | 0.74 | 14.2 | 0.87 |
| 10 | StarNet | 18.0 | 18.0 | 165 | 0.07 | 0.11 | 0.37 | 0.3 | 6.90 |
| 10 | tetra3-tuned-warp | 98.0 | 98.0 | 185 | 0.56 | 0.21 | 1.00 | 19.9 | 0.87 |
| 10 | StarNet-warp | 99.0 | 99.0 | 139 | 0.35 | 0.54 | 0.53 | 0.1 | 5.28 |
| 15 | tetra3 | 9.0 | 9.0 | 230 | 0.09 | 0.04 | 1.51 | 13.1 | 2.27 |
| 15 | tetra3-tuned | 45.0 | 45.0 | 393 | 0.13 | 0.06 | 0.91 | 14.1 | 1.72 |
| 15 | StarNet | 8.0 | 8.0 | 323 | 0.06 | 0.09 | 0.51 | 0.6 | 7.16 |
| 15 | tetra3-tuned-warp | 72.0 | 73.0 | 290 | 0.30 | 0.10 | 1.32 | 20.0 | 1.31 |
| 15 | StarNet-warp | 100.0 | 100.0 | 150 | 0.34 | 0.50 | 0.82 | 0.3 | 5.87 |
| 20 | tetra3 | 13.0 | 13.0 | 219 | 0.09 | 0.05 | 1.45 | 13.0 | 2.18 |
| 20 | tetra3-tuned | 31.0 | 31.0 | 272 | 0.12 | 0.06 | 0.94 | 14.0 | 1.94 |
| 20 | StarNet | 8.0 | 8.0 | 209 | 0.06 | 0.10 | 0.55 | 0.7 | 7.31 |
| 20 | tetra3-tuned-warp | 61.0 | 61.0 | 302 | 0.25 | 0.09 | 1.36 | 19.8 | 1.52 |
| 20 | StarNet-warp | 96.0 | 96.0 | 179 | 0.29 | 0.44 | 0.96 | 0.3 | 5.42 |
