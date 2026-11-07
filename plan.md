# Lost in Space — UB Hacking 2026 Plan

> *"We taught a camera to navigate by the stars — go ahead, try to get it lost."*

**Targets:** General Hack + Radial Ventures track (AI × physical world; simulation allowed)
**Constraint:** Zero soldering, zero 3D printing — Grove plug-ins, jumper wires, hot glue, cardboard.

---

## 1. Pitch

A spacecraft's computer crashes and it wakes up blind — no idea which way it's facing. **Lost in Space** is a working star tracker you can try to break. A simulated sky (real star catalog) plays on a screen inside a dark box; a real webcam watches it. Our AI sees **only pixels**: it detects stars, identifies constellations, and solves the spacecraft's orientation — then (stretch) its position in the solar system from planets. A servo-driven spacecraft model turns to show its answer.

Judges are mission control: **Solar Flare** (random reset), **Tumble** (spin faster), **Glare** (blind the camera). It recovers every time and shows estimate vs hidden truth.

**Earth relevance (Radial):** GPS jamming/spoofing hits ships, aircraft, drones daily. Vision-based celestial navigation = GPS-free fallback. Same pipeline + accelerometer + time → lat/long.

---

## 2. System Architecture

```
JUDGE ─► CONSOLE (Arduino Uno + Grove) ──USB serial──► LAPTOP
         🔴 Flare  🎛️ Tumble                           │
         🖥️ OLED  🔊 Buzzer  ⚙️ Servo(s) ◄── angles ───┤
                                                       │
         [1] Sky simulator (hidden TRUE attitude) ─────┤──► truth (dashboard only)
                  │ HDMI / screen                      │
                  ▼                                    │
         DARK BOX: tablet/monitor ◄── 📷 webcam        │
                  (💡 phone flashlight / Grove LED = glare)
                  │ pixels ONLY                        │
                  ▼                                    │
         [2] Star detection → [3] Star ID (AI) → [4] Attitude solve (RANSAC)
                  → [5] Unknown objects → [6] Position from planets (stretch)
                  → [7] Dashboard: truth vs estimate, error °, confidence, state
```

**Rule:** the tracker never sees the true attitude. Only the simulator and dashboard know it.

---

## 3. Hardware (zero-solder / zero-print)

| Part | Source | Notes |
|---|---|---|
| Arduino Uno + **Grove base shield** | Hardware table | Confirm shield is included |
| Grove button, rotary knob, OLED, buzzer, LED | Grove Beginner/Starter kit | Plug-in only |
| 1–2 SG90-type servos (+ horns/screws) | Hardware table | Rent early — go fast |
| Breadboard, jumper wires, capacitor | Hardware table / passives | Cap across 5V/GND to reduce brownout |
| Hot glue gun | Consumables | Pan-tilt = servo 2 glued on servo 1 horn |
| USB webcam, laptop, tablet/old monitor | Bring own | Webcam must allow manual exposure |
| Cardboard box, black paper, tape | Scavenge | Dark box + console panel + webcam stand |
| Spacecraft model | Cardboard/foil/toy | Glued to servo horn |

**Fallback:** if servos misbehave → 3D attitude view on dashboard only (webcam + console still physical).

---

## 4. Software Modules

| # | Module | Tech | Priority |
|---|---|---|---|
| 1 | **Catalog loader** — stars mag ≤ 5 (~1,600), constellation lines | `skyfield` / Hipparcos or BSC5 | Must |
| 2 | **Sky renderer** — attitude → image, PSF blobs, noise, blur, tumble, planets | numpy / OpenCV / pygame | Must |
| 3 | **Camera capture** — fixed low exposure, calibration | OpenCV | Must |
| 4 | **Star detection** — threshold → blobs → shape filter → sub-px centroids | OpenCV | Must |
| 5 | **Classical star ID** — triangle/pyramid matching (baseline) | numpy, KD-tree | Must |
| 6 | **Attitude solve** — RANSAC + SVD (Wahba), confidence, NO LOCK state | numpy | Must |
| 7 | **Serial bridge** — console events in, servo angles/status out | `pyserial` | Must |
| 8 | **Arduino firmware** — button, knob, OLED, buzzer, servos | Arduino, Servo, U8g2 | Must |
| 9 | **Dashboard** — camera feed + overlay, truth vs estimate, state | pygame or web (FastAPI + canvas) | Must |
| 10 | **Learned star ID** — PointNet-style neighbor fingerprint, trained on synthetic skies | PyTorch (CPU) | Should |
| 11 | **Robustness comparison** — classical vs learned under occlusion/glare/blur | — | Should |
| 12 | **Unknown-object flagging** — non-catalog points after lock | — | Should |
| 13 | **Position from planets** — planet ID + triangulation + range from apparent size | `skyfield` ephemeris, least squares | Stretch |
| 14 | **Earth mode** — accelerometer + time → lat/long | Grove accelerometer | Pitch only |

---

## 5. Team Roles

| Role | Owns |
|---|---|
| **Coder A (+ Claude)** | Modules 1–6, 10–13 (simulator, vision, AI, attitude) |
| **Coder B / Hardware** | Modules 7–8, dark box, console, servo model, camera mounting |
| **Design / Pitch** | Module 9 dashboard polish, slides, Devpost, demo script, Radial pitch |

---

## 6. Timeline (24h)

| Hours | Software | Hardware / Other |
|---|---|---|
| **0–2** | Catalog + renderer + detection; **test webcam-on-screen ASAP** | Rent parts first; build dark box |
| **2–6** | Classical star ID + attitude solve; end-to-end in pure sim, then via camera | Arduino: button/knob/OLED/buzzer working; serial protocol |
| **6–10** | Calibration, real-camera noise fixes, NO LOCK handling | Servo(s) + spacecraft model; console panel |
| **10–16** | Learned star ID training + classical vs learned comparison; unknown objects | Integrate console ↔ laptop; dashboard v1 |
| **16–20** | Planets + position (stretch); dashboard polish | Labels, cable management, rehearse |
| **20–24** | **Feature freeze.** Venue test, bug fixes, backup video | Slides, Devpost, pitch rehearsal, sleep in shifts |

**Go/no-go checkpoint (hour 2):** stars visible cleanly through webcam-on-screen? If not → pure-sim mode + console + servos.

---

## 7. Risks & Fallbacks

| Risk | Mitigation / Fallback |
|---|---|
| Webcam filming screen: flicker, moiré, exposure | Manual exposure/gain, larger PSF blobs, defocus slightly; fallback = pure sim feed |
| "Isn't star ID already solved (Tetra3)?" | Show classical baseline failing under occlusion/glare/blur while learned holds |
| Learned model not ready | Demo runs on classical; show learned on recorded hard cases |
| Servo brownout / jitter | Capacitor, separate USB 5V, or drop to 1 servo / dashboard 3D view |
| Planet positioning too slow to build | Cut — pitch it as roadmap |
| Venue lighting | Fully closed dark box |

---

## 8. Demo Script (~3 min)

1. **Hook (20s):** "Your spacecraft just rebooted. It has no idea which way it's facing."
2. **Lock (20s):** show tracker locking — constellation overlay, model snaps into place.
3. **Judge breaks it (60s):** hand them the console — Flare, Tumble, flashlight glare. It recovers.
4. **AI matters (30s):** toggle classical vs learned under heavy glare — classical fails, ours holds.
5. **Where am I (30s, stretch):** planets identified → position pin on solar-system map.
6. **Earth pitch (20s):** GPS jamming → same pipeline navigates ships/drones without GPS.

---

## 9. Judge Q&A Prep

- **Doesn't the simulator know the answer?** Yes — the tracker doesn't. It sees only real camera pixels, exactly how flight software is tested (hardware-in-the-loop).
- **Why AI vs classical?** Robustness: missing/fake stars, glare, blur. We show the comparison live.
- **Stars give position?** No — stars give attitude. Position comes from nearby bodies (planets/Moon) against the star background.
- **Real-world use?** GPS-denied navigation; attitude for small satellites; anomaly detection (unknown objects).

---

## 10. Open Items

- [ ] Run dataset/feasibility review prompt in separate chat (catalogs, ephemeris, Tetra3, learned star-ID literature)
- [ ] Confirm hackathon rules on AI-assisted coding / disclosure
- [ ] Confirm judging time + Devpost deadline
- [ ] Rent: Uno, Grove kit (with shield), 2 servos, breadboard, jumpers
- [ ] Bring: USB webcam (manual exposure), tablet/monitor, cardboard box

## Backup Shortlist

2. **Hull Breach** — tap localization from sound (most feasible)
3. **Invisible Crew** — Wi-Fi RSSI activity/fall sensing (needs RSSI test / ESP32)
4. **First Contact** — alien tone language decoder
