from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import psutil

from brandai.adapters.workers import WorkerLease


def gpu_snapshot() -> dict:
    raw = subprocess.check_output([
        "nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits"], text=True, encoding="utf-8").strip().splitlines()[0]
    name, total, used, free, util = [part.strip() for part in raw.split(",")]
    return {"name": name, "total_mib": int(total), "used_mib": int(used),
            "free_mib": int(free), "utilization_percent": int(util), "captured_at": time.time()}


@dataclass(slots=True)
class GPUJobMetrics:
    before: dict
    after_start: dict | None = None
    after_stop: dict | None = None
    startup_seconds: float | None = None
    cleanup_seconds: float | None = None


class SingleGPUController:
    def __init__(self, *, server_exe: Path, model: Path, log_dir: Path, port: int = 18080,
                 minimum_free_mib: int = 11_000, cleanup_tolerance_mib: int = 384):
        self.server_exe, self.model, self.log_dir = server_exe, model, log_dir
        self.port, self.minimum_free_mib = port, minimum_free_mib
        self.cleanup_tolerance_mib = cleanup_tolerance_mib
        self.process: subprocess.Popen | None = None
        self.last_metrics: GPUJobMetrics | None = None

    @contextmanager
    def llama_server(self):
        metrics = self.start()
        try:
            yield f"http://127.0.0.1:{self.port}", metrics
        finally:
            self.stop(metrics)

    def start(self) -> GPUJobMetrics:
        if self.process and self.process.poll() is None: raise RuntimeError("GPU controller already owns a process")
        before = gpu_snapshot()
        if before["free_mib"] < self.minimum_free_mib:
            raise RuntimeError(f"insufficient free VRAM: {before['free_mib']} MiB < {self.minimum_free_mib} MiB")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = open(self.log_dir / "llama-onboarding.log", "a", encoding="utf-8")
        started = time.monotonic()
        command = [str(self.server_exe), "-m", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
                   "-ngl", "99", "-c", "8192", "--parallel", "1", "--jinja", "--no-webui",
                   "--reasoning-format", "deepseek", "--chat-template-kwargs", '{"enable_thinking":false}']
        self.process = subprocess.Popen(command, cwd=self.server_exe.parent, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self.process.poll() is not None: raise RuntimeError("llama.cpp exited during startup")
                try:
                    if httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2).status_code == 200: break
                except httpx.HTTPError: pass
                time.sleep(.5)
            else: raise TimeoutError("llama.cpp health check timed out")
        except Exception:
            self._terminate(); log.close(); raise
        metrics = GPUJobMetrics(before=before, after_start=gpu_snapshot(), startup_seconds=time.monotonic() - started)
        self.last_metrics = metrics
        log.close()
        return metrics

    def stop(self, metrics: GPUJobMetrics | None = None) -> None:
        started = time.monotonic()
        baseline = (metrics or self.last_metrics).before if (metrics or self.last_metrics) else gpu_snapshot()
        self._terminate()
        deadline = time.monotonic() + 60
        current = gpu_snapshot()
        while current["free_mib"] < baseline["free_mib"] - self.cleanup_tolerance_mib and time.monotonic() < deadline:
            time.sleep(1); current = gpu_snapshot()
        if current["free_mib"] < baseline["free_mib"] - self.cleanup_tolerance_mib:
            raise RuntimeError(f"VRAM did not recover: before={baseline['free_mib']} MiB after={current['free_mib']} MiB")
        if metrics:
            metrics.after_stop = current
            metrics.cleanup_seconds = time.monotonic() - started

    def _terminate(self) -> None:
        if not self.process: return
        try:
            root = psutil.Process(self.process.pid)
            children = root.children(recursive=True)
            for process in children: process.terminate()
            root.terminate()
            _, alive = psutil.wait_procs([root, *children], timeout=10)
            for process in alive: process.kill()
        except psutil.NoSuchProcess: pass
        finally:
            self.process = None


class LocalLlamaGPUWorker:
    def __init__(self, controller: SingleGPUController): self.controller = controller

    @contextmanager
    def lease(self, capability: str):
        if capability != "llm": raise ValueError(f"local llama worker does not provide {capability}")
        with self.controller.llama_server() as (base_url, _metrics):
            yield WorkerLease(base_url=base_url, worker_id="local-rtx4060ti", capabilities=frozenset({"llm"}))
