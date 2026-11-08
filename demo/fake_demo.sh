#!/usr/bin/env bash
# One-command demo with no camera: sim.live -> solve_live --follow-sim -> dashboard + console bridge.
#
#   demo/fake_demo.sh            keyboard plays the console (f = flare, 0-1023 = tumble knob, q = quit)
#   demo/fake_demo.sh --board    real Arduino console on USB instead of the keyboard
#   demo/fake_demo.sh --tuned    classical solver keeps motion-blur streaks (holds lock while tumbling)
#   demo/fake_demo.sh --full     sim full screen (default: window)
#   demo/fake_demo.sh --open     also open the dashboard in Chrome kiosk mode
#
# Dashboard: http://localhost:8080   Logs: $LOG_DIR (default: a temp folder, printed at start)
# Ctrl+C (or q in keyboard mode) stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python; fi
fi

BOARD=0 TUNED=0 FULL=0 OPEN=0
for arg in "$@"; do
  case "$arg" in
    --board) BOARD=1 ;;
    --tuned) TUNED=1 ;;
    --full)  FULL=1 ;;
    --open)  OPEN=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
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
pkill -f -- "python[0-9.]* -u -m (sim\.live|camera\.solve_live|dashboard|bridge\.serial_bridge)( |$)" 2>/dev/null || true
sleep 0.5

SIM_ARGS=(); [[ $FULL == 0 ]] && SIM_ARGS+=(--window)
"$PY" -u -m sim.live ${SIM_ARGS[@]+"${SIM_ARGS[@]}"} > "$LOG_DIR/sim.log" 2>&1 &
PIDS+=($!)

DASH_ARGS=(); [[ $OPEN == 1 ]] && DASH_ARGS+=(--open)
"$PY" -u -m dashboard ${DASH_ARGS[@]+"${DASH_ARGS[@]}"} > "$LOG_DIR/dashboard.log" 2>&1 &
PIDS+=($!)

SOLVE_ARGS=(--follow-sim --quiet); [[ $TUNED == 1 ]] && SOLVE_ARGS+=(--max-area 5000)
"$PY" -u -m camera.solve_live "${SOLVE_ARGS[@]}" > "$LOG_DIR/solve.log" 2>&1 &
PIDS+=($!)

sleep 3
for p in "${PIDS[@]}"; do
  if ! kill -0 "$p" 2>/dev/null; then
    echo "[demo] a component exited early; logs in $LOG_DIR:"; tail -n 5 "$LOG_DIR"/*.log; exit 1
  fi
done

echo "[demo] sim, dashboard, solver running  (solver: $([[ $TUNED == 1 ]] && echo tuned || echo classical))"
echo "[demo] dashboard: http://localhost:8080    logs: $LOG_DIR"
if [[ $BOARD == 1 ]]; then
  echo "[demo] console: real board over USB"
  "$PY" -u -m bridge.serial_bridge
else
  echo "[demo] console: keyboard. Type then Enter:  f = solar flare   0-1023 = tumble knob   +/- = knob +-100   q = quit"
  "$PY" -u -m bridge.serial_bridge --fake
fi
