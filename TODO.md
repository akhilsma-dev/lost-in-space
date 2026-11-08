# Lost in Space — TODO

Status as of 2026-11-07. Rule: work on a branch, a human reviews it, then it goes to main.

## Planning & data
- [x] Plan (`plan.md`) and feasibility review (`feasibility-review.md`)
- [x] Star catalogue, constellation lines, star names (`data/`)
- [x] tetra3 database built (`data/tetra3_wide.npz`, 30–70° FOV, mag ≤ 5.5)
- [x] Wiring guide + diagram (`docs/WIRING.md`)
- [ ] Download DE421 ephemeris (`de421.bsp`) before venue Wi-Fi dies
- [ ] Confirm hackathon rules on AI-assisted coding / disclosure
- [ ] Confirm judging time + Devpost deadline

## Sky simulator (`sim/`)
- [x] Full-screen sky at hidden attitude, mag ≤ 5.5, magnitude-scaled blobs
- [x] Tumble + motion blur, glare, flare (random reset), pause
- [x] Calibration checkerboard mode
- [x] Truth on UDP 5005, commands on UDP 5006
- [x] Verified: tetra3 solves 15/15 static frames (~70″)
- [ ] Planets / Sun / Moon from DE421

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
- [ ] Flash and test the firmware on the real board (OLED, buzzer, servos)
- [ ] Build: dark box, console panel, servo mount + spacecraft model, webcam stand

## Dashboard (`dashboard/`)
- [x] Browser dashboard: camera view + overlays, truth vs estimate, error, lock state, history
- [x] Mock mode + "no signal" handling, screenshots
- [x] Slot for solve-rate comparison
- [ ] Load the real StarNet benchmark into the solve-rate panel
- [ ] Test on the demo laptop with the real camera feed

## Stretch
- [ ] Position from 3+ planets with error ellipsoid
- [ ] Earth mode: accelerometer + time → lat/long
- [ ] Stellarium as an independent sky source

## Demo & pitch
- [ ] Full end-to-end dry run: sim → camera → solve → dashboard + console
- [ ] Record backup video
- [ ] Slides, Devpost write-up, demo script rehearsal (`plan.md` §8)
