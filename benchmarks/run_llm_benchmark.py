from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import LOG_DIR, ResourceSampler, gpu_snapshot, write_json

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "benchmarks" / "llm_cases.json").read_text(encoding="utf-8"))


def request_json(url: str, payload: dict | None = None, timeout: int = 1800) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_ready(port: int, process: subprocess.Popen, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"llama-server exited with code {process.returncode}")
        try:
            request_json(f"http://127.0.0.1:{port}/health", timeout=3)
            return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise TimeoutError("llama-server did not become healthy")


parser = argparse.ArgumentParser()
parser.add_argument("--name", required=True)
parser.add_argument("--server", required=True, type=Path)
parser.add_argument("--model", required=True, type=Path)
parser.add_argument("--gpu-layers", required=True, type=int)
parser.add_argument("--context", type=int, default=4096)
parser.add_argument("--port", type=int, default=18080)
parser.add_argument("--case", action="append", dest="case_ids")
parser.add_argument("--reasoning-format", default="deepseek")
parser.add_argument("--max-tokens", type=int, default=700)
args = parser.parse_args()

selected = CASES if not args.case_ids else [case for case in CASES if case["id"] in args.case_ids]
server_log = LOG_DIR / f"{args.name}-server.log"
command = [
    str(args.server), "--model", str(args.model), "--host", "127.0.0.1", "--port", str(args.port),
    "--ctx-size", str(args.context), "--n-gpu-layers", str(args.gpu_layers), "--parallel", "1",
    "--no-webui", "--metrics", "--jinja", "--reasoning-format", args.reasoning_format,
    "--chat-template-kwargs", '{"enable_thinking":false}',
]
started_at = time.time()
before = gpu_snapshot()
with server_log.open("w", encoding="utf-8") as log:
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True)
    sampler = ResourceSampler(process.pid).start()
    results = []
    try:
        wait_ready(args.port, process)
        load_seconds = time.time() - started_at
        for case in selected:
            payload = {
                "model": args.name,
                "messages": [
                    {"role": "system", "content": case["system"]},
                    {"role": "user", "content": case["prompt"]},
                ],
                "temperature": 0.35,
                "top_p": 0.9,
                "max_tokens": args.max_tokens,
                "seed": 20261122,
                "stream": False,
            }
            if "json_schema" in case:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "grounded_claims", "strict": True, "schema": case["json_schema"]},
                }
            case_started = time.time()
            response = request_json(f"http://127.0.0.1:{args.port}/v1/chat/completions", payload)
            elapsed = time.time() - case_started
            usage = response.get("usage", {})
            message = response["choices"][0]["message"]
            output = message.get("content", "")
            reasoning = message.get("reasoning_content")
            completion_tokens = usage.get("completion_tokens")
            json_valid = None
            if "json_schema" in case:
                try:
                    json.loads(output)
                    json_valid = True
                except json.JSONDecodeError:
                    json_valid = False
            results.append({
                "case_id": case["id"], "elapsed_seconds": elapsed, "usage": usage,
                "tokens_per_second": (completion_tokens / elapsed) if completion_tokens else None,
                "json_valid": json_valid, "reasoning_content": reasoning, "output": output,
            })
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
        resources = sampler.stop()

payload = {
    "model_name": args.name, "model_path": str(args.model), "gpu_layers": args.gpu_layers,
    "context": args.context, "load_seconds": load_seconds, "started_gpu": before,
    "ended_gpu": gpu_snapshot(), "resources": resources, "results": results,
}
output_path = LOG_DIR / f"{args.name}-benchmark.json"
write_json(output_path, payload)
print(output_path)

