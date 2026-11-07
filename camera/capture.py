"""USB webcam capture with best-effort locking of exposure / gain / focus / white balance.

Run:  .venv/bin/python -m camera.capture --list            which camera indices open
      .venv/bin/python -m camera.capture --index 0 --probe  report which controls actually stick
      .venv/bin/python -m camera.capture --index 0          live preview (s = save frame, q = quit)

Why locking matters: the camera films a mostly-black screen, so auto-exposure and
auto-gain crank up and hunt, and autofocus hunts with nothing to lock on. On Linux
(V4L2) OpenCV can usually lock all of these. On macOS (AVFoundation) OpenCV
ignores most control writes: `open_camera` tries each one, reads it back and
records what really happened in `cam.report`, so callers can print it instead of
assuming. If nothing sticks, the fallbacks are: screen at 100% brightness, dim
stars in software, and let tetra3's local background subtraction absorb the
AE gain (it does), or lock settings with the vendor app / `uvc-util` beforehand.
"""
import argparse
import platform
import time

import cv2
import numpy as np

IS_MAC = platform.system() == "Darwin"
BACKEND = cv2.CAP_AVFOUNDATION if IS_MAC else cv2.CAP_ANY

# (name, property, value) tried in order. Values follow V4L2 conventions; other
# backends may interpret them differently, which is why we read them back.
LOCKS = [
    ("auto_exposure_off", cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if not IS_MAC else 0),  # V4L2: 0.25 = manual
    ("autofocus_off", cv2.CAP_PROP_AUTOFOCUS, 0),
    ("auto_wb_off", cv2.CAP_PROP_AUTO_WB, 0),
]


class Camera:
    def __init__(self, index=0, width=1920, height=1080, fps=30, exposure=None, gain=None,
                 focus=None, wb=None, fourcc="MJPG", lock=True):
        self.index = index
        self.cap = cv2.VideoCapture(index, BACKEND)
        if not self.cap.isOpened():
            raise RuntimeError(f"camera {index} did not open (on macOS: grant camera permission "
                               f"to the terminal app in System Settings > Privacy & Security > Camera)")
        self.report = {}
        if fourcc and not IS_MAC:  # AVFoundation picks its own pixel format
            self._set("fourcc", cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        self._set("width", cv2.CAP_PROP_FRAME_WIDTH, width)
        self._set("height", cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._set("fps", cv2.CAP_PROP_FPS, fps)
        if lock:
            for name, prop, val in LOCKS:
                self._set(name, prop, val)
        for name, prop, val in (("exposure", cv2.CAP_PROP_EXPOSURE, exposure), ("gain", cv2.CAP_PROP_GAIN, gain),
                                ("focus", cv2.CAP_PROP_FOCUS, focus), ("wb_temperature", cv2.CAP_PROP_WB_TEMPERATURE, wb)):
            if val is not None:
                self._set(name, prop, val)
        # first frames after open are black / still adjusting; an iPhone (Continuity Camera)
        # takes ~1 s to deliver real frames, so wait for a non-black one (up to 3 s)
        end = time.time() + 3.0
        ok, frame = self.cap.read()
        while time.time() < end and (not ok or frame.max() == 0):
            ok, frame = self.cap.read()
        for _ in range(5):  # let auto-exposure settle a little
            ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError(f"camera {index} opened but returned no frame")
        self.h, self.w = frame.shape[:2]
        self.report["actual_resolution"] = f"{self.w}x{self.h}"

    def _set(self, name, prop, val):
        before = self.cap.get(prop)
        accepted = bool(self.cap.set(prop, val))
        after = self.cap.get(prop)
        stuck = accepted and abs(after - val) < 1e-3
        self.report[name] = {"want": val, "before": before, "set_returned": accepted, "readback": after,
                             "works": stuck}
        return stuck

    def read(self):
        """-> (capture_time, gray uint8 frame) or (t, None) on failure."""
        ok, frame = self.cap.read()
        t = time.time()
        if not ok:
            return t, None
        return t, frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def read_avg(self, n):
        """Average n frames (static scenes only): cuts sensor noise by sqrt(n)."""
        acc, t0 = None, None
        for _ in range(n):
            t, g = self.read()
            if g is None:
                continue
            t0 = t0 or t
            acc = g.astype(np.float32) if acc is None else acc + g
        return t0, None if acc is None else np.clip(acc / n, 0, 255).astype(np.uint8)

    def summary(self):
        works = [k for k, v in self.report.items() if isinstance(v, dict) and v["works"]]
        fails = [k for k, v in self.report.items() if isinstance(v, dict) and not v["works"]]
        return (f"camera {self.index}: {self.report['actual_resolution']}  controls that stuck: "
                f"{', '.join(works) or 'none'}  ignored: {', '.join(fails) or 'none'}")

    def release(self):
        self.cap.release()


def list_cameras(max_index=5):
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, BACKEND)
        if cap.isOpened():
            ok, f = cap.read()
            found.append((i, f.shape[1] if ok else None, f.shape[0] if ok else None))
        cap.release()
    return found


def frame_stats(gray):
    return (f"min {gray.min():3d} median {int(np.median(gray)):3d} max {gray.max():3d}  "
            f"saturated {100 * np.mean(gray >= 250):.3f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--exposure", type=float, default=None, help="backend units (V4L2: 100 us steps)")
    ap.add_argument("--gain", type=float, default=None)
    ap.add_argument("--focus", type=float, default=None)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--probe", action="store_true", help="print control report + frame stats and exit")
    ap.add_argument("--snap", default=None, help="save one frame to this path and exit")
    args = ap.parse_args()

    if args.list:
        for i, w, h in list_cameras():
            print(f"index {i}: opens, default frame {w}x{h}")
        return
    cam = Camera(args.index, args.width, args.height, exposure=args.exposure, gain=args.gain,
                 focus=args.focus, lock=not args.no_lock)
    print(cam.summary())
    if args.probe:
        for k, v in cam.report.items():
            print(f"  {k:18s} {v}")
        t, g = cam.read()
        print("frame:", frame_stats(g))
        cam.release()
        return
    if args.snap:
        t, g = cam.read()
        cv2.imwrite(args.snap, g)
        print("saved", args.snap, frame_stats(g))
        cam.release()
        return

    from camera.common import RECORDINGS, centroids
    win = f"camera {args.index} (s = save, q = quit)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    last_print = 0
    while True:
        t, g = cam.read()
        if g is None:
            print("frame grab failed")
            break
        c = centroids(g)
        vis = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        for x, y in c[:200]:
            cv2.circle(vis, (int(x), int(y)), 8, (0, 200, 255), 1)
        cv2.putText(vis, f"{len(c)} blobs  {frame_stats(g)}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (0, 255, 0), 2)
        cv2.imshow(win, vis)
        if t - last_print > 2:
            print(f"{len(c):3d} blobs  {frame_stats(g)}")
            last_print = t
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("s"):
            RECORDINGS.mkdir(exist_ok=True)
            p = RECORDINGS / f"cam_{int(t)}.png"
            cv2.imwrite(str(p), g)
            print("saved", p)
    cam.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
