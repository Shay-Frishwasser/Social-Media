# BrandAI project state — 2026-11-23

## Product and architecture

BrandAI is a local-AI, web-based Social Media and Brand Manager supporting Instagram, TikTok, LinkedIn, Facebook, YouTube, and X in Hebrew and English. The Phase 1 application runs on localhost/LAN. It creates ready-to-publish packages for human delivery rather than publishing automatically.

The React UI and FastAPI API are stateless application layers over:

- `<runtime-root>/system.db` holds users, the brand registry, jobs, global reminders, and versioned platform playbooks.
- `<runtime-root>/brands/<uuid>/brand.db` holds sources, claims, brand versions, strategies, cadence, content revisions, feedback, and photo briefs.
- `<runtime-root>/brands/<uuid>/assets` holds brand-contained uploads and media.

`BRANDAI_RUNTIME_ROOT` defaults to `C:\brandai-runtime` on Windows and `~/brandai-runtime` on macOS. Keep databases, virtual environments, browser caches, model weights, and generated media outside the checkout. Git carries source, migrations and playbook templates between machines. Each checkout uses its own local runtime root.

SQLite uses WAL, foreign keys, FTS5 trigram indexing, float32 embedding BLOBs, and NumPy cosine similarity. Profiles, design systems, and content-package revisions have database triggers preventing update/delete.

## Models and licenses

| Role | Model/runtime | License state |
|---|---|---|
| Hebrew/English LLM | Qwen3.5-9B Q8_0 GGUF, llama.cpp CUDA | Apache-2.0; pinned in `licenses/model-manifest.json` |
| Embeddings | intfloat/multilingual-e5-base, CPU | MIT; pinned |
| Image generation | FLUX.2 Klein 4B, Diffusers subprocess | Apache-2.0; pinned |
| Fonts | Heebo, Assistant, Rubik, Noto Sans Hebrew, Inter | OFL 1.1; hashes in `licenses/font-manifest.json` |
| Primary foreground segmentation | Meta SAM 2.1 Hiera tiny or small | Apache-2.0 checkpoints. Worker adapter configured. Pin and provision the runner and checkpoint before hardware use. [Meta SAM 2 license](https://github.com/facebookresearch/sam2#license) |
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

### Milestone 5 — strategy, cadence and platform rules

- `brandai.strategy` uses a deterministic `StrategyPlanner` that can be injected into the API factory. The planner chooses approved claim IDs. The generator validates the selection, renders bilingual editorial hooks and angles, and maps three to five pillars to approved visual templates, color roles and FLUX.2 style constraints. It uses the latest approved profile and design system, effective approved claims, and verbatim evidence for observations. Audience pain points and desires come from approved audience fields when present.
- `strategies` holds versioned strategy documents with the profile and design IDs, effective evidence, register and Hebrew address tags, and per-channel pillar weights. LinkedIn defaults to a local 3:2:1 editorial mix for value, community and product. The approved strategy's selected platforms define the active channels for cadence. Approval records the human reviewer and rechecks provenance.
- `platform_rule_overrides` holds brand-local revisions for posting frequency, preferred days and times, excluded topics, pillar weights, depth mix, account type and link policy. A playbook is seeded from repository `playbooks/<platform>/v1.json` when its runtime copy is missing. Strategy and cadence snapshots keep the base version, exact SHA-256, effective rules and brand override revision. Set `BRANDAI_PLAYBOOK_SOURCE` if running from an install outside the source checkout.
- `cadence_plans` holds versioned weekly and monthly plan documents, and `planned_content_slots` holds time, platform, pillar, approved claim and depth for each scheduled item. Weeks start on Monday and months start on the first. Times are selected in Asia/Jerusalem and stored as UTC. Skipped daylight-saving times are unavailable and repeated wall times use their earlier instant. Monthly plans apportion partial weeks using their eligible weekdays.
- Approval reserves date ranges by channel and publishing instants across the brand. Draft plans can explore the same period. Updating a plan creates a draft revision, and approving the revision supersedes the previous approved plan in the same transaction. On-demand slots refer to a same-brand photo brief and preserve scheduled cadence counts. A standalone brief uses the existing on-demand flow without a calendar reservation. Calendar approval and photo brief status are independent.
- Brand migrations run on first access through `BrandAIStore`. Milestone 5 adds the two planning tables using brand migration `0004_strategy_cadence`. Existing planning rows are readable as legacy documents and can be revised into the structured format before approval.

#### Backend API

All paths below are relative to `/api/brands/{brand_id}`. OpenAPI serves request and response schemas at `/docs`.

| Method and path | Purpose |
|---|---|
| `GET /strategies` and `GET /strategies/{strategy_id}` | List versions and inspect a strategy |
| `POST /strategies` | Generate a draft for selected platforms |
| `PUT /strategies/{strategy_id}` | Create a draft revision with selected evidence, tone tags or pillar weights |
| `POST /strategies/{strategy_id}/approve` | Approve a strategy version |
| `GET /cadence-plans` and `GET /cadence-plans/{plan_id}` | List versions and inspect a plan with its slots |
| `POST /cadence-plans` | Plan a week or month with optional per-channel overrides |
| `PUT /cadence-plans/{plan_id}` | Create a revised draft and carry its on-demand slots forward |
| `POST /cadence-plans/{plan_id}/on-demand-slots` | Add an existing photo brief at an offset-aware timestamp in a new draft |
| `POST /cadence-plans/{plan_id}/approve` | Approve a plan and reserve its slots |
| `GET /playbooks/{platform}` and `PUT /playbooks/{platform}` | Inspect effective rules and replace the brand override with a new version |

For cadence, Monday is weekday 0 and Sunday is weekday 6. Local times use `HH:MM`. Omitted plan-level override fields inherit the brand rules. Pillar weights use plan overrides first, strategy overrides second, and then brand rules and platform defaults. The six supported depth labels are `micro`, `standard`, `deep_dive`, `educational_framework`, `story`, and `carousel`.

#### SAM 2.1 worker configuration

`hero_cutout_scene` selects SAM 2.1 via `ForegroundSegmentationProvider.cutout(source, destination)`. Set `BRANDAI_SAM2_VARIANT` to `sam2.1_hiera_tiny` (default) or `sam2.1_hiera_small`. `BRANDAI_SAM2_DIR` defaults to `<runtime-root>/models/segmentation/sam2`. The adapter leases `foreground_segmentation` through `GPUWorkerProvider` and passes the resolved checkpoint and model config to an injected runner. The runner supplies the subject prompt, creates the RGBA result, and preserves source RGB pixels with mask edits confined to alpha. Milestone 5 exercises the contract with a stub. BiRefNet remains disabled.

## macOS backend verification

Use Python 3.12 and a local runtime. The tests run against disposable brand stores with deterministic LLM, embedding and GPU worker doubles. To include the loopback browser smoke test, install Chromium in a runtime cache and leave `PLAYWRIGHT_BROWSERS_PATH` set while running pytest.

```sh
export BRANDAI_RUNTIME_ROOT="$HOME/brandai-runtime"
uv venv --python 3.12 "$BRANDAI_RUNTIME_ROOT/.venv"
uv pip install --python "$BRANDAI_RUNTIME_ROOT/.venv/bin/python" -e '.[dev]'
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PLAYWRIGHT_BROWSERS_PATH="$BRANDAI_RUNTIME_ROOT/cache/playwright"
"$BRANDAI_RUNTIME_ROOT/.venv/bin/python" -m playwright install chromium
"$BRANDAI_RUNTIME_ROOT/.venv/bin/python" -m pytest -q
```

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
