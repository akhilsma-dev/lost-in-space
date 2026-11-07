"""Synthetic 'webcam filming the screen', for testing the camera pipeline with no hardware.

Takes a screen image (what sim.live would display) and returns what a cheap
webcam slightly off-axis would see: keystone homography, a bit of defocus, LCD
black-level glow, vignetting, sensor noise and MJPEG compression. The true
screen->camera homography is known, so calibration accuracy can be checked.
"""
import cv2
import numpy as np


class FakeCamera:
    def __init__(self, screen_w, screen_h, cam_w=1920, cam_h=1080, seed=0, noise=3.0, blur=0.9,
                 black=10.0, jpeg=85):
        self.sw, self.sh, self.w, self.h = screen_w, screen_h, cam_w, cam_h
        self.rng = np.random.default_rng(seed)
        self.noise, self.blur, self.black, self.jpeg = noise, blur, black, jpeg
        src = np.float32([[0, 0], [screen_w, 0], [screen_w, screen_h], [0, screen_h]])
        dst = np.float32([[0.07, 0.09], [0.94, 0.05], [0.92, 0.95], [0.09, 0.91]]) * np.float32([cam_w, cam_h])
        self.H_screen2cam = cv2.getPerspectiveTransform(src, dst)
        yy, xx = np.mgrid[:cam_h, :cam_w]
        r2 = ((xx - cam_w / 2) ** 2 + (yy - cam_h / 2) ** 2) / (cam_w / 2) ** 2
        self.vignette = (1 - 0.25 * r2).astype(np.float32)

    def view(self, screen_u8):
        img = cv2.warpPerspective(screen_u8.astype(np.float32), self.H_screen2cam, (self.w, self.h),
                                  flags=cv2.INTER_AREA, borderValue=0)
        lit = cv2.warpPerspective(np.full((self.sh, self.sw), self.black, np.float32), self.H_screen2cam,
                                  (self.w, self.h))
        img = (img * 0.9 + lit) * self.vignette
        if self.blur > 0:
            img = cv2.GaussianBlur(img, (0, 0), self.blur)
        img += self.rng.normal(0, self.noise, img.shape).astype(np.float32)
        u8 = np.clip(img, 0, 255).astype(np.uint8)
        if self.jpeg:
            u8 = cv2.imdecode(cv2.imencode(".jpg", u8, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg])[1],
                              cv2.IMREAD_GRAYSCALE)
        return u8
