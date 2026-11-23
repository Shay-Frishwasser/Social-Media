from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TypeVar
from zoneinfo import ZoneInfo

from brandai.errors import GenerationBlockedError

from .models import CadenceDocument, Depth, Platform, StrategyDocument, SUBSTANTIAL_DEPTHS


JERUSALEM = ZoneInfo("Asia/Jerusalem")
DEFAULT_TIMES = tuple(time(hour) for hour in range(8, 22))
T = TypeVar("T", bound=str)


@dataclass(frozen=True, slots=True)
class Slot:
    platform: Platform
    at_utc: datetime
    pillar_id: str | None = None
    claim_id: str | None = None
    depth: Depth | None = None
    photo_brief_id: str | None = None


def add_days(day: date, count: int) -> date:
    return day + timedelta(days=count)


def end_date(start: date, period: str) -> date:
    if period == "weekly":
        return add_days(start, 7)
    return date(start.year + (start.month == 12), start.month % 12 + 1, 1)


def utc_naive(when: datetime) -> datetime:
    return when.astimezone(UTC).replace(tzinfo=None)


def local_instant(day: date, local_time: time) -> datetime | None:
    # A repeated wall time uses the earlier instant. A skipped wall time has no slot.
    local = datetime.combine(day, local_time, tzinfo=JERUSALEM)
    converted = local.astimezone(UTC)
    if converted.astimezone(JERUSALEM).replace(tzinfo=None) != local.replace(tzinfo=None):
        return None
    return converted.replace(tzinfo=None)


def _choose(weights: Mapping[T, int], used: Counter[T]) -> T:
    position = sum(used.values()) + 1
    total = sum(weights.values())
    return max((key for key in weights if weights[key]), key=lambda key: (position * weights[key] - total * used[key], key))


def _depth_mix(weights: dict[Depth, int], count: int, start: date) -> Iterable[Depth]:
    used: Counter[Depth] = Counter()
    light = {key: value for key, value in weights.items() if key not in SUBSTANTIAL_DEPTHS and value}
    substantial = {key: value for key, value in weights.items() if key in SUBSTANTIAL_DEPTHS and value}
    if count == 1:
        # A low-frequency channel still alternates editorial depth across consecutive weeks.
        group = substantial if (start.toordinal() // 7) % 2 == 0 else light
        ordered_depths = sorted(group, key=lambda key: (-group[key], key))
        yield ordered_depths[(start.toordinal() // 14) % len(ordered_depths)]
        return
    for index in range(count):
        missing_group = light if not any(used[key] for key in light) else substantial
        if index == 1:
            depth = _choose(missing_group, Counter())
        elif index == 2 and sum(value > 0 for value in weights.values()) >= 3:
            depth = _choose({key: value for key, value in weights.items() if not used[key]}, Counter())
        else:
            depth = _choose(weights, used)
        used[depth] += 1
        yield depth


def _spread(candidates: list[datetime], count: int) -> list[datetime]:
    if count == 0:
        return []
    return [candidates[((2 * index + 1) * len(candidates)) // (2 * count)] for index in range(count)]


def _week_dates(week: date, weekdays: list[int], frequency: int) -> Counter[date]:
    # Assign frequency across all preferred weekdays before trimming a partial calendar week.
    days = [add_days(week, weekday) for weekday in sorted(weekdays)]
    extra = frequency % len(days)
    indexes = {((2 * index + 1) * len(days)) // (2 * extra) for index in range(extra)}
    return Counter({day: frequency // len(days) + (index in indexes) for index, day in enumerate(days)})


def _match_times(entries: list[tuple[Platform, list[datetime]]]) -> list[tuple[Platform, datetime]]:
    # Augmenting paths keep overlapping local-time preferences from blocking a feasible assignment.
    owners: dict[datetime, int] = {}

    def place(index: int, visited: set[datetime]) -> bool:
        for candidate in entries[index][1]:
            if candidate in visited:
                continue
            visited.add(candidate)
            previously = owners.get(candidate)
            if previously is None or place(previously, visited):
                owners[candidate] = index
                return True
        return False

    for index in sorted(range(len(entries)), key=lambda item: len(entries[item][1])):
        if not place(index, set()):
            platform, _ = entries[index]
            raise GenerationBlockedError(f"insufficient non-overlapping posting times for {platform}")
    return [(entries[index][0], instant) for instant, index in sorted(owners.items())]


def _assign_times(document: CadenceDocument, busy: set[datetime]) -> dict[Platform, list[datetime]]:
    start, end = document.request.start_date, document.end_date_exclusive
    by_platform: dict[Platform, list[datetime]] = {platform: [] for platform in document.platforms}
    week = add_days(start, -start.weekday())
    while week < end:
        entries_by_day: dict[date, list[tuple[Platform, list[datetime]]]] = {}
        for platform in document.platforms:
            playbook = document.playbooks[platform]
            weekdays = playbook.overrides.weekdays or list(range(7))
            times = playbook.overrides.local_times or DEFAULT_TIMES
            for day, count in _week_dates(week, weekdays, playbook.posts_per_week).items():
                if not count or not start <= day < end:
                    continue
                candidates = sorted(instant for clock in times if (
                    (instant := local_instant(day, clock)) is not None and instant not in busy
                ))
                if len(candidates) < count:
                    raise GenerationBlockedError(f"insufficient posting times for {platform} on {day}")
                for preferred in _spread(candidates, count):
                    by_preference = sorted(candidates, key=lambda instant: (abs(instant - preferred), instant))
                    entries_by_day.setdefault(day, []).append((platform, by_preference))
        for day in sorted(entries_by_day):
            for platform, instant in _match_times(entries_by_day[day]):
                by_platform[platform].append(instant)
        week = add_days(week, 7)
    return by_platform


def _topic_key(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def allowed_claims(strategy: StrategyDocument, excluded_topics: list[str]) -> dict[str, list[str]]:
    evidence = {claim.id: claim for claim in strategy.claims}
    excluded = [key for topic in excluded_topics if (key := _topic_key(topic))]
    by_pillar: dict[str, list[str]] = {}
    for angle in strategy.messaging_angles:
        claim = evidence[angle.claim_id]
        text = f"{angle.pillar_id} {claim.field_key} {angle.evidence_text} {json.dumps(claim.value, ensure_ascii=False)}"
        normalized_text = f" {_topic_key(text)} "
        if not any(f" {topic} " in normalized_text for topic in excluded):
            by_pillar.setdefault(angle.pillar_id, []).append(angle.claim_id)
    return by_pillar


def generate_slots(document: CadenceDocument, strategy: StrategyDocument, busy: set[datetime]) -> list[Slot]:
    scheduled = _assign_times(document, busy)
    slots = []
    for platform, times in scheduled.items():
        playbook = document.playbooks[platform]
        allowed = allowed_claims(strategy, playbook.overrides.excluded_topics)
        weights = {key: weight for key, weight in document.pillar_weights[platform].items()
                   if key in allowed and weight}
        if times and not weights:
            raise GenerationBlockedError(f"excluded topics leave no eligible weighted pillars for {platform}")
        used: Counter[str] = Counter()
        depths = _depth_mix(playbook.depth_mix, len(times), document.request.start_date)
        for instant, depth in zip(times, depths, strict=True):
            pillar = _choose(weights, used)
            claims = allowed[pillar]
            slots.append(Slot(platform, instant, pillar, claims[used[pillar] % len(claims)], depth))
            used[pillar] += 1
    return sorted(slots, key=lambda slot: slot.at_utc)
