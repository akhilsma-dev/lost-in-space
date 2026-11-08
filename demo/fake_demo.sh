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
#   demo/fake_demo.sh --open     also open the dashboard in Chrome kiosk mode
#
# Dashboard: http://localhost:8080 (V = voice callouts on/off)   Virtual console: http://localhost:8081   Logs: $LOG_DIR (default: a temp folder, printed at start)
# Ctrl+C (or q in keyboard mode) stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python; fi
fi

BOARD=0 TUNED=1 STARNET=0 FULL=0 OPEN=0 WEB=1
for arg in "$@"; do
  case "$arg" in
    --board) BOARD=1 ;;
    --tuned) TUNED=1 ;;
    --classic) TUNED=0 ;;
    --starnet) STARNET=1 ;;
    --full)  FULL=1 ;;
    --open)  OPEN=1 ;;
    --no-web) WEB=0 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
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
  grep -qE "^(LOCK|LOST) " "$LOG_DIR/solve.log" 2>/dev/null && break
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
