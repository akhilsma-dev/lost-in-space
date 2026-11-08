# Lost in Space — TODO

Status as of 2026-11-08. Rule: work on a branch, a human reviews it, then it goes to main.

## Judge QA fixes (2026-11-08, see `docs/qa/judge-review-2026-11-08.md`, scored 6.3/10)
- [x] **Knob dead in `--nav` mode:** scan pauses while spinning; WHERE AM I? button (virtual console) runs one sweep on demand; recovery back on in nav mode
- [ ] **Put the CNN on screen:** `--starnet` in the demo if it holds 2-5 Hz, classical/CNN toggle, real numbers in the SOLVE RATE panel (no "pending")
- [ ] Prepare the answer for StarNet's Stellarium result (38% vs tuned tetra3 100%)
- [ ] Voice / glare from perception: callouts from solver state, not sim truth; glare that visibly drops confidence
- [ ] Solver without truth: measured rate for detumble, calibrated FOV instead of the truth packet's
- [ ] Drop `--flare-moves` teleport: keep the position through a flare, show the re-fix agreeing
- [ ] Polish: POSITION title clipped at 1600x900, planet label overlap, console panel blank on load, caption for the ~6 s lock
- [ ] `fake_demo.sh` pkill hits other worktrees' demos: scope it to this checkout

## Planning & data
- [x] Plan (`plan.md`) and feasibility review (`feasibility-review.md`)
- [x] Star catalogue, constellation lines, star names (`data/`)
- [x] tetra3 database built (`data/tetra3_wide.npz`, 30–70° FOV, mag ≤ 5.5)
- [x] Wiring guide + diagram (`docs/WIRING.md`)
- [ ] Download DE421 ephemeris (`de421.bsp`) before venue Wi-Fi dies: `.venv/bin/python -c "from skyfield.api import Loader; Loader('data')('de421.bsp')"` (needed by `--planets` / `--nav`)
- [ ] Confirm hackathon rules on AI-assisted coding / disclosure
- [ ] Confirm judging time + Devpost deadline

## Sky simulator (`sim/`)
- [x] Full-screen sky at hidden attitude, mag ≤ 5.5, magnitude-scaled blobs
- [x] Tumble + motion blur, glare, flare (random reset), pause
- [x] Calibration checkerboard mode
- [x] Truth on UDP 5005, commands on UDP 5006
- [x] Verified: tetra3 solves 15/15 static frames (~70″)
- [x] Planets / Moon from DE421: `sim.live --planets` (hidden position; the Sun is not drawn)

## Camera + solve (`camera/`)
- [x] Capture with exposure/focus locking attempts
- [x] Checkerboard → homography calibration (`data/calib.json`)
- [x] Live tetra3 solve, LOCK/LOST + confidence, estimates on UDP 5008
- [x] `--sim` / `--sim-camera` modes (20/20 lock, ~4″ in sim)
- [x] `camera/box_test.sh` end-to-end real-camera test script
- [ ] **Go/no-go: real camera on real screen.** First iPhone run failed (checkerboard cropped, room-light reflections → 1,528 blobs, no solve). Re-run in the closed dark box with the phone further back.
- [ ] Lock the camera settings that work and pin them in a config file

## Learned front-end (`ml/`)
- [x] Synthetic patch generator (blur, glare, hot pixels, planet)
- [x] StarNet CNN trained (`ml/weights/starnet.pt`), `ml.detect.detect()` API
- [x] Benchmark: 100% solve at 0–20°/s vs tetra3 alone 11% / tuned 67% (`ml/out/benchmark.md`, `solve_rate.png`)
- [x] Wire `ml.detect` into `camera/solve_live.py`: `--detector starnet` (`demo/fake_demo.sh --starnet`); ~150 ms/frame on MPS
- [ ] Test StarNet on real webcam frames (answers the "trained on your own sim" question)
- [ ] Fine-tune on real camera patches if real-frame results are weak
- [ ] Unknown-object list (blobs StarNet keeps that tetra3 doesn't match)

## Console hardware (`firmware/`, `bridge/`)
- [x] Arduino firmware: button, knob, OLED, buzzer, LED, 2 servos (`firmware/console/console.ino`)
- [x] Serial protocol doc (`firmware/PROTOCOL.md`)
- [x] Serial bridge with `--fake` board mode; flare/knob → sim
- [x] Servos show "turn since lock" (Option B)
- [x] Connect solve → bridge: solve_live sends estimates to 5008 (dashboard) + 5007 (bridge); sim truth to 5005 (dashboard) + 5009 (solver)
- [x] Fake-mode demo, no camera / no board: `demo/fake_demo.sh` (solve_live `--follow-sim`)
- [x] Virtual console (board down): web page that plays the board, embedded in the dashboard (`bridge/webconsole.py`)
- [x] Voice callouts: dashboard speaks lock / flare / tumble events (`voice/`), ElevenLabs clips committed
- [ ] Flash and test the firmware on the real board (OLED, buzzer, servos)
- [ ] Build: dark box, console panel, servo mount + spacecraft model, webcam stand

## Dashboard (`dashboard/`)
- [x] Browser dashboard: camera view + overlays, truth vs estimate, error, lock state, history
- [x] Mock mode + "no signal" handling, screenshots
- [x] Slot for solve-rate comparison
- [ ] Load the real StarNet benchmark into the solve-rate panel
- [ ] Test on the demo laptop with the real camera feed

## Stretch
- [x] Position from 3+ planets with error ellipsoid: `demo/fake_demo.sh --nav` (live, dashboard POSITION panel); offline benchmark `camera/navigate.py --bench` (~10,000 km clean, ~23,000 km through the fake webcam)
- [ ] Earth mode: accelerometer + time → lat/long
- [ ] Stellarium as an independent sky source

## Star finder switch (branch claude/model-switch)
- [x] Live CLASSIC / TUNED / StarNet switch in the SOLVE RATE panel, LOCK HELD per model, benchmark rows (`fake_demo.sh --models`)
- [x] `fake_demo.sh --webcam`: the solver films the sim through the synthetic webcam
- [ ] **Coast through single-frame dropouts while LOCKed** (carry the attitude at the measured rate for up to ~0.5 s).
      Without it no model holds lock for long at 20 deg/s through the webcam (one bad frame = LOST), so the live
      StarNet vs tetra3 contrast is luck. With it: StarNet (97% frames) holds, tetra3 (51%) still drops.

## To add if time: getting from A to B
- [ ] **Point at a target (attitude A → B):** a console/dashboard target picker (a named star, Earth, a planet).
      The craft slews there with the sim's `slew` command and the star tracker confirms arrival (pointing error
      shrinking to arcseconds). This is what an operator really commands; it reuses the WHERE AM I? slews.
- [ ] **Travel (position A → B), time-lapse:** show an orbit transfer the way missions fly it: point the engine
      (needs attitude), burn (delta-v), coast on the new orbit, burn again (a Hohmann transfer, e.g. Earth → Mars),
      with WHERE AM I? fixes along the way to correct course. Needs a sped-up clock in sim.live and a trajectory
      on the POSITION map. Bigger job; judges can't try it at the table, so lower priority than pointing.

## Demo & pitch
- [ ] Full end-to-end dry run: sim → camera → solve → dashboard + console
- [ ] Record backup video
- [ ] Slides, Devpost write-up, demo script rehearsal (`plan.md` §8)
