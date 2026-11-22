# BrandAI project state — 2026-11-22

## Product and architecture

BrandAI is a local-AI, web-based Social Media and Brand Manager supporting Instagram, TikTok, LinkedIn, Facebook, YouTube, and X in Hebrew and English. The Phase 1 application runs on localhost/LAN. It creates ready-to-publish packages for human delivery rather than publishing automatically.

The React UI and FastAPI API are stateless application layers over:

- `C:\brandai-runtime\system.db`: users, brand registry, jobs, global reminders, versioned platform playbooks.
- `C:\brandai-runtime\brands\<uuid>\brand.db`: sources/chunks, claims, immutable profile/design versions, assets, strategies, cadence, content revisions, post memory, delivery records, feedback, and on-demand photo briefs.
- `C:\brandai-runtime\brands\<uuid>\assets`: brand-contained uploads and media.

SQLite uses WAL, foreign keys, FTS5 trigram indexing, float32 embedding BLOBs, and NumPy cosine similarity. Profiles, design systems, and content-package revisions have database triggers preventing update/delete.

## Models and licenses

| Role | Model/runtime | License state |
|---|---|---|
| Hebrew/English LLM | Qwen3.5-9B Q8_0 GGUF, llama.cpp CUDA | Apache-2.0; pinned in `licenses/model-manifest.json` |
| Embeddings | intfloat/multilingual-e5-base, CPU | MIT; pinned |
| Image generation | FLUX.2 Klein 4B, Diffusers subprocess | Apache-2.0; pinned |
| Fonts | Heebo, Assistant, Rubik, Noto Sans Hebrew, Inter | OFL 1.1; hashes in `licenses/font-manifest.json` |
| Cutout candidate | ZhengPeng7/BiRefNet | Code/model card MIT, but upstream training-dataset provenance has commercial ambiguity; adapter disabled pending sign-off |

## Completed milestones

### Milestone 0 — environment/license verification

Windows 11 host, RTX 4060 Ti 16 GB, 32 GB RAM, Python 3.12 runtime, native llama.cpp CUDA, isolated Diffusers, Playwright Chromium, and model/license pins verified.

### Milestone 1 — real hardware gate

Qwen3.5-9B Q8 chosen; DictaLM thinking model dropped. E5 CPU embeddings and FLUX.2 Klein/Chromium visual pipeline passed. Sequential GPU ownership and `nvidia-smi` cleanup gates established.

### Milestone 2 — storage/provenance foundation

Global/per-brand Alembic schemas, physical brand isolation, immutable versioning, truth hierarchy, FTS/vector memory, six versioned platform playbooks, content-revision history, and tests.

### Milestone 3 — smart research/onboarding

Robots-aware/rate-limited httpx + Trafilatura crawler, conditional Playwright rendering, best-effort DDGS discovery, secure assets, CPU E5 ingestion, strict observed/inferred extraction, knowledge gaps/conflicts, CSS/logo/font signals, and local GPU lifecycle. The real python.org smoke test completed in 46.78 seconds and cleaned up its disposable runtime.

### Milestone 4 — approval workspace and design studio

- FastAPI endpoints for brand creation/listing, onboarding jobs, claim/source review, claim approval/override, gap answers, profile approval, design-system version/approval, uploads, assets, and on-demand briefs.
- React/Vite Hebrew-first RTL workspace: claim review, live design studio, and three-mode photo-post flow.
- Agency visual system: role-based 60/30/10 palette, WCAG contrast calculations, bilingual typography hierarchy, logo geometry, photographic direction, feed-cohesion rules, and text-free FLUX constraints.
- `SocialPublisher`, `PackageDelivery`, `GPUWorkerProvider`, and foreground-segmentation boundaries.
- On-demand modes: `original_branded`, `smart_enhance`, `hero_cutout_scene`. Briefs never mutate the brand profile.

## Second Windows workstation bootstrap

Prerequisites: Windows 11, current NVIDIA driver/CUDA-capable GPU, Git, Python 3.12, `uv`, Node.js 22+, and enough disk for model weights.

```powershell
git clone https://github.com/Shay-Frishwasser/Hop.git
cd Hop
New-Item -ItemType Directory -Force C:\brandai-runtime | Out-Null
py -3.12 -m venv C:\brandai-runtime\.venv
Copy-Item .env.example .env
uv pip install --python C:\brandai-runtime\.venv\Scripts\python.exe -e '.[dev]'
cd web
npm install
$env:PLAYWRIGHT_BROWSERS_PATH='C:\brandai-runtime\cache\playwright'
npx playwright install chromium
npm run build
cd ..
```

Download the exact revisions from `licenses/model-manifest.json` into:

- `C:\brandai-runtime\models\llm\qwen3.5-9b-q8\Qwen_Qwen3.5-9B-Q8_0.gguf`
- `C:\brandai-runtime\models\embeddings\multilingual-e5-base`
- `C:\brandai-runtime\models\image\flux2-klein-4b`

Install the pinned Windows CUDA llama.cpp release under `C:\brandai-runtime\tools\llama.cpp-b12633`. Copy the five font files plus each `OFL.txt` into the directories recorded by `licenses/font-manifest.json`. No BiRefNet checkpoint should be downloaded until its license gate is explicitly approved.

Initialize and verify:

```powershell
& C:\brandai-runtime\.venv\Scripts\python.exe scripts\init_milestone2.py
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'
& C:\brandai-runtime\.venv\Scripts\python.exe -m pytest -q
& C:\brandai-runtime\.venv\Scripts\python.exe -m uvicorn brandai.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Keep the service on loopback until the secure-tunnel/deployment milestone.

## Future worker/publisher boundary

`GPUWorkerProvider.lease(capability)` returns a worker lease. The current implementation starts/stops native llama.cpp locally. A future EC2 provider can wake an AMI, wait for health, lease the endpoint, then terminate it without changing extraction/domain services. AWS currently lists `g7.2xlarge` as one RTX PRO 4500 Blackwell GPU with 32 GB VRAM; `g7e.2xlarge` is a different 96 GB RTX PRO 6000 option.

Future Meta Graph and LinkedIn OAuth implementations must implement `SocialPublisher`. Email and download implement `PackageDelivery`. Credentials belong in deployment secret storage, never SQLite playbooks or repository files.
