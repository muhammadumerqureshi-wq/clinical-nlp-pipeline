"""Test helpers: sample notes and a one-call full-visit runner."""

from types import SimpleNamespace

# Mix of every behaviour the pipeline must get right: a negated symptom, a
# severity-refinable condition, a negated family-history entity, an exam
# finding, and multi-word / overlapping dictionary terms.
SAMPLE_NOTE = (
    "Patient reports shortness of breath and wheezing for the past 3 days, "
    "worse at night. Denies fever. Says her asthma has been getting worse "
    "compared to last month, currently mild persistent. No family history "
    "of respiratory issues. Lungs: bilateral expiratory wheeze on auscultation."
)


def coded_by_term(coding, term):
    return next(c for c in coding.coded_entities if c.source_entity.normalized_term == term)


def approve_all_reviewable(coding, reviewer_id="dr_test"):
    """Approve every entity that is eligible for clinician review."""
    from schemas.coding import MappingMethod
    from schemas.review import EntityReviewInput, ReviewDecision

    excluded = {MappingMethod.NOT_CODED_NEGATED, MappingMethod.NOT_CODED_FAMILY_HISTORY}
    return [
        EntityReviewInput(coding_id=c.coding_id, decision=ReviewDecision.APPROVED, reviewer_id=reviewer_id)
        for c in coding.coded_entities
        if c.mapping_method not in excluded
    ]


def run_visit(services, db, encounter, note_text, reviews=None, persist=True):
    """
    Run one visit through extract -> code -> review -> (persist).
    `reviews` defaults to approving everything reviewable.
    """
    from schemas.persistence import ClinicalNoteCreate
    from schemas.review import ReviewSubmission

    note, analysis = services.persistence.create_clinical_note(
        db, encounter.id, ClinicalNoteCreate(raw_text=note_text)
    )
    extraction = services.extractor.extract(note_text, note_id=note.id, analysis_id=analysis.id)
    coding = services.code_mapper.map_entities(extraction.entities, note_id=note.id, analysis_id=analysis.id)
    if reviews is None:
        reviews = approve_all_reviewable(coding)
    review_result = services.review_manager.apply_reviews(ReviewSubmission(
        analysis_id=analysis.id, note_id=note.id,
        coded_entities=coding.coded_entities, reviews=reviews,
    ))
    persisted = skipped = status = None
    if persist:
        persisted, skipped, status = services.persistence.persist_approved_results(
            db, analysis.id, coding, review_result
        )
    return SimpleNamespace(
        note=note, analysis=analysis, extraction=extraction, coding=coding,
        review_result=review_result, persisted=persisted, skipped=skipped, status=status,
    )
