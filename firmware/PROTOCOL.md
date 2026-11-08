# Console serial protocol

USB serial, **115200 baud, 8N1**, ASCII, one message per line terminated by `\n`
(`\r` is ignored). Fields are separated by single spaces. Lines longer than 63
characters are dropped by the board.

Opening the port resets the board (Uno auto-reset), so the laptop waits ~2 s
after opening before it sends anything.

## Board → laptop

| Line | When | Meaning |
|---|---|---|
| `HELLO console 1` | once after reset | firmware name + protocol version |
| `BTN FLARE` | button D6 pressed (debounced 30 ms, on press only) | trigger a solar flare |
| `BTN NAV` | **virtual console only** (sim demo): WHERE AM I? pressed | bridge sends `whereami` to `camera.solve_live` (UDP 5011): one planet scan sweep |
| `KNOB <0-1023>` | knob A0 moved ≥ 6 counts (smoothed, ≤ 20/s); also after reset and after `PING` | raw knob position |
| `PONG` | reply to `PING` | link check |
| `ERR <text>` | malformed / unknown laptop line | debugging aid |

## Laptop → board

| Line | Effect |
|---|---|
| `STATUS LOCK` | OLED shows LOCK, LED D4 on; lock chirp on the buzzer when entering LOCK |
| `STATUS SEARCH` | OLED shows SEARCH, LED off |
| `STATUS LOST` | OLED shows LOST, LED off; alarm on the buzzer when entering LOST (not when coming from NO LINK) |
| `ATT <ra> <dec> <roll>` | attitude in degrees, shown on the OLED |
| `SERVO <pan> <tilt>` | servo targets in degrees (pan D9 clamped 0–180, tilt D10 clamped 20–160). The board slews towards them at ≤ 60 °/s. |
| `PING` | board replies `PONG` then `KNOB <n>` |
| `NAV <state>` | **virtual console only** (sent with `STAGE`, i.e. not to a real board by default): state of the WHERE AM I? scan from `solve_live --nav`: `IDLE`, `SCAN k/8`, `HOLD` (paused: spinning), `WAIT` (paused: no lock), `FIX`, `NO FIX`. The console shows its WHERE AM I? button once it gets one. |
| `STAGE <text>` | acquisition stage from the solver, e.g. `STAGE VERIFYING 3/6`, `STAGE DETUMBLING 12.0`, `STAGE SEARCH SLEW`, `STAGE COASTING` (LOCKed, carrying the attitude through frames that did not solve; the virtual console titles the OLED COASTING), `STAGE LOCK` (upper-case ASCII, ≤ 40 chars of text, sent on change). Shown by the virtual console under the status title while not locked. **Optional:** the bridge sends it only to the virtual / fake board by default (`--stage-lines` for a real board); the current firmware does not handle it and would answer `ERR unknown STAGE`. |

**Link timeout:** if the board receives no line for 3 s it shows `NO LINK`
and turns the LED off; servos hold their last position. The bridge re-sends
`STATUS` at least once a second as a keep-alive.

**Knob → tumble rate:** the bridge maps `KNOB n` to `rate n*20/1023` deg/s
(the OLED shows the same number). Changing the range means changing
`KNOB_MAX_RATE` in the sketch and `--max-rate` in the bridge.

## Example session

```
< HELLO console 1
< KNOB 0
> PING
< PONG
< KNOB 0
> STATUS SEARCH
< KNOB 512
< BTN FLARE
> STATUS LOCK
> ATT 83.82 -5.39 12.0
> SERVO 41.9 84.6
```

## Laptop-side UDP ports (serial_bridge.py)

| Port | Direction | Content |
|---|---|---|
| 5006 | bridge → sim | `flare`, `rate <deg/s>` (sim/live.py command port; `camera.solve_live` also sends `rate` here for its recovery maneuvers) |
| 5011 | bridge → solver | `whereami` (from `BTN NAV`; `solve_live --nav-scan-mode button`, `--nav-cmd-port`) |
| 5007 | solver → bridge | JSON per line/datagram: `{"status": "LOCK", "ra": .., "dec": .., "roll": ..}`; optional `"stage"` → `STAGE` line; optional `"pan"`, `"tilt"` override the servo mapping (default mapping: model centres on LOCK and shows turn since then; see `bridge/README.md`) |
