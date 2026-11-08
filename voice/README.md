# Voice callouts (mission control talks to us)

The dashboard speaks what the star tracker is doing, like a flight controller:
"Attitude lock acquired.", "Solar flare! Sensor blinded.", "Lock reacquired in two seconds.",
"Tumble rate fifteen degrees per second.", "Pointing error under ten arc seconds."
Each line also appears as a caption on the camera view (useful in a loud venue).

All audio is made **once** and committed (`dashboard/static/voice/`), so the demo needs no network.

## Make the clips

```bash
export ELEVENLABS_API_KEY=...          # your key; don't commit it
python -m voice.generate               # ElevenLabs, about 30 short clips
```

- `--voice <id>` (or `ELEVENLABS_VOICE_ID`) picks the voice; the default is the premade "Adam".
- `--placeholder` makes stand-in clips with macOS `say` (offline, no key); the voice chip's tooltip
  says "placeholder" while they're in use. The committed clips are ElevenLabs ("Adam").
- Unchanged clips are skipped; `--force` redoes them, `--only flare,lost` redoes just those.
- `--list` prints every callout. The text lives in `voice/lines.py`.
- New lines without a clip yet are skipped silently, so adding a line never breaks the demo; run
  `python -m voice.generate` once to make the missing clips (unchanged ones are skipped).
- `node voice/test_callouts.js` replays lock / flare scenarios through `voice.js` and checks what it says.

## When it speaks (`dashboard/static/voice.js`)

| Event | Callout |
|---|---|
| dashboard opens | "Mission control online…" |
| first lock | "Attitude lock acquired." |
| lock back after an outage | "Lock reacquired in N seconds." (1–10 s, else "Lock reacquired.") |
| lock lost | "Star tracker has lost lock." (not right after a flare) |
| still lost after 3 s | "Searching the sky." (at most every 15 s) |
| solver starts a search slew (stage `SEARCH SLEW`) | "Searching the sky." (the slew's rate burst is not read out) |
| detumble maneuver brings the rate to 0 | "Spacecraft stabilized." (the rate rule) |
| first candidate after losing lock (stage `VERIFYING`) | "Star pattern found. Verifying." (once per outage) |
| detumble maneuver starts (stage `DETUMBLING`) | "Spinning too fast to acquire. Detumbling." (at most every 15 s) |
| WHERE AM I? sweep starts | "Scanning for planets." |
| WHERE AM I? pressed while spinning | "Hold still for navigation.", then "Scanning for planets." once still |
| first position fix | "Position fixed from the planets." |
| flare whiteout starts | "Solar flare! Sensor blinded." (interrupts other lines) |
| knob rests in a new 5 °/s band | "Tumble rate N degrees per second." / "Spacecraft stabilized." |
| 3 s into a lock | "Pointing error under N arc seconds." (at most every 30 s) |
| lock flapping (4 changes in 8 s) | "Lock unstable." then lock/lost lines pause for 8 s |
| glare on/off, feeds lost/back | "Glare detected." / "Telemetry lost." … |

Urgent lines (flare, telemetry lost) cut in; other lines queue (max 3) and are dropped if they are
more than 4 s old by the time they would play.

**Controls:** the 🔊 chip in the dashboard header or the `V` key toggles the voice. Browsers block sound
until the page is clicked once (the chip says so). `dashboard --open` starts Chrome with autoplay allowed.

## Voice commands (talk to the probe)

`voice/server.py` (port 5010, started by `demo/fake_demo.sh`; `--no-voice-cmd` to skip) turns speech or typed text
into commands with `voice/commands.py` (regex, no network) and forwards only those: sim commands to UDP 5006,
solver commands (`whereami`, `detector …`, `camera …`) to `camera.solve_live`'s UDP 5011. The dashboard's
**COMMAND** button (bottom right) opens the panel: a microphone plus a text box. Typed commands always work;
speech needs `ELEVENLABS_API_KEY` in the voice server's environment and `pip install elevenlabs sounddevice`.

| say / type | command | goes to |
|---|---|---|
| "tumble about the roll axis at ten degrees per second" | `axis 0 0 1`, `rate 10` | sim |
| "faster" / "slow down by five" / "stop tumbling" | `rate_add 5` / `rate_add -5` / `rate 0` | sim |
| "solar flare", "glare on", "calibration off", "pause" | `flare`, `glare 1`, `calib 0`, `pause 1` | sim |
| "set boost to two" / "magnitude four point five" | `boost 2` (star brightness) / `mag 4.5` (faintest star drawn) | sim |
| "where am I" | `whereami` (the WHERE AM I? sweep; needs `--nav`) | solver |
| "use StarNet" / "switch to tuned tetra" / "classic" | `detector starnet / tuned / classic` (needs `--models`) | solver |
| "look through the webcam" / "clean camera" | `camera webcam / clean` | solver |

`python -m voice.commands "where am I" "faster"` prints what a sentence parses to. The dashboard's truth sky
view does not follow `mag` / `boost` (it redraws from truth with the default catalog); the solver sees the change.
