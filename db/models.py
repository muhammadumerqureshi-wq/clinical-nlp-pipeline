"""
ORM models for the persistence layer.

Relationships (all one-to-many parent -> children, except where noted):

    Patient (1) --> (many) Encounter
    Encounter (1) --> (many) ClinicalNote
    ClinicalNote (1) --> (many) Analysis        [a note could be re-analyzed]
    Analysis (1) --> (many) ExtractedEntity
    ExtractedEntity (1) --> (1) CodedEntity      [one-to-one: each entity gets at most one coding attempt]
    CodedEntity (1) --> (1) HumanReview          [one-to-one: one clinician decision per coded entity]

IMPORTANT: only entities the clinician actually APPROVED or EDITED get a
CodedEntityORM + HumanReviewORM row at all (see core/persistence_service.py).
Rejected / pending / not_applicable entities from Module 3 are never
written here -- this module's job is to be the durable record of
clinically-approved data, not a raw dump of every AI guess.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship

from db.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PatientORM(Base):
    __tablename__ = "patients"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    dob = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    encounters = relationship("EncounterORM", back_populates="patient", cascade="all, delete-orphan")


class EncounterORM(Base):
    __tablename__ = "encounters"

    id = Column(String, primary_key=True, default=gen_uuid)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False)
    encounter_date = Column(String, nullable=True)
    encounter_type = Column(String, nullable=True)
    reason_for_visit = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    patient = relationship("PatientORM", back_populates="encounters")
    clinical_notes = relationship("ClinicalNoteORM", back_populates="encounter", cascade="all, delete-orphan")


class ClinicalNoteORM(Base):
    __tablename__ = "clinical_notes"

    id = Column(String, primary_key=True, default=gen_uuid)
    encounter_id = Column(String, ForeignKey("encounters.id"), nullable=False)
    raw_text = Column(Text, nullable=False)
    author_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    encounter = relationship("EncounterORM", back_populates="clinical_notes")
    analyses = relationship("AnalysisORM", back_populates="clinical_note", cascade="all, delete-orphan")


class AnalysisORM(Base):
    __tablename__ = "analyses"

    id = Column(String, primary_key=True, default=gen_uuid)
    clinical_note_id = Column(String, ForeignKey("clinical_notes.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending | needs_review | approved | rejected
    created_at = Column(DateTime, default=utcnow)
    finalized_at = Column(DateTime, nullable=True)

    clinical_note = relationship("ClinicalNoteORM", back_populates="analyses")
    extracted_entities = relationship("ExtractedEntityORM", back_populates="analysis", cascade="all, delete-orphan")


class ExtractedEntityORM(Base):
    """
    Persisted entity -- ONLY written here if the clinician approved/edited
    its code (see persistence_service.persist_approved_results). id reuses
    Module 1's original entity_id, so the persisted record traces back to
    the exact same identifier used throughout the in-memory pipeline.
    """
    __tablename__ = "extracted_entities"

    id = Column(String, primary_key=True)  # = Module 1's entity_id, not a fresh uuid
    analysis_id = Column(String, ForeignKey("analyses.id"), nullable=False)

    entity_type = Column(String, nullable=False)
    raw_text = Column(String, nullable=False)
    normalized_term = Column(String, nullable=False)

    # --- Attributes ---
    location = Column(String, nullable=True)
    duration = Column(String, nullable=True)
    severity = Column(String, nullable=True)

    # --- Context ---
    negated = Column(Boolean, default=False)
    temporal_status = Column(String, default="current")
    subject = Column(String, default="patient")
    trend = Column(String, nullable=True)

    confidence_score = Column(Float, nullable=False)
    source_note_id = Column(String, nullable=True)

    analysis = relationship("AnalysisORM", back_populates="extracted_entities")
    coded_entity = relationship(
        "CodedEntityORM", back_populates="extracted_entity",
        uselist=False, cascade="all, delete-orphan",
    )


class CodedEntityORM(Base):
    """id reuses Module 2's coding_id for the same reason as above."""
    __tablename__ = "coded_entities"

    id = Column(String, primary_key=True)  # = Module 2's coding_id
    extracted_entity_id = Column(String, ForeignKey("extracted_entities.id"), nullable=False, unique=True)

    code_system = Column(String, default="ICD-10-CM")
    candidate_codes = Column(JSON, nullable=True)  # [{code, description, match_score}, ...] -- AI's full shortlist, preserved for audit
    selected_code = Column(String, nullable=True)          # the AI's ORIGINAL proposal (kept even if clinician edited it)
    selected_description = Column(String, nullable=True)
    mapping_method = Column(String, nullable=False)
    mapping_confidence = Column(Float, default=0.0)

    extracted_entity = relationship("ExtractedEntityORM", back_populates="coded_entity")
    human_review = relationship(
        "HumanReviewORM", back_populates="coded_entity",
        uselist=False, cascade="all, delete-orphan",
    )


class HumanReviewORM(Base):
    __tablename__ = "human_reviews"

    id = Column(String, primary_key=True, default=gen_uuid)
    coded_entity_id = Column(String, ForeignKey("coded_entities.id"), nullable=False, unique=True)

    decision = Column(String, nullable=False)  # "approved" | "edited" (only these ever get persisted)
    final_code = Column(String, nullable=True)         # the clinician's FINAL code -- may differ from CodedEntityORM.selected_code if edited
    final_description = Column(String, nullable=True)
    reviewer_id = Column(String, nullable=True)
    reviewer_notes = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    coded_entity = relationship("CodedEntityORM", back_populates="human_review")
