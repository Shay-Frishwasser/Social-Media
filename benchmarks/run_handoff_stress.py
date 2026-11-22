from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from common import LOG_DIR, ROOT, gpu_snapshot, write_json

parser = argparse.ArgumentParser()
parser.add_argument("--cycles", type=int, default=3)
parser.add_argument("--minimum-free-mib", type=int, default=12000)
args = parser.parse_args()

python = sys.executable
server = Path(os.environ["BRANDAI_LLAMACPP_DIR"]) / "llama-server.exe"
qwen = Path(os.environ["BRANDAI_LLM_DIR"]) / "qwen3.5-9b-q8" / "Qwen_Qwen3.5-9B-Q8_0.gguf"
flux = Path(os.environ["BRANDAI_IMAGE_DIR"]) / "flux2-klein-4b"
env = os.environ.copy()
env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})


def cleanup_gate(timeout: int = 120) -> dict:
    deadline = time.time() + timeout
    samples = []
    while time.time() < deadline:
        snap = gpu_snapshot()
        samples.append(snap)
        if snap["free_mib"] >= args.minimum_free_mib:
            return {"passed": True, "snapshot": snap, "samples": len(samples)}
        time.sleep(1)
    return {"passed": False, "snapshot": samples[-1], "samples": len(samples)}


def run_stage(label: str, command: list[str]) -> dict:
    before = gpu_snapshot()
    started = time.time()
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    gate = cleanup_gate()
    stage = {
        "label": label, "returncode": result.returncode, "wall_seconds": time.time() - started,
        "before": before, "cleanup": gate, "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-2000:],
    }
    if result.returncode != 0 or not gate["passed"]:
        write_json(LOG_DIR / "handoff-stress.json", {"passed": False, "stages": stages + [stage]})
        raise RuntimeError(f"stage failed: {label}")
    return stage


stages = []
for cycle in range(1, args.cycles + 1):
    for position, case_id in (("before-image", "he_plural_mixed_brand"), ("after-image", "he_formal_b2b")):
        if position == "after-image":
            stages.append(run_stage(
                f"cycle-{cycle}-image",
                [python, str(ROOT / "benchmarks" / "run_image_benchmark.py"), "--model", str(flux),
                 "--width", "512", "--height", "512", "--steps", "4"],
            ))
        stages.append(run_stage(
            f"cycle-{cycle}-llm-{position}",
            [python, str(ROOT / "benchmarks" / "run_llm_benchmark.py"), "--name", f"stress-qwen-{cycle}-{position}",
             "--server", str(server), "--model", str(qwen), "--gpu-layers", "99", "--context", "2048",
             "--port", str(18100 + cycle * 2 + (1 if position == "after-image" else 0)),
             "--case", case_id, "--max-tokens", "300"],
        ))

payload = {
    "passed": all(stage["returncode"] == 0 and stage["cleanup"]["passed"] for stage in stages),
    "cycles": args.cycles, "minimum_free_mib": args.minimum_free_mib, "stages": stages,
}
write_json(LOG_DIR / "handoff-stress.json", payload)
print(LOG_DIR / "handoff-stress.json")
