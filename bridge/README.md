# Console bridge: how to test

Files: `firmware/console/console.ino` (board), `bridge/serial_bridge.py` (laptop),
`firmware/PROTOCOL.md` (serial + UDP protocol).

## 0. Whole demo, one command (no camera, no board)

```bash
demo/fake_demo.sh            # add --tuned to hold lock while tumbling, --board for the real console
```

Starts `sim.live`, the dashboard (http://localhost:8080), `camera.solve_live --follow-sim` (estimates on
5008 + 5007) and this bridge with the keyboard as the console. The real solver drives STATUS / ATT / SERVO.

## 1. Without hardware (fake board)

Two terminals, from the repo root, with the venv active:

```bash
python -m sim.live --window
```

```bash
python -m bridge.serial_bridge --fake --truth-demo
```

Type in the bridge terminal (then Enter): `f` = solar flare, a number `0-1023` =
knob position, `+`/`-` = knob ±100, `q` = quit.

Expect: `f` makes the sim jump to a new attitude; knob numbers change the
tumble rate (`rate 0..20` printed by the sim). `--truth-demo` feeds the sim's
own truth (UDP 5005) back as if the solver were locked, so the bridge prints
the `STATUS LOCK` / `ATT` / `SERVO` lines it would send to the board (ATT/SERVO
shown once a second; `--verbose` shows all). Don't use `--truth-demo` while
the dashboard is listening on 5005.

To exercise the solver path by hand:

```bash
python -c "import socket,json; socket.socket(2,2).sendto(json.dumps({'status':'LOST','ra':10,'dec':-5,'roll':0}).encode(),('127.0.0.1',5007))"
```

## 2. With the board

1. Arduino IDE: Library Manager → install **U8g2**. Board = Arduino Uno.
   Open `firmware/console/console.ino`, Upload.
2. Serial Monitor at 115200, line ending "Newline": you should see
   `HELLO console 1` and `KNOB n`. Turn the knob → new `KNOB` lines; press the
   button → `BTN FLARE`. Type `STATUS LOCK` → chirp + LED on + OLED "LOCK";
   `STATUS LOST` → alarm; `SERVO 30 120` → servos move smoothly (≤ 60 °/s).
   After 3 s without input the OLED shows `NO LINK`.
3. Close the Serial Monitor (only one program can own the port), then run
   `python -m bridge.serial_bridge` (auto-detects `/dev/cu.usbserial-*` /
   `usbmodem*`, or pass `--port`). It waits 2 s after opening because that
   resets the board.

## Servo behaviour (spacecraft model)

Default `--servo-mode relative`: when the solver reaches LOCK the model goes to
centre (90°, 90°) and remembers that pointing. After that it shows how far the
spacecraft has turned since the lock: pan = left/right, tilt = up/down (roll
isn't shown). This works well up to about ±90° of turn, then the servos sit at
their limits. Pressing flare, or any new LOCK after SEARCH/LOST, re-centres it.
While SEARCH/LOST the model holds still. If a direction looks backwards on the
real rig, add `--flip-pan` / `--flip-tilt`. `--servo-mode absolute` is the old
mapping (pan = RA/2, tilt = 90 + Dec), which swings end to end when RA crosses 0°.

Note: in fake mode the knob starts at 0, so the bridge immediately sets the sim
to `rate 0`; type a knob value (e.g. `400`) to make it tumble.

Tunables: in the sketch, the constants at the top (`SERVO_MAX_DPS`, tilt limits,
`BTN_ACTIVE_HIGH` if the button reads inverted); in the bridge, `--max-rate`,
`--rate-step`, `--no-servo`.
