"""Module 5 -- two-lane relevant-history retrieval (SQL exact + BM25 fuzzy)."""

import uuid

import pytest
from rank_bm25 import BM25Okapi

from core.history_retrieval import MIN_CORPUS_SIZE_FOR_BM25
from db import models as m
from schemas.persistence import ClinicalNoteCreate, EncounterCreate, PatientCreate
from tests.helpers import run_visit


@pytest.fixture()
def patient(services, db):
    return services.persistence.create_patient(db, PatientCreate(name="Ayesha Khan"))


def new_encounter(services, db, patient, date):
    return services.persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date=date))


def add_legacy_entities(services, db, encounter, texts):
    """Insert already-approved entities directly, bypassing the extractor, to
    simulate older records phrased in ways the dictionary does not cover."""
    _, analysis = services.persistence.create_clinical_note(db, encounter.id, ClinicalNoteCreate(raw_text="legacy"))
    for i, text in enumerate(texts):
        db.add(m.ExtractedEntityORM(
            id=str(uuid.uuid4()), analysis_id=analysis.id, entity_type="finding",
            raw_text=text, normalized_term=f"legacy_term_{i}", confidence_score=0.9,
        ))
    db.commit()


# ---------- Lane 1: deterministic SQL ----------

def test_sql_lane_returns_exact_term_matches_from_past_visits(services, db, patient):
    past = new_encounter(services, db, patient, "2026-06-01")
    run_visit(services, db, past, "Patient has dry cough. History of mild asthma, well controlled.")
    today = new_encounter(services, db, patient, "2026-07-20")
    current = services.extractor.extract("Patient reports cough today. Asthma currently moderate persistent.")

    result = services.history.get_relevant_history(
        db, patient.id, current.entities, exclude_encounter_id=today.id
    )
    terms = {e.normalized_term for e in result.mandatory_safety_context}
    assert terms == {"cough", "asthma"}
    assert all(e.retrieval_method == "sql_exact" and e.match_score == 1.0 for e in result.mandatory_safety_context)
    asthma = next(e for e in result.mandatory_safety_context if e.normalized_term == "asthma")
    assert asthma.severity == "mild"
    assert asthma.encounter_date == "2026-06-01"


def test_trend_summary_is_chronological(services, db, patient):
    for date, note in [("2026-05-01", "Patient has mild asthma."), ("2026-06-01", "Patient has moderate asthma.")]:
        run_visit(services, db, new_encounter(services, db, patient, date), note)
    today = new_encounter(services, db, patient, "2026-07-20")
    current = services.extractor.extract("Patient has asthma.")

    result = services.history.get_relevant_history(db, patient.id, current.entities, exclude_encounter_id=today.id)
    points = result.trend_summary["asthma"]
    assert [p["encounter_date"] for p in points] == ["2026-05-01", "2026-06-01"]
    assert [p["severity"] for p in points] == ["mild", "moderate"]


def test_current_encounter_is_never_matched_against_itself(services, db, patient):
    today = new_encounter(services, db, patient, "2026-07-20")
    run_visit(services, db, today, "Patient has asthma.")
    current = services.extractor.extract("Patient has asthma.")

    result = services.history.get_relevant_history(db, patient.id, current.entities, exclude_encounter_id=today.id)
    assert result.mandatory_safety_context == []


def test_history_is_scoped_to_the_requested_patient(services, db, patient):
    other = services.persistence.create_patient(db, PatientCreate(name="Someone Else"))
    run_visit(services, db, new_encounter(services, db, other, "2026-06-01"), "Patient has asthma.")
    current = services.extractor.extract("Patient has asthma.")

    result = services.history.get_relevant_history(db, patient.id, current.entities)
    assert result.mandatory_safety_context == []
    assert result.relevant_historical_context == []


def test_only_persisted_approved_entities_are_retrievable(services, db, patient):
    past = new_encounter(services, db, patient, "2026-06-01")
    run_visit(services, db, past, "Patient has asthma.", reviews=[])  # nothing approved -> nothing persisted
    current = services.extractor.extract("Patient has asthma.")

    result = services.history.get_relevant_history(db, patient.id, current.entities)
    assert result.mandatory_safety_context == []


# ---------- Lane 2: BM25 ----------

def test_bm25_surfaces_related_history_that_shares_no_normalized_term(services, db, patient):
    legacy = new_encounter(services, db, patient, "2026-05-01")
    add_legacy_entities(services, db, legacy, [
        "chest tightness noted on prior exam",
        "dry cough for two weeks",
        "mild headache resolved with rest",
        "fatigue reported during follow-up",
    ])
    current = services.extractor.extract("Patient reports chest discomfort today.")

    result = services.history.get_relevant_history(db, patient.id, current.entities)
    assert result.mandatory_safety_context == []
    assert [e.raw_text for e in result.relevant_historical_context] == ["chest tightness noted on prior exam"]
    assert result.relevant_historical_context[0].retrieval_method == "bm25_fuzzy"


def test_regression_bm25_is_skipped_on_tiny_corpus_instead_of_returning_misleading_empty(services, db, patient):
    """Classic BM25 IDF goes negative when a term appears in most/all documents
    of a very small corpus, silently discarding genuine matches. Below
    MIN_CORPUS_SIZE_FOR_BM25 the lane is skipped explicitly."""
    # Demonstrate the underlying quirk the guard exists for:
    one_doc = BM25Okapi([["chest", "tightness", "noted", "on", "prior", "exam"]])
    assert one_doc.get_scores(["chest", "discomfort"])[0] < 0

    legacy = new_encounter(services, db, patient, "2026-05-01")
    add_legacy_entities(services, db, legacy, ["chest tightness noted on prior exam"])
    assert 1 < MIN_CORPUS_SIZE_FOR_BM25
    current = services.extractor.extract("Patient reports chest discomfort today.")

    result = services.history.get_relevant_history(db, patient.id, current.entities)
    assert result.relevant_historical_context == []


def test_bm25_never_resurfaces_entries_already_found_by_sql(services, db, patient):
    run_visit(services, db, new_encounter(services, db, patient, "2026-06-01"),
              "Patient has dry cough. History of mild asthma.")
    legacy = new_encounter(services, db, patient, "2026-05-01")
    add_legacy_entities(services, db, legacy, ["alpha one", "beta two", "gamma three"])
    current = services.extractor.extract("Patient reports cough. Asthma, moderate.")

    result = services.history.get_relevant_history(db, patient.id, current.entities)
    sql_ids = {e.entity_id for e in result.mandatory_safety_context}
    bm25_ids = {e.entity_id for e in result.relevant_historical_context}
    assert sql_ids and sql_ids.isdisjoint(bm25_ids)
