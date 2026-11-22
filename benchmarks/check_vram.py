from __future__ import annotations

import argparse
import json
import time

from common import gpu_snapshot

parser = argparse.ArgumentParser()
parser.add_argument("--minimum-free-mib", type=int, default=12000)
parser.add_argument("--timeout-seconds", type=int, default=90)
args = parser.parse_args()

deadline = time.time() + args.timeout_seconds
history = []
while True:
    snapshot = gpu_snapshot()
    history.append(snapshot)
    if snapshot["free_mib"] >= args.minimum_free_mib:
        print(json.dumps({"status": "ready", "snapshot": snapshot, "samples": len(history)}))
        raise SystemExit(0)
    if time.time() >= deadline:
        print(json.dumps({"status": "timeout", "snapshot": snapshot, "samples": len(history)}))
        raise SystemExit(2)
    time.sleep(1)

