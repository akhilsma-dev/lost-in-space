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
| flare whiteout starts | "Solar flare! Sensor blinded." (interrupts other lines) |
| knob rests in a new 5 °/s band | "Tumble rate N degrees per second." / "Spacecraft stabilized." |
| 3 s into a lock | "Pointing error under N arc seconds." (at most every 30 s) |
| lock flapping (4 changes in 8 s) | "Lock unstable." then lock/lost lines pause for 8 s |
| glare on/off, feeds lost/back | "Glare detected." / "Telemetry lost." … |

Urgent lines (flare, telemetry lost) cut in; other lines queue (max 3) and are dropped if they are
more than 4 s old by the time they would play.

**Controls:** the 🔊 chip in the dashboard header or the `V` key toggles the voice. Browsers block sound
until the page is clicked once (the chip says so). `dashboard --open` starts Chrome with autoplay allowed.
