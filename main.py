"""
Clinical Entity Extraction Module — FastAPI service.

Exposes the extraction engine as an HTTP endpoint, matching the
architecture: Raw Note -> Clinical NLP Pipeline -> Structured Entities.

Run locally:
    uvicorn main:app --reload
Then POST to http://127.0.0.1:8000/extract
"""

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from core.extractor import ClinicalEntityExtractor
from core.code_mapper import ClinicalCodeMapper
from core.review_manager import ClinicianReviewManager
from core.persistence_service import PersistenceService
from core.history_retrieval import HistoryRetrievalService
from core.soap_generator import SOAPGenerator
from schemas.entity import ExtractionResult
from schemas.coding import CodingResult
from schemas.review import ReviewSubmission, ReviewResult
from schemas.history import RelevantHistoryRequest, RelevantHistoryResult
from schemas.soap import SOAPDraft
from schemas.persistence import (
    PatientCreate, PatientOut, EncounterCreate, EncounterOut,
    ClinicalNoteCreate, ClinicalNoteOut,
    PersistApprovedRequest, PersistApprovedResponse,
    AnalysisOut, ApprovedEntityOut, PersistedEntityOut, PersistedCodeOut, PersistedReviewOut,
)
from db.database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Clinical Entity Extraction Module",
    description="Extracts structured clinical entities, maps them to ICD-10-CM codes, "
                "manages clinician review, persists approved data, retrieves relevant "
                "patient history, and generates draft SOAP notes.",
    version="0.6.0",
)

extractor = ClinicalEntityExtractor()
code_mapper = ClinicalCodeMapper()
review_manager = ClinicianReviewManager()
persistence = PersistenceService()
history_service = HistoryRetrievalService()
soap_generator = SOAPGenerator()


class ClinicalNoteInput(BaseModel):
    raw_text: str
    note_id: Optional[str] = None
    analysis_id: Optional[str] = None


@app.post("/extract", response_model=ExtractionResult)
def extract_entities(payload: ClinicalNoteInput):
    """
    Module 1 only: raw note -> structured entities.

    NOTE: in the production architecture we designed, this step should be
    triggered asynchronously from POST /clinical-notes (the caller gets a
    `pending` analysis_id immediately, and this extraction runs as a
    background job). This endpoint exposes the extraction engine directly
    so it can be tested/demoed in isolation.
    """
    return extractor.extract(
        raw_text=payload.raw_text,
        note_id=payload.note_id,
        analysis_id=payload.analysis_id,
    )


@app.post("/code", response_model=CodingResult)
def code_entities(payload: ClinicalNoteInput):
    """
    Module 1 + Module 2 chained: raw note -> structured entities -> ICD-10-CM codes.

    This is the realistic end-to-end call a clinician-facing app would make:
    submit a raw note, get back coded entities with full traceability back
    to the original extracted entity (source_entity on each CodedEntity),
    including entities that were deliberately NOT coded (negated / family
    history / unmapped) with their reasons preserved.
    """
    extraction = extractor.extract(
        raw_text=payload.raw_text,
        note_id=payload.note_id,
        analysis_id=payload.analysis_id,
    )
    return code_mapper.map_entities(
        extraction.entities,
        note_id=payload.note_id,
        analysis_id=payload.analysis_id,
    )


@app.post("/review", response_model=ReviewResult)
def review_entities(submission: ReviewSubmission):
    """
    Module 3: applies clinician Approve/Edit/Reject decisions to a set of
    coded entities (typically the output of a prior POST /code call).

    Only entities that actually received a proposed code from Module 2 are
    subject to review -- entities Module 2 skipped (negated, family
    history, unmapped) pass through as `not_applicable`. The overall
    `analysis_status` stays "needs_review" until every codeable entity has
    an explicit decision, so a note can't be silently finalized with
    unreviewed codes still hanging.
    """
    return review_manager.apply_reviews(submission)


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ==================== Module 4: Persistence Layer ====================

@app.post("/patients", response_model=PatientOut)
def create_patient(data: PatientCreate, db: Session = Depends(get_db)):
    return persistence.create_patient(db, data)


@app.get("/patients/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = persistence.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@app.post("/patients/{patient_id}/encounters", response_model=EncounterOut)
def create_encounter(patient_id: str, data: EncounterCreate, db: Session = Depends(get_db)):
    if persistence.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return persistence.create_encounter(db, patient_id, data)


@app.post("/encounters/{encounter_id}/clinical-notes", response_model=ClinicalNoteOut)
def create_clinical_note(encounter_id: str, data: ClinicalNoteCreate, db: Session = Depends(get_db)):
    """
    Creates the note and its initial (pending) Analysis. Does NOT run
    Module 1/2/3 -- in this MVP those still run separately via /extract,
    /code, /review, using this note_id/analysis_id for traceability. In a
    production system this endpoint would trigger that pipeline
    asynchronously (see the async design discussed earlier).
    """
    if persistence.get_encounter(db, encounter_id) is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    note, analysis = persistence.create_clinical_note(db, encounter_id, data)
    return ClinicalNoteOut(
        id=note.id, encounter_id=note.encounter_id, raw_text=note.raw_text,
        author_id=note.author_id, created_at=note.created_at,
        analysis_id=analysis.id, analysis_status=analysis.status,
    )


@app.post("/analyses/{analysis_id}/approved-results", response_model=PersistApprovedResponse)
def persist_approved_results(analysis_id: str, payload: PersistApprovedRequest, db: Session = Depends(get_db)):
    """
    The core Module 4 operation: takes Module 2's CodingResult + Module 3's
    ReviewResult (already computed elsewhere, no NLP/coding re-run here),
    and persists ONLY the entities the clinician approved or edited.
    """
    try:
        persisted, skipped, status = persistence.persist_approved_results(
            db, analysis_id, payload.coding_result, payload.review_result
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return PersistApprovedResponse(
        analysis_id=analysis_id, analysis_status=status,
        persisted_count=persisted, skipped_count=skipped,
    )


@app.get("/analyses/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: str, db: Session = Depends(get_db)):
    """Read back exactly what's persisted for this analysis -- the durable,
    clinician-approved record, not the raw AI output."""
    analysis = persistence.get_analysis(db, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    approved = []
    for entity in analysis.extracted_entities:
        code = entity.coded_entity
        review = code.human_review if code else None
        if code is None or review is None:
            continue  # defensive -- shouldn't happen given persist logic, but never fabricate a joined row
        approved.append(ApprovedEntityOut(
            entity=PersistedEntityOut.model_validate(entity),
            code=PersistedCodeOut.model_validate(code),
            review=PersistedReviewOut.model_validate(review),
        ))

    return AnalysisOut(
        id=analysis.id, clinical_note_id=analysis.clinical_note_id,
        status=analysis.status, approved_entities=approved,
    )


@app.get("/patients/{patient_id}/approved-entities", response_model=list[PersistedEntityOut])
def get_approved_entities_for_patient(patient_id: str, db: Session = Depends(get_db)):
    """
    The downstream-facing read: every clinician-approved entity across all
    of this patient's encounters. This is what SOAP generation, progress
    analysis, and the timeline module will consume -- the single source
    of truth, never raw/unreviewed AI output.
    """
    if persistence.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    entities = persistence.get_approved_entities_for_patient(db, patient_id)
    return [PersistedEntityOut.model_validate(e) for e in entities]


# ==================== Module 5: Relevant Patient History Retrieval ====================

@app.post("/patients/{patient_id}/relevant-history", response_model=RelevantHistoryResult)
def get_relevant_history(patient_id: str, payload: RelevantHistoryRequest, db: Session = Depends(get_db)):
    """
    Two-lane retrieval over this patient's PAST persisted (approved-only)
    entities, given today's in-progress note's raw Module 1 extraction:

    - mandatory_safety_context: deterministic SQL exact-match on
      normalized_term. Guaranteed inclusion, never subject to ranking.
    - relevant_historical_context: BM25 keyword search over past raw_text,
      for paraphrased history that doesn't share an exact normalized_term
      with anything in today's note.

    Intended to feed Module 6 (SOAP Generation) as retrieved context
    alongside today's own approved entities.
    """
    if payload.patient_id != patient_id:
        raise HTTPException(status_code=400, detail="patient_id in URL and body must match")
    if persistence.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    return history_service.get_relevant_history(
        db, patient_id=patient_id,
        current_entities=payload.current_entities,
        exclude_encounter_id=payload.exclude_encounter_id,
    )


# ==================== Module 6: SOAP Generation ====================

@app.post("/analyses/{analysis_id}/soap-draft", response_model=SOAPDraft)
def generate_soap_draft(analysis_id: str, relevant_history: RelevantHistoryResult, db: Session = Depends(get_db)):
    """
    Generates a DRAFT SOAP note from ONLY:
      - today's approved/edited entities for this analysis_id (Module 4)
      - the relevant_history payload the caller already computed via
        POST /patients/{id}/relevant-history (Module 5) -- this endpoint
        never calls history retrieval itself.

    Always returns status="draft". No clinician approval/editing workflow
    is implemented here -- that's Module 3's job, reusable later on
    whatever this produces.
    """
    try:
        return soap_generator.generate(db, analysis_id=analysis_id, relevant_history=relevant_history)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
