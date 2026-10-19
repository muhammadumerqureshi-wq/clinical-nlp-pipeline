"""Module 6 -- draft SOAP generation (deterministic template composer)."""

import pytest

from db import models as m
from schemas.history import RelevantHistoryResult
from schemas.persistence import ClinicalNoteCreate, EncounterCreate, PatientCreate
from schemas.soap import GenerationMethod
from tests.helpers import run_visit

VISIT1 = "Patient has mild asthma, well controlled."
VISIT2 = ("Patient reports shortness of breath and wheezing for 3 days. "
          "Asthma currently moderate persistent, worsening. "
          "Lungs: bilateral expiratory wheeze on auscultation.")


@pytest.fixture()
def two_visits(services, db):
    patient = services.persistence.create_patient(db, PatientCreate(name="Ayesha Khan"))
    e1 = services.persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-06-01"))
    run_visit(services, db, e1, VISIT1)
    e2 = services.persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-07-20"))
    today = run_visit(services, db, e2, VISIT2)
    history = services.history.get_relevant_history(
        db, patient.id, today.extraction.entities, exclude_encounter_id=e2.id
    )
    return today, history


def empty_history(patient_id="p"):
    return RelevantHistoryResult(
        patient_id=patient_id, mandatory_safety_context=[], relevant_historical_context=[], trend_summary={}
    )


def test_soap_is_always_a_draft(services, db, two_visits):
    today, history = two_visits
    soap = services.soap.generate(db, today.analysis.id, history)
    assert soap.status == "draft"
    assert soap.generation_method == GenerationMethod.TEMPLATE_COMPOSER


def test_all_four_sections_are_built_from_approved_data(services, db, two_visits):
    today, history = two_visits
    soap = services.soap.generate(db, today.analysis.id, history)
    assert "shortness of breath for 3 days" in soap.subjective
    assert "wheezing for 3 days" in soap.subjective
    assert "bilateral expiratory wheeze" in soap.objective
    assert "J45.40" in soap.assessment
    assert "prior documented severity: mild" in soap.assessment  # from the bounded history


def test_plan_is_never_fabricated(services, db, two_visits):
    today, history = two_visits
    soap = services.soap.generate(db, today.analysis.id, history)
    assert "clinician to complete" in soap.plan
    assert soap.source_entity_ids["plan"] == []
    assert any("Plan section" in w for w in soap.warnings)


def test_every_section_is_traceable_to_persisted_entities(services, db, two_visits):
    today, history = two_visits
    soap = services.soap.generate(db, today.analysis.id, history)
    persisted_ids = {e.id for e in db.get(m.AnalysisORM, today.analysis.id).extracted_entities}
    for section in ("subjective", "objective", "assessment"):
        assert soap.source_entity_ids[section], f"{section} has no source entities"
        assert set(soap.source_entity_ids[section]) <= persisted_ids
    assert soap.history_entity_ids_used  # the trend statement cites its history entry


def test_history_is_used_only_when_the_caller_supplies_it(services, db, two_visits):
    """The generator must never go fetch history itself."""
    today, _ = two_visits
    soap = services.soap.generate(db, today.analysis.id, empty_history())
    assert "prior documented severity" not in soap.assessment
    assert soap.history_entity_ids_used == []


def test_empty_analysis_produces_placeholders_and_warning_not_invented_content(services, db):
    patient = services.persistence.create_patient(db, PatientCreate(name="Empty"))
    enc = services.persistence.create_encounter(db, patient.id, EncounterCreate())
    _, analysis = services.persistence.create_clinical_note(db, enc.id, ClinicalNoteCreate(raw_text="x"))

    soap = services.soap.generate(db, analysis.id, empty_history())
    assert soap.subjective.startswith("No subjective")
    assert soap.objective.startswith("No objective")
    assert soap.assessment.startswith("No coded diagnosis")
    assert any("No approved entities" in w for w in soap.warnings)


def test_unknown_analysis_raises(services, db):
    with pytest.raises(ValueError):
        services.soap.generate(db, "does-not-exist", empty_history())


def test_regression_situational_variation_is_not_reported_as_worsening(services, db):
    """'worse at night' must not leak into the SOAP Subjective as 'worsening'."""
    patient = services.persistence.create_patient(db, PatientCreate(name="P"))
    enc = services.persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-07-20"))
    out = run_visit(services, db, enc, "Patient reports cough for 3 days, worse at night.")
    soap = services.soap.generate(db, out.analysis.id, empty_history())
    assert "cough for 3 days" in soap.subjective
    assert "worsening" not in soap.subjective
