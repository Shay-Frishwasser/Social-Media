# BrandAI engineering contract

This file is binding context for Codex and other coding agents working in this repository.

## Product invariants

- BrandAI is a multi-company AI Social Media and Brand Manager. Every company has a physically isolated brand database and asset directory.
- AI inference, embeddings, brand memory, generated media, and live SQLite data run locally. Never introduce cloud AI APIs or cloud storage into domain logic.
- Internet access is allowed only for public brand research, transactional email, and future official social publishing connectors.
- Generated content always passes deterministic grounding/format checks and human approval. Never present LLM self-review as a trusted approval gate.
- User edits outrank observed claims; approved observed claims outrank inferences. Observed claims require a source ID and verbatim quoted span.
- Do not mutate immutable brand-profile, design-system, or content-revision rows. Append a new version.

## Repository/runtime split

- Source lives in this Git repository. The current Windows checkout may be inside OneDrive.
- Heavy or mutable state lives under `C:\brandai-runtime` by default, configurable with `BRANDAI_RUNTIME_ROOT`.
- Never place `.venv`, model weights, Hugging Face/Torch/Playwright caches, generated media, or SQLite files in the repository.
- Global DB: `C:\brandai-runtime\system.db`.
- Brand DB: `C:\brandai-runtime\brands\<brand-uuid>\brand.db`.
- Brand assets: `C:\brandai-runtime\brands\<brand-uuid>\assets`.

## Approved local AI stack

- LLM: `Qwen3.5-9B Q8_0` via native Windows CUDA `llama.cpp`, thinking disabled for structured extraction.
- Embeddings: `intfloat/multilingual-e5-base` on CPU with mandatory `passage:` and `query:` prefixes.
- Images: `FLUX.2 Klein 4B`, isolated Diffusers subprocess.
- Typography/composition: Playwright Chromium with Heebo, Assistant, Rubik, Noto Sans Hebrew, and Inter (OFL).
- Foreground segmentation: adapter exists for BiRefNet, but the checkpoint stays disabled until commercial training-dataset provenance is formally accepted. Do not bypass this guard.
- GPU work must go through `GPUWorkerProvider`/`SingleGPUController`, verify free VRAM with `nvidia-smi`, and verify cleanup after process termination.

## Storage and security rules

- A brand repository receives only that brand's SQLAlchemy session and asset root. Never attach or query another brand DB.
- User uploads are limited to safe image types, 25 MiB, validated dimensions, and sanitized SVG without active/external content.
- On-demand photo briefs are transient campaign records. They must not modify the permanent brand profile or approved design system.
- Social publishing uses the `SocialPublisher` adapter. Do not put Meta/LinkedIn OAuth code into domain services.
- Keep local API bindings on loopback by default. Secure-tunnel/server exposure is a separate deployment milestone.

## Development commands (Windows PowerShell)

```powershell
uv pip install --python 'C:\brandai-runtime\.venv\Scripts\python.exe' -e '.[dev]'
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'
& 'C:\brandai-runtime\.venv\Scripts\python.exe' -m pytest -q
cd web; npm install; npm run build
& 'C:\brandai-runtime\.venv\Scripts\python.exe' -m uvicorn brandai.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Run Alembic through `BrandAIStore`; global and per-brand migration histories are independent. Tests must use `tmp_path` runtime roots and must not leave production brand rows.

## Current milestone state

Milestones 0–4 are implemented. Read `docs/PROJECT_STATE.md` before changing architecture or milestone boundaries. Do not start Milestone 5 without explicit approval.
