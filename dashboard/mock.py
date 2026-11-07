"""Standalone mock feeds over real UDP, to exercise the dashboard's network path.

  .venv/bin/python -m dashboard.mock                 truth -> 5005 (30 Hz), estimates -> 5008 (10 Hz)
  .venv/bin/python -m dashboard.mock --no-truth      estimates only (dashboard should show truth "no signal")
  .venv/bin/python -m dashboard.mock --no-est        truth only

To pair mock estimates with a real sim.live, use `python -m dashboard --mock-est` instead
(only one process can listen on 5005).
"""
import argparse
import json
import socket
import time

from dashboard.feeds import MockEstimator, MockTruth


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--truth-port", type=int, default=5005)
    ap.add_argument("--est-port", type=int, default=5008)
    ap.add_argument("--no-truth", action="store_true")
    ap.add_argument("--no-est", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mt, me = MockTruth(seed=a.seed), MockEstimator(a.seed)
    n = 0
    print(f"mock: truth {'off' if a.no_truth else a.truth_port}, estimates {'off' if a.no_est else a.est_port}")
    while True:
        tr = mt.step()
        if not a.no_truth:
            out.sendto(json.dumps(tr).encode(), ("127.0.0.1", a.truth_port))
        if not a.no_est and n % 3 == 0:
            out.sendto(json.dumps(me.make(tr)).encode(), ("127.0.0.1", a.est_port))
        n += 1
        time.sleep(1 / 30)


if __name__ == "__main__":
    main()
