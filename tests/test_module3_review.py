"""Module 3 -- clinician review workflow."""

import pytest

from schemas.review import EntityReviewInput, ReviewDecision, ReviewSubmission
from tests.helpers import SAMPLE_NOTE, coded_by_term


@pytest.fixture()
def coding(extractor, code_mapper):
    extraction = extractor.extract(SAMPLE_NOTE, note_id="N-1", analysis_id="A-1")
    return code_mapper.map_entities(extraction.entities, note_id="N-1", analysis_id="A-1")


def submit(review_manager, coding, reviews):
    return review_manager.apply_reviews(ReviewSubmission(
        analysis_id="A-1", note_id="N-1", coded_entities=coding.coded_entities, reviews=reviews,
    ))


def decision(result, term):
    return next(f for f in result.final_entities if f.source_entity.normalized_term == term)


def review(coding, term, decision, **kw):
    return EntityReviewInput(
        coding_id=coded_by_term(coding, term).coding_id, decision=decision, reviewer_id="dr_ali", **kw
    )


def test_mixed_review_leaves_analysis_in_needs_review(review_manager, coding):
    result = submit(review_manager, coding, [
        review(coding, "shortness of breath", ReviewDecision.APPROVED),
        review(coding, "asthma", ReviewDecision.EDITED,
               edited_code="J45.40", edited_description="Moderate persistent asthma, uncomplicated",
               reviewer_notes="Moderate, not mild."),
    ])
    assert result.analysis_status == "needs_review"
    assert (result.approved_count, result.edited_count, result.rejected_count) == (1, 1, 0)
    # wheezing + the finding are reviewable but still undecided
    assert result.pending_count == 2
    # fever + family-history entity have nothing to review
    assert result.not_applicable_count == 2


def test_edit_overrides_code_and_records_reviewer(review_manager, coding):
    result = submit(review_manager, coding, [
        review(coding, "asthma", ReviewDecision.EDITED,
               edited_code="J45.40", edited_description="Moderate persistent asthma, uncomplicated",
               reviewer_notes="Moderate, not mild."),
    ])
    asthma = decision(result, "asthma")
    assert asthma.final_code == "J45.40"
    assert asthma.reviewer_id == "dr_ali"
    assert asthma.reviewer_notes == "Moderate, not mild."
    assert asthma.reviewed_at is not None


def test_approve_keeps_ai_code(review_manager, coding):
    result = submit(review_manager, coding, [review(coding, "asthma", ReviewDecision.APPROVED)])
    assert decision(result, "asthma").final_code == "J45.30"


def test_reject_clears_final_code(review_manager, coding):
    result = submit(review_manager, coding, [review(coding, "asthma", ReviewDecision.REJECTED)])
    asthma = decision(result, "asthma")
    assert asthma.review_decision == ReviewDecision.REJECTED
    assert asthma.final_code is None


def test_unreviewed_entity_is_pending_not_approved(review_manager, coding):
    result = submit(review_manager, coding, [])
    wheezing = decision(result, "wheezing")
    assert wheezing.review_decision == ReviewDecision.PENDING
    assert wheezing.final_code == "R06.2"  # AI proposal visible, nothing finalized
    assert result.analysis_status == "needs_review"


def test_explicit_pending_submission_is_not_treated_as_a_decision(review_manager, coding):
    result = submit(review_manager, coding, [review(coding, "wheezing", ReviewDecision.PENDING)])
    assert decision(result, "wheezing").review_decision == ReviewDecision.PENDING


def test_negated_and_family_entities_are_not_reviewable(review_manager, coding):
    result = submit(review_manager, coding, [])
    assert decision(result, "fever").review_decision == ReviewDecision.NOT_APPLICABLE
    assert decision(result, "respiratory issues").review_decision == ReviewDecision.NOT_APPLICABLE


def test_regression_finding_is_reviewable_without_a_code(review_manager, coding):
    """Findings were once auto-marked not_applicable because they had no code."""
    finding_term = "bilateral expiratory wheeze"
    assert decision(submit(review_manager, coding, []), finding_term).review_decision == ReviewDecision.PENDING

    result = submit(review_manager, coding, [review(coding, finding_term, ReviewDecision.APPROVED)])
    finding = decision(result, finding_term)
    assert finding.review_decision == ReviewDecision.APPROVED
    assert finding.final_code is None  # approval never fabricates a code


def test_all_reviewable_approved_means_analysis_approved(review_manager, coding):
    terms = ["shortness of breath", "wheezing", "asthma", "bilateral expiratory wheeze"]
    result = submit(review_manager, coding, [review(coding, t, ReviewDecision.APPROVED) for t in terms])
    assert result.analysis_status == "approved"
    assert result.pending_count == 0


def test_all_reviewable_rejected_means_analysis_rejected(review_manager, coding):
    terms = ["shortness of breath", "wheezing", "asthma", "bilateral expiratory wheeze"]
    result = submit(review_manager, coding, [review(coding, t, ReviewDecision.REJECTED) for t in terms])
    assert result.analysis_status == "rejected"


def test_source_entity_survives_review(review_manager, coding):
    result = submit(review_manager, coding, [])
    originals = {c.entity_id: c.source_entity for c in coding.coded_entities}
    assert all(f.source_entity == originals[f.entity_id] for f in result.final_entities)
