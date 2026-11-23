"""Private, single-process EC2 inference gateway. The API host owns the live databases."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import Field

from brandai.onboarding.visual import normalize_hex
from brandai.strategy.models import Model
from brandai.visual_studio.providers import pixel_art_direction

ROOT = Path(os.environ.get("BRANDAI_WORKER_ROOT", "/opt/brandai-worker"))
STATE = Path(os.environ.get("BRANDAI_WORKER_STATE", "/var/lib/brandai-worker"))
MODEL = ROOT / "models/llm/Qwen_Qwen3.5-9B-Q8_0.gguf"
LOCAL_LLM = "http://127.0.0.1:18080"
MAX_BYTES = 25 * 1024 * 1024
LOCK = Lock()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Background(Model):
    size: tuple[int, int]
    palette: tuple[str, str, str]
    composition: Literal["centered", "copy_space_start", "copy_space_end", "wide_editorial"]
    art_direction: str = Field(max_length=1200)
    seed: int = Field(ge=0, le=2**32 - 1)
    text_free: Literal[True]
    negative_prompt: str = Field(max_length=400)


def _free_vram() -> int:
    output = subprocess.check_output(
        ["nvidia-smi", "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], text=True, timeout=8,
    )
    return int(output.strip())


@contextmanager
def _gpu():
    with LOCK:
        before = _free_vram()
        if before < 24_000:
            raise HTTPException(503, "worker has insufficient free VRAM")
        try:
            yield
        finally:
            deadline = time.monotonic() + 40
            while _free_vram() < before - 384:
                if time.monotonic() >= deadline:
                    raise HTTPException(503, "worker VRAM did not recover")
                time.sleep(1)


@contextmanager
def _spawn(arguments: list[str]):
    with subprocess.Popen(arguments, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(10)


@app.get("/health")
def health():
    try:
        if not MODEL.is_file() or not (ROOT / "models/flux2-klein-4b/model_index.json").is_file() or _free_vram() <= 0:
            raise RuntimeError
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
        raise HTTPException(503, "worker models or CUDA are unavailable") from None
    return {"ready": True}


@app.post("/v1/chat/completions")
def llm(payload: dict):
    arguments = [
        str(ROOT / "bin/llama-server"), "-m", str(MODEL), "--host", "127.0.0.1", "--port", "18080",
        "-ngl", "99", "-c", "8192", "--parallel", "1", "--jinja", "--no-webui",
        "--reasoning-format", "deepseek", "--chat-template-kwargs", '{"enable_thinking":false}',
    ]
    if payload.get("stream") or len(json.dumps(payload).encode()) > 96_000:
        raise HTTPException(422, "worker expects a bounded structured request")
    with _gpu(), _spawn(arguments) as process, httpx.Client(trust_env=False) as client:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and process.poll() is None:
            try:
                if client.get(f"{LOCAL_LLM}/health", timeout=2).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.5)
        else:
            raise HTTPException(503, "llama.cpp failed to start")
        result = client.post(f"{LOCAL_LLM}/v1/chat/completions", json=payload, timeout=600)
        return Response(result.content, status_code=result.status_code, media_type="application/json")


def _pixels(capability: str, source: Path, destination: Path, *arguments: str) -> Response:
    executable = [sys.executable, str(ROOT / "pixel_runner.py"), capability, str(source), str(destination), *arguments]
    with _gpu(), _spawn(executable) as child:
        if child.wait(600) != 0 or not destination.is_file() or destination.stat().st_size > MAX_BYTES:
            raise HTTPException(503, "pixel worker failed")
    return Response(destination.read_bytes(), media_type="image/png")


@app.post("/worker/background")
def background(payload: Background):
    if not all(256 <= dimension <= 2048 for dimension in payload.size):
        raise HTTPException(422, "unsupported background dimensions")
    with TemporaryDirectory(dir=STATE) as temp:
        request = Path(temp) / "request.json"
        try:
            palette = [normalize_hex(color) for color in payload.palette]
        except ValueError:
            raise HTTPException(422, "invalid color roles") from None
        request.write_text(json.dumps({**payload.model_dump(), "palette": palette,
                                       "art_direction": pixel_art_direction(payload.art_direction)}), encoding="utf-8")
        return _pixels("flux", request, Path(temp) / "background.png")


@app.post("/worker/segment")
def segment(photo: UploadFile = File(...),
            variant: Literal["sam2.1_hiera_tiny", "sam2.1_hiera_small"] = Form(...)):
    with TemporaryDirectory(dir=STATE) as temp:
        source = Path(temp) / "photo.png"
        data = photo.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(422, "worker expects a bounded PNG photo")
        source.write_bytes(data)
        return _pixels("sam2", source, Path(temp) / "foreground.png", "--variant", variant)
