from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import psutil
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
RUNTIME = Path(os.environ["BRANDAI_RUNTIME_ROOT"])
LOG_DIR = Path(os.environ["BRANDAI_LOG_DIR"])
GENERATED_DIR = Path(os.environ["BRANDAI_GENERATED_DIR"])
LOG_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def gpu_snapshot() -> dict:
    command = [
        "nvidia-smi",
        "--query-gpu=timestamp,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    raw = subprocess.check_output(command, text=True, encoding="utf-8").strip()
    timestamp, name, driver, total, used, free, util = [part.strip() for part in raw.split(",")]
    return {
        "timestamp": timestamp,
        "name": name,
        "driver": driver,
        "total_mib": int(total),
        "used_mib": int(used),
        "free_mib": int(free),
        "utilization_percent": int(util),
    }


class ResourceSampler:
    def __init__(self, pid: int | None = None, interval: float = 0.25):
        self.pid = pid or os.getpid()
        self.interval = interval
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        self._thread.join(timeout=5)
        if not self.samples:
            return {}
        return {
            "peak_gpu_used_mib": max(s["gpu"]["used_mib"] for s in self.samples),
            "min_gpu_free_mib": min(s["gpu"]["free_mib"] for s in self.samples),
            "peak_process_rss_mib": max(s["process_rss_mib"] for s in self.samples),
            "sample_count": len(self.samples),
            "first_gpu": self.samples[0]["gpu"],
            "last_gpu": self.samples[-1]["gpu"],
        }

    def _run(self):
        process = psutil.Process(self.pid)
        while not self._stop.is_set():
            try:
                rss = process.memory_info().rss / 1024 / 1024
                for child in process.children(recursive=True):
                    try:
                        rss += child.memory_info().rss / 1024 / 1024
                    except psutil.Error:
                        pass
                self.samples.append({"at": time.time(), "gpu": gpu_snapshot(), "process_rss_mib": rss})
            except (psutil.Error, subprocess.SubprocessError):
                pass
            self._stop.wait(self.interval)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

