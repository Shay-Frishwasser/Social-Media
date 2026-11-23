#!/usr/bin/env bash
set -euo pipefail
umask 027

# Run deliberately on the provisioned Linux image builder. All inputs are staged offline.
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 && "$EUID" -eq 0 ]] || {
  echo "Run as root on the Linux x86_64 NVIDIA AMI builder." >&2
  exit 2
}
stage="$(realpath "${1:?usage: ami_bootstrap.sh /path/to/checksummed-stage}")"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
target=/opt/brandai-worker
state=/var/lib/brandai-worker
port="${BRANDAI_AWS_WORKER_PORT:-8080}"
max_age="${BRANDAI_AWS_HARD_SHUTDOWN_SECONDS:-4800}"
[[ "$port" =~ ^[0-9]{4,5}$ && "$max_age" =~ ^[0-9]{4,5}$ ]] || exit 2
(( 10#$port >= 1024 && 10#$port <= 65535 && 10#$max_age >= 1200 && 10#$max_age <= 8400 )) || exit 2
command -v nvidia-smi >/dev/null
command -v python3.12 >/dev/null
command -v sha256sum >/dev/null
[[ ! -e "$target" ]] || { echo "Bake into a clean base image." >&2; exit 2; }

# The stage manifest locks every staged byte and must come from the trusted build pipeline.
python3.12 - "$stage" "$repo_root" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

stage, repo = map(Path, sys.argv[1:])
entries = {}
manifest = stage / "SHA256SUMS"
for line in manifest.read_text().splitlines():
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_./+\[\],-]+)", line)
    if not match:
        raise SystemExit("Malformed hash manifest")
    digest, name = match.groups()
    path = stage / name
    if name in entries or path.resolve() != path or not path.is_relative_to(stage) or not path.is_file():
        raise SystemExit("Invalid or duplicate stage path")
    entries[name] = digest
actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file() and p != manifest}
if set(entries) != actual or any(p.is_symlink() for p in stage.rglob("*")):
    raise SystemExit("Stage file set differs from the lock")
required = {
    "requirements.lock", "revisions.json", "bin/llama-server",
    "models/llm/Qwen_Qwen3.5-9B-Q8_0.gguf", "models/flux2-klein-4b/model_index.json",
    "models/multilingual-e5-base/config.json", "models/sam2/sam2.1_hiera_tiny.pt",
    "models/sam2/sam2.1_hiera_small.pt",
}
if not required <= entries.keys() or not any(key.startswith("playwright/chromium_headless_shell-") for key in entries):
    raise SystemExit("Missing pinned runtime artifacts")
for name, expected in entries.items():
    with (stage / name).open("rb") as artifact:
        if hashlib.file_digest(artifact, "sha256").hexdigest() != expected:
            raise SystemExit(f"Artifact hash mismatch: {name}")
font_manifest = json.loads((repo / "licenses/font-manifest.json").read_text())
for font in font_manifest["fonts"]:
    name = "fonts/" + font["file"]
    if entries.get(name) != font["sha256"] or (Path(name).parent / "OFL.txt").as_posix() not in entries:
        raise SystemExit("Font or OFL license differs from the repository manifest")
revision_data = json.loads((stage / "revisions.json").read_text())
model_manifest = json.loads((repo / "licenses/model-manifest.json").read_text())
for model in model_manifest["selected"]:
    if model["role"] in {"general_llm", "image"} or model["repo"] == "intfloat/multilingual-e5-base":
        repo_name = model.get("artifact_repo", model["repo"])
        pin = model.get("artifact_revision", model["revision"])
        if revision_data.get(repo_name) != pin:
            raise SystemExit(f"Unexpected model revision: {repo_name}")
for key in ("sam2", "llama.cpp"):
    if not re.fullmatch(r"[0-9a-f]{40}", revision_data.get(key, "")):
        raise SystemExit(f"Pin the exact {key} source commit")
PY

id brandai-worker >/dev/null 2>&1 || useradd --system --shell /usr/sbin/nologin brandai-worker
install -d "$target" "$target/bin" "$target/src"
install -d -o brandai-worker -g brandai-worker -m 0750 "$state"
install -m 0755 "$stage/bin/llama-server" "$target/bin/llama-server"
cp -a -- "$stage/models" "$stage/fonts" "$stage/playwright" "$target/"
cp -- "$stage/revisions.json" "$stage/SHA256SUMS" "$target/"
cp -R -- "$repo_root/src/brandai" "$target/src/"
install -m 0644 "$script_dir/worker_gateway.py" "$script_dir/pixel_runner.py" "$target/"
python3.12 -m venv "$target/.venv"
"$target/.venv/bin/python" -m pip install --no-index --find-links "$stage/wheels" --require-hashes -r "$stage/requirements.lock"
"$target/.venv/bin/python" -m pip check
chown -R root:brandai-worker "$target"
chmod -R g+rX "$target"

cat > /etc/systemd/system/brandai-worker.service <<EOF_UNIT
[Unit]
Description=BrandAI private GPU worker
After=network-online.target
[Service]
User=brandai-worker
Group=brandai-worker
WorkingDirectory=$target
Environment=BRANDAI_WORKER_ROOT=$target
Environment=BRANDAI_WORKER_STATE=$state
Environment=PYTHONPATH=$target/src
Environment=PLAYWRIGHT_BROWSERS_PATH=$target/playwright
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=HOME=$state
ExecStart=$target/.venv/bin/python -m uvicorn worker_gateway:app --host 0.0.0.0 --port $port --workers 1 --no-access-log
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true
ReadWritePaths=$state
[Install]
WantedBy=multi-user.target
EOF_UNIT
cat > /etc/systemd/system/brandai-worker-max-age.timer <<EOF_UNIT
[Timer]
OnBootSec=${max_age}s
AccuracySec=5s
Unit=brandai-worker-poweroff.service
[Install]
WantedBy=timers.target
EOF_UNIT
cat > /etc/systemd/system/brandai-worker-poweroff.service <<'EOF_UNIT'
[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl poweroff
EOF_UNIT
systemctl daemon-reload
systemctl enable brandai-worker.service brandai-worker-max-age.timer
echo "Worker staged. Capture the AMI with the service and watchdog enabled for its next boot."
