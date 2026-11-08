"""Frame bus: sim.live shares the exact frames it shows with camera.solve_live --follow-sim through
shared memory (like a capture card on the screen), so the solver needs no redraw from truth.

Layout: header (8 x int64: magic, w, h, latest frame, slot0 frame, slot1 frame, 0, 0) + two w*h
uint8 slots. Frame f goes to slot f % 2; the slot's frame number is set to -1 while it is written,
so a reader that sees the same number before and after its copy has a whole frame.
The segment name travels in the truth packet ("shm").
"""
from multiprocessing import shared_memory

import numpy as np

MAGIC = 0x4C495346   # "LISF"
HDR = 8 * 8


class FrameWriter:
    def __init__(self, name, w, h):
        self.name, self.w, self.h = name, w, h
        size = HDR + 2 * w * h
        try:   # a segment left behind by a crashed run
            old = shared_memory.SharedMemory(name=name, track=False)
            old.close()
            old.unlink()
        except FileNotFoundError:
            pass
        self.shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        self.hdr = np.ndarray((8,), np.int64, self.shm.buf, 0)
        self.slots = np.ndarray((2, h, w), np.uint8, self.shm.buf, HDR)
        self.hdr[:] = [MAGIC, w, h, -1, -1, -1, 0, 0]

    def write(self, frame, img):
        if img.dtype != np.uint8 or img.shape != (self.h, self.w):
            return   # e.g. a colour calibration image: the solver skips calib frames anyway
        s = frame % 2
        self.hdr[4 + s] = -1
        self.slots[s] = img
        self.hdr[4 + s] = frame
        self.hdr[3] = frame

    def close(self):
        del self.hdr, self.slots
        self.shm.close()
        self.shm.unlink()


class FrameReader:
    def __init__(self, name):
        self.shm = shared_memory.SharedMemory(name=name, track=False)
        self.hdr = np.ndarray((8,), np.int64, self.shm.buf, 0)
        if self.hdr[0] != MAGIC:
            raise ValueError(f"{name}: not a sim.live frame bus")
        self.w, self.h = int(self.hdr[1]), int(self.hdr[2])
        self.slots = np.ndarray((2, self.h, self.w), np.uint8, self.shm.buf, HDR)

    def read(self, frame):
        """Copy of frame `frame`, or None if it is not (or no longer) in the bus."""
        s = frame % 2
        if self.hdr[4 + s] != frame:
            return None
        img = self.slots[s].copy()
        return img if self.hdr[4 + s] == frame else None

    def close(self):
        del self.hdr, self.slots
        self.shm.close()
