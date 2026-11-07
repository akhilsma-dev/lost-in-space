# Feasibility review: "Lost in Space" star tracker (UB Hacking 2026, Radial Ventures track)

Reviewed 2026-11-07. Items marked "unverified" were not confirmed against a source.

## 1. Verdict: GO WITH CHANGES

The core demo (webcam films a simulated sky, software identifies stars and recovers attitude, servos point a model) is feasible in 24 h. Use ESA's open-source `tetra3`/`cedar-solve` as the star-ID and attitude engine and do not rebuild it. Because tetra3 is geometric hashing, not machine learning, the "meaningful AI" for Radial must come from a learned perception layer that tetra3 depends on: a small CNN that finds and cleans star centroids under motion blur and glare, and classifies leftover objects (planet / Moon / glare / hot pixel / unknown). Prove it is essential by showing tetra3's solve rate with and without it. Treat planet triangulation as a stretch with an honest error bar (tens of thousands of km).

## 2. Dataset & library table

| Name | Link | License | Size | Python loader | Gotchas | Status |
|---|---|---|---|---|---|---|
| Hipparcos main catalog (hip_main.dat) | https://rhodesmill.org/skyfield/stars.html | Public data (CDS/ESA, free use, cite) | 118,218 rows; ~13 MB gz | `from skyfield.data import hipparcos; df = hipparcos.load_dataframe(load.open(hipparcos.URL))`, then `df[df.magnitude <= 5]` (~1,600 rows) | Default URL has 404'd before (skyfield issue 454); mirror on VizieR I/239. Epoch J1991.25, irrelevant for demo. Some rows have NaN positions, drop them. | OK |
| Yale Bright Star Catalog BSC5 (V/50) | https://vizier.u-strasbg.fr/viz-bin/ftp-index?V/50 | Public data, no stated restrictions (unverified) | 9,110 objects, ~1.5 MB | pandas fixed-width read, or let tetra3 `generate_database(star_catalog='bsc5')` fetch it | Column format is fixed-width; 14 non-stars included. Fine backup if Hipparcos download fails. | OK |
| Tycho-2 | VizieR I/259 | Public data | 2.5M stars, ~500 MB | tetra3 `'tyc_main'` | Overkill for mag 5, slow download. Skip. | Skip |
| Gaia DR3 | astroquery.gaia | Public | huge | astroquery | Gaia is incomplete for the brightest stars (G < ~3) because of saturation. Wrong tool for a mag-5 tracker. | No |
| Constellation lines: d3-celestial `constellations.lines.json` | https://github.com/ofrohn/d3-celestial | BSD-3-Clause (whole repo; data derived from IAU charts) | ~100 KB GeoJSON, RA/Dec line strings | `json.load` | Use this one. Stellarium's file is `constellationship.fab` (not .fff) and lists HIP pairs; stellarium-skycultures repo carries LICENSE-AGPL-3.0, per-culture license not clearly stated. | OK |
| JPL DE421 via Skyfield | https://rhodesmill.org/skyfield/planets.html | Public (JPL/NAIF) | de421.bsp 17 MB (1900-2050); de440s.bsp 32 MB | `load('de421.bsp')` once; cached on disk; fully offline afterwards | Download before the venue Wi-Fi dies. Sun, Moon, Mercury..Neptune barycenters included; for Mars/Jupiter use the barycenter, fine at this accuracy. | OK |
| tetra3 (ESA) / cedar-solve (fork) | https://github.com/esa/tetra3 , https://github.com/oakamil/cedar-solve , https://tetra3.readthedocs.io | Apache-2.0 | pip install; database generation for mag 7 / 60 deg takes minutes | `t3 = tetra3.Tetra3(); t3.generate_database(max_fov=70, star_max_magnitude=6, star_catalog='hip_main')`; `t3.solve_from_image(img)` returns RA, Dec, Roll, FOV, RMSE, Matches, Prob | It is the whole classical pipeline including centroiding (`get_centroids_from_image`). Master branch changed in 2023; cedar-solve is the maintained one. Use it as your baseline and ground-truth checker, not as the thing you claim to have built. | OK |
| astrometry.net | https://astrometrynet.readthedocs.io | GPL/BSD mix | index files 100s of MB, C build | `pip install astrometry` wrapper exists | Wide-field indexes exist (Tycho-2 based) but setup cost is high for 24 h. Skip. | Skip |
| OpenStarTracker | openstartracker.org (I believe UB Nanosatellite Lab; unverified) | unverified | C++/Python | - | Nice UB tie-in for the pitch; not worth integrating. | Skip |
| Learned star ID (reference only, now optional) | Rijlaarsdam et al., Sensors 2020, 20(13):3684, https://www.mdpi.com/1424-8220/20/13/3684 ; Wang et al. 1D-CNN Sensors 2021 https://mdpi-res.com/d_attachment/sensors/sensors-21-07686/article_deploy/sensors-21-07686.pdf | papers (CC-BY) | - | you implement | Rijlaarsdam's features are histograms of binned neighbor distances, rotation-invariant by construction, fed to a small fully connected net; trained on catalog-generated synthetic scenes. This is the design to copy. A PointNet on raw coordinates must learn invariance from augmentation and will train slower. | OK |
| Rendering | numpy + OpenCV (`cv2.GaussianBlur`, `cv2.filter2D`), display with `cv2.namedWindow` fullscreen or pygame | BSD/Apache | - | - | Flux = 10^(-0.4*(m - m_ref)); stamp a Gaussian PSF at sub-pixel positions; add Poisson + Gaussian read noise; motion blur = average N sub-frames along the slew or a line kernel. | OK |
| Grove Beginner Kit (Seeeduino Lotus, ATmega328P, SSD1315 OLED, button, pot, buzzer, LED, LIS3DHTR accel, DHT11, BMP280, light, sound) | https://wiki.seeedstudio.com/Grove-OLED-Display-0.96-SSD1315/ | Arduino libs LGPL/BSD | - | `pyserial`: `serial.Serial(port, 115200, timeout=0.1)` | On ATmega328 Seeed says use U8g2 **software I2C** constructor (`U8G2_SSD1306_128X64_NONAME_F_SW_I2C`), Adafruit_SSD1306 misbehaves on SSD1315. Board uses CH340 USB-serial (driver usually built into macOS now). Opening the port resets the board: sleep 2 s. Servo lib kills PWM on pins 9/10; two servos can brown out USB 5V, add a cap or external 5V. Accelerometer on the kit covers the Earth-variant pitch. | OK |

## 3. Red flags, ranked

1. **With tetra3 as the solver, the AI must be load-bearing elsewhere.** tetra3 is not ML, so "we used tetra3" alone does not meet Radial's AI requirement. Mitigation: a learned perception front-end that tetra3 cannot work without in hard conditions. (a) A patch CNN that detects stars and outputs sub-pixel centroids from motion-blurred streaks and glare halos; (b) a classifier that labels each blob star / planet-Moon / glare / hot pixel / unknown, so only stars go to tetra3 and the rest become "unidentified objects". Measure it: tetra3 with its built-in `get_centroids_from_image` vs tetra3 fed your centroids, over 200 frames at increasing tumble rate and glare. A plot where the classical curve collapses and yours holds is the whole AI argument.
2. **Webcam-on-screen imaging.** Rolling shutter + backlight PWM gives banding; auto-exposure/auto-focus hunt on a black frame; MJPEG compression eats faint stars; moiré on single-pixel stars. Mitigation: screen brightness 100% (most LCDs stop PWM dimming at max), dim stars in software; lock exposure, gain, focus, white balance (OpenCV `CAP_PROP_AUTO_EXPOSURE`, `CAP_PROP_AUTOFOCUS`; on macOS OpenCV exposure control is flaky, so pick a camera whose vendor app can lock settings, or use a Linux laptop with `v4l2-ctl`); render stars as Gaussian blobs sigma 1.5-3 screen px, never 1 px; code brightness in blob size as well as intensity; use 1080p YUY2 at 15-30 fps if bandwidth allows; dark box with matte black interior.
3. **Geometry: the camera does not see a gnomonic projection.** Screen = gnomonic projection of the sky; camera views that plane from an off-axis point = a homography. Any homography of a plane image equals a change of camera intrinsics plus a rotation, so it is solvable, but your fingerprints will be scale- and shear-distorted across the frame. Mitigation (1 hour, do it first): show a checkerboard or dot grid on the screen, detect it with `cv2.findChessboardCorners`, compute `cv2.findHomography` camera->screen pixels, warp every frame (or just the centroids) back to screen coordinates. From then on treat screen pixels as an ideal pinhole with known focal length, and the rendered FOV is exactly known. This also deletes lens distortion if you do `cv2.calibrateCamera` with a few poses first.
4. **Simulator-to-simulator training.** Training the NN on your own renderer and testing on your own renderer is the circularity a judge will probe. The attitude is hidden, so it is not cheating, but it is not generalization either. Mitigation: evaluate on something you did not render: Stellarium screenshots on the tablet (free, GPL, different renderer, has a Remote Control plugin HTTP API, unverified details) and tetra3's bundled test images. Report the number.
5. **Planet triangulation error will be large.** Screen 2000 px over 60 deg = 108 arcsec/px. Centroids 0.2 px, attitude from 30 stars maybe 10-20 arcsec, planet direction error 30-60 arcsec = 2e-4 rad. At 1 AU baseline that is ~30,000 km position error; plus planets lie near the ecliptic so the out-of-plane component is poorly constrained. Mitigation: present it as an error ellipsoid ("we know where we are to within 0.02% of an AU"), use 3+ bodies including the Moon or Sun, and compute the ellipsoid from the Jacobian so the number is honest. Range from Earth/Moon apparent size works only when the disk is many pixels wide; keep it as a special-case demo.
6. **Serial + servo + display timing.** A blocking `readline` or a flooded serial buffer stalls the vision loop. Mitigation: pyserial in its own thread, newline protocol, send servo targets at 10 Hz max, slew-rate limit on the Arduino.
7. **Magnitude 5 vs stars per frame.** Whole sky 41,253 sq deg; ~1,600 stars mag<=5 gives ~0.04/sq deg, so a 60x45 deg frame holds ~100 catalog stars and ~30 brighter than mag 4. Plenty. The real limit is how many the camera recovers after an 8-bit screen and webcam noise. Expect 20-40 reliable detections. Build the tetra3 database with `star_max_magnitude=6` and render mag<=5.5.

## 4. Revised scope

Must-have (the demo works without anything else):
- Renderer from Hipparcos mag<=5.5 at a hidden random attitude, fullscreen on monitor/tablet, keyboard-driven tumble.
- Screen-camera homography calibration.
- tetra3/cedar-solve as the star-ID and attitude engine (its Prob and RMSE outputs are your confidence).
- Learned front-end: patch CNN for star detection + sub-pixel centroid under blur/glare, and blob classifier (star / planet-Moon / glare / hot pixel / unknown). Train on synthetic patches, fine-tune on a few hundred real camera patches auto-labeled by tetra3 solves on clean frames.
- Unknown-object list: blobs the classifier keeps but tetra3 does not match.
- Live overlay: identified stars with names, constellation lines, truth vs estimate error in degrees. Scoreboard: solve rate with classical centroids vs learned centroids as tumble and glare increase.
- Arduino: flare button (reset attitude), knob (tumble rate), OLED status, buzzer on lock.

Should-have:
- 2-axis servo pointer to estimated attitude.
- IR LED glare injected in the box; show the classifier rejecting it.
- Sun/Moon/planets rendered from DE421 and labeled as "unidentified -> planet" by the classifier.

Stretch:
- Position triangulation from 3+ bodies with error ellipsoid.
- Earth variant: kit accelerometer for local vertical + time -> lat/lon.
- Stellarium as an independent sky source.

## 5. Hour-by-hour (team: 1 coder + 2-3 hardware)

| Hour | Coder | Hardware team |
|---|---|---|
| 0-1 | pip install skyfield cedar-solve opencv-python torch; download hip_main, de421; `generate_database(max_fov=70, star_max_magnitude=6)` running in background | Collect kit, build dark box (cardboard + black cloth), mount webcam, mount screen |
| 1-3 | Renderer (gnomonic, Gaussian PSF, noise, tumble), fullscreen display | Arduino: button, pot, OLED (U8g2 SW I2C), buzzer; serial protocol `{"att":[...],"status":"LOCK"}` |
| 3-4 | Homography/lens calibration with on-screen grid; lock camera settings | Servo bracket, spacecraft model, power |
| 4-6 | OpenCV detection; tetra3 solve on warped frame; first end-to-end attitude on screen (MILESTONE: truth vs estimate, likely by hour 5 now) | Integrate serial with coder's test script |
| 6-9 | Synthetic patch generator (blur streaks, glare, hot pixels, planets), small CNN for centroid + class, train on CPU (expect <30 min) | Collect 300+ real camera patches (stars, glare, hot pixels) by placing IR LED, logging frames |
| 9-11 | Wire CNN centroids into tetra3, unknown-object list, with/without scoreboard | Servos driven by estimate; OLED shows RA/Dec/roll |
| 11-12 | Buffer / sleep | Sleep in shifts |
| 12-14 | Blob classifier on real patches; glare demo | Box polish, cable management |
| 14-16 | Planets/Moon from DE421 rendered and flagged | Record B-roll video now (fallback asset) |
| 16-18 | Stretch: triangulation + error ellipsoid, or Earth lat/lon | Rehearse pitch |
| 18-20 | Freeze features. Bug fixes only | Devpost text, diagrams |
| 20-22 | Full dress rehearsal x3 from cold start; pin all settings in a config file | Charge everything, spare USB cables |
| 22-24 | Buffer; practice the 3-minute pitch | - |

Cut order if behind: triangulation -> Earth variant -> servos -> planets -> real-patch classifier (keep synthetic NN). Minimum impressive demo: hidden attitude on screen, camera, stars named live, attitude error in degrees, press flare button, re-lock in under a second, with the tetra3 comparison panel.

## 6. Judge Q&A

1. **"tetra3 does the star ID. What did you build?"** We deliberately reused ESA's flight-heritage-style solver instead of reinventing it. What it cannot do is see through a tumbling, glare-filled camera. Our learned front-end turns blurred streaks and halos into clean centroids and throws out glare and planets. Here is tetra3's solve rate with its own centroids and with ours as tumble rate rises.
2. **"You trained and tested on your own simulator. Isn't that circular?"** The network never sees the true attitude; it only sees camera pixels. We also evaluated on Stellarium renders and tetra3's test images [give the numbers], and the classifier was fine-tuned on real webcam patches collected at this table.
3. **"Why a neural network instead of thresholding?"** A threshold-and-blob centroider is biased on motion-blurred streaks and cannot tell a glare ghost from a star. The CNN learns the streak shape and outputs where the star was at mid-exposure, plus a class label. The scoreboard shows the difference in solve rate, not just in a loss curve.
4. **"How accurate is your position fix?"** Attitude to ~0.01 deg; direction to each planet to ~1 arcminute; that gives a position ellipsoid of a few tens of thousands of km at 1 AU, which we display. Real deep-space nav does the same math with better optics; our error is dominated by the 108 arcsec screen pixels.
5. **"What is the physical-world connection? It is a screen."** Real optics, real sensor, real noise, real glare from a real IR LED, real servos pointing a real model, real console. The screen replaces the sky because the hackathon is indoors in November; the pipeline is identical with a lens on the roof, and the same code with the kit's accelerometer gives latitude and longitude on Earth without GPS.

## Sources
- tetra3 docs https://tetra3.readthedocs.io ; repo https://github.com/esa/tetra3 ; cedar-solve https://github.com/oakamil/cedar-solve
- Skyfield stars https://rhodesmill.org/skyfield/stars.html ; planets https://rhodesmill.org/skyfield/planets.html ; issue 454 https://github.com/skyfielders/python-skyfield/issues/454
- BSC5 at VizieR https://vizier.u-strasbg.fr/viz-bin/ftp-index?V/50
- d3-celestial https://github.com/ofrohn/d3-celestial ; Stellarium sky cultures https://github.com/Stellarium/stellarium-skycultures
- Rijlaarsdam et al. 2020 https://www.mdpi.com/1424-8220/20/13/3684 ; Wang et al. 2021 https://mdpi-res.com/d_attachment/sensors/sensors-21-07686/article_deploy/sensors-21-07686.pdf
- Grove OLED SSD1315 wiki https://wiki.seeedstudio.com/Grove-OLED-Display-0.96-SSD1315/ ; Arduino forum SSD1315 thread https://forum.arduino.cc/t/ssd1315/695280
- Star counts (mag 6.5 ~9,100 stars) https://skyandtelescope.org/astronomy-resources/astronomy-questions-answers/if-you-combine-the-magnitudes-of-all-visible-stars-how-bright-a-star-will-you-come-up-with/
- Radial Ventures track: I could not find a public UB Hacking 2026 track page; criteria taken from your brief.
