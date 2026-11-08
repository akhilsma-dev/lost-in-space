# Judge-persona QA, 2026-11-08

Branch `test/acq-stages-planet-nav` (acquisition stages + position from planets). Scorecard page with
screenshots: https://claude.ai/artifact/Jm1j3S4wauxEFxrJ1xm9aW (private to the owner; share from its Share menu).

**How:** the full no-hardware chain (`sim.live --planets --flare-moves`, `solve_live --follow-sim --max-area 5000
--nav --nav-scan 3 --nav-reset-on-blind`, dashboard, virtual console), i.e. `demo/fake_demo.sh --nav`, run on
spare ports and driven by script like a judge: flare at rest, knob 10 then 20 deg/s, flare, glare on/off.
Then an independent reviewer playing an aerospace hackathon judge scored it from the screenshots, logs and code.
Its key claims were checked against the repo. **Not covered:** the dark box, the real webcam, the Arduino.

Evidence: [`judge-2026-11-08/`](judge-2026-11-08/) (screenshots, `drive.log` = actions with times,
`solve_timeline.txt` = solver stages and nav fixes).

## Verdict: 6.3 / 10

Competitive in **General**. Not yet in **Radial Ventures (AI x physical world)**: in the demo as run, the
"physical world" is a re-rendered frame and the CNN is switched off.

| Category | Score | Why |
|---|---:|---|
| Originality | 6 | Star ID is solved (tetra3). New: "try to break it" framing, visible staged acquisition, position from planets with an error ellipsoid |
| Technical depth | 8 | Homography calibration, synthetic webcam, trained CNN + out-of-distribution test, weighted least-squares position fix with covariance and NEES checks |
| Works when poked | 5 | Attitude solid; knob dead in `--nav` mode, glare has no effect, no real-camera run |
| Demo feel | 7 | Flare → VERIFYING → relock + voice is a good arc |
| UX polish | 7 | Dense, good-looking; clipping, label overlap, "pending" panel |
| Real-world relevance | 5 | ~10,000 km fix is deep-space, not ships/drones; Earth lat/long mode is pitch only |
| Completeness vs pitch | 5 | Camera go/no-go open, firmware not flashed, CNN off by default, no classical/CNN toggle |

## Measured in the walkthrough

- First LOCK 5.8 s after start; flare → LOCK 6.4 s (twice). No false locks; attitude error 1.2-58″.
- Nav fix within ~5-10 s of lock, error vs truth 2,800-13,500 km, 3-7 bodies.
- Estimate feed 2-5 Hz with nav on (5-7 Hz without); sim 12-16 fps.
- Earlier run without nav: flare at 20 deg/s → RATE HIGH → DETUMBLING → LOCK in 11.6 s.

## Findings, ranked by cost at the judging table

1. **Critical: tumble knob does nothing in `--nav` mode.** `--nav-scan` sends `point` every 3 s and
   `sim/live.py` `point()` sets `rate_dps = 0`. Console shows 20.0 deg/s, sky still (`07_…jpg`). Voice:
   "Tumble rate ten" then "Spacecraft stabilized" 2.4 s later untouched; 20 deg/s never announced. Also means
   the detumble recovery can't be shown in `--nav` mode.
   *Fix:* no scan pointings while the knob rate > 0 (or keep the rate through `point`), or start the scan from a button.
2. **Critical: the AI isn't on screen.** `fake_demo.sh` defaults to tuned tetra3; StarNet only with `--starnet`.
   SOLVE RATE panel shows "pending"; `dashboard/solve_rate.example.json` has placeholder numbers marked EXAMPLE.
   *Fix:* fill the panel from `ml/out/benchmark_simcam.md` (StarNet 97% vs tuned tetra3 51% at 20 deg/s,
   webcam + image warp) and demo with a live classical/CNN toggle.
3. **High: StarNet's out-of-distribution test undercuts the AI claim.** On Stellarium frames StarNet solves 38%
   vs tuned tetra3 100%, and flags glare in 100% of frames with no glare (`ml/out/stellarium_eval_results.md`).
   *Fix:* say it up front: wins under fast tumble on our renderer, doesn't generalise yet, here's the plan.
4. **High: physical pipeline unproven.** Real-camera go/no-go open (first run: 1,528 blobs, no solve);
   firmware never flashed. `--follow-sim` frames are noiseless re-renders, so arcsecond errors are best case.
   *Fix:* one lock through the box, recorded as backup video.
5. **Medium: glare is a no-op; callouts read truth.** Glare on: LOCK at 0.99 (`08_…jpg`). Glare / flare / tumble
   callouts come from sim truth flags in `dashboard/static/voice.js`, so "Solar flare!" plays ~1 s before the
   screen changes.
   *Fix:* callouts from solver state (blind frame, star count, measured rate); glare that visibly hurts.
6. **Medium: solver reads some truth.** FOV from the truth packet; detumble uses the truth rate.
   *Fix:* use the rate measured from successive solves (already computed for RATE HIGH) and a calibrated FOV.
7. **Medium: `--flare-moves` breaks physics.** Flare teleports the spacecraft 203 → 170 → 306 Mkm from Earth;
   scan pointings are teleports, not slews.
   *Fix:* keep the position through a flare and show the re-fix landing on the same answer.
8. **Low: polish.** POSITION title clipped at 1600x900 (`03_…jpg`; fine at 1920x1080); Mercury/Venus/Earth/Sun
   labels overlap; console panel blank on first load; "LIVE (30 s)" unexplained; the 6 s lock invites
   "is the delay fake?". *Fix:* layout + one caption: "6 consistent solves over 5 s before we trust it."

## Originality

Resembles tetra3 / cedar-solve, astrometry.net, OpenStarTracker (UB's own nanosat lab), commercial cubesat
trackers, monitor-based hardware-in-the-loop star-tracker benches, Deep Space 1 optical navigation.
Library: star ID + attitude = `tetra3.solve_from_centroids` (default centroider is tetra3's); ephemeris
Skyfield/DE421; voice = pre-generated clips. Theirs: sky renderer, calibration + synthetic webcam, StarNet and
its benchmark, acquisition state machine + recovery, the position-from-planets navigator, dashboard, protocol,
firmware, virtual console.

## Questions to prepare for

1. "Is the neural net running right now? What does it buy over tetra3 with one parameter changed?"
   Strong answer: CNN replaces the blob finder; 97% vs 51% at 20 deg/s; 38% on Stellarium; toggle it live.
2. "Show me a lock through the real webcam in the box. What error?" Strong: open the box, lock, quote a measured error.
3. "10,000 km error: how is that GPS-denied navigation for ships or drones?" Strong: planets = deep-space position
   (10,000 km at 1 AU is useful, ellipsoid honest); on Earth, star attitude + gravity + time = lat/long, with an
   error budget.

## Top 5 changes before judging (impact per hour)

1. Fix the knob in `--nav` mode (~30 min).
2. Put the CNN on screen: `--starnet` if it holds 2-5 Hz, classical/CNN toggle, real solve-rate numbers (~1 h).
3. One real-camera lock, recorded (2-4 h).
4. Glare and voice from perception, not truth (1-2 h).
5. Polish: POSITION clipping, planet labels, drop `--flare-moves`, console on load, lock-time caption (~1 h).

Also noted while testing: `demo/fake_demo.sh` kills every `python -u -m sim.live|camera.solve_live|dashboard|
bridge.serial_bridge` on the machine, including another worktree's demo. `data/de421.bsp` is gitignored and must
be present in each checkout.
