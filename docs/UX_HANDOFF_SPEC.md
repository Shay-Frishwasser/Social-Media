# BrandAI UX handoff

**Design baseline** · Backend Milestones 0–8 at `b2bc9ed`. English specification and wireframe labels. Hebrew-first production UI with right-to-left workspace geometry and bilingual content.

**Designer deliverables** · [Figma wireframe](https://www.figma.com/design/iq1z0oRuSRGfSliYzu85cI), [SVG reference board](../design/figma/wireframes-overview.svg), [design tokens](../design/figma/design-tokens.json), and [import guide](../design/figma/FIGMA_IMPORT_GUIDE.md). The [project state](PROJECT_STATE.md) and local FastAPI OpenAPI document describe the implementation. This document specifies the proposed UX. Items marked **Frontend behavior** can be designed against the existing API. Items marked **Backend dependency** require a contract before they appear as functional controls.

## Vision, personas, and release boundary

BrandAI gives teams one workspace to establish a brand’s evidence, approve its identity, plan content, create visual assets, and deliver the exact approved revision. A successful workflow makes it easy to answer three questions. What supports this claim? Which version did I approve? What must I fix before this can go out?

| Persona | Typical task | What the interface should prioritize |
|---|---|---|
| Agency or brand manager | Switch between clients, build a B2B LinkedIn strategy, approve a weekly schedule, enrich an educational post with public research | Persistent brand context, version comparisons, exact citations, fast access to blocked items and delivery receipts |
| SMB owner | Upload a dish photo on a phone, type tonight’s event, compare treatments, send the package or schedule a post | Short forms, illustrative mode comparison, one clear next action, visible local time, actionable recovery without losing the photo or note |

Editorial decisions and approvals are human. These personas describe needs and permissions to design for. A multi-user account system, invitation workflow, and reviewer role enforcement are **backend dependencies**. The current API accepts reviewer metadata and runs on loopback. Brand switching is a data boundary. Every request, draft, upload, cache entry and job subscription must be scoped to the active brand.

### Visual direction

Use near-black olive charcoal for the workspace, matte panel layers, fine neutral outlines, and one pale lime action accent. Reserve mint for contextual accents. Place soft mint/lime ambient light around the reference board and spacious entry moments. Keep dense review and calendar surfaces solid and legible. Use a restrained editorial serif for board headings only. The product shell and functional copy use Heebo for Hebrew and Inter for English. Rounded cards, generous section spacing, and grayscale photo placeholders keep this a low-fidelity handoff.

The workspace retains BrandAI’s own right-hand navigation, evidence and version hierarchy, and publishing checklist. The reference imagery informs atmosphere and palette. Use fictional brands and placeholder media in all design examples. Workspace colors stay stable when switching brands. The editable campaign palette belongs to the brand and appears in approved visual previews and exports.

## Information architecture and journeys

- Global right-side rail on desktop. Put `Brand switcher / בחירת מותג` at the top, current brand name in the header and switching confirmation only when unsaved edits exist. Offer `New brand / מותג חדש`. Return focus to the switcher trigger. Cancel stale polling and discard data fetched for the wrong brand.
- Group navigation as **Setup** (Onboarding, Identity, Design system), **Plan** (Strategy, Calendar), and **Create & deliver** (Photo studio, Content, Publishing). Jobs open in a contextual drawer and link back to their source workflow. Show prerequisites inside the target screen so a deep link remains useful.
- Agency journey. Create brand → research → verify claims and gaps → approve profile and design → approve strategy and cadence → generate from a slot → attach public research → resolve copy findings → approve current revision → render or select a matching asset → verify gate → export, email or schedule → track receipt.
- SMB journey. Open Photo studio → upload photo → add event note and channel → select treatment → create brief → generate copy or add the brief to a draft calendar → review copy and visual → approve and deliver. Standalone generation still requires the active brand strategy to include the chosen platform.
- Recovery journey. Open the gate issue or job result → return to the exact object and revision → correct or regenerate → reapprove affected versions → rerun preflight. Preserve validated text and photo uploads where recovery permits.

### Shared version and async behavior

Show current, approved and historical versions as distinct records with review actor and time when returned. Approval must target the open revision and refresh stale views. Optimistic updates are acceptable for transient filters, never for approvals, reservations, uploads, or provider receipts. A saved draft does not replace an approved artifact until its own successful approval. New brand profile/design versions can invalidate downstream bindings. Deep-link from a stale indicator to the blocking version.

Jobs use `QUEUED`, `RUNNING`, `RETRY_WAIT`, `SUCCEEDED`, `DEAD_LETTER`. Keep affected actions disabled while the same operation is in flight. Poll by brand and job ID, slow polling when a page is hidden, and stop for terminal states. On reload, resume from known job IDs and the brand job list. Display server progress when meaningful. Use indeterminate motion when phase or ETA is unknown. Preserve idempotency keys across network retries for the same intent. Show `ממתין לעיבוד`, `מתבצע עיבוד`, `ניסיון נוסף מתוכנן`, `העיבוד הושלם`, or `העיבוד הופסק לאחר מספר ניסיונות`. Keep the draft or previous result visible through retry and failure.

## 01 · Brand onboarding and live research

**Purpose and entry.** Start here from a new brand or an empty workspace. Show a compact input card on the right and a research status card on the left.

- Fields in tab order. Brand name `שם המותג` (required), public HTTPS website `כתובת האתר` (required for automated research), optional channel/handle rows `חשבונות ברשתות החברתיות`. Show handles and URLs as LTR fields. Add, remove and validate channel/handle pairs independently.
- Primary action `יצירת מותג והתחלת מחקר`. Create the brand once using `POST /api/brands`, then submit site/name to the onboarding job. If enqueue fails, keep the created brand ID and retry the job step. Keep progress visible in the workspace and show a results action only when the job returns a profile.
- Show queued, collecting and preparing-for-review as explanatory stages only when backed by a returned status or explicit progress contract. Current job status is coarse. HTTPS, robots, inaccessible sources, limited evidence and unavailable workers need distinct explanation where provided. Surface a next action to correct the site, use existing evidence or return to the workspace.
- **Backend dependency.** Onboarding only submits `website_url` and `company_name`. Saving or researching social handles and entering a complete manual onboarding profile require API work. Keep handles marked as proposed. Knowledge-gap answers in an existing review are supported and do not substitute for creating a complete manual profile from scratch.
- Copy. `נדרשת כתובת אתר ציבורית שמתחילה ב־HTTPS`, `המחקר ממתין בתור`, `לא נמצאו מקורות זמינים. אפשר לבדוק את הכתובת ולנסות שוב.`, `המחקר הסתיים. אפשר לעבור לאימות הממצאים.`
- API. `POST /api/brands`, `POST /jobs` with `onboarding_research` (or legacy `POST /research`), `GET /jobs/{job_id}`, `GET /profile`.

## 02 · Brand identity and claim verification

**Purpose and entry.** Show `PENDING_REVIEW` after onboarding. The right column has a claim list and filter. The left inspector switches between evidence details, a user edit, and knowledge gaps.

- Claim rows show current value, origin, status, confidence when supplied, and change affordance. Translate `observed` as `נצפה במקור`, `inferred` as `מסקנה משוערת`, a user override as `עודכן ידנית`. Approval and origin are separate badges. Reviewers can filter all, observed, inferred, approved and unresolved.
- A source action `למקור ולציטוט` opens a drawer with title, normalized URL, source ID and exact `quoted_span`, preserving punctuation and source language in a direction-isolated block. An external link opens separately. Missing source or quote in an observed claim requires correction or an explicit user override. An inference must remain visibly inferred even after approval. Confidence is not verification.
- Inline override `עדכון ידני` records the owner's value and reviewer. Confirm which value takes precedence before saving. Keep original evidence in history when supplied. Truth hierarchy is user edit → approved observed → inference. `פתרון פערי מידע` keeps each question next to its draft answer and shows acknowledgement after submission.
- `אישור פרופיל המותג` moves the current review snapshot to `APPROVED`; offer a summary of outstanding issues before confirmation. **Frontend behavior** Use warnings for unresolved gaps. Treat a gap as a blocking requirement only if the server or an agreed product rule says so. Strategy uses effective approved claims and requires approved profile and design.
- Copy. `הציטוט מופיע כפי שנשמר במקור`, `לא נמצא מקור לאימות הטענה`, `הערך המעודכן יגבר על הערך הקודם`, `הפרופיל אושר`.
- API. `GET /claims`, `POST /claims/{claim_id}/approve`, `POST /claims/{claim_id}/override`, `POST /knowledge-gaps`, `GET /profile`, `POST /profile/approve`.

## 03 · Visual design system studio

**Purpose and entry.** Edit the draft design and inspect previews beside the controls. Show semantic roles as three cards with a proportional strip. Keep approval in the version action bar.

- Roles `ראשי` 60%, `משני` 30%, `הדגשה` 10%. Each row exposes a labeled swatch, HEX input, text/background pairing, ratio and WCAG AA badge. Preview contrast continuously as a client aid. Label normal text at 4.5:1 and large text at 3:1 in the editor. Final render text gate uses at least 4.5:1. Keep brand key colors separate from semantic app status.
- Preview actual bilingual paragraphs, sizes and overlay typography. Offer Heebo, Assistant, Rubik and Noto Sans Hebrew for Hebrew, with Inter as the English default and approved OFL families for applicable text. Show font load failure and recovered fallback. Persist approved font configurations with the design version.
- Upload/select logo and show the selected asset, intrinsic ratio, allowed anchors and margin guides. Reserve one rendered logo height of clear space in final composition. If the logo or overlay cannot fit, show the conflict rather than squeezing typography or resizing the mark silently.
- Art-direction panel shows framing, light, palette, shot cues and approved layouts. Explain that generated pixels use photographic cues only. Chromium applies text and brand chrome after image generation. Saving creates a draft; review and approve it separately. Compare selected draft and approved versions without transferring unpublished changes to existing assets.
- Copy. `יחס ניגודיות 4.7:1 · עומד ב־AA`, `יחס הניגודיות נמוך מדי לטקסט רגיל`, `מרווח ביטחון סביב הלוגו`, `שמירת טיוטה`, `אישור שפת העיצוב`.
- API. `GET/PUT /design-system`, `POST /design-system/approve`, `POST /assets?kind=logo`, `GET /assets`, `GET /api/fonts/{font_key}`.

## 04 · Strategy and platform playbooks

**Purpose and entry.** Start from approved identity and design. Right side holds pillar cards and editable weights. Left side switches among voice, messaging hooks and playbook details.

- Select Instagram, TikTok, LinkedIn, Facebook, YouTube or X from the brand’s intended channels. Show 3–5 pillars with purpose, audience, approved source/evidence bindings and approved visual templates. Sliders must have direct numeric inputs, announce values, display per-platform totals and reject a total other than 100. LinkedIn’s default value/community/product split follows the approximate 3:2:1 editorial guidance, not a hard marketing rule.
- Distinguish editorial register `סגנון כתיבה` from Hebrew audience address `לשון פנייה`. Show neutral, inclusive, feminine, masculine and plural options with examples from the supported vocabulary, such as `אפשר לשתף מחשבה בתגובות.` and `מוזמנות ומוזמנים לשתף.` Preview hooks, angles and their evidence. A strategy hook suggestion is not an automatic exception to content validation.
- Per-channel drawer shows effective rules and their source version and override. Offer supported posting frequency, preferred weekdays/times, excluded topics, depth mix, account type and link placement. Inherits should look different from explicit values. Preserve recorded base version, hash and override revision when inspecting old drafts. Brand edits outrank base playbooks. Plan override wins for cadence settings. For pillar weights specifically, show plan → strategy → brand → base precedence.
- Show version history, save as draft and explicit approve. A stale approved input or playbook conflict leads back to the exact review, then regenerates a new strategy. **Backend dependency.** A hook manager with arbitrary authoring or per-hook approval requires a new contract. Provide view-and-copy behavior now.
- Copy. `עמודי תוכן`, `סך המשקלים חייב להיות 100%`, `כללים שהותאמו לערוץ`, `המקורות השתנו. יש לעדכן את האסטרטגיה לפני האישור.`
- API. `GET/POST /strategies`, `GET/PUT /strategies/{strategy_id}`, `POST /strategies/{strategy_id}/approve`, `GET/PUT /playbooks/{platform}`.

## 05 · Cadence and content calendar

**Purpose and entry.** Requires an active approved strategy. Show Weekly and Monthly toggles, channel filters, plan status, local time, and calendar/list views. Slot selection opens an inspector with time, platform, pillar, depth and linked content or brief.

- Weeks start Monday, which is the rightmost column in the RTL grid. Use Asia/Jerusalem for calendar display and input, Monday = 0, and persist the matching UTC instant. An unavailable DST time should offer the nearest valid choices. Confirm the offset for a repeated hour. Show the actual saved time and zone in the publishing confirmation.
- Use readable text badges for `micro` → `מיקרו`, `standard` → `רגיל`, `deep_dive` → `צלילה לעומק`, `educational_framework` → `מסגרת לימודית`, `story` → `סיפור`, `carousel` → `קרוסלה`. Badges communicate editorial depth. A story badge does not imply support for the platform’s Stories API.
- Draft plans are editable through versioning. Approving reserves time ranges per channel and publishing instants for the brand. Mark approved reservations as locked. Edit by creating a revised plan and show the effect of approving it on the previous version and slot ids. **Backend dependency.** Freeform drag-and-drop or per-slot lock/unlock is not part of the current API. The lock is a visual representation of the plan reservation.
- `הוספת אירוע חד־פעמי` selects an existing same-brand brief and timestamp inside the plan window and active channel. Creates a new draft with the insertion; review before replacing the approved plan. Warn if a time or brief is already reserved. Leaving the brief standalone does not use up cadence capacity. Distinguish reserved editorial slot from scheduled social delivery and its receipt.
- Copy. `שבוע`, `חודש`, `שעון ישראל`, `מועד שמור בתוכנית שאושרה`, `המועד כבר שמור. בחרו שעה אחרת.`, `יצירת תוכן מהמשבצת`.
- API. `GET/POST /cadence-plans`, `GET/PUT /cadence-plans/{plan_id}`, `POST /cadence-plans/{plan_id}/approve`, `POST /cadence-plans/{plan_id}/on-demand-slots`.

## 06 · On-demand photo post and Wow studio

**Purpose and entry.** Surface Photo studio from navigation, calendar insertion and an empty content view. Use a short form plus a three-card treatment selector with labeled example thumbnails and original-photo context.

- Upload a same-brand JPEG, PNG or WebP up to 25 MiB, then select platform and provide an explicit factual event note, up to 2,000 characters. Prompt with `מה קורה, מתי, ומה חשוב לציין?` A prompt used as body is retained as authored wording. Show platform format limits before generation even when the brief itself accepts more text. Keep the original photo visible through upload, selection and retry.
- `מקורי עם מיתוג` (`original_branded`) frames the unretouched photo. `שיפור חכם` (`smart_enhance`) crops around the subject and adjusts lighting, contrast and color. `סטודיו Wow` (`hero_cutout_scene`) uses a segmentation mask and generated studio plate, preserving the original subject’s RGB through compositing and placing brand chrome separately.
- Comparison cards are illustrative until a render exists. Show loading per variant and distinguish test providers from production when identity is supplied. Offer `שמירת פוסט לצילום`, then `יצירת תוכן` or `הוספה ללוח`. The brief is transient and never changes the permanent brand profile. Standalone visual rendering is available from a photo brief with approved brand inputs. Publication requires a matching content-linked render.
- Copy. `העלאת תמונת מוצר או מנה`, `התמונה המקורית נשמרת`, `התמונה גדולה מדי. אפשר להעלות קובץ עד 25 MiB.`, `לא הצלחנו להפריד את המוצר מהרקע. אפשר לנסות שוב או לבחור תמונה אחרת.`
- **Backend dependency.** The API supports brief creation by id but lacks a standalone brief list, edit, delete and cancel surface. Keep local ownership of the in-progress selection until an agreed persistence contract exists. Changing mode is a new brief, not an overwrite. Live three-way previews require separately created runs; illustrative comparisons are available directly.
- API. `POST /assets`, `POST /on-demand-photo-briefs`, `POST /visual-assets/render`, `POST /content/generate/from-on-demand/{brief_id}`, or queued equivalents.

## 07 · Content creation, research and visual compositor

**Purpose and entry.** Open an existing item or create from an approved placement. Use a right-side copy editor and left tabbed inspector for validation, research and visuals. Show content status separately from job status, with revision history and context.

- Editable fields. Hook `פתיח`, body `גוף התוכן`, CTA `קריאה לפעולה`, hashtags `תגיות`. Show character counts and live guidance from the pinned format rules. Body uses complete approved evidence paragraphs or verbatim event text, separated by blank lines. Arbitrary paraphrase, unsupported numbers and unapproved wording can fail validation. Respect approved Hebrew voice and the controlled hook/CTA/hashtag vocabulary. Choose language auto/Hebrew/English with an explanation that proposal language does not translate quotations.
- Research tab. Ask only for a public topic, optionally limit approved domains, and show a refresh action. Display exact quotes, numbered source labels, origin URL, title and retrieval time. Link a brief or create one inline, preserving brand and optional content scope. Archived quote verification confirms matching wording, not independent truth. Use whole attributed paragraphs and their matched external evidence IDs. Treat private client notes as content, not search queries.
- Validation tab. Show deterministic result, field-level messages, citation proof, retry attempt history, and comparison findings. Map similarity violations to the referenced content item and show returned lexical, semantic and opening scores with definitions. Show `לא זמין` when a score is absent, for example where a historical vector uses another model. The current response exposes comparison scores on violations rather than a universal quality score. Never display a fabricated percentage or replace human review. Generation attempts at most one automatic recovery before returning the authoritative status.
- Visual tab. Show aspect presets `1:1`, `4:5`, `9:16`, `16:9`, carousel toggle, frame index, safe zones and provider/seed/provenance. Render from a valid current revision. Rerender makes a new asset with pinned inputs and selected option changes. Keep old visuals listed as historical and invalidate the selected publishing asset when revision changes.
- **Backend dependency.** The current content-linked file endpoint gates final PNG, PDF and ZIP bytes. Before approval the UI can show render metadata, layout choices and original inputs. A protected pre-approval image or PDF preview needs a separate design and service contract. After gate success show an actual image/PDF viewer. A saved image from another revision must not appear as a current preview.
- States. `VALIDATION_FAILED` → `READY_FOR_REVIEW` → `APPROVED`, with edits producing an immutable revision and requiring fresh review. Scheduled or published content is read-only; active or uncertain deliveries can also lock editing. An approval request includes current `revision_id`. Use the server’s content status to determine review eligibility. Copy. `נמצא נתון ללא מקור מאומת`, `נמצאה חפיפה גבוהה לתוכן קודם`, `שמירת גרסה ובדיקה מחדש`, `אישור הגרסה הנוכחית`, `התצוגה הסופית זמינה לאחר אישור ובדיקת פרסום`.
- API. `GET /content`, `POST /content/generate/from-slot/{slot_id}` or `…/from-on-demand/{brief_id}`, `GET/PATCH /content/{content_id}`, `POST /content/{content_id}/approve`, `GET/POST /research/topics`, `GET /research/topics/{topic_id}`, `GET /visual-assets` and its render, rerender and download routes. AWS deployments queue generation and visual tasks through `POST /jobs`.

### BrandAI v1 caption budgets

Use these defaults as examples. Read the item’s effective format rules before making actual UI decisions.

| Channel | Caption / opening / CTA characters | Hashtags | Link placement default |
|---|---|---|---|
| Instagram | 2200 / 125 / 100 | 5 | Forbidden |
| TikTok | 2200 / 100 / 100 | 5 | Forbidden |
| LinkedIn | 3000 / 150 / 125 | 5 | Body |
| Facebook | 3000 / 140 / 120 | 4 | Body |
| YouTube | 5000 / 100 / 110 | 5 | Body |
| X | 280 / 80 / 60 | 2 | Body |

## 08 · Hard publishing gate, export and dispatch

**Purpose and entry.** Start from a content item with selected render. Put the checklist before delivery choices. Show a compact version strip with brand, revision, render and destination. Re-run the gate after relevant changes and trust its verdict at delivery time even if the prior checklist was clean.

| Gate result | Hebrew reason and recovery action |
|---|---|
| Profile or design approval stale | `נדרש אישור עדכני לפרופיל או לשפת העיצוב` → review identity/design and regenerate affected content/render |
| Current revision pending, failed or replaced | `הגרסה הנוכחית טרם אושרה` → open the exact revision and review |
| Validation context or wording fails | `יש לתקן את הטקסט או לעדכן את מקורותיו` → open findings, evidence, or cadence and resolve |
| Missing visual or mismatched revision/design | `נדרש קובץ חזותי שתואם לגרסה המאושרת` → render or select a matching asset |
| Integrity, contrast, or safe zone fails | `הקובץ לא עבר את בדיקת התקינות` → inspect details and rerender |
| All checks pass | `מוכן למסירה` → activate delivery options supported for this destination |

- `הורדת חבילת ZIP` builds then downloads through the validated endpoint. The bundle includes final media, approved caption, manifest, source provenance and recommended Jerusalem publish window. Email requires a recipient and explicit confirmation. Show `המסירה נוספה לתור` after a job is queued, `החבילה נשלחה במייל` after delivery, and `פורסם בהצלחה` only after a social receipt.
- Meta Graph supports configured Instagram Business and Facebook Page destinations. LinkedIn supports configured member/organization destinations and carousel PDF through document posting. Other playbook channels can use export or email. Show account label, platform and token metadata only. The account drawer issues one-time OAuth state and completes when a provider integration returns credentials. **Backend dependency.** The actual provider login/callback service integration, revocation UI and permission management are separate. Expose connect/reconnect as a functional control only after provisioned.
- `תזמון פרסום` confirms specific destination, media, caption revision and offset-aware Israel time. Gate runs again when the job executes. Delivery states include queued, dispatching, delivered, blocked, failed and needs reconciliation. Hold uncertain outcomes and show manual verification `מצב הפרסום אינו ודאי. בדקו בחשבון הערוץ לפני ניסיון נוסף.` Do not issue a new idempotency key for the same intent. Distinguish scheduled content from an elapsed calendar slot. **Backend dependency.** Cancel/reschedule and resolution of uncertain provider outcomes lack a production UI contract.
- Show job drawer with queued, running, backoff, completed and dead-letter rows, last update and source jumps. Hide infrastructure IDs from the primary view. **Backend dependency.** The public API returns queue status without direct EC2/GPU state. Use copy such as `משימת עיבוד בתור` based on actual job activity. Warming, busy and draining indicators require telemetry before they can be shown as facts. The manual run-next control is a local development tool.
- API. `GET /publishing/gate/{content_id}`, `POST/GET /publishing/export/{content_id}`, `POST /publishing/dispatch/{content_id}`, `GET /publishing/records/{record_id}`, `GET/POST /publishing/connections`, OAuth-state and complete routes, `GET /jobs` and `GET /jobs/{job_id}`.

## Component inventory

| Component | Anatomy and interaction contract |
|---|---|
| Workspace rail and brand picker | Current brand, grouped nav, searchable brand list, job drawer trigger, pending unsaved changes confirmation |
| Stage header and action bar | Breadcrumbs, title, version badge, primary action, history, error details; sticky only if it leaves keyboard focus visible |
| Status pill and banner | Icon plus text, accessible state name, progress value where known, one next action; dense list variants |
| Evidence row and drawer | Origin, reviewer status, exact quote, isolated technical IDs/URL, external action, editor/history |
| Input, upload and slider | Persistent labels, helper and field errors, dropzone plus file button, upload progress, numeric slider peer |
| Palette band and contrast tile | Role label, HEX, foreground/background pair, measured AA badge, alternative safe color guidance |
| Playbook and calendar components | Channel picker, effective-rule drawer, day/grid/list slot, depth badge, locked reservation and local timestamp |
| Mode and aspect cards | Native radio semantics, miniature preview placeholder, selected/disabled state, comparison caption and provenance |
| Content inspector | Revision diff, citation and validation lists, per-field findings, carousel pagination and visual metadata |
| Gate checklist and dialogs | Requirement rows, distinct result icons, fixes with contextual deep links, destination confirmation, OAuth redaction |
| Jobs and receipt drawer | Kind, real status, attempt, scheduled time, result or remediation, stable job/record URL |

## Screen state matrix

| Screen | Empty | Loading / GPU work | Needs review / blocked | Validation error | Approved / published |
|---|---|---|---|---|---|
| 01 Onboarding | New brand fields | Queued or running job, suspend duplicate submit | Research finished; open identity | Bad URL, inaccessible source, unavailable worker; keep brand | Route to reviewed profile, show prerequisite checklist |
| 02 Identity | No claims yet; route to research | Skeleton or source panel loading | Observed/inferred reviewed individually; unresolved gaps | Missing quote/source, failed save or stale version | Approved profile summary and evidence history |
| 03 Design | Empty roles/logo with starting draft | Fonts loading or save pending | Draft side-by-side with approved design | Invalid HEX, AA fail, logo upload/fit error | Approved version, preview, create revision action |
| 04 Strategy | Select channel or finish identity | Generating strategy / loading rules | Draft weight totals and effective playbooks | Bad totals, stale inputs, disallowed rule | Approved strategy and links to create plan |
| 05 Calendar | Empty period and create plan | Creating or updating cadence | Draft slots and explicit reserve action | Conflict or invalid Israel time; retain edits | Reserved locks; content/delivery status stays separate |
| 06 Photo | Upload placeholder and examples | Upload/generation/segmentation job | Mode chosen or brief saved; approve content downstream | Bad file, note, invalid render or unavailable provider | Brief summary and approved content/delivery direction |
| 07 Content | Select slot/brief | Separate generation, research and render jobs | Findings, revisions and approval controls | Specific claim/copy/density/repetition failures | Current approval and verified asset or render action |
| 08 Publishing | Choose content and render | Gate checking, packaging or queued/dispatching | Failed row links and disabled delivery | Destination, expired token, failed job or uncertainty | ZIP ready, email delivered, or published receipt |

## RTL, accessibility and responsive behavior

- Page root `lang=he dir=rtl` for production Hebrew. Keep logical start/end in CSS, mirrored directional controls, tab focus in DOM reading order, and semantic landmarks. English wireframe labels use LTR text anchored within the RTL geometry. Do not reverse Latin names, URLs, hashes, IDs, times, handles, percentages or aspect ratios. Use `<bdi>`, `dir=auto` for names/quotes and per-block explicit direction for known language. In a full English locale use LTR navigation. Keep English wireframe labels as review text for the Hebrew-first layout.
- Provide labels and visible focus. Use a 44 × 44 px minimum target in product interactions, labeled controls beyond color-only pills, a live region for async status, and focus on the first blocking issue when appropriate. Drawers and modals trap focus, support Escape, restore the trigger, and preserve unfinished fields when possible. Respect reduced motion.
- Desktop **1280+ px**. Twelve columns, 240–264 px right rail, 24–32 px gutters, optional 320–400 px left inspector. Board desktop frames are schematic at 900 × 590 to make all eight readable in one reference.
- Tablet **768–1279 px**. Eight columns, compact right rail or menu, inspector opens as a drawer if space is limited. Calendar can switch to a week list. Keep action bars in flow with a visible sticky option.
- Mobile **up to 767 px**. Four columns and 16 px edge padding. Brand picker and job shortcut in header. Use a right-opening navigation drawer and bottom sheet for contextual actions. Stack controls first and preview second, use review summaries before footers, and default dense calendar views to day-grouped agenda. Preserve keyboard focus below any sticky footer.
- Inspect at 360/390, 768, 1024, and 1440 px. Verify long Hebrew labels, LTR URLs inside Hebrew paragraphs, a repeated DST hour, upload failure, claim conflict, keyboard approval, and a delivery that needs reconciliation. Keep backend-only dependencies in the Figma annotations and implementation backlog.
