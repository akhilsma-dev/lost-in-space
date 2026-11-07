"""StarNet: a small fully convolutional CenterNet-style star detector.

Why a heatmap net and not "blob proposals + patch CNN":
  The thing that breaks tetra3 under tumble is its blob finder — a smeared
  star becomes a long, faint streak whose per-pixel brightness sits near the
  noise, and whose area exceeds tetra3's blob-size limits. A proposal stage
  built from thresholds would inherit exactly that failure. A heatmap net sees
  the whole frame, learns the matched filter for streaks itself (receptive field
  ~200 px > the 72 px streak at 20 deg/s), and does detection, sub-pixel
  centroiding and classification in one ~9 GMAC pass. Training is fully
  convolutional on 256 px crops; inference runs on the full frame.

Outputs at stride 4 (1920x1080 -> 480x270):
  heat  (3)  sigmoid heatmaps: star, planet, hot pixel
  off   (2)  sub-cell (x, y) offset of the object centre, in cells
  mag   (1)  star magnitude (used to sort stars brightest-first for tetra3)
  glare (1)  glare-region logit
"""
import torch
import torch.nn as nn
import torch.nn.functional as F

STRIDE = 4


def cbr(i, o, k=3, s=1, d=1):
    return nn.Sequential(nn.Conv2d(i, o, k, s, padding=d * (k // 2), dilation=d, bias=False),
                         nn.BatchNorm2d(o), nn.ReLU(inplace=True))


class StarNet(nn.Module):
    def __init__(self, c=(16, 32, 64, 96)):
        super().__init__()
        c1, c2, c3, c4 = c
        self.s2 = cbr(1, c1, 5, 2)                                                   # /2
        self.s4 = nn.Sequential(cbr(c1, c2, 3, 2), cbr(c2, c2))                     # /4
        self.s8 = nn.Sequential(cbr(c2, c3, 3, 2), cbr(c3, c3), cbr(c3, c3, d=2))   # /8
        self.s16 = nn.Sequential(cbr(c3, c4, 3, 2), cbr(c4, c4, d=2), cbr(c4, c4, d=4))  # /16
        self.up8 = cbr(c4 + c3, c3)
        self.up4 = cbr(c3 + c2, c2)
        self.head = cbr(c2, c2)
        self.heat = nn.Conv2d(c2, 3, 1)
        self.off = nn.Conv2d(c2, 2, 1)
        self.mag = nn.Conv2d(c2, 1, 1)
        self.glare = nn.Conv2d(c2, 1, 1)
        nn.init.constant_(self.heat.bias, -4.6)  # prior ~1% foreground (CenterNet trick)

    def forward(self, x):
        """x: (B, 1, H, W) float in [0, 1]; H, W multiples of 16."""
        f2 = self.s2(x)
        f4 = self.s4(f2)
        f8 = self.s8(f4)
        f16 = self.s16(f8)
        u8 = self.up8(torch.cat([F.interpolate(f16, size=f8.shape[-2:], mode="bilinear", align_corners=False), f8], 1))
        u4 = self.up4(torch.cat([F.interpolate(u8, size=f4.shape[-2:], mode="bilinear", align_corners=False), f4], 1))
        h = self.head(u4)
        return dict(heat=self.heat(h), off=torch.sigmoid(self.off(h)), mag=self.mag(h) + 4.0, glare=self.glare(h))


def focal_loss(logits, target, ignore=None, alpha=2, beta=4):
    """CenterNet penalty-reduced focal loss. target in [0, 1], ==1 at centres."""
    p = torch.sigmoid(logits).clamp(1e-4, 1 - 1e-4)
    pos = target.eq(1).float()
    neg = 1 - pos
    if ignore is not None:
        neg = neg * (1 - ignore)
    pl = -torch.log(p) * (1 - p) ** alpha * pos
    nl = -torch.log(1 - p) * p ** alpha * (1 - target) ** beta * neg
    return (pl.sum() + nl.sum()) / pos.sum().clamp(min=1)


def loss_fn(out, t):
    ign = torch.zeros_like(t["heat"])
    ign[:, 0] = t["ignore"]
    l_heat = focal_loss(out["heat"], t["heat"], ign)
    reg = t["reg"]
    n = reg.sum().clamp(min=1)
    l_off = (torch.abs(out["off"] - t["off"]).sum(1) * reg).sum() / n
    star_reg = reg * t["heat"][:, 0].eq(1).float()
    l_mag = (torch.abs(out["mag"][:, 0] - t["mag"]) * star_reg).sum() / star_reg.sum().clamp(min=1)
    l_glare = F.binary_cross_entropy_with_logits(out["glare"][:, 0], t["glare"])
    total = l_heat + 1.0 * l_off + 0.1 * l_mag + 0.5 * l_glare
    return total, dict(heat=l_heat.item(), off=l_off.item(), mag=l_mag.item(), glare=l_glare.item())
