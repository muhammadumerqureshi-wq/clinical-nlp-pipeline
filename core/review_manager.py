"""
Module 3 — Clinician Review Engine.

Takes Module 2's CodedEntity list plus a clinician's submitted decisions,
and produces the final "Approved Data" for this analysis: for each entity,
either the AI's code (approved), a corrected code (edited), no code
(rejected), a flag that it's still awaiting review (pending) -- or, for
entities that are genuinely excluded from clinical relevance (negated,
family history), a not_applicable marker (nothing to review).

IMPORTANT: reviewability is gated on WHY an entity has no code
(mapping_method), not merely WHETHER it has one. Findings, measurements,
and entities with no dictionary match are still fully reviewable/
approvable as clinical facts -- they simply never carry an ICD-10-CM
code. Only NOT_CODED_NEGATED and NOT_CODED_FAMILY_HISTORY entities are
excluded from the review workflow entirely, since those genuinely have
nothing for a clinician to confirm.
"""

from datetime import datetime, timezone
from typing import List, Dict

from schemas.coding import CodedEntity, MappingMethod
from schemas.review import (
    EntityReviewInput, ReviewSubmission, ReviewResult, FinalEntity, ReviewDecision,
)

# Entities excluded for these reasons have genuinely NOTHING for a
# clinician to review or confirm -- they were deliberately excluded
# (denied, or belong to someone else's history), not merely "uncoded".
SAFETY_EXCLUDED_METHODS = {MappingMethod.NOT_CODED_NEGATED, MappingMethod.NOT_CODED_FAMILY_HISTORY}


class ClinicianReviewManager:

    def apply_reviews(self, submission: ReviewSubmission) -> ReviewResult:
        reviews_by_coding_id: Dict[str, EntityReviewInput] = {
            r.coding_id: r for r in submission.reviews
        }

        final_entities: List[FinalEntity] = []
        for coded_entity in submission.coded_entities:
            final_entities.append(
                self._resolve_entity(coded_entity, reviews_by_coding_id.get(coded_entity.coding_id))
            )

        counts = self._tally(final_entities)
        analysis_status = self._determine_analysis_status(counts)

        return ReviewResult(
            analysis_id=submission.analysis_id,
            note_id=submission.note_id,
            analysis_status=analysis_status,
            final_entities=final_entities,
            approved_count=counts[ReviewDecision.APPROVED],
            edited_count=counts[ReviewDecision.EDITED],
            rejected_count=counts[ReviewDecision.REJECTED],
            pending_count=counts[ReviewDecision.PENDING],
            not_applicable_count=counts[ReviewDecision.NOT_APPLICABLE],
        )

    def _resolve_entity(self, coded_entity: CodedEntity, review: EntityReviewInput = None) -> FinalEntity:
        # --- Case 1: genuinely nothing to review -- negated or family history ---
        # These are the ONLY reasons an entity should be excluded from the
        # review workflow entirely. This is deliberately narrower than the
        # old check (`selected_code is None`), which incorrectly also
        # excluded findings, measurements, and dictionary-miss entities
        # from review -- even though those are real, clinically approvable
        # facts that simply don't carry an ICD-10-CM code. A clinician can
        # (and should) still be able to approve "bilateral expiratory
        # wheeze" as accurate even though it will never get a billing code.
        if coded_entity.mapping_method in SAFETY_EXCLUDED_METHODS:
            return FinalEntity(
                coding_id=coded_entity.coding_id,
                entity_id=coded_entity.entity_id,
                source_entity=coded_entity.source_entity,
                final_code=None,
                final_description=None,
                review_decision=ReviewDecision.NOT_APPLICABLE,
            )

        # --- Case 2: reviewable (with or without a code), but no decision submitted yet ---
        # Covers EXACT_MATCH, ATTRIBUTE_REFINED (has a code), and
        # NOT_CODEABLE_TYPE / UNMAPPED (no code, but still a real entity
        # the clinician can confirm or reject).
        if review is None:
            return FinalEntity(
                coding_id=coded_entity.coding_id,
                entity_id=coded_entity.entity_id,
                source_entity=coded_entity.source_entity,
                final_code=coded_entity.selected_code,       # None for findings -- never fabricated
                final_description=coded_entity.selected_description,
                review_decision=ReviewDecision.PENDING,
            )

        # --- Case 3: a review decision was submitted ---
        return self._apply_decision(coded_entity, review)

    @staticmethod
    def _apply_decision(coded_entity: CodedEntity, review: EntityReviewInput) -> FinalEntity:
        reviewed_at = datetime.now(timezone.utc)

        if review.decision == ReviewDecision.APPROVED:
            final_code = coded_entity.selected_code
            final_description = coded_entity.selected_description

        elif review.decision == ReviewDecision.EDITED:
            # Clinician overrides the AI's code with their own. Both edited_code
            # and edited_description are required for an edit to make sense --
            # if either is missing, fall back to treating it as a no-op edit
            # (i.e. same as approved) rather than silently producing a broken
            # half-updated record.
            final_code = review.edited_code or coded_entity.selected_code
            final_description = review.edited_description or coded_entity.selected_description

        elif review.decision == ReviewDecision.REJECTED:
            # Rejected means this code should NOT be used at all, even
            # though Module 2 proposed one -- final_code must be None,
            # not the AI's original guess.
            final_code = None
            final_description = None

        else:
            # PENDING or NOT_APPLICABLE submitted explicitly by a client is
            # treated as "no real decision was made" -- keep the AI's
            # proposal visible but flagged as still pending.
            return FinalEntity(
                coding_id=coded_entity.coding_id,
                entity_id=coded_entity.entity_id,
                source_entity=coded_entity.source_entity,
                final_code=coded_entity.selected_code,
                final_description=coded_entity.selected_description,
                review_decision=ReviewDecision.PENDING,
            )

        return FinalEntity(
            coding_id=coded_entity.coding_id,
            entity_id=coded_entity.entity_id,
            source_entity=coded_entity.source_entity,
            final_code=final_code,
            final_description=final_description,
            review_decision=review.decision,
            reviewer_id=review.reviewer_id,
            reviewer_notes=review.reviewer_notes,
            reviewed_at=reviewed_at,
        )

    @staticmethod
    def _tally(final_entities: List[FinalEntity]) -> Dict[ReviewDecision, int]:
        counts = {d: 0 for d in ReviewDecision}
        for fe in final_entities:
            counts[fe.review_decision] += 1
        return counts

    @staticmethod
    def _determine_analysis_status(counts: Dict[ReviewDecision, int]) -> str:
        # Any entity still awaiting a decision means the whole analysis is
        # not yet finalized -- a clinician can't sign off on a note with
        # some codes still unreviewed.
        if counts[ReviewDecision.PENDING] > 0:
            return "needs_review"

        # If literally every codeable entity was rejected, there's nothing
        # left to build a SOAP/coding output from -- treat the whole
        # analysis as rejected rather than a hollow "approved".
        reviewed_total = (counts[ReviewDecision.APPROVED]
                          + counts[ReviewDecision.EDITED]
                          + counts[ReviewDecision.REJECTED])
        if reviewed_total > 0 and counts[ReviewDecision.REJECTED] == reviewed_total:
            return "rejected"

        return "approved"
