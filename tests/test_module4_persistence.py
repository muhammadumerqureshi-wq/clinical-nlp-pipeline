"""Module 4 -- persistence of clinician-approved data only."""

import pytest

from db import models as m
from schemas.persistence import ClinicalNoteCreate, EncounterCreate, PatientCreate
from schemas.review import EntityReviewInput, ReviewDecision, ReviewSubmission
from tests.helpers import SAMPLE_NOTE, coded_by_term, run_visit


@pytest.fixture()
def encounter(services, db):
    patient = services.persistence.create_patient(db, PatientCreate(name="Ayesha Khan", dob="1990-04-12", gender="F"))
    return services.persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-07-20"))


def mixed_reviews(coding):
    return [
        EntityReviewInput(coding_id=coded_by_term(coding, "shortness of breath").coding_id,
                          decision=ReviewDecision.APPROVED, reviewer_id="dr_ali"),
        EntityReviewInput(coding_id=coded_by_term(coding, "asthma").coding_id,
                          decision=ReviewDecision.EDITED, edited_code="J45.40",
                          edited_description="Moderate persistent asthma, uncomplicated",
                          reviewer_id="dr_ali", reviewer_notes="Moderate, not mild."),
    ]


def run_mixed(services, db, encounter):
    """Extract + code a note, then review it with a deliberately mixed outcome:
    one approval, one edit, the rest left pending. Returns (analysis, coding, review_result)."""
    note, analysis = services.persistence.create_clinical_note(
        db, encounter.id, ClinicalNoteCreate(raw_text=SAMPLE_NOTE)
    )
    extraction = services.extractor.extract(SAMPLE_NOTE, note_id=note.id, analysis_id=analysis.id)
    coding = services.code_mapper.map_entities(extraction.entities, note_id=note.id, analysis_id=analysis.id)
    review_result = services.review_manager.apply_reviews(ReviewSubmission(
        analysis_id=analysis.id, note_id=note.id,
        coded_entities=coding.coded_entities, reviews=mixed_reviews(coding),
    ))
    return analysis, coding, review_result


def test_only_approved_or_edited_entities_are_persisted(services, db, encounter):
    analysis, coding, review_result = run_mixed(services, db, encounter)
    persisted, skipped, status = services.persistence.persist_approved_results(db, analysis.id, coding, review_result)

    assert (persisted, skipped) == (2, 4)
    assert status == "needs_review"  # wheezing + finding still pending
    rows = db.query(m.ExtractedEntityORM).filter_by(analysis_id=analysis.id).all()
    assert {r.normalized_term for r in rows} == {"shortness of breath", "asthma"}


def test_ai_proposal_and_clinician_edit_are_both_preserved(services, db, encounter):
    analysis, coding, review_result = run_mixed(services, db, encounter)
    services.persistence.persist_approved_results(db, analysis.id, coding, review_result)

    asthma = db.query(m.ExtractedEntityORM).filter_by(analysis_id=analysis.id, normalized_term="asthma").one()
    assert asthma.coded_entity.selected_code == "J45.30"          # what the AI proposed
    assert asthma.coded_entity.human_review.final_code == "J45.40"  # what the clinician decided
    assert asthma.coded_entity.human_review.decision == "edited"
    assert asthma.coded_entity.human_review.reviewer_notes == "Moderate, not mild."


def test_attributes_and_context_are_persisted(services, db, encounter):
    analysis, coding, review_result = run_mixed(services, db, encounter)
    services.persistence.persist_approved_results(db, analysis.id, coding, review_result)

    sob = db.query(m.ExtractedEntityORM).filter_by(analysis_id=analysis.id, normalized_term="shortness of breath").one()
    assert sob.duration == "3 days"
    assert sob.negated is False
    assert sob.subject == "patient"
    assert sob.temporal_status == "current"
    assert sob.source_note_id == analysis.clinical_note_id


def test_persisted_ids_match_pipeline_ids(services, db, encounter):
    analysis, coding, review_result = run_mixed(services, db, encounter)
    services.persistence.persist_approved_results(db, analysis.id, coding, review_result)
    sob = coded_by_term(coding, "shortness of breath")
    row = db.get(m.ExtractedEntityORM, sob.entity_id)
    assert row is not None
    assert row.coded_entity.id == sob.coding_id


def test_repersisting_is_idempotent(services, db, encounter):
    analysis, coding, review_result = run_mixed(services, db, encounter)
    for _ in range(2):
        services.persistence.persist_approved_results(db, analysis.id, coding, review_result)
    assert db.query(m.ExtractedEntityORM).count() == 2
    assert db.query(m.CodedEntityORM).count() == 2
    assert db.query(m.HumanReviewORM).count() == 2


def test_nothing_is_persisted_without_review_decisions(services, db, encounter):
    out = run_visit(services, db, encounter, SAMPLE_NOTE, reviews=[])
    assert out.persisted == 0
    assert db.query(m.ExtractedEntityORM).count() == 0


def test_analysis_status_is_updated_and_finalized(services, db, encounter):
    out = run_visit(services, db, encounter, SAMPLE_NOTE)  # approve everything reviewable
    assert out.status == "approved"
    analysis = db.get(m.AnalysisORM, out.analysis.id)
    assert analysis.status == "approved"
    assert analysis.finalized_at is not None


def test_persisting_unknown_analysis_raises(services, db, encounter):
    analysis, coding, review_result = run_mixed(services, db, encounter)
    with pytest.raises(ValueError):
        services.persistence.persist_approved_results(db, "does-not-exist", coding, review_result)


def test_relationship_chain_patient_to_review(services, db, encounter):
    out = run_visit(services, db, encounter, SAMPLE_NOTE)
    patient = db.get(m.PatientORM, encounter.patient_id)
    chain_review = (patient.encounters[0].clinical_notes[0].analyses[0]
                    .extracted_entities[0].coded_entity.human_review)
    assert chain_review is not None
    assert out.persisted == len(patient.encounters[0].clinical_notes[0].analyses[0].extracted_entities)


def test_approved_entities_query_is_scoped_to_one_patient(services, db):
    p1 = services.persistence.create_patient(db, PatientCreate(name="Patient One"))
    p2 = services.persistence.create_patient(db, PatientCreate(name="Patient Two"))
    e1 = services.persistence.create_encounter(db, p1.id, EncounterCreate(encounter_date="2026-07-01"))
    e2 = services.persistence.create_encounter(db, p2.id, EncounterCreate(encounter_date="2026-07-02"))
    run_visit(services, db, e1, "Patient has asthma.")
    run_visit(services, db, e2, "Patient reports cough.")

    one = services.persistence.get_approved_entities_for_patient(db, p1.id)
    assert {e.normalized_term for e in one} == {"asthma"}


def test_foreign_keys_are_enforced(services, db):
    """A note pointing at a non-existent encounter must be rejected by the DB."""
    from sqlalchemy.exc import IntegrityError
    db.add(m.ClinicalNoteORM(encounter_id="no-such-encounter", raw_text="x"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
