# Out-of-distribution test: star fields rendered by Stellarium

**Question.** StarNet was trained only on frames from our own renderer (`sim/sky.py` via `ml/patches.py`),
and `ml/out/benchmark.md` (100 % correct up to 20 deg/s) tests it on that same renderer. Did it learn
*stars*, or our renderer's quirks? Here every frame is drawn by independent code: Stellarium 26.3
(real catalogues, its own star sizes, colours and halos, Milky Way, nebula textures, planets).

**Answer (honest version).** Partly renderer quirks. On 100 Stellarium frames StarNet gets a correct
attitude on **38 %** of frames, while the one-line-tuned classical baseline (tetra3, blob-area cap
lifted) gets **100 %**. Default tetra3 gets **4 %**. StarNet never returns a *wrong* attitude (all its
solves are correct), but it calls almost every bright star a **planet**: in our renderer the "planet"
is the one big bright blob, and Stellarium draws all mag < 4 stars as big bright blobs. Counting
StarNet's planet detections as stars (a diagnostic, not a fix we are claiming) lifts it to **88 %**,
which says the localisation mostly transfers and the class head is what overfit.

## Results (100 frames, uniform random pointings, static camera)

| method | correct % | any solve % | median att err (arcsec) | centroids/frame (median) | cat recall (mag<=5.5) | top-20 hit | s/frame* |
|---|---:|---:|---:|---:|---:|---:|---:|
| tetra3 (defaults) | 4 | 4 | 5 | 1119 | 0.65 | 0.98 | 4.91 |
| tetra3-tuned (`max_area=5000`) | **100** | 100 | 18 | 1151 | 0.98 | 0.97 | 2.04 |
| StarNet (ours) | 38 | 38 | 65 | 60 | 0.58 | 0.94 | 14.85 |
| *StarNet star+planet (diagnostic)* | *88* | *88* | *46* | *76* | *0.72* | *0.93* | *15.50* |

* correct = attitude within 1 deg of truth (same as the synthetic benchmark). No method produced a wrong
  solve on any frame, so correct % = any-solve %.
* cat recall = fraction of in-frame catalogue stars (tetra3 DB, BSC5 mag <= 5.5, projected with the true
  attitude) that have a centroid within 3 px. top-20 hit = fraction of the 20 brightest centroids
  (what tetra3 builds patterns from first) that sit on a catalogue star.
* \*s/frame is not comparable with `benchmark.md`: this run shared the machine with another session's
  benchmark (load average ~75 on 10 cores), and failed solves burn the full 2 s solver timeout. A first
  identical run under lighter load gave 5 / 100 / 41 % (tetra3 / tuned / StarNet), 1.4 s/frame for tuned and
  10.7 s/frame for StarNet; the 3-point StarNet difference is borderline frames hitting the 2 s timeout.

### Why StarNet fails: what it calls each catalogue star

All 100 frames, nearest StarNet detection (any class) within 3 px of each in-frame catalogue star:

| catalogue mag | n | star | planet | hot_pixel | missed |
|---|---:|---:|---:|---:|---:|
| < 1 | 51 | 0.00 | 0.14 | 0.00 | 0.86 |
| 1 - 2 | 142 | 0.00 | 0.55 | 0.00 | 0.45 |
| 2 - 3 | 450 | 0.06 | 0.79 | 0.00 | 0.15 |
| 3 - 4 | 1161 | 0.06 | 0.88 | 0.01 | 0.06 |
| 4 - 4.5 | 1363 | 0.79 | 0.13 | 0.03 | 0.04 |
| 4.5 - 5 | 2614 | 0.84 | 0.01 | 0.06 | 0.09 |
| 5 - 5.5 | 4280 | 0.38 | 0.01 | 0.32 | 0.30 |

* Every star brighter than mag ~4 is labelled planet or missed (the very brightest, with the biggest
  halos, are mostly missed). So StarNet hands tetra3 only mag 4-5.5 stars; patterns built from those
  sometimes still work (sparse fields) and fail in dense fields: StarNet's correct rate falls from 68 % in
  the sparsest quarter of frames (<= 74 catalogue stars) to 8 % in the densest quarter (> 120, Milky Way).
* Faint (mag 5-5.5) stars, which Stellarium draws as sharp 1-2 px points, are often called **hot pixels**.
* StarNet flags **glare in 100 %** of frames; there is no glare in these images (likely the Milky Way /
  diffuse background).
* Its centroids are also less accurate here: median attitude error 65" vs 18" for tetra3-tuned on the
  same frames (on our renderer they were equal, ~69").

### Example frames (`ml/out/stellarium_examples/`)

Each figure shows the four front-ends on one frame: green circles = catalogue stars at the true attitude,
x = the 20 brightest centroids passed to tetra3, dots = the rest.

* `f000_starnet-fails.png`: dense field near the Milky Way; StarNet's 20 picks are all faint stars,
  the bright ones are skipped (labelled planet); tetra3-tuned and the star+planet diagnostic both solve.
* `f002_starnet-ok_tetra3-default-fails.png`: sparser field, StarNet solves; default tetra3 fails.
* `f022_all-ok.png`: Venus near the centre and the Moon's glow upper right (Stellarium also brightens
  the background when a bright body is in view); all methods solve.
* `f006_body-in-view.png`: Mars listed near the frame, StarNet solves, default tetra3 does not.

## Comparison with the in-distribution benchmark (`ml/out/benchmark.md`)

| static camera, no glare | our renderer (benchmark.md, rate 0) | Stellarium (this test) |
|---|---:|---:|
| tetra3 | 99.5-100 % | 4 % |
| tetra3-tuned | 100 % | 100 % |
| StarNet | 100 % | 38 % (88 % if planet = star) |

* The synthetic benchmark overstates StarNet. Its 100 % does not transfer to a different renderer even
  for a static, noise-free, glare-free sky; the "planet" and "hot pixel" classes are keyed to how *our*
  renderer draws those objects (one big bright blob; single sharp pixels), and real-catalogue bright stars
  look like that in Stellarium.
* Default tetra3 also collapses (4 %), for a mechanical reason: Stellarium's bright stars have halos
  larger than tetra3's 100 px blob-area cap, so the brightest stars are thrown away (it solves more often
  when a bright planet is in view, because Stellarium's auto-exposure then shrinks the halos: 24 % on the
  17 frames with a body nearby). This is not a Stellarium brightness problem (all stars are clearly
  detectable, ~1100 blobs per frame), so we kept Stellarium's default star scaling (step 5 not needed).
* tetra3-tuned is the robust baseline: 100 % on both renderers when static. StarNet's case in
  `benchmark.md` rests on motion blur (10-20 deg/s), which this static test does not exercise. Stellarium
  cannot render motion blur, so the tumble claim is still only tested in-distribution.
* What it suggests for StarNet (not done here): train with a mix of star renderings (halo profiles,
  saturation, colour) and real-catalogue brightness distributions, or merge star/planet at inference and
  let tetra3 reject non-stars; and add Stellarium (or real sky) frames to the validation set.

## Setup

* **Stellarium 26.3**, installed with `brew install --cask stellarium` (to `/Applications/Stellarium.app`).
* Driven by its **scripting engine** (`--startup-script`), not the Remote Control HTTP plugin: the agent
  sandbox does not allow opening a local HTTP server. `sim/stellarium_capture.py` writes the `.ssc`
  script, launches Stellarium with a private `--user-dir` holding `sim/stellarium/config.ini` (the user's
  own Stellarium settings are untouched), Stellarium screenshots every pointing and quits (~4 min).
* **Camera / view settings** (`sim/stellarium/config.ini` + script):
  * projection `ProjectionPerspective` (gnomonic, as tetra3 assumes);
  * screenshots 1920x1080 via `screenshot_custom_size` (independent of the window size);
  * horizontal FOV 50 deg. Stellarium's FOV setting is the **vertical** FOV, so it is set to
    2 atan(tan 25 deg x 1080/1920) = 29.395 deg;
  * equatorial mount: image-up = celestial north, east left = tetra3 roll 0. Odd frames are rotated 180 deg
    afterwards (exact pixel permutation, no resampling) so half the set is roll 180;
  * date/time fixed at JD 2451545.0 (2000-01-01 12:00), time rate 0, aberration off (of-date ~= J2000);
    observer Greenwich (irrelevant without ground/atmosphere except planet parallax);
  * no landscape, fog or atmosphere; no labels (stars, planets, nebulae, landscape), no constellation
    lines/art/boundaries, no grids or lines, no cardinal points, no planet markers/hints, GUI hidden;
  * kept for realism: Milky Way (intensity 1, default), nebula textures, Sun/Moon/planets;
  * stars: Stellarium defaults (relative and absolute scale 1.0), twinkle off, Bortle 1, luminance
    adaptation on (default). **No brightness tuning was applied.**
* **Pointings**: 100, uniform on the sphere (seed 2026). Truth in `data/stellarium/manifest.csv`
  (file, RA, Dec, roll, h/v FOV, size, bodies within the frame's half-diagonal, so a listed body may sit
  just outside the frame corner). Body positions at the fixed epoch: `data/stellarium/bodies.csv`
  (17 frames list a body; the Sun is near 2 of them). Images (147 MB) are in `data/stellarium/images/`
  (gitignored; rerun the capture script to regenerate them; it is deterministic).
* **Convention check** before the main run: tetra3 (tuned centroids, free FOV 30-70 deg) on test frames
  returned RA/Dec within 0.03 deg, roll 0.00 (or 180.00 for rotated frames) and FOV 50.01 deg, including
  a frame at Dec +89.3. Catalogue stars projected with the recorded attitude land a median 0.05 px from
  detected centroids. So pointing, roll and FOV conventions match exactly.
* **Evaluation** (`ml/stellarium_eval.py`): same three methods and solver settings as `ml/benchmark.py`
  (tetra3 database `data/tetra3_wide.npz`, fov_estimate 50 +/- 10 deg, 2 s timeout, StarNet weights
  `ml/weights/starnet.pt` on CPU, one thread per worker). Frames are converted to 8-bit gray (PIL "L") once.
  Per-frame results: `ml/out/stellarium_frames.csv`; raw tables: `ml/out/stellarium_eval_results.md`.

Reproduce:

    .venv/bin/python -m sim.stellarium_capture        # needs Stellarium in /Applications, ~4 min
    .venv/bin/python -m ml.stellarium_eval            # ~4-10 min depending on machine load
