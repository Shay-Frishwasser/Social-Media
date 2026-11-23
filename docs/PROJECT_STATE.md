# BrandAI project state — 2026-11-23

## Product and architecture

BrandAI is a local-AI, web-based Social Media and Brand Manager supporting Instagram, TikTok, LinkedIn, Facebook, YouTube, and X in Hebrew and English. The Phase 1 application runs on localhost/LAN. It creates ready-to-publish packages for human delivery rather than publishing automatically.

The React UI and FastAPI API are stateless application layers over:

- `<runtime-root>/system.db` holds users, the brand registry, jobs, global reminders, and versioned platform playbooks.
- `<runtime-root>/brands/<uuid>/brand.db` holds sources, claims, brand versions, strategies, cadence, content revisions, topic quotes, visual provenance, delivery and job records, feedback, and photo briefs.
- `<runtime-root>/brands/<uuid>/assets` holds brand-contained uploads and media. Rendered files live under `visuals/<visual-id>`.

`BRANDAI_RUNTIME_ROOT` defaults to `C:\brandai-runtime` on Windows and `~/brandai-runtime` on macOS. Keep databases, virtual environments, browser caches, model weights, and generated media outside the checkout. Git carries source, migrations and playbook templates between machines. Each checkout uses its own local runtime root.

SQLite uses WAL, foreign keys, FTS5 trigram indexing, float32 embedding BLOBs, and NumPy cosine similarity. Profiles, design systems, content-package revisions, and content revisions have database immutability triggers.

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

### Milestone 6 — grounded content, validation and diversity

- Brand migration `0005_content_pipeline` adds `content_items` and `content_revisions`. An item links the brand, strategy, platform, content depth, pinned playbook version and rules, and either a cadence slot or a standalone photo brief. A calendar photo also retains the brief ID. Revisions store hook, body, CTA, hashtags, evidence IDs, Hebrew register and gender checks, the visual directive, attempt reports and a model-tagged float32 embedding when copy passes grounding and format checks.
- A slot needs an active approved cadence plan and its approved strategy. A standalone brief requires its target platform in the latest approved strategy and uses the effective playbook when generation begins. A brief already on an approved calendar uses that slot and the calendar's pinned rules. Generation is idempotent per slot or standalone brief. A replacement calendar has new slot IDs and can generate content while preserving the earlier revisions for audit.
- The CPU validator treats copy as a series of complete evidence paragraphs separated by blank lines. Each paragraph must reproduce an effective approved claim with a matching citation or the explicitly authored event note. Layout whitespace may vary. Hooks, CTAs and hashtags come from controlled language and platform choices. A free-form paraphrase, extra number, unsupported promise, wrong Hebrew address, missing CTA, or prohibited link returns a field-specific violation. Hebrew voice applies to editorial wording, with approved evidence retained as supplied.
- V1 caption, hook, CTA and hashtag budgets are BrandAI editorial rules. Links stay in the body on channels that allow them, and the brand playbook can tighten that rule with `content_link_placement: "forbidden"`. Each item retains the playbook hash, override revision and applied format budgets. FLUX.2 directives use the approved pillar's layout and style with text-free constraints. Photo directives use the existing treatment planner for all three photo modes. This milestone generates the directive for later execution.
- Generation makes one corrective retry with structured violations before storing either `VALIDATION_FAILED` or `READY_FOR_REVIEW`. The diversity gate compares current brand drafts and delivery history with word-shingle Jaccard, opening-hook patterns and embedding cosine. The default comparison window is 20 items. Applications can inject `DiversitySettings` to change the window or thresholds. Historical vectors are compared only within the same embedding model, with lexical checks continuing across model changes.
- Manual edits append an immutable revision and revalidate the current copy. Approval names the exact current revision and rechecks evidence and similarity. It moves a ready item to `APPROVED`. Internal delivery methods record `SCHEDULED` and `PUBLISHED` after approval. Recording publication requires a successful `SocialPublisher` receipt. Actual delivery remains an adapter concern.

#### Content API

All paths are relative to `/api/brands/{brand_id}/content`.

| Method and path | Purpose |
|---|---|
| `POST /generate/from-slot/{slot_id}` | Generate from an approved calendar slot |
| `POST /generate/from-on-demand/{brief_id}` | Generate from a standalone or calendar-linked photo brief |
| `GET /` | List items with platform and status filters, limit and offset |
| `GET /{content_id}` | Read the current revision and its history |
| `PATCH /{content_id}` | Edit copy, citations or visual selection and revalidate |
| `POST /{content_id}/approve` | Approve the specified review-ready revision |

Generation returns 201 for a new item and 200 for an existing item at the same placement. Check the returned lifecycle status and validation report for review eligibility. Edits and approval require the current `revision_id`. Visual edits accept an approved layout and supported framing or subject choice. Photo recipes retain the same asset and treatment mode.

#### Provider configuration

`MockDeterministicContentProvider` and the CPU hash embedding stub are the default. On a provisioned Windows workstation, set `BRANDAI_CONTENT_PROVIDER=local` for the Qwen3.5-9B Q8 llama.cpp adapter and local CPU E5 embeddings. The local worker serializes llama.cpp leases within one API process and keeps the existing VRAM and cleanup checks. A future AWS `g7.2xlarge` worker can supply the same `GPUWorkerProvider` lease to `QwenContentProvider` via `create_app(content_provider=..., content_embeddings=...)`. The API and content domain use the leased structured interface without AWS provisioning logic. An unavailable provider returns 503.

### Milestone 7 — topic research and visual asset composition

- Brand migration `0006_research_and_visual_assets` adds `topic_research`, immutable `external_evidence`, and `visual_assets`. It adds research provenance to each content revision with an empty default for earlier revisions.
- The research API accepts an explicitly public topic with optional allowed DNS domains and a content item ID. Discovery uses `SearchProvider`, then the existing static crawler enforces robots rules against each discovered page. The public transport requires HTTPS, validates all DNS answers, pins the connection to a public IP with the original TLS hostname, caps response bytes, and skips redirects. The research path never launches a browser on third-party pages. Empty results are visible as briefs with no evidence. Cached results expire for new retrieval after 24 hours, while historical citation snapshots remain available locally.
- External evidence records the source URL, title, retrieval time, exact quoted span, and SHA-256 of the archived extracted source text. Verification checks that the quote appears verbatim in that capture. Source credibility remains part of human review. To enrich a slot or photo post, pass `research_topic` for live retrieval or `research_topic_id` for an existing brief. The validator requires an exact attributed paragraph and its evidence ID and checks brand claims independently. Revision responses include numbered source metadata. Content-scoped briefs can be attached to their existing item through a revision.
- The `VisualPipeline` renders a generated background, an unchanged framed source photo, a subject-aware Pillow enhancement, or a hero cutout with a generated plate and soft shadow. The hero path uses only the mask channel from segmentation and keeps original foreground RGB. The background request projects locked art direction onto photographic traits and carries approved colors, composition and seed. Caption and event wording go only to Chromium.
- Chromium composes 1:1, 4:5, 9:16, and 16:9 layouts. It uses the brand's 60/30/10 role colors for chrome, checks 4.5:1 text contrast on its opaque backdrop, uses per-block RTL/LTR typography, and checks layout bounds against brand and vertical-platform safe zones. Approved uploaded logos use one rendered mark-height of clear space. It embeds provisioned OFL font files locally. Preview rendering can use Chromium's fallback if the files are missing, and `layout.local_fonts` records which fonts were embedded.
- Visual assets record the exact content revision or photo brief and design version, aspect, treatment, seed, provider identities, pixels, primary PNG SHA-256, and a per-file manifest. Carousels include individual PNGs, a ZIP of the frames, and a multipage PDF made from the same HTML. Re-rendering creates a new record with the same source and design plus any requested render-option changes. Downloads check the stored file digest.

#### Backend routes

Paths below are relative to `/api/brands/{brand_id}`.

| Method and path | Purpose |
|---|---|
| `POST /research/topics` | Retrieve a public topic or return its cached brief. Use `refresh: true` to fetch again. |
| `GET /research/topics` | List briefs, optionally filtered by `content_id` |
| `GET /research/topics/{topic_id}` | Read a brief and its quotations |
| `POST /visual-assets/render` | Render by `content_id` or `on_demand_brief_id` |
| `GET /visual-assets` and `GET /visual-assets/{asset_id}` | List renders and inspect their provenance |
| `POST /visual-assets/{asset_id}/rerender` | Create a render variant using the pinned source |
| `GET /visual-assets/{asset_id}/download` | Fetch `format=png\|pdf\|zip`. PNG pages start at `page=1`. |

#### macOS, Windows and AWS workers

The default visual providers use a seeded Pillow gradient and an ellipse mask. Production callers can inject `VisualPipeline(FluxBackgroundProvider(worker, runner), SAM2Provider(settings, worker, runner))` with separate runners for the two capabilities. They lease `image_generation` and `foreground_segmentation` through the existing worker boundary. Windows and a future AWS `g7.2xlarge` worker can use the same contracts with provisioned runners and pinned model revisions. The macOS suite exercises both stubs and real CPU/Chromium composition. GPU inference, provisioning, and live external search are outside that verification.

### Milestone 8 — publishing, delivery jobs and EC2 controller

- Brand migration `0007_publishing_and_jobs` adds `publish_records`, `social_connections` and `background_jobs`. Queue payloads and delivery receipts remain in the brand DB. The older unscoped job read endpoint still resolves previously registered onboarding jobs.
- The hard gate requires the latest approved profile and design versions, explicit approval of the current content revision, fresh deterministic copy validation, a matching render, and successful contrast, safe-zone, dimensions and SHA-256 checks for every recorded export. It runs before scheduling, export generation, export download, and immediately before outbound dispatch. Content-linked PNG/PDF/ZIP downloads use the same gate. Structured issues identify which approval or asset needs attention.
- ZIP packages carry the approved caption and structured copy, individual PNGs, the PDF where applicable, citations and provenance, and a recommended Asia/Jerusalem publication window. When there is an upcoming cadence reservation it anchors the recommendation. Otherwise the fallback is the next day at 10:00 local time. Email attaches the package and supports TLS SMTP through an injected or configured adapter.
- Official social adapters use Meta for Instagram containers and Facebook Page photos or posts, and LinkedIn for image or PDF document posts. Token values are stored as Fernet-encrypted, brand-and-connection-bound ciphertext. OAuth state is one-use and expires after ten minutes. Credential APIs return metadata and redact validation inputs. Use the approved grant type for each account and configure token refresh through the provider hook or LinkedIn client credentials.
- Jobs are atomically claimed with renewable leases, idempotency keys, exponential backoff, provider retry timing, progress and dead-letter status. Scheduling stores the selected Asia/Jerusalem instant in UTC. At execution the worker repeats preflight. A destination has one social dispatch per content item. A provider response that might have accepted a post or email moves the record to `NEEDS_RECONCILIATION`, and the queue will not retry that final effect automatically.
- The dedicated `brandai-jobs` process drains onboarding, content generation, visual render and delivery jobs. Run `python -m brandai.jobs.cli --once` to tick each active brand or `brandai-jobs --poll-seconds 3` as a supervised worker. The AWS configuration keeps the API role queue-only for GPU workloads. The worker starts a private On-Demand instance on a GPU lease, serializes active work, drains idle instances, and enforces startup and batch deadlines. The image has an independent boot watchdog. The deployment guide documents artifact pins, private networking, IAM and storage. [AWS deployment guide](../deploy/aws/DEPLOYMENT.md)

#### Backend routes

Paths are relative to `/api/brands/{brand_id}`.

| Method and path | Purpose |
|---|---|
| `GET /publishing/gate/{content_id}` | Inspect structured preflight and optionally pin `visual_asset_id` |
| `POST /publishing/export/{content_id}` and `GET /publishing/export/{content_id}` | Build a ZIP and download it after revalidation |
| `POST /publishing/dispatch/{content_id}` | Enqueue email, Meta or LinkedIn delivery now or at an explicit local timestamp |
| `GET /publishing/records/{record_id}` | Inspect delivery state, schedule, receipt or blocked reasons |
| `GET /publishing/connections` and `POST /publishing/connections` | List and manage redacted connection records |
| `POST /publishing/connections/{connection_id}/oauth-state` and `POST /publishing/connections/{connection_id}/complete` | Issue a one-use state and accept credentials from an approved OAuth integration |
| `POST /jobs`, `GET /jobs` and `GET /jobs/{job_id}` | Enqueue work, list jobs and poll progress |
| `POST /jobs/run-next` | Execute a due job in local development |

Use `kind=onboarding_research` with `website_url` and `company_name`, `kind=content_generation` with a `slot_id` or `brief_id`, or `kind=visual_render` with a content item, brief, or `previous_id` for a re-render. The payload carries the same generation or render options as the synchronous routes. Use a stable `idempotency_key` for each job or delivery. Scheduled social delivery uses the local Jerusalem offset on the supplied calendar date.

## UX and Figma handoff

The [UX handoff specification](UX_HANDOFF_SPEC.md) maps eight Hebrew-first workspace flows to the completed backend. It covers approval and publishing gates, Hebrew production microcopy, RTL behavior, responsive layouts and proposed interactions that require additional API support.

The [Figma import guide](../design/figma/FIGMA_IMPORT_GUIDE.md) links to the editable Design board and explains how to use the [English-labeled RTL reference board](../design/figma/wireframes-overview.svg) and [DTCG design tokens](../design/figma/design-tokens.json). The wireframes are design artifacts for the next frontend milestone.

## Local web workspace

The React + Vite + TypeScript workspace uses the existing local FastAPI service for brand onboarding and review, visual design, strategy, cadence, photo briefs, content research and composition, and gated publishing. The production build is served by FastAPI. Vite proxies `/api` to loopback port 8000 during development. The app uses hash routes so browser reloads load the same screen from either server.

Install Python 3.12 and a current Node.js LTS release. Set up the Python runtime and Playwright cache as shown below or in the Windows bootstrap. After that, use one command per platform.

```sh
"$HOME/brandai-runtime/.venv/bin/python" scripts/run_local.py
```

```powershell
& 'C:\brandai-runtime\.venv\Scripts\python.exe' scripts/run_local.py
```

Open `http://127.0.0.1:8000`. Add `--dev` for Vite hot reload at `http://127.0.0.1:5173`. The launcher installs locked Node dependencies when absent and starts the existing brand-local queue worker alongside the API. The default uses deterministic content, image and segmentation providers and supports both manual intake and deterministic extraction of verbatim public-site excerpts for review. On a Windows workstation with the pinned Qwen and embedding assets installed, use `--provider local` to enable native text inference and automated public brand research. Local visuals use CPU-friendly providers. Use `--offline` to disable new onboarding and topic research requests in the workspace. Email and social delivery use existing optional adapter configuration. Account listings show redacted connection metadata. A complete provider OAuth login and callback still requires a separate integration.

The workspace reads `GET /api/workspace` for local font and provider capabilities. `POST /profile/manual` creates a reviewable profile from owner-provided facts when automated extraction is unavailable. `GET /profile?allow_empty=true` and `GET /design-system?allow_empty=true` expose empty workspace state while retaining the default 404 behavior. `GET /assets/{asset_id}/preview` serves only verified, same-brand uploaded media. `GET /on-demand-photo-briefs` lists saved briefs. The content editor uses `POST /content/{content_id}/validate` to preview grounding and repetition findings without writing a revision. Saving repeats those checks. Content-linked PNG, PDF and ZIP downloads retain the hard publishing gate.

Verify the web build with `npm ci --prefix web`, `cd web && npx tsc --noEmit`, and `npm run build --prefix web` from the repository root. To include the real loopback Chromium walkthrough, set `BRANDAI_WEB_E2E=1` and run `python -m pytest -q` from the installed Python environment with the Chromium and font configuration below. The walkthrough injects deterministic public-research fixtures and uses a disposable runtime.

## macOS backend verification

Use Python 3.12 and a local runtime. The tests run against disposable brand stores with deterministic LLM, search, embedding, SMTP, social and EC2 worker doubles. To include the visual, publishing bundle and loopback browser tests, install Chromium in a runtime cache and leave `PLAYWRIGHT_BROWSERS_PATH` set while running pytest. For reproducible typography, install the pinned OFL files and licenses from `licenses/font-manifest.json` and set `BRANDAI_FONT_DIR` to that font directory.

```sh
export BRANDAI_RUNTIME_ROOT="$HOME/brandai-runtime"
uv venv --python 3.12 "$BRANDAI_RUNTIME_ROOT/.venv"
uv pip install --python "$BRANDAI_RUNTIME_ROOT/.venv/bin/python" -e '.[dev]'
export BRANDAI_CONTENT_PROVIDER=mock BRANDAI_TOPIC_RESEARCH=offline HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export BRANDAI_FONT_DIR="$BRANDAI_RUNTIME_ROOT/fonts"
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

Meta Graph and LinkedIn delivery implement `SocialPublisher`. Email uses a separate delivery adapter. Keep encryption keys and application credentials in deployment secret storage. Encrypted connection credentials stay in brand-local SQLite records.
