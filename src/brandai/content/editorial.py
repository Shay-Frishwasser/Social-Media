from __future__ import annotations

import re

from brandai.errors import GenerationBlockedError
from brandai.strategy.models import Depth, EffectivePlaybook, HebrewAddress, Register

from .models import FormatRules, Language


# Conservative BrandAI caption budgets for v1. They are editorial constraints, not API limits.
V1_BUDGETS = {
    "instagram": (2200, 125, 100, 5, "forbidden"),
    "tiktok": (2200, 100, 100, 5, "forbidden"),
    "linkedin": (3000, 150, 125, 5, "body"),
    "facebook": (3000, 140, 120, 4, "body"),
    "youtube": (5000, 100, 110, 5, "body"),
    "x": (280, 80, 60, 2, "body"),
}

EN_HOOKS = (
    "A closer look.", "Start with this detail.", "One point to consider.", "In focus today.",
    "Take a moment to explore.", "Here is the context.",
)
HE_HOOKS = (
    "מבט מקרוב.", "כדאי להתחיל מהפרט הזה.", "נקודה אחת למחשבה.", "במוקד היום.",
    "הזדמנות להתעמק.", "הנה ההקשר.",
)
EN_DEPTH_HOOKS = {
    "micro": "A quick note.", "standard": "Worth a look.", "deep_dive": "Let’s look at the details.",
    "educational_framework": "A starting point for discussion.", "story": "A moment to share.",
    "carousel": "A closer look, one point at a time.",
}
HE_DEPTH_HOOKS = {
    "micro": "בקצרה.", "standard": "שווה מבט.", "deep_dive": "מקום להתעמק בפרטים.",
    "educational_framework": "נקודת פתיחה לדיון.", "story": "רגע לשיתוף.",
    "carousel": "מבט מקרוב, נקודה אחר נקודה.",
}

EN_CTAS = {
    "instagram": ("Save this post.", "Share this post."),
    "tiktok": ("Share your take.", "Leave a comment."),
    "linkedin": ("Share your perspective.", "Add your experience in the comments."),
    "facebook": ("Join the discussion.", "Leave a comment."),
    "youtube": ("Leave a comment.", "Share your perspective below."),
    "x": ("Reply with your take.", "Join the conversation."),
}
HE_CTAS = {
    "neutral": ("אפשר לשתף מחשבה בתגובות.", "אפשר להצטרף לדיון."),
    "inclusive": ("אפשר לשתף מחשבה בתגובות.", "מוזמנות ומוזמנים לשתף."),
    "feminine": ("שתפי מחשבה בתגובות.", "הצטרפי לדיון."),
    "masculine": ("שתף מחשבה בתגובות.", "הצטרף לדיון."),
    "plural": ("שתפו מחשבה בתגובות.", "הצטרפו לדיון."),
}

# These vocabulary entries carry no brand or event claims.
HASHTAGS = {
    "en": ("#BrandUpdate", "#InFocus", "#FromTheBrand"),
    "he": ("#עדכון", "#במוקד", "#מבטמקרוב"),
}
HE_REGISTER_HOOKS = {
    "professional": ("נקודה לדיון מקצועי.", "עיון בפרטים."),
    "conversational": ("אפשר להסתכל מקרוב.", "מה במוקד היום?"),
    "formal": ("לעיון ולדיון.", "להלן הפרטים."),
    "neutral": (),
}
# Restrictive wording already stored in legacy free-text overrides.
LINK_BAN = re.compile(r"\b(?:no (?:external )?links|links? (?:are )?(?:prohibited|forbidden|disallowed)|"
                      r"(?:ban|avoid|do not (?:use|include)) (?:external )?links)\b|ללא קישורים|אין להוסיף קישורים", re.I)


def format_rules(playbook: EffectivePlaybook) -> FormatRules:
    if playbook.playbook_version != 1:
        raise GenerationBlockedError("the playbook version has no content format rules")
    caption, hook, cta, hashtags, placement = V1_BUDGETS[playbook.platform]
    if LINK_BAN.search(playbook.link_policy):
        placement = "forbidden"
    elif playbook.overrides.content_link_placement == "forbidden":
        placement = "forbidden"
    return FormatRules(caption_max=caption, hook_max=hook, cta_max=cta, hashtag_max=hashtags,
                       link_placement=placement)


def copy_choices(
    playbook: EffectivePlaybook, depth: Depth, language: Language, register: Register, address: HebrewAddress,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if language == "en":
        return (EN_DEPTH_HOOKS[depth], *EN_HOOKS), EN_CTAS[playbook.platform], HASHTAGS["en"]
    return (*HE_REGISTER_HOOKS[register], HE_DEPTH_HOOKS[depth], *HE_HOOKS), HE_CTAS[address], HASHTAGS["he"]
