#!/usr/bin/env bash
# One-command demo with no camera: sim.live -> solve_live --follow-sim -> dashboard + console bridge.
#
#   demo/fake_demo.sh            virtual console (web page, also in the dashboard) + keyboard play the board
#                                (keyboard: f = flare, 0-1023 = tumble knob, q = quit)
#   demo/fake_demo.sh --no-web   keyboard only, no virtual console page
#   demo/fake_demo.sh --board    real Arduino console on USB instead of the keyboard
#   demo/fake_demo.sh --classic  plain tetra3 blob filter (loses lock near 5 deg/s; default keeps blur streaks)
#   demo/fake_demo.sh --starnet  StarNet (ml/, our CNN) finds the stars, tetra3 matches them (GPU/MPS if present)
#   demo/fake_demo.sh --full     sim full screen (default: window)
#   demo/fake_demo.sh --models   all star finders loaded; switch CLASSIC / TUNED / StarNet live on the dashboard
#   demo/fake_demo.sh --webcam   start with the solver seeing the sim through the synthetic webcam (like the real box);
#                                either way the CLEAN / WEBCAM switch on the dashboard's camera view flips it live
#   demo/fake_demo.sh --open     also open the dashboard in Chrome kiosk mode
#   demo/fake_demo.sh --nav      position from planets: hidden position + planets on screen; WHERE AM I? on the
#                                virtual console slews through 8 ecliptic pointings (20 deg/s, 2 s look each; paused while spinning) and
#                                fixes the position (dashboard POSITION panel); with --board it scans continuously;
#                                a flare also moves the spacecraft, so the position is found again after it
#
# Dashboard: http://localhost:8080 (V = voice callouts on/off)   Virtual console: http://localhost:8081   Logs: $LOG_DIR (default: a temp folder, printed at start)
# Ctrl+C (or q in keyboard mode) stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python; fi
fi

BOARD=0 TUNED=1 STARNET=0 FULL=0 OPEN=0 WEB=1 NAV=0 MODELS=0 WEBCAM=0
for arg in "$@"; do
  case "$arg" in
    --board) BOARD=1 ;;
    --tuned) TUNED=1 ;;
    --classic) TUNED=0 ;;
    --starnet) STARNET=1 ;;
    --full)  FULL=1 ;;
    --open)  OPEN=1 ;;
    --no-web) WEB=0 ;;
    --nav)   NAV=1 ;;
    --models) MODELS=1 ;;
    --webcam) WEBCAM=1 ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)"; exit 1 ;;
  esac
done

LOG_DIR="${LOG_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/lost-in-space.XXXX")}"
mkdir -p "$LOG_DIR"

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  echo; echo "[demo] stopping..."
  for p in ${PIDS[@]+"${PIDS[@]}"}; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Free the ports in case an earlier run is still alive.
# Match only "<python> -u -m <our module>" so editors / shells mentioning these names are left alone.
# ("Python" is capitalised for macOS framework builds.)
pkill -f -- "[Pp]ython[0-9.]* -u -m (sim\.live|camera\.solve_live|dashboard|bridge\.serial_bridge)( |$)" 2>/dev/null || true
sleep 0.5

SIM_ARGS=(); [[ $FULL == 0 ]] && SIM_ARGS+=(--window)
[[ $NAV == 1 ]] && SIM_ARGS+=(--planets --flare-moves)
"$PY" -u -m sim.live ${SIM_ARGS[@]+"${SIM_ARGS[@]}"} > "$LOG_DIR/sim.log" 2>&1 &
PIDS+=($!)

CONSOLE_URL=http://localhost:8081/
DASH_ARGS=(); [[ $OPEN == 1 ]] && DASH_ARGS+=(--open)
[[ $BOARD == 0 && $WEB == 1 ]] && DASH_ARGS+=(--console-url "$CONSOLE_URL")
"$PY" -u -m dashboard ${DASH_ARGS[@]+"${DASH_ARGS[@]}"} > "$LOG_DIR/dashboard.log" 2>&1 &
PIDS+=($!)

SOLVE_ARGS=(--follow-sim --quiet)
if [[ $STARNET == 1 ]]; then SOLVE_ARGS+=(--detector starnet); SOLVER_NAME=StarNet
elif [[ $TUNED == 1 ]]; then SOLVE_ARGS+=(--max-area 5000); SOLVER_NAME=tuned
else SOLVER_NAME=classical; fi
# --models: load all three star finders (classic / tuned tetra3, StarNet); the dashboard's SOLVE RATE panel
# switches between them live (StarNet takes ~10-15 s to load). --webcam: the solver films the sim through the
# synthetic webcam (keystone, defocus, noise, JPEG), like the real box: that is where StarNet earns its keep.
[[ $MODELS == 1 ]] && SOLVE_ARGS+=(--models --max-area 5000) && SOLVER_NAME="switchable: classic / tuned / StarNet"
[[ $WEBCAM == 1 ]] && SOLVE_ARGS+=(--sim-camera) && SOLVER_NAME="$SOLVER_NAME, through the webcam"
# planet nav: with the virtual console the scan runs when WHERE AM I? is pressed; a real board has no
# such button, so there it scans continuously (both pause while the craft spins)
if [[ $NAV == 1 ]]; then
  SOLVE_ARGS+=(--nav --nav-scan 2 --nav-reset-on-blind); SOLVER_NAME="$SOLVER_NAME + planet nav"
  [[ $BOARD == 0 && $WEB == 1 ]] && SOLVE_ARGS+=(--nav-scan-mode button)
fi
# SOLVE_EXTRA="--coast 0 --no-recover" demo/fake_demo.sh ...: extra solver options (testing)
[[ -n ${SOLVE_EXTRA:-} ]] && SOLVE_ARGS+=($SOLVE_EXTRA)
"$PY" -u -m camera.solve_live "${SOLVE_ARGS[@]}" > "$LOG_DIR/solve.log" 2>&1 &
PIDS+=($!)

# Wait until the solver has produced its first answer (StarNet takes ~10-15 s to load), so the
# console is only handed over once the whole chain is live.
echo "[demo] starting ($SOLVER_NAME solver)..."
for i in $(seq 1 120); do
  for p in "${PIDS[@]}"; do
    if ! kill -0 "$p" 2>/dev/null; then
      echo "[demo] a component exited early; logs in $LOG_DIR:"; tail -n 5 "$LOG_DIR"/*.log; exit 1
    fi
  done
  grep -qE "^(LOCK|LOST|SEARCH) " "$LOG_DIR/solve.log" 2>/dev/null && break
  [[ $i == 120 ]] && { echo "[demo] solver gave no answer in 60 s; see $LOG_DIR/solve.log"; exit 1; }
  sleep 0.5
done

echo "[demo] sim, dashboard, solver running  (solver: $SOLVER_NAME)"
echo "[demo] dashboard: http://localhost:8080    logs: $LOG_DIR"
if [[ $BOARD == 1 ]]; then
  echo "[demo] console: real board over USB"
  "$PY" -u -m bridge.serial_bridge
else
  BRIDGE_ARGS=(--fake)
  if [[ $WEB == 1 ]]; then
    BRIDGE_ARGS+=(--web 8081)
    echo "[demo] console: virtual board at $CONSOLE_URL (also inside the dashboard)"
  fi
  echo "[demo] keyboard also works. Type then Enter:  f = solar flare   0-1023 = tumble knob   +/- = knob +-100   q = quit"
  "$PY" -u -m bridge.serial_bridge "${BRIDGE_ARGS[@]}"
fi
