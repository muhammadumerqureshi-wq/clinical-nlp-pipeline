"""
End-to-end regression: a clinical finding must be reviewable, persistable,
retrievable and usable in SOAP Objective WITHOUT ever receiving an ICD-10-CM
code. (Originally findings were silently dropped because "no code" was used as
a proxy for "nothing to review".)
"""

from db import models as m
from schemas.coding import MappingMethod
from schemas.persistence import EncounterCreate, PatientCreate
from schemas.review import ReviewDecision
from tests.helpers import coded_by_term, run_visit

NOTE = "Lungs: bilateral expiratory wheeze on auscultation."
TERM = "bilateral expiratory wheeze"


def test_finding_flows_through_every_module_without_a_code(services, db):
    patient = services.persistence.create_patient(db, PatientCreate(name="Test Patient"))
    past_enc = services.persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-06-01"))

    # 1. extracted
    out = run_visit(services, db, past_enc, NOTE)
    finding = next(e for e in out.extraction.entities if e.normalized_term == TERM)
    assert finding.entity_type == "finding"

    # 6a. never force-coded by Module 2
    coded = coded_by_term(out.coding, TERM)
    assert coded.selected_code is None
    assert coded.mapping_method == MappingMethod.NOT_CODEABLE_TYPE

    # 2. passes human review without a code
    final = next(f for f in out.review_result.final_entities if f.entity_id == finding.entity_id)
    assert final.review_decision == ReviewDecision.APPROVED
    assert final.final_code is None

    # 3. persisted as approved clinical data, still without a code (6b)
    row = db.get(m.ExtractedEntityORM, finding.entity_id)
    assert row is not None
    assert row.coded_entity.selected_code is None
    assert row.coded_entity.mapping_method == MappingMethod.NOT_CODEABLE_TYPE.value
    assert row.coded_entity.human_review.decision == "approved"

    # 4. retrievable by Module 5
    today_enc = services.persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-07-20"))
    today = run_visit(services, db, today_enc, NOTE + " Patient also reports cough.")
    history = services.history.get_relevant_history(
        db, patient.id, today.extraction.entities, exclude_encounter_id=today_enc.id
    )
    assert any(h.normalized_term == TERM for h in history.mandatory_safety_context)

    # 5. used by SOAP Objective
    soap = services.soap.generate(db, today.analysis.id, history)
    assert TERM in soap.objective
    today_finding = next(e for e in today.extraction.entities if e.normalized_term == TERM)
    assert today_finding.entity_id in soap.source_entity_ids["objective"]
