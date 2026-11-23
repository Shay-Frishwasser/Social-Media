export const platforms = ['instagram', 'tiktok', 'linkedin', 'facebook', 'youtube', 'x'] as const
export type Platform = typeof platforms[number]
export const platformLabel: Record<Platform, string> = {
  instagram: 'Instagram', tiktok: 'TikTok', linkedin: 'LinkedIn', facebook: 'Facebook',
  youtube: 'YouTube', x: 'X',
}

export const depths = {
  micro: 'מיקרו', standard: 'רגיל', deep_dive: 'צלילה לעומק',
  educational_framework: 'מסגרת לימודית', story: 'סיפור', carousel: 'קרוסלה',
}
export type Depth = keyof typeof depths
export type Version = {id: string; version: number; status: string}
export type Brand = {id: string; name: string; status: string}
export type Capabilities = {
  fonts: string[]; onboarding_research: boolean; topic_research: boolean
  email: boolean; social: string[]; provider: string
}
export type Claim = {
  id: string; field_key: string; value: unknown; origin: 'observed' | 'inferred'
  confidence: number; status: string; quoted_span: string | null; is_user_override: boolean
  source: {id: string; title: string; url: string | null} | null
}
export type Profile = Version & {
  document: {knowledge_gaps?: {question: string; reason: string}[]; conflicts?: {field_key: string; description: string}[]}
}
export type Asset = {id: string; kind: string; metadata: {width: number; height: number; media_type: string}}
export type DesignFields = {
  colors: {primary: string; secondary: string; accent: string; contrast?: unknown}
  font_pairings: {hebrew_family: string; english_family: string}
  logo_safe_zones: {safe_zone_percent: number; minimum_clear_space: string; asset_id?: string}
  layout_templates: unknown[]
  flux_style_prefix: string
  flux_constraints: Record<string, unknown>
}
export type Design = Version & DesignFields

export const modes = [
  {id: 'original_branded', title: 'מקורי עם מיתוג', description: 'מסגרת ולוגו מסביב לתמונה המקורית.'},
  {id: 'smart_enhance', title: 'שיפור חכם', description: 'חיתוך סביב הנושא והתאמות אור וצבע.'},
  {id: 'hero_cutout_scene', title: 'סטודיו Wow', description: 'המוצר המקורי על רקע סטודיו חדש.'},
] as const
export type Mode = typeof modes[number]['id']
export type Brief = {id: string; prompt: string; treatment_mode: Mode; platform: Platform; source_asset_id: string}
export type Job = {
  id: string; kind: string; status: string; attempts: number; progress: number
  error_code: string | null; result: Record<string, unknown> | null; updated_at: string; available_at: string
}

export type RuleOverrides = {
  posts_per_week?: number | null; content_link_placement?: 'body' | 'forbidden' | null
  weekdays?: number[] | null; local_times?: string[] | null; excluded_topics?: string[]
  pillar_weights?: Record<string, number> | null; depth_mix?: Partial<Record<Depth, number>> | null
  account_type?: 'company_page' | 'personal_profile' | null; link_policy?: string | null
}
export type Playbook = {
  playbook_version: number; override_version: number | null; posts_per_week: number
  rules: Record<string, unknown>; depth_mix: Partial<Record<Depth, number>>
  link_policy: string; overrides: RuleOverrides
}
export type Strategy = Version & {legacy?: boolean; document: {
  platforms: Platform[]; pillars: {id: string; title: string; goal: string; claim_ids: string[]; audience_claim_ids: string[]}[]
  pillar_weights: Partial<Record<Platform, Record<string, number>>>
  pillar_weight_overrides: Partial<Record<Platform, Record<string, number>>>
  messaging_register: string; hebrew_address: string
  messaging_angles: {pillar_id: string; claim_id: string; hook: string; evidence_text: string}[]
}}
export type Slot = {
  id: string; platform: Platform; kind: 'cadence' | 'on_demand'; scheduled_at: string
  pillar_id: string | null; depth: Depth | null; photo_brief_id: string | null
}
export type Plan = Version & {legacy?: boolean; slots: Slot[]; document: {
  request: {start_date: string; period: 'weekly' | 'monthly'; platforms: Platform[] | null}
}}
export type Citation = {id: string; label: number; url: string; title: string; retrieved_at: string; quoted_span: string}
export type Topic = {id: string; topic: string; content_id: string | null; evidence: Citation[]; created_at: string}
export type Violation = {code: string; field: string; message: string; details: Record<string, unknown>}
export type Copy = {hook: string; body: string; cta: string; hashtags: string[]}
export type ValidationReport = {valid: boolean; violations: Violation[]; attempts: {number: number; violations: Violation[]}[]}
export type Revision = Copy & {
  id: string; version: number; research_topic_id: string | null; external_citations: Citation[]
  cited_claim_ids: string[]; cited_external_evidence_ids: string[]
  hebrew_register_check: {passed: boolean; applies: boolean}
  validation_report: ValidationReport
}
export type ContentSummary = {id: string; platform: Platform; status: string; content_depth: Depth; created_at: string}
export type Content = ContentSummary & {
  revision: Revision; revisions: Revision[]
  context: {format_rules: {caption_max: number; hook_max: number; cta_max: number; hashtag_max: number; link_placement: string}}
}
export const aspects = ['1:1', '4:5', '9:16', '16:9'] as const
export type Aspect = typeof aspects[number]
export type Visual = {
  id: string; aspect: Aspect; content_revision_id: string | null; on_demand_brief_id: string | null
  width: number; height: number; seed: number; providers: Record<string, string>
  files: {format: 'png' | 'pdf' | 'zip'; page: number | null; name: string}[]
  layout: {contrast: number; safe_zone_px: Record<string, number>; template_id: string | null}
}
export type Gate = {
  allowed: boolean; revision_id: string | null; visual_asset_id: string | null
  issues: {code: string; message: string}[]
}
export type Connection = {
  id: string; provider: 'meta' | 'linkedin'; platform: Platform; label: string; has_token: boolean; expires_at: string | null
}
export type Receipt = {id: string; status: string; job_id: string | null; scheduled_at: string | null; detail: Record<string, unknown>}
