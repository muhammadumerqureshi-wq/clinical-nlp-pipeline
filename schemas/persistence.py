"""
Pydantic schemas for Module 4 -- persistence layer request/response bodies.

The "persist approved results" input intentionally reuses Module 2's
CodingResult and Module 3's ReviewResult schemas directly -- this module
does NOT redefine or re-derive that data, it only consumes it and decides
what's eligible to be written to the database.
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict

from schemas.coding import CodingResult
from schemas.review import ReviewResult


# ---------- Patient / Encounter / Clinical Note (request bodies) ----------

class PatientCreate(BaseModel):
    name: str
    dob: Optional[str] = None
    gender: Optional[str] = None


class EncounterCreate(BaseModel):
    encounter_date: Optional[str] = None
    encounter_type: Optional[str] = None
    reason_for_visit: Optional[str] = None


class ClinicalNoteCreate(BaseModel):
    raw_text: str
    author_id: Optional[str] = None


# ---------- Patient / Encounter / Clinical Note (read responses) ----------

class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    dob: Optional[str] = None
    gender: Optional[str] = None
    created_at: datetime


class EncounterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: str
    encounter_date: Optional[str] = None
    encounter_type: Optional[str] = None
    reason_for_visit: Optional[str] = None
    created_at: datetime


class ClinicalNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    encounter_id: str
    raw_text: str
    author_id: Optional[str] = None
    created_at: datetime
    analysis_id: str      # convenience -- the Analysis auto-created alongside this note
    analysis_status: str


# ---------- Persist approved results (the core Module 4 operation) ----------

class PersistApprovedRequest(BaseModel):
    """
    Body for POST /analyses/{analysis_id}/approved-results.

    coding_result = Module 2's output (has mapping_method + candidate_codes,
                    needed for audit trail even after a clinician edits a code)
    review_result = Module 3's output (has the clinician's actual decisions)

    No NLP or coding runs here -- this endpoint only writes what these two
    already-computed results say, filtered to approved/edited entities only.
    """
    coding_result: CodingResult
    review_result: ReviewResult


class PersistApprovedResponse(BaseModel):
    analysis_id: str
    analysis_status: str
    persisted_count: int   # entities actually written (approved or edited)
    skipped_count: int     # entities NOT written (rejected / pending / not_applicable)


# ---------- Read back persisted data (single source of truth for downstream) ----------

class PersistedEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    entity_type: str
    raw_text: str
    normalized_term: str
    location: Optional[str] = None
    duration: Optional[str] = None
    severity: Optional[str] = None
    negated: bool
    temporal_status: str
    subject: str
    trend: Optional[str] = None
    confidence_score: float


class PersistedCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code_system: str
    selected_code: Optional[str] = None       # AI's original proposal
    selected_description: Optional[str] = None
    mapping_method: str
    mapping_confidence: float


class PersistedReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    decision: str
    final_code: Optional[str] = None          # clinician's FINAL code (may differ from AI's if edited)
    final_description: Optional[str] = None
    reviewer_id: Optional[str] = None
    reviewer_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class ApprovedEntityOut(BaseModel):
    """One fully-joined, persisted, clinician-approved entity."""
    entity: PersistedEntityOut
    code: PersistedCodeOut
    review: PersistedReviewOut


class AnalysisOut(BaseModel):
    id: str
    clinical_note_id: str
    status: str
    approved_entities: List[ApprovedEntityOut]
