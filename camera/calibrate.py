"""Camera -> screen homography from the sim's checkerboard. Writes data/calib.json.

Run (sim.live running full screen on the box display, webcam pointed at it):
      .venv/bin/python -m camera.calibrate --index 0
It sends `calib 1` to the sim (UDP 5006), averages a few frames, finds the 9x6
inner corners, fits the homography against `sim.sky.checkerboard`'s exact screen
coordinates, then sends `calib 0`. Screen size comes from the sim's truth packet
(UDP 5005), else --screen-w/--screen-h.

No hardware:  .venv/bin/python -m camera.calibrate --sim
      films the checkerboard with camera.fakecam and checks the fit against the
      known true homography.

Other:  --image path.png   calibrate from a saved camera frame instead.
Corner order from OpenCV is ambiguous (the board can be seen rotated or the
grid walked from either end), so all four orderings are tried and the one whose
predicted black/white squares match the camera image is kept.
"""
import argparse
import datetime
import pathlib
import time

import cv2
import numpy as np

from camera.common import CALIB, RECORDINGS, TruthListener, save_calib, send_sim, warp_points
from sim.sky import checkerboard

COLS, ROWS = 9, 6


def find_corners(gray):
    flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    ok, c = cv2.findChessboardCornersSB(gray, (COLS, ROWS), flags=flags)
    if ok:
        return c.reshape(-1, 2), "SB"
    ok, c = cv2.findChessboardCorners(gray, (COLS, ROWS), flags=cv2.CALIB_CB_ADAPTIVE_THRESH
                                      | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if ok:
        c = cv2.cornerSubPix(gray, c, (11, 11), (-1, -1),
                             (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.01))
        return c.reshape(-1, 2), "classic"
    return None, None


def square_score(gray, H_cam2screen, screen_corners):
    """Correlation between expected square colours and the camera image under H."""
    sq = screen_corners[1, 0] - screen_corners[0, 0]
    x0, y0 = screen_corners[0] - sq
    rr, cc = np.mgrid[:ROWS + 1, :COLS + 1]
    centers = np.stack([x0 + (cc + 0.5) * sq, y0 + (rr + 0.5) * sq], -1).reshape(-1, 2)
    expect = np.where(((rr + cc) % 2 == 0).ravel(), -1.0, 1.0)  # black squares at (r+c) even
    cam = warp_points(centers, np.linalg.inv(H_cam2screen))
    h, w = gray.shape
    xi = np.clip(cam[:, 0].round().astype(int), 0, w - 1)
    yi = np.clip(cam[:, 1].round().astype(int), 0, h - 1)
    v = gray[yi, xi].astype(np.float64)
    v = (v - v.mean()) / (v.std() + 1e-9)
    return float(np.mean(v * expect))


def jacobian_det(H, pt):
    x, y = pt
    d = H[2, 0] * x + H[2, 1] * y + H[2, 2]
    u = (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / d
    v = (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / d
    J = np.array([[H[0, 0] - u * H[2, 0], H[0, 1] - u * H[2, 1]],
                  [H[1, 0] - v * H[2, 0], H[1, 1] - v * H[2, 1]]]) / d
    return float(np.linalg.det(J))


def fit(gray, screen_w, screen_h, allow_mirror=False):
    cam_pts, method = find_corners(gray)
    if cam_pts is None:
        return None
    _, scr = checkerboard(screen_w, screen_h, COLS, ROWS)
    # checkerboard() gives square edges as integer pixel indices; with pixel centres at integer
    # coordinates (OpenCV, tetra3 and the renderer's convention) an edge sits half a pixel earlier
    scr = scr - 0.5
    g = cam_pts.reshape(ROWS, COLS, 2)
    best = None
    for name, cand in (("as-is", g), ("rot180", g[::-1, ::-1]), ("flip-x", g[:, ::-1]), ("flip-y", g[::-1, :])):
        c = cand.reshape(-1, 2)
        H, _ = cv2.findHomography(c, scr, 0)
        # A camera filming a screen never mirrors it, and tetra3's edge-ratio patterns are mirror
        # invariant (a mirrored frame still "solves", with roll -> 180 - roll), so reject
        # reflections here. The 10x7-square board then disambiguates as-is vs rot180 by colour.
        if (jacobian_det(H, c.mean(0)) < 0) != allow_mirror:
            continue
        score = square_score(gray, H, scr)
        if best is None or score > best["score"]:
            res = warp_points(c, H) - scr
            best = {"H": H, "score": score, "order": name, "cam_pts": c,
                    "rms": float(np.sqrt(np.mean(np.sum(res ** 2, 1)))),
                    "max": float(np.max(np.linalg.norm(res, axis=1)))}
    best["method"] = method
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--sim", action="store_true", help="use the synthetic camera (no hardware)")
    ap.add_argument("--image", default=None, help="calibrate from a saved camera frame")
    ap.add_argument("--screen-w", type=int, default=1920)
    ap.add_argument("--screen-h", type=int, default=1080)
    ap.add_argument("--avg", type=int, default=8, help="frames to average")
    ap.add_argument("--settle", type=float, default=1.5, help="seconds to wait after showing the board")
    ap.add_argument("--no-command", action="store_true", help="don't toggle the sim's checkerboard over UDP")
    ap.add_argument("--out", default=str(CALIB))
    ap.add_argument("--mirrored", action="store_true", help="the camera image is mirrored (selfie-style)")
    args = ap.parse_args()

    sw, sh = args.screen_w, args.screen_h
    cam_info, true_H = {}, None
    if args.sim:
        from camera.fakecam import FakeCamera
        fc = FakeCamera(sw, sh)
        gray = fc.view(checkerboard(sw, sh)[0])
        true_H = np.linalg.inv(fc.H_screen2cam)
        cam_info = {"source": "fakecam"}
    elif args.image:
        gray = cv2.imread(args.image, cv2.IMREAD_GRAYSCALE)
        cam_info = {"source": args.image}
    else:
        from camera.capture import Camera
        truth = TruthListener()
        pkt = truth.wait(2.0)
        if pkt:
            sw, sh = pkt["w"], pkt["h"]
            print(f"sim is up: screen {sw}x{sh}, mode {pkt['mode']}")
        else:
            print(f"no truth packets on UDP 5005; assuming screen {sw}x{sh}")
        if not args.no_command:
            send_sim("calib 1")
        cam = Camera(args.index)
        print(cam.summary())
        time.sleep(args.settle)
        for _ in range(5):
            cam.read()  # flush frames buffered before the board appeared
        _, gray = cam.read_avg(args.avg)
        cam.release()
        if not args.no_command:
            send_sim("calib 0")
        cam_info = {"source": f"camera {args.index}", "controls": cam.report}

    h, w = gray.shape
    RECORDINGS.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = RECORDINGS / f"calib_raw_{stamp}.png"
    cv2.imwrite(str(raw_path), gray)

    r = fit(gray, sw, sh, args.mirrored)
    if r is None:
        print(f"FAILED: no {COLS}x{ROWS} checkerboard found. Frame saved to {raw_path}.\n"
              "  Check: board fully in view, screen at full brightness, camera in focus, "
              "no glare across the board.")
        raise SystemExit(1)

    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.drawChessboardCorners(vis, (COLS, ROWS), r["cam_pts"].reshape(-1, 1, 2).astype(np.float32), True)
    corners_scr = np.float64([[0, 0], [sw, 0], [sw, sh], [0, sh]])
    outline = warp_points(corners_scr, np.linalg.inv(r["H"])).astype(np.int32)
    cv2.polylines(vis, [outline], True, (0, 255, 255), 2)  # where the screen edges map to
    vis_path = RECORDINGS / f"calib_{stamp}.png"
    cv2.imwrite(str(vis_path), vis)

    # how many camera pixels per screen pixel, and does the whole screen fit in the frame?
    inside = np.all((outline >= 0) & (outline < [w, h]))
    scale = float(np.sqrt(abs(np.linalg.det(np.linalg.inv(r["H"])[:2, :2]))))
    print(f"corners: {len(r['cam_pts'])} ({r['method']}), order {r['order']}, colour score {r['score']:.2f}")
    print(f"reprojection RMS {r['rms']:.3f} screen px (max {r['max']:.3f}); "
          f"~{scale:.2f} camera px per screen px at centre; full screen in view: {bool(inside)}")
    if true_H is not None:
        grid = np.stack(np.meshgrid(np.linspace(0, sw, 9), np.linspace(0, sh, 9)), -1).reshape(-1, 2)
        cam = warp_points(grid, np.linalg.inv(true_H))
        err = np.linalg.norm(warp_points(cam, r["H"]) - grid, axis=1)
        print(f"[sim] vs true homography over the whole screen: mean {err.mean():.3f}, max {err.max():.3f} screen px")
    if r["rms"] > 1.0:
        print("WARNING: RMS > 1 screen px; lens distortion or a bad detection. Re-run, or move the camera "
              "so the board covers more of the frame.")
    if not inside:
        print("WARNING: part of the screen is outside the camera frame; stars there will be missed.")

    save_calib({"H_cam2screen": r["H"].tolist(), "cam_w": w, "cam_h": h, "screen_w": sw, "screen_h": sh,
                "rms_screen_px": r["rms"], "max_screen_px": r["max"], "corner_order": r["order"],
                "detector": r["method"], "cam_px_per_screen_px": scale, "screen_in_view": bool(inside),
                "created": stamp, "camera": cam_info}, pathlib.Path(args.out))
    print(f"wrote {args.out}\noverlay: {vis_path}")


if __name__ == "__main__":
    main()
