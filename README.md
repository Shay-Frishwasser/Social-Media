# BrandAI local POC

This repository contains Milestones 0–4: license/hardware gates, isolated storage and provenance, evidence-grounded onboarding, and the initial FastAPI + React approval workspace.

All heavy and mutable state lives outside OneDrive under `C:\brandai-runtime` and is configured through `.env`.

## Benchmark entry points

- `benchmarks/run_llm_benchmark.py`: Hebrew/English copy and grounded JSON extraction against a native `llama.cpp` server.
- `benchmarks/run_image_benchmark.py`: FLUX.2 Klein 4B generation in an isolated process with GPU/RAM sampling.
- `benchmarks/compose_asset.py`: deterministic Chromium composition with RTL text, an actual SVG asset, and locally bundled fonts.
- `benchmarks/check_vram.py`: NVML/`nvidia-smi` cleanup gate used between processes.

Results are written to `C:\brandai-runtime\logs` and media to `C:\brandai-runtime\generated`.

## Milestone 2 storage

- `C:\brandai-runtime\system.db` is the global coordination registry.
- `C:\brandai-runtime\brands\<brand-uuid>\brand.db` is the only location for a brand's knowledge, claims, profiles, designs, content, and memory.
- `C:\brandai-runtime\brands\<brand-uuid>\assets` is the enforced asset boundary for that brand.
- Alembic has independent global and brand migration histories.
- Immutable profile, design-system, and content-revision rows are protected by SQLite triggers as well as repository APIs.

Initialize the runtime schema and copy/register the six playbooks:

```powershell
& 'C:\brandai-runtime\.venv\Scripts\python.exe' scripts\init_milestone2.py
```

Run verification:

```powershell
& 'C:\brandai-runtime\.venv\Scripts\python.exe' -m pytest -q
```

## Milestone 3 onboarding

The onboarding package provides a robots-aware/rate-limited website crawler, conditional Playwright rendering, best-effort public search, safe user-material ingestion, CPU E5 embeddings, grounded Qwen extraction, CSS/logo/font signals, and deterministic provenance validation. Observed claims are rejected unless their quoted span is present verbatim in the referenced source; inferred claims cannot carry a source or quote.

The real-site smoke test uses a disposable database under `C:\brandai-runtime\smoke`, verifies GPU allocation and cleanup, writes its report to `C:\brandai-runtime\logs\milestone3-real-site-smoke.json`, and removes the disposable brand state:

```powershell
& 'C:\brandai-runtime\.venv\Scripts\python.exe' scripts\run_milestone3_smoke.py
```

## Milestone 4 workspace

Build the Hebrew-first React application and start the local API:

```powershell
cd web
npm install
npm run build
cd ..
& 'C:\brandai-runtime\.venv\Scripts\python.exe' -m uvicorn brandai.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The workspace supports claim/source review, user overrides, knowledge-gap answers, immutable design-system approval, secure image uploads, and on-demand photo briefs. Read [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) before changing architecture.

