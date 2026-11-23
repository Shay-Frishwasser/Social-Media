from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from subprocess import SubprocessError

import httpx
from pydantic import ValidationError
from sqlalchemy import text

from brandai.adapters.publishing import DeliveryReceipt
from brandai.db.base import utcnow
from brandai.db.brand_models import ContentItem, ContentRevision
from brandai.errors import GenerationBlockedError, InvalidStateTransitionError
from brandai.onboarding.embeddings import EmbeddingProvider
from brandai.store import BrandAIStore
from brandai.strategy.models import Platform

from .context import PreparedContent, prepare_content
from .diversity import DiversityScorer, DiversitySettings
from .generator import ContentLLMProvider
from .models import (
    ContentApproval, ContentContext, ContentGenerate, ContentGenerationUnavailable, ContentPatch, ContentProposal,
    ContentResponse, ContentStatus, ContentSummary, DraftCopy, HebrewRegisterCheck, ValidationAttempt,
    ValidationReport, Violation, VisualSelection,
)
from .repository import ContentRepository, revision_copy
from .validator import validate_copy


@dataclass
class Evaluation:
    violations: list[Violation]
    voice: HebrewRegisterCheck
    event_used: bool
    embedding: bytes | None
    embedding_model: str | None


class ContentService:
    def __init__(
        self, store: BrandAIStore, provider: ContentLLMProvider, embeddings: EmbeddingProvider,
        diversity: DiversitySettings,
    ):
        self.store, self.provider = store, provider
        self.scorer = DiversityScorer(embeddings, diversity)

    def list_content(self, brand_id: str, *, platform: Platform | None, status: ContentStatus | None,
                     limit: int, offset: int) -> list[ContentSummary]:
        with self.store.brand_session(brand_id) as session:
            return ContentRepository(session, brand_id).list(platform=platform, status=status, limit=limit, offset=offset)

    def get_content(self, brand_id: str, content_id: str) -> ContentResponse:
        with self.store.brand_session(brand_id) as session:
            session.execute(text("BEGIN"))
            repo = ContentRepository(session, brand_id)
            return repo.response(repo.get(content_id))

    def generate(
        self, brand_id: str, payload: ContentGenerate, *, slot_id: str | None = None, brief_id: str | None = None,
    ) -> tuple[ContentResponse, bool]:
        sources = {"slot_id": slot_id, "brief_id": brief_id, "language": None if payload.language == "auto" else payload.language}
        with self.store.brand_session(brand_id) as session:
            prepared = prepare_content(self.store, session, **sources)
            repo = ContentRepository(session, brand_id)
            if existing := repo.existing(prepared):
                return repo.response(existing), False

        attempts: list[ValidationAttempt] = []
        feedback: list[Violation] = []
        selection = prepared.default_visual.selection
        for attempt_number in (1, 2):
            copy, provider_issues = self._propose(prepared, feedback)
            evaluation = self._evaluate(copy, selection, prepared, provider_issues=provider_issues)
            # The model and embedding calls already finished. Reserve the final brand write only
            # long enough to recheck evidence, compare history, and append an immutable revision.
            with self.store.brand_session(brand_id, immediate=True) as session:
                fresh = prepare_content(self.store, session, **sources)
                self._require_unchanged(prepared, fresh)
                repo = ContentRepository(session, brand_id)
                if existing := repo.existing(prepared):
                    return repo.response(existing), False
                self._check_diversity(repo, copy, evaluation)
                feedback = evaluation.violations
                attempts.append(ValidationAttempt(number=attempt_number, violations=feedback))
                if feedback and attempt_number == 1:
                    continue
                row = repo.create(prepared)
                return self._append(repo, row, copy, selection, prepared, evaluation, attempts, payload.created_by, version=1), True
        raise AssertionError("generation must finalize on the second attempt")

    def _propose(self, prepared: PreparedContent, feedback: list[Violation]) -> tuple[DraftCopy, list[Violation]]:
        try:
            proposed = self.provider.generate(prepared, feedback=tuple(feedback))
            return ContentProposal.model_validate(proposed), []
        except (ValidationError, ValueError, KeyError, IndexError, TypeError):
            message = "Return one structured caption with hook, body, CTA, hashtags and cited claim IDs."
            return DraftCopy(), [Violation(code="provider_format", field="proposal", message=message)]
        except (httpx.HTTPError, OSError, RuntimeError, SubprocessError) as exc:
            raise ContentGenerationUnavailable("the local content model or worker is unavailable") from exc

    def _evaluate(
        self, copy: DraftCopy, selection: VisualSelection, prepared: PreparedContent, *,
        provider_issues: list[Violation] | None = None, prior: ContentRevision | None = None,
    ) -> Evaluation:
        violations, voice, event_used = validate_copy(copy, selection, prepared)
        if provider_issues:
            violations = [*provider_issues, *violations]
        embedding, embedding_model = None, None
        if not violations:
            try:
                if prior is None:
                    embedding = self.scorer.encode(copy)
                    embedding_model = self.scorer.embeddings.model_name
                else:
                    embedding, embedding_model = prior.embedding, prior.embedding_model
            except (OSError, RuntimeError, ValueError, ImportError) as exc:
                raise ContentGenerationUnavailable("the configured content embedding provider is unavailable") from exc
        return Evaluation(violations, voice, event_used, embedding, embedding_model)

    def _check_diversity(
        self, repo: ContentRepository, copy: DraftCopy, evaluation: Evaluation, *, omit_id: str | None = None,
    ) -> None:
        if evaluation.violations or evaluation.embedding is None or evaluation.embedding_model is None:
            return
        history = repo.recent(self.scorer.settings.history_limit, omit_content_id=omit_id)
        evaluation.violations.extend(self.scorer.violations(
            copy, evaluation.embedding, history, embedding_model=evaluation.embedding_model,
        ))

    @staticmethod
    def _append(
        repo: ContentRepository, row: ContentItem, copy: DraftCopy, selection: VisualSelection, prepared: PreparedContent,
        evaluation: Evaluation, attempts: list[ValidationAttempt], actor: str, *, version: int,
    ) -> ContentResponse:
        report = ValidationReport(
            valid=not evaluation.violations, violations=evaluation.violations,
            attempts=attempts, event_note_used=evaluation.event_used,
        )
        return repo.append(row, copy, prepared.directive(selection), evaluation.voice, report, actor,
                           version=version, embedding=evaluation.embedding, embedding_model=evaluation.embedding_model)

    @staticmethod
    def _check_head(revision: ContentRevision, expected: str) -> None:
        if revision.id != expected:
            raise InvalidStateTransitionError("the current revision changed; reload content before continuing")

    @staticmethod
    def _require_unchanged(previous: PreparedContent, current: PreparedContent) -> None:
        if previous != current:
            raise GenerationBlockedError("content inputs changed during processing; retry with the current inputs")

    def _current_context(self, repo: ContentRepository, row: ContentItem) -> PreparedContent:
        return prepare_content(
            self.store, repo.session, slot_id=row.cadence_slot_id, brief_id=row.on_demand_brief_id,
            language=row.language, pinned_strategy_id=row.strategy_id,
            pinned_context=ContentContext.model_validate(row.context_json),
        )

    def patch(self, brand_id: str, content_id: str, payload: ContentPatch) -> ContentResponse:
        with self.store.brand_session(brand_id) as session:
            repo = ContentRepository(session, brand_id)
            row = repo.get(content_id)
            latest = self._editable_head(repo, row, str(payload.revision_id))
            prepared = self._current_context(repo, row)
            copy_fields = payload.model_dump(exclude_unset=True, exclude={"revision_id", "edited_by", "visual_directive"})
            copy = DraftCopy.model_validate({**revision_copy(latest).model_dump(), **copy_fields})
            selection = payload.visual_directive or VisualSelection.model_validate(latest.visual_directive["selection"])
        evaluation = self._evaluate(copy, selection, prepared)
        with self.store.brand_session(brand_id, immediate=True) as session:
            repo = ContentRepository(session, brand_id)
            row = repo.get(content_id)
            latest = self._editable_head(repo, row, str(payload.revision_id))
            self._require_unchanged(prepared, self._current_context(repo, row))
            self._check_diversity(repo, copy, evaluation, omit_id=row.id)
            attempt = ValidationAttempt(number=1, violations=evaluation.violations)
            return self._append(
                repo, row, copy, selection, prepared, evaluation, [attempt], payload.edited_by, version=latest.version + 1,
            )

    def _editable_head(self, repo: ContentRepository, row: ContentItem, expected: str) -> ContentRevision:
        if row.status in {"SCHEDULED", "PUBLISHED"}:
            raise InvalidStateTransitionError("scheduled or published content cannot be edited")
        latest = repo.latest(row)
        self._check_head(latest, expected)
        return latest

    def approve(self, brand_id: str, content_id: str, payload: ContentApproval) -> ContentResponse:
        with self.store.brand_session(brand_id, immediate=True) as session:
            repo = ContentRepository(session, brand_id)
            row = repo.get(content_id)
            latest = repo.latest(row)
            self._check_head(latest, str(payload.revision_id))
            if row.status == "APPROVED":
                return repo.response(row)
            if row.status != "READY_FOR_REVIEW":
                raise InvalidStateTransitionError("approval requires a ready-for-review revision")
            self._revalidate_saved_copy(repo, row, latest, check_diversity=True)
            row.status, row.approved_by, row.approved_at = "APPROVED", payload.approved_by, utcnow()
            row.approved_revision_version = latest.version
            return repo.response(row)

    def record_scheduled(self, brand_id: str, content_id: str, revision_id: str, when: datetime) -> ContentResponse:
        if when.utcoffset() is None:
            raise ValueError("scheduling requires a timezone-aware timestamp")
        with self.store.brand_session(brand_id, immediate=True) as session:
            repo = ContentRepository(session, brand_id)
            row = repo.get(content_id)
            revision = self._require_approved(repo, row, revision_id, allowed=("APPROVED",))
            self._revalidate_saved_copy(repo, row, revision, check_diversity=False)
            row.status, row.scheduled_at = "SCHEDULED", when.astimezone(UTC)
            return repo.response(row)

    def record_published(
        self, brand_id: str, content_id: str, revision_id: str, receipt: DeliveryReceipt,
    ) -> ContentResponse:
        if receipt.status.upper() != "PUBLISHED" or not receipt.provider.strip():
            raise InvalidStateTransitionError("a successful publishing adapter receipt is required")
        with self.store.brand_session(brand_id, immediate=True) as session:
            repo = ContentRepository(session, brand_id)
            row = repo.get(content_id)
            self._require_approved(repo, row, revision_id, allowed=("APPROVED", "SCHEDULED"))
            row.status, row.published_at = "PUBLISHED", utcnow()
            row.delivery_receipt = {"provider": receipt.provider, "external_id": receipt.external_id, "status": receipt.status}
            return repo.response(row)

    def _require_approved(
        self, repo: ContentRepository, row: ContentItem, revision_id: str, *, allowed: tuple[str, ...],
    ) -> ContentRevision:
        revision = repo.latest(row)
        self._check_head(revision, revision_id)
        if row.status not in allowed or row.approved_revision_version != revision.version:
            raise InvalidStateTransitionError("delivery requires human approval of the current revision")
        return revision

    def _revalidate_saved_copy(
        self, repo: ContentRepository, row: ContentItem, revision: ContentRevision, *, check_diversity: bool,
    ) -> None:
        prepared = self._current_context(repo, row)
        copy = revision_copy(revision)
        selection = VisualSelection.model_validate(revision.visual_directive["selection"])
        evaluation = self._evaluate(copy, selection, prepared, prior=revision)
        if check_diversity:
            self._check_diversity(repo, copy, evaluation, omit_id=row.id)
        if evaluation.violations:
            codes = ", ".join(dict.fromkeys(issue.code for issue in evaluation.violations))
            raise GenerationBlockedError(f"the current revision failed its validation checks ({codes})")
