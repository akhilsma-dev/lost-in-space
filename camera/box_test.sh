#!/bin/zsh
# End-to-end go/no-go through a real camera, all logged to recordings/box_test.log.
# Run from macOS Terminal.app (it can get camera permission; apps' child processes often can't):
#   cd <repo> && camera/box_test.sh [camera_index]      (default 1: index 0 is usually the built-in camera)
# Aim the camera at this Mac's screen first. The sky goes full screen for ~1 minute; press nothing.
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-$(git rev-parse --path-format=absolute --git-common-dir)/../.venv/bin/python}
IDX=${1:-1}
mkdir -p recordings
LOG=recordings/box_test.log
exec > >(tee "$LOG") 2>&1
echo "== $(date)  camera index $IDX  python $PY"

echo "== cameras"
"$PY" -m camera.capture --list 2>&1 | grep -v WARN
for i in 0 1 2; do "$PY" -m camera.capture --index $i --snap recordings/box_cam$i.png 2>&1 | grep -E "saved|camera $i"; done

echo "== probe index $IDX (before sim)"
"$PY" -m camera.capture --index $IDX --probe 2>&1 | grep -v WARN

echo "== starting sim full screen"
"$PY" -m sim.live --seed 3 > recordings/box_sim.log 2>&1 &
SIM=$!
sleep 6

echo "== calibrate"
"$PY" -m camera.calibrate --index $IDX --settle 3 2>&1 | grep -v -E "WARN|INFO"
CAL=${pipestatus[1]}

if [ $CAL -eq 0 ]; then
  echo "== solve (static), 60 frames"
  "$PY" -m camera.solve_live --index $IDX --frames 60 --quiet --save-shot recordings/box_solve_static.png 2>&1 | grep -v -E "WARN|INFO"
  echo "== solve with --avg 4, 20 frames"
  "$PY" -m camera.solve_live --index $IDX --frames 20 --avg 4 --quiet 2>&1 | grep -v -E "WARN|INFO"
  echo "== solve at 2 deg/s tumble, 40 frames"
  echo "rate 2" | nc -u -w0 127.0.0.1 5006
  "$PY" -m camera.solve_live --index $IDX --frames 40 --quiet --save-shot recordings/box_solve_tumble.png 2>&1 | grep -v -E "WARN|INFO"
fi
# raw frame of the sky for inspection
"$PY" -m camera.capture --index $IDX --snap recordings/box_sky_raw.png 2>&1 | grep saved

kill $SIM 2>/dev/null
echo "== done (log: $LOG)"
