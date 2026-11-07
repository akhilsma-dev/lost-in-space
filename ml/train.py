"""Train StarNet on freshly rendered crops (infinite synthetic stream).

  .venv/bin/python -m ml.train                  # default: 4000 steps, ~14 min on Apple-silicon MPS, ~65 min CPU-only
  .venv/bin/python -m ml.train --steps 300      # smoke test
  .venv/bin/python -m ml.train --device cpu     # force CPU (MPS / CUDA used if present)

Writes ml/weights/starnet.pt (+ a loss log at ml/out/train_log.csv).
"""
import argparse
import os
import pathlib
import time

import numpy as np
import torch
from torch.utils.data import DataLoader, IterableDataset

from ml.model import StarNet, loss_fn
from ml.patches import make_frame, random_crops

ROOT = pathlib.Path(__file__).resolve().parent
KEYS = ["heat", "ignore", "off", "mag", "reg", "glare"]


class CropStream(IterableDataset):
    def __init__(self, size=256, per_frame=8, seed=0):
        self.size, self.per_frame, self.seed = size, per_frame, seed

    def __iter__(self):
        info = torch.utils.data.get_worker_info()
        wid = info.id if info else 0
        rng = np.random.default_rng([self.seed, wid, os.getpid()])
        while True:
            fr = make_frame(rng)
            for img, t in random_crops(fr, rng, self.size, self.per_frame):
                yield (torch.from_numpy(img[None].astype(np.float32) / 255.0),
                       {k: torch.from_numpy(t[k]) for k in KEYS})


def pick_device(name):
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=4000)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--device", default="auto")
    ap.add_argument("--out", default=str(ROOT / "weights" / "starnet.pt"))
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    torch.manual_seed(a.seed)
    dev = pick_device(a.device)
    model = StarNet().to(dev)
    print(f"device={dev}  params={sum(p.numel() for p in model.parameters()) / 1e3:.0f}k")
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=a.steps, pct_start=0.05)
    dl = DataLoader(CropStream(seed=a.seed), batch_size=a.batch, num_workers=a.workers,
                    persistent_workers=a.workers > 0, prefetch_factor=4 if a.workers else None)

    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "out").mkdir(exist_ok=True)
    log = open(ROOT / "out" / "train_log.csv", "w")
    log.write("step,loss,heat,off,mag,glare,sec\n")
    t0, run = time.time(), None
    model.train()
    for step, (x, t) in enumerate(dl, 1):
        x = x.to(dev, non_blocking=True)
        t = {k: v.to(dev, non_blocking=True) for k, v in t.items()}
        loss, parts = loss_fn(model(x), t)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        sched.step()
        cur = np.array([loss.item(), *parts.values()])
        run = cur if run is None else 0.98 * run + 0.02 * cur
        if step % 50 == 0 or step == a.steps:
            el = time.time() - t0
            log.write(f"{step},{','.join(f'{v:.4f}' for v in run)},{el:.0f}\n"); log.flush()
            print(f"step {step:5d}/{a.steps}  loss {run[0]:.3f}  heat {run[1]:.3f}  off {run[2]:.3f}  "
                  f"mag {run[3]:.2f}  glare {run[4]:.3f}  {el / 60:.1f} min", flush=True)
        if step % 1000 == 0 or step == a.steps:
            torch.save({"state_dict": model.state_dict(), "step": step}, a.out)
        if step >= a.steps:
            break
    print("saved", a.out)


if __name__ == "__main__":
    main()
