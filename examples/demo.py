"""
End-to-end demo of the pipeline on two synthetic visits for one patient.

    python examples/demo.py

Uses a throwaway SQLite database; nothing is written to your working tree.
All clinical text below is synthetic.
"""

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.mkdtemp(), 'demo.db')}"

from core.code_mapper import ClinicalCodeMapper  # noqa: E402
from core.extractor import ClinicalEntityExtractor  # noqa: E402
from core.history_retrieval import HistoryRetrievalService  # noqa: E402
from core.persistence_service import PersistenceService  # noqa: E402
from core.review_manager import ClinicianReviewManager  # noqa: E402
from core.soap_generator import SOAPGenerator  # noqa: E402
from db.database import Base, SessionLocal, engine  # noqa: E402
from schemas.coding import MappingMethod  # noqa: E402
from schemas.persistence import ClinicalNoteCreate, EncounterCreate, PatientCreate  # noqa: E402
from schemas.review import EntityReviewInput, ReviewDecision, ReviewSubmission  # noqa: E402

VISIT_1 = "Patient has mild asthma, well controlled."
VISIT_2 = (
    "Patient reports shortness of breath and wheezing for 3 days, worse at night. "
    "Denies fever. Asthma currently moderate persistent. "
    "No family history of respiratory issues. "
    "Lungs: bilateral expiratory wheeze on auscultation."
)
EXCLUDED = {MappingMethod.NOT_CODED_NEGATED, MappingMethod.NOT_CODED_FAMILY_HISTORY}


def banner(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def process_visit(db, services, encounter, text, label):
    extractor, mapper, reviewer, persistence = services
    note, analysis = persistence.create_clinical_note(db, encounter.id, ClinicalNoteCreate(raw_text=text))
    extraction = extractor.extract(text, note_id=note.id, analysis_id=analysis.id)
    coding = mapper.map_entities(extraction.entities, note_id=note.id, analysis_id=analysis.id)

    banner(f"{label}: extraction + coding")
    for c in coding.coded_entities:
        e = c.source_entity
        code = c.selected_code or "-"
        flags = []
        if e.context.negated:
            flags.append("negated")
        if e.context.subject.value == "family":
            flags.append("family history")
        print(f"  {e.raw_text!r:38} {e.entity_type.value:10} code={code:8} "
              f"{c.mapping_method.value}{'  [' + ', '.join(flags) + ']' if flags else ''}")

    # The "clinician" approves everything that is eligible for review.
    reviews = [
        EntityReviewInput(coding_id=c.coding_id, decision=ReviewDecision.APPROVED, reviewer_id="dr_demo")
        for c in coding.coded_entities if c.mapping_method not in EXCLUDED
    ]
    review = reviewer.apply_reviews(ReviewSubmission(
        analysis_id=analysis.id, note_id=note.id, coded_entities=coding.coded_entities, reviews=reviews,
    ))
    persisted, skipped, status = persistence.persist_approved_results(db, analysis.id, coding, review)
    print(f"\n  review -> {status}; persisted {persisted} approved entities, skipped {skipped}")
    return analysis, extraction


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    extractor, mapper, reviewer = ClinicalEntityExtractor(), ClinicalCodeMapper(), ClinicianReviewManager()
    persistence, history, soap = PersistenceService(), HistoryRetrievalService(), SOAPGenerator()
    services = (extractor, mapper, reviewer, persistence)

    patient = persistence.create_patient(db, PatientCreate(name="Synthetic Patient", gender="F"))
    enc1 = persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-06-01"))
    enc2 = persistence.create_encounter(db, patient.id, EncounterCreate(encounter_date="2026-07-20"))

    process_visit(db, services, enc1, VISIT_1, "VISIT 1 (2026-06-01)")
    analysis2, extraction2 = process_visit(db, services, enc2, VISIT_2, "VISIT 2 (2026-07-20)")

    banner("Relevant history retrieved for visit 2 (bounded, approved data only)")
    context = history.get_relevant_history(
        db, patient.id, extraction2.entities, exclude_encounter_id=enc2.id
    )
    for h in context.mandatory_safety_context:
        print(f"  [{h.retrieval_method}] {h.normalized_term} (severity={h.severity}, seen {h.encounter_date})")
    print(f"  fuzzy (BM25) matches: {len(context.relevant_historical_context)}")

    banner("DRAFT SOAP note for visit 2")
    draft = soap.generate(db, analysis2.id, context)
    print(f"  status : {draft.status.upper()}  (method: {draft.generation_method.value})")
    print(f"  S: {draft.subjective}")
    print(f"  O: {draft.objective}")
    print(f"  A: {draft.assessment}")
    print(f"  P: {draft.plan}")
    for w in draft.warnings:
        print(f"  ! {w}")
    db.close()


if __name__ == "__main__":
    main()
