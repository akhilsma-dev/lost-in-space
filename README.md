# Lost in Space

A star tracker you can try to break. A tumbling, sensor-blinded spacecraft works out which way it is
pointing, and where it is in the solar system, from camera pixels alone. A small neural network
(**StarNet**) finds stars inside motion-blur streaks and glare, and a classic solver (**tetra3**) matches
them against the catalog.

![dashboard locked on Orion](dashboard/screenshots/7_live_udp_mock.png)

## The problem

When a spacecraft's computer resets, it no longer knows its orientation. Real satellites recover by
photographing the stars and matching the pattern to a catalog: the *lost-in-space problem*. But
spacecraft usually get lost because something went wrong, and then they are often tumbling. Every star
smears into a streak, and standard star-finding software expects neat dots.

Filmed through a simulated webcam at 20°/s tumble, the best tetra3 setup solves **51%** of frames. With
StarNet in front of it, the same solver solves **97%**.

## How it works

```
sky simulator ──► screen ──► camera ──► StarNet / blob finder ──► tetra3 ──► attitude ──► position ──► dashboard
   (sim/)                    (camera/)        (ml/)                                    (planets)     (dashboard/)
     ▲  hidden truth over UDP ──────────────────────────────────────────────────────────────────────────┘
     └─ flare / tumble commands ◄── console (firmware/ + bridge/) and voice commands (voice/)
```

- **Sky simulator** (`sim/`) renders the real sky (stars to magnitude 5.5, planets from NASA JPL's DE421
  ephemeris) at a hidden attitude, with motion blur, glare, sensor noise and hot pixels. Only the
  simulator and the dashboard know the truth.
- **Camera side** (`camera/`) captures the screen with a webcam, maps it back to screen pixels through a
  calibration homography, and solves. A synthetic webcam (`camera/fakecam.py`) lets you test without
  hardware.
- **StarNet** (`ml/`) is a 470k-parameter, fully convolutional CenterNet-style network trained on
  freshly rendered, exactly labelled frames. It outputs star / planet / hot-pixel heatmaps with
  sub-pixel offsets, so junk never reaches the solver. The dashboard can switch live between classic
  tetra3, tuned tetra3 and StarNet.
- **Lost-in-space recovery:** the solver goes through DETECTING → MATCHING → VERIFYING (six agreeing
  solves, ~5 s) → LOCK. If it stays lost it detumbles (brakes the spin) and slews to a new patch of sky.
  Once locked it **coasts** through up to 1.5 s of bad frames, carrying the attitude forward at the
  measured spin rate.
- **Where am I?** After lock, the spacecraft sweeps 8 pointings along the ecliptic, spots planets
  against the star background and solves its **position in the solar system** by least squares
  against DE421, with an error ellipsoid and the radio light-time back to Earth.
- **Console** (`firmware/`, `bridge/`): an Arduino Uno with Grove parts (button, knob, OLED, buzzer, LED,
  two servos). Hit **Solar Flare** to blind the sensor and reset the attitude; turn the knob for up to
  20°/s tumble. A virtual console web page stands in when no board is connected.
- **Voice** (`voice/`): mission control speaks lock, flare and recovery callouts (ElevenLabs). You can
  also command the probe by voice or text: "solar flare", "faster", "where am I", "use StarNet".
- **Dashboard** (`dashboard/`): a stdlib Python server pushing Server-Sent Events to a full-screen page.
  It shows the solved camera frame with identified stars, planets and constellation lines, the hidden
  truth, pointing error in arcseconds, lock state and history, a spacecraft inset and the position map.

## Quick start

Requires Python 3 and macOS or Linux.

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install numpy scipy opencv-python pillow pyserial cedar-solve skyfield
```

```bash
.venv/bin/pip install torch matplotlib pandas
```

The second install is only needed for StarNet and the benchmarks. For position from planets, fetch the
ephemeris once (~17 MB, git-ignored):

```bash
.venv/bin/python -c "from skyfield.api import Loader; Loader('data')('de421.bsp')"
```

Run the whole demo with no camera and no board:

```bash
demo/fake_demo.sh --models --nav --open
```

This starts the simulator, the solver, the dashboard on http://localhost:8080 and the virtual console on
http://localhost:8081. Press **Space** or click **FLARE** to blind the tracker and watch it re-lock;
drag the knob to add tumble; click **WHERE AM I?** to find the position. Useful flags:

| flag | effect |
|---|---|
| `--models` | load all star finders; switch CLASSIC / TUNED / StarNet live on the dashboard |
| `--starnet` | start with StarNet as the star finder |
| `--webcam` | solver sees the sim through the synthetic webcam (also a live CLEAN / WEBCAM switch) |
| `--nav` | planets on screen and position from planets |
| `--board` | use the real Arduino console over USB |
| `--full` | simulator full screen instead of a window |
| `--no-voice-cmd` | don't start the voice command server |
| `--no-web` | keyboard only (`f` = flare, `0`–`1023` = knob, `q` = quit) |

Typed commands work out of the box from the dashboard's **COMMAND** button. Spoken commands need an
ElevenLabs key and two extra packages:

```bash
cp .env.example .env
```

```bash
.venv/bin/pip install elevenlabs sounddevice
```

Then put your key in `.env` (git-ignored); `fake_demo.sh` loads it.

Dashboard only, with synthetic feeds:

```bash
.venv/bin/python -m dashboard --mock --open
```

## Results

All results below are through the synthetic webcam (`camera/fakecam.py`: keystone, defocus, LCD black
glow, vignetting, sensor noise, JPEG), which StarNet never saw in training.

### Solve rate

Correct = attitude within 1° of truth, 100 frames per cell. The camera image is warped back to screen
pixels before detection. Full table in [ml/out/benchmark_simcam.md](ml/out/benchmark_simcam.md).

| tumble (°/s) | 0 | 2 | 5 | 10 | 15 | 20 |
|---|---:|---:|---:|---:|---:|---:|
| tetra3, tuned, no glare | 100 | 100 | 100 | 100 | 84 | 51 |
| **StarNet → tetra3, no glare** | **100** | **100** | **100** | **100** | **98** | **97** |
| tetra3, tuned, glare | 100 | 100 | 100 | 98 | 72 | 61 |
| **StarNet → tetra3, glare** | **99** | **100** | **100** | **99** | **100** | **96** |

- Up to 10°/s both methods are at 98–100%. StarNet's lead shows during fast tumble: +14 to +46 points
  at 15–20°/s.
- Warping the image before detection matters: running StarNet on raw camera pixels and warping only
  the centroids drops it to 8–18% with glare.

### Holding lock

Seconds lock held in a 30 s trial after locking at rest (2 trials each). Details in
[camera/README.md](camera/README.md).

| | 10°/s | 15°/s | 20°/s |
|---|---|---|---|
| tuned tetra3, no coasting | 8, 30 | 6, 1 | 0.6, 0.9 |
| StarNet, no coasting | 15, 30 | 15, 30 | 18, 26 |
| tuned tetra3, coast 1.5 s | 30, 30 | 30, 30 | 30, 30 |
| **StarNet, coast 1.5 s** | **30, 30** | **30, 30** | **30, 30** |

Coasting is what holds lock at speed; without it StarNet holds clearly longer than tuned tetra3. After a
flare the tracker goes LOST within 0.7 s and re-locks in about 6 s.

### Position from planets

24 hidden positions 0.7–2 AU from the Sun. All 24 fixed; median error **~23,000 km** from planets
alone, **~15,000 km** with a Sun sensor. Details in
[camera/out/nav_bench_simcam.md](camera/out/nav_bench_simcam.md).

## Repository layout

| folder | what |
|---|---|
| [`sim/`](sim/) | sky renderer (`sky.py`), planets (`planets.py`) and live simulator (`live.py`); truth out over UDP |
| [`camera/`](camera/README.md) | webcam capture, calibration, live solve, recovery stages, position from planets, synthetic webcam |
| [`ml/`](ml/README.md) | StarNet model, training, detection API, benchmarks |
| [`dashboard/`](dashboard/README.md) | mission-control web dashboard |
| [`bridge/`](bridge/README.md) | serial bridge to the console and the virtual console page |
| [`firmware/`](firmware/PROTOCOL.md) | Arduino console sketch and serial/UDP protocol |
| [`voice/`](voice/README.md) | voice callouts and voice commands (ElevenLabs) |
| [`docs/`](docs/WIRING.md) | console wiring |
| [`demo/`](demo/) | one-command demo and a standalone tetra3 example |
| [`data/`](data/) | star catalog, constellation lines, star names, tetra3 database |

Each folder's README covers its own commands and options.

## With real hardware

Show the simulator on a screen inside a dark box, point a webcam at it, then:

```bash
.venv/bin/python -m sim.live
```

```bash
.venv/bin/python -m camera.calibrate --index 0
```

```bash
.venv/bin/python -m camera.solve_live --index 0 --show
```

```bash
.venv/bin/python -m dashboard --open
```

Re-run `calibrate` whenever the camera or screen moves. On macOS, the terminal needs camera permission
(System Settings → Privacy & Security → Camera). See [camera/README.md](camera/README.md) and
[bridge/README.md](bridge/README.md) for the console.

## Credits

- [tetra3 / cedar-solve](https://github.com/smroid/cedar-solve) for star identification and attitude.
- [Skyfield](https://rhodesmill.org/skyfield/) and NASA JPL's DE421 ephemeris for planet positions.
- Star data and constellation lines from [d3-celestial](https://github.com/ofrohn/d3-celestial)
  (see `data/LICENSE.d3-celestial`) and the Yale Bright Star Catalog.
- Voice by ElevenLabs.
