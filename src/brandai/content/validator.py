from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from .context import PreparedContent
from .models import DraftCopy, HebrewRegisterCheck, Violation, VisualSelection


NUMBER = re.compile(r"[\d%$€£₪]|\b(?:percent|dollars?|shekels?|euros?)\b|אחוז|שקלים", re.I)
LINK = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]{1,20}://|\bwww\.|\b(?:mailto|tel):|"
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:[a-z]{2,24}|[א-ת]{2,12})(?![\w-])|"
    r"\b[\w-]+\s*(?:\[dot\]|\(dot\)|\[\.\])\s*[\w-]+|"
    r"\b[\w-]+\s+dot\s+(?:com|net|org|io|co|il)\b)",
    re.I,
)
BREAKS = re.compile(r"\n\s*\n")
HASHTAG_TOKEN = re.compile(r"#[\w]+\Z")


def normalized_block(text: str) -> str:
    # Preserve words, their order, and punctuation. Only layout whitespace is equivalent.
    return " ".join(text.split())


def _unsafe_character(text: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char not in "\n\r\t" for char in text)


def caption_text(copy: DraftCopy) -> str:
    return "\n".join(segment for segment in (copy.hook, copy.body, copy.cta, " ".join(copy.hashtags)) if segment)


def validate_copy(
    copy: DraftCopy, selection: VisualSelection, context: PreparedContent,
) -> tuple[list[Violation], HebrewRegisterCheck, bool]:
    failures: list[Violation] = []

    def flag(code: str, field: str, message: str, **details: object) -> None:
        failures.append(Violation(code=code, field=field, message=message, details=details))

    rules = context.provenance.format_rules
    original = {"hook": copy.hook, "body": copy.body, "cta": copy.cta, "hashtags": " ".join(copy.hashtags)}
    for field, value in original.items():
        if _unsafe_character(value):
            flag("unsupported_character", field, "Remove hidden formatting or control characters.")
        if LINK.search(unicodedata.normalize("NFKC", value)) and (rules.link_placement == "forbidden" or field != "body"):
            flag("link_placement", field, "Links are prohibited here by the pinned platform rules.")

    for field, value, limit in (
        ("caption", caption_text(copy), rules.caption_max), ("hook", copy.hook, rules.hook_max),
        ("cta", copy.cta, rules.cta_max),
    ):
        if len(value) > limit:
            flag("character_limit", field, "Shorten the text to the platform budget.", actual=len(value), maximum=limit)

    voice_ok = normalized_block(copy.hook) in context.hooks and normalized_block(copy.cta) in context.ctas
    for field, choices in (("hook", context.hooks), ("cta", context.ctas)):
        text = normalized_block(original[field])
        if not text:
            flag(f"{field}_required", field, f"Choose one approved {field}.")
        elif text not in choices:
            flag("hebrew_address_or_register" if context.language == "he" else "editorial_choice", field,
                 f"Choose a supported {field} for this language, platform and voice.")
            flag("ungrounded_assertion", field, "Only approved editorial text is allowed here.")
            if NUMBER.search(text):
                flag("ungrounded_numeric_assertion", field, "A numeric assertion is outside the approved evidence body.")

    if len(copy.hashtags) > rules.hashtag_max or len(set(copy.hashtags)) != len(copy.hashtags):
        flag("hashtags_format", "hashtags", "Use distinct hashtags within the platform budget.", maximum=rules.hashtag_max)
    for tag in copy.hashtags:
        if not HASHTAG_TOKEN.fullmatch(tag) or tag not in context.hashtags:
            flag("unapproved_hashtag", "hashtags", "Use a hashtag from the neutral vocabulary.")

    evidence: dict[str, set[str]] = defaultdict(set)
    for claim_id, fact in context.facts.items():
        evidence[normalized_block(fact)].add(claim_id)

    # Check an entire paragraph before accepting it. A quote with an extra number or marketing
    # promise is ungrounded, even when the proposal also cites a legitimate claim.
    blocks = BREAKS.split(copy.body.replace("\r\n", "\n").strip()) if copy.body.strip() else []
    declared = {str(claim_id) for claim_id in copy.cited_claim_ids}
    matched: set[str] = set()
    external = {citation.paragraph: str(citation.id) for citation in context.external}
    declared_external = {str(evidence_id) for evidence_id in copy.cited_external_evidence_ids}
    matched_external: set[str] = set()
    event_used = False
    for index, raw in enumerate(blocks, start=1):
        block = normalized_block(raw)
        is_event = context.event_note is not None and block == normalized_block(context.event_note)
        event_used |= is_event
        if block in external:
            evidence_id = external[block]
            if evidence_id in declared_external:
                matched_external.add(evidence_id)
            else:
                flag("missing_external_citation", "body", "Cite the archived source for this quotation.",
                     paragraph=index, evidence_id=evidence_id)
        elif block in evidence:
            supported = evidence[block] & declared
            matched.update(supported)
            if not supported and not is_event:
                flag("missing_claim_citation", "body", "Cite the approved claim for this exact paragraph.", paragraph=index,
                     eligible_claim_ids=sorted(evidence[block]))
        elif not is_event:
            flag("ungrounded_assertion", "body", "Use a complete approved fact or the explicit event note.", paragraph=index)
            if NUMBER.search(block):
                flag("ungrounded_numeric_assertion", "body", "This paragraph contains a number or price outside approved evidence.",
                     paragraph=index)
    if len(blocks) != len({normalized_block(block) for block in blocks}):
        flag("repeated_paragraph", "body", "Use each evidence paragraph once.")
    if declared - matched or len(declared) != len(copy.cited_claim_ids):
        flag("invalid_claim_citation", "cited_claim_ids", "Cite only effective claims used in the body, once each.",
             unused_claim_ids=sorted(declared - matched))
    if declared_external != matched_external or len(declared_external) != len(copy.cited_external_evidence_ids):
        flag("invalid_external_citation", "cited_external_evidence_ids",
             "Cite only archived external quotations used with their source labels.",
             unused_evidence_ids=sorted(declared_external - matched_external))
    if context.research_topic_id and not matched_external:
        flag("external_evidence_required", "body", "Include an attributed quotation from the selected research brief.")
    if context.provenance.required_claim_id and context.provenance.required_claim_id not in matched:
        flag("missing_planned_claim", "body", "Include and cite the planned subject's approved claim.",
             claim_id=context.provenance.required_claim_id)
    if context.event_note is not None and not event_used:
        flag("missing_event_note", "body", "Include the user's event note as a grounded paragraph.")
    if not blocks:
        flag("body_required", "body", "Include at least one grounded paragraph.")

    if selection.template_id is not None and selection.template_id not in context.template_ids:
        flag("unapproved_visual_template", "visual_directive", "Select a layout approved for this pillar or photo brief.")
    if context.brief_id and selection.subject:
        flag("photo_subject_change", "visual_directive", "The treatment uses the original photo subject.")
    check = HebrewRegisterCheck(
        applies=context.language == "he", messaging_register=context.register,
        hebrew_address=context.address, passed=context.language != "he" or voice_ok,
    )
    return failures, check, event_used
