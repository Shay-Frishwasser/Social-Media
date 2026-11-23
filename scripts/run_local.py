"""Start the loopback API, local job queue, and optional Vite development server."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def wait_for_api(process: subprocess.Popen) -> bool:
    deadline = time.monotonic() + 30
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:8000/api/health", timeout=0.5) as response:
                if response.status == 200:
                    return process.poll() is None
        except (URLError, OSError):
            pass
        time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Start Vite with hot reload at port 5173.")
    parser.add_argument("--provider", choices=("mock", "local"), default="mock",
                        help="Use the Windows GPU text provider or deterministic local providers.")
    parser.add_argument("--offline", action="store_true", help="Disable public onboarding and topic research.")
    options = parser.parse_args()
    if options.provider == "local" and os.name != "nt":
        parser.error("the native GPU text provider requires Windows")
    root = Path(__file__).resolve().parents[1]
    web = root / "web"
    onboarding = "0" if options.offline else "1" if options.provider == "local" else "deterministic"
    environment = dict(os.environ, BRANDAI_CONTENT_PROVIDER=options.provider,
                       BRANDAI_TOPIC_RESEARCH="offline" if options.offline else "live",
                       BRANDAI_AUTOMATED_ONBOARDING=onboarding,
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root / "src"), environment.get("PYTHONPATH"))))
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    node = shutil.which("node")
    if not npm or not node:
        parser.error("Node.js and npm are required to build and serve the frontend")
    if not (web / "node_modules" / "vite").is_dir():
        subprocess.run([npm, "ci"], cwd=web, check=True, env=environment)
    if not options.dev:
        subprocess.run([npm, "run", "build"], cwd=web, check=True, env=environment)
    processes: list[subprocess.Popen] = []
    try:
        api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "brandai.api.app:create_app", "--factory",
             "--host", "127.0.0.1", "--port", "8000"], cwd=root, env=environment,
        )
        processes.append(api)
        if not wait_for_api(api):
            print("BrandAI API did not become ready.", file=sys.stderr)
            return api.returncode or 1
        processes.append(subprocess.Popen([sys.executable, "-m", "brandai.jobs.cli"], cwd=root, env=environment))
        if options.dev:
            vite = web / "node_modules" / "vite" / "bin" / "vite.js"
            processes.append(subprocess.Popen([node, str(vite)], cwd=web, env=environment))
        port = 5173 if options.dev else 8000
        print(f"BrandAI is starting at http://127.0.0.1:{port}", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.25)
        return next(process.returncode or 1 for process in processes if process.returncode is not None)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
