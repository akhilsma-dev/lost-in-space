# Console bridge: how to test

Files: `firmware/console/console.ino` (board), `bridge/serial_bridge.py` (laptop),
`firmware/PROTOCOL.md` (serial + UDP protocol).

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

Tunables: in the sketch, the constants at the top (`SERVO_MAX_DPS`, tilt limits,
`BTN_ACTIVE_HIGH` if the button reads inverted); in the bridge, `--max-rate`,
`--rate-step`, `--no-servo`.
