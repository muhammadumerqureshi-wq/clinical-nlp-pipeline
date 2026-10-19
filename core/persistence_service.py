"""
Module 4 — Persistence Service.

Writes ONLY clinician-approved/edited structured clinical data to the
database. Runs no NLP, no coding, no review logic -- it assumes Module 3
has already produced a final decision for every entity, and its only job
is deciding what's eligible to be durably stored, then storing it with
correct relationships and full audit fields.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from db import models as m
from schemas.persistence import PatientCreate, EncounterCreate, ClinicalNoteCreate
from schemas.coding import CodingResult
from schemas.review import ReviewResult, ReviewDecision

# Only these two decisions ever result in a persisted record. Rejected,
# pending, and not_applicable entities are intentionally never written --
# see the requirement this module was built against: "persist only
# clinician-approved structured clinical data."
PERSISTABLE_DECISIONS = {ReviewDecision.APPROVED, ReviewDecision.EDITED}


class PersistenceService:

    # ---------- Patient / Encounter / Clinical Note setup ----------

    def create_patient(self, db: Session, data: PatientCreate) -> m.PatientORM:
        patient = m.PatientORM(name=data.name, dob=data.dob, gender=data.gender)
        db.add(patient)
        db.commit()
        db.refresh(patient)
        return patient

    def get_patient(self, db: Session, patient_id: str) -> Optional[m.PatientORM]:
        return db.query(m.PatientORM).filter(m.PatientORM.id == patient_id).first()

    def get_encounter(self, db: Session, encounter_id: str) -> Optional[m.EncounterORM]:
        return db.query(m.EncounterORM).filter(m.EncounterORM.id == encounter_id).first()

    def create_encounter(self, db: Session, patient_id: str, data: EncounterCreate) -> m.EncounterORM:
        encounter = m.EncounterORM(
            patient_id=patient_id,
            encounter_date=data.encounter_date,
            encounter_type=data.encounter_type,
            reason_for_visit=data.reason_for_visit,
        )
        db.add(encounter)
        db.commit()
        db.refresh(encounter)
        return encounter

    def create_clinical_note(self, db: Session, encounter_id: str,
                              data: ClinicalNoteCreate) -> Tuple[m.ClinicalNoteORM, m.AnalysisORM]:
        """Creates the note AND its initial pending Analysis in one step --
        matches the MVP endpoint design from earlier (submitting a note
        immediately kicks off an analysis job, async in a real system)."""
        note = m.ClinicalNoteORM(encounter_id=encounter_id, raw_text=data.raw_text, author_id=data.author_id)
        db.add(note)
        db.flush()  # assigns note.id without committing yet

        analysis = m.AnalysisORM(clinical_note_id=note.id, status="pending")
        db.add(analysis)
        db.commit()
        db.refresh(note)
        db.refresh(analysis)
        return note, analysis

    # ---------- Persist ONLY clinician-approved data ----------

    def persist_approved_results(self, db: Session, analysis_id: str,
                                  coding_result: CodingResult,
                                  review_result: ReviewResult) -> Tuple[int, int, str]:
        """
        Returns (persisted_count, skipped_count, final_analysis_status).

        For every entity in review_result.final_entities:
          - if review_decision is APPROVED or EDITED -> write
            ExtractedEntityORM + CodedEntityORM + HumanReviewORM
          - otherwise (REJECTED / PENDING / NOT_APPLICABLE) -> skip entirely,
            nothing is written for that entity

        Uses db.merge() (not db.add()) so re-running this on the same
        analysis_id is idempotent -- re-persisting after a later edit
        updates the existing rows rather than creating duplicates.
        """
        analysis = db.query(m.AnalysisORM).filter(m.AnalysisORM.id == analysis_id).first()
        if analysis is None:
            raise ValueError(f"Analysis {analysis_id} not found. Create it via "
                              f"POST /encounters/{{encounter_id}}/clinical-notes first.")

        coded_by_id = {c.coding_id: c for c in coding_result.coded_entities}

        persisted = 0
        skipped = 0

        for final_entity in review_result.final_entities:
            if final_entity.review_decision not in PERSISTABLE_DECISIONS:
                skipped += 1
                continue

            coded_entity = coded_by_id.get(final_entity.coding_id)
            if coded_entity is None:
                # Defensive: the review_result references a coding_id that
                # isn't in the coding_result passed alongside it. This
                # would mean the two payloads are out of sync -- never
                # silently persist a review with no matching source record.
                skipped += 1
                continue

            source = coded_entity.source_entity

            entity_row = m.ExtractedEntityORM(
                id=source.entity_id,
                analysis_id=analysis_id,
                entity_type=source.entity_type.value,
                raw_text=source.raw_text,
                normalized_term=source.normalized_term,
                location=source.attributes.location,
                duration=source.attributes.duration,
                severity=source.attributes.severity,
                negated=source.context.negated,
                temporal_status=source.context.temporal_status.value,
                subject=source.context.subject.value,
                trend=source.context.trend.value if source.context.trend else None,
                confidence_score=source.confidence_score,
                source_note_id=source.source_note_id,
            )
            db.merge(entity_row)
            db.flush()  # entity must exist before the coded_entity FK below

            code_row = m.CodedEntityORM(
                id=coded_entity.coding_id,
                extracted_entity_id=source.entity_id,
                code_system=coded_entity.code_system.value,
                candidate_codes=[c.model_dump() for c in coded_entity.candidate_codes],
                selected_code=coded_entity.selected_code,
                selected_description=coded_entity.selected_description,
                mapping_method=coded_entity.mapping_method.value,
                mapping_confidence=coded_entity.mapping_confidence,
            )
            db.merge(code_row)
            db.flush()  # coded_entity must exist before human_review FK below

            review_row = m.HumanReviewORM(
                id=f"review-{coded_entity.coding_id}",  # deterministic id -> idempotent re-persist
                coded_entity_id=coded_entity.coding_id,
                decision=final_entity.review_decision.value,
                final_code=final_entity.final_code,
                final_description=final_entity.final_description,
                reviewer_id=final_entity.reviewer_id,
                reviewer_notes=final_entity.reviewer_notes,
                reviewed_at=final_entity.reviewed_at or datetime.now(timezone.utc),
            )
            db.merge(review_row)

            persisted += 1

        analysis.status = review_result.analysis_status
        analysis.finalized_at = (
            datetime.now(timezone.utc) if review_result.analysis_status in ("approved", "rejected") else None
        )
        db.add(analysis)
        db.commit()

        return persisted, skipped, analysis.status

    # ---------- Read back (single source of truth for downstream modules) ----------

    def get_analysis(self, db: Session, analysis_id: str) -> Optional[m.AnalysisORM]:
        return db.query(m.AnalysisORM).filter(m.AnalysisORM.id == analysis_id).first()

    def get_approved_entities_for_patient(self, db: Session, patient_id: str):
        """
        The downstream-facing read: every persisted (== clinician-approved
        or edited) entity across ALL of this patient's encounters, in
        chronological order by encounter. This is the query SOAP
        generation, progress/trend analysis, and the patient timeline
        module will all be built on top of -- none of them should ever
        need to re-read raw AI output, only this approved layer.
        """
        return (
            db.query(m.ExtractedEntityORM)
            .join(m.AnalysisORM, m.ExtractedEntityORM.analysis_id == m.AnalysisORM.id)
            .join(m.ClinicalNoteORM, m.AnalysisORM.clinical_note_id == m.ClinicalNoteORM.id)
            .join(m.EncounterORM, m.ClinicalNoteORM.encounter_id == m.EncounterORM.id)
            .filter(m.EncounterORM.patient_id == patient_id)
            .order_by(m.EncounterORM.created_at)
            .all()
        )
