"""
Module 6 — SOAP Generation.

IMPORTANT (honesty note): this environment has no live LLM API access, so
this is implemented as a DETERMINISTIC TEMPLATE COMPOSER, not an actual
LLM call. It only assembles text from structured fields that literally
exist on approved entities / retrieved history -- it cannot fabricate
anything, by construction, because there is no generative model in the
loop at all. `GenerationMethod.LLM` is reserved in the schema for a real
future implementation that would call an actual model, but MUST preserve
the exact same "never invent, always flag gaps" behavior this version
guarantees structurally.

Consumes:
  - today's approved entities for ONE analysis_id (Module 4, scoped to a
    single visit -- fetched here via persistence_service, which is fine:
    this is "today's own approved data", not independently retrieving the
    patient's history)
  - a RelevantHistoryResult passed in by the caller, already computed by
    Module 5. This module NEVER calls HistoryRetrievalService itself.
"""

from datetime import datetime, timezone
from typing import List
from sqlalchemy.orm import Session

from db import models as m
from schemas.soap import SOAPDraft, GenerationMethod
from schemas.history import RelevantHistoryResult


class SOAPGenerator:

    def generate(self, db: Session, analysis_id: str,
                 relevant_history: RelevantHistoryResult) -> SOAPDraft:

        analysis = db.query(m.AnalysisORM).filter(m.AnalysisORM.id == analysis_id).first()
        if analysis is None:
            raise ValueError(f"Analysis {analysis_id} not found")

        # Only entities that are actually persisted -- i.e. already
        # clinician-approved/edited (Module 4 guarantees nothing else is
        # ever written here). No separate "is it approved" check needed.
        today_entities = analysis.extracted_entities

        subjective_text, subjective_ids = self._build_subjective(today_entities)
        objective_text, objective_ids = self._build_objective(today_entities)
        assessment_text, assessment_ids, history_ids_used = self._build_assessment(
            today_entities, relevant_history
        )
        plan_text, plan_warning = self._build_plan(today_entities)

        warnings = []
        if plan_warning:
            warnings.append(plan_warning)
        if not today_entities:
            warnings.append("No approved entities exist for this analysis -- SOAP sections are empty by design, not fabricated.")

        return SOAPDraft(
            analysis_id=analysis_id,
            subjective=subjective_text,
            objective=objective_text,
            assessment=assessment_text,
            plan=plan_text,
            source_entity_ids={
                "subjective": subjective_ids,
                "objective": objective_ids,
                "assessment": assessment_ids,
                "plan": [],  # never populated -- see _build_plan, nothing is ever derived here
            },
            history_entity_ids_used=history_ids_used,
            generation_method=GenerationMethod.TEMPLATE_COMPOSER,
            generated_at=datetime.now(timezone.utc),
            warnings=warnings,
        )

    # ---------- Subjective: patient-reported symptoms ----------

    def _build_subjective(self, entities: List[m.ExtractedEntityORM]):
        symptoms = [e for e in entities if e.entity_type == "symptom" and not e.negated]
        if not symptoms:
            return "No subjective symptoms recorded in approved data.", []

        phrases = []
        for e in symptoms:
            phrase = e.normalized_term
            if e.duration:
                phrase += f" for {e.duration}"
            if e.trend:
                phrase += f", {e.trend}"
            phrases.append(phrase)

        text = "Patient reports " + "; ".join(phrases) + "."
        return text, [e.id for e in symptoms]

    # ---------- Objective: exam findings ----------

    def _build_objective(self, entities: List[m.ExtractedEntityORM]):
        findings = [e for e in entities if e.entity_type in ("finding", "measurement") and not e.negated]
        if not findings:
            return "No objective findings recorded in approved data.", []

        phrases = []
        for e in findings:
            phrase = e.normalized_term
            if e.location:
                phrase += f" ({e.location})"
            phrases.append(phrase)

        text = "Exam notable for " + "; ".join(phrases) + "."
        return text, [e.id for e in findings]

    # ---------- Assessment: diagnoses + coded reasoning + trend context ----------

    def _build_assessment(self, entities: List[m.ExtractedEntityORM],
                           relevant_history: RelevantHistoryResult):
        conditions = [e for e in entities if e.entity_type == "condition" and not e.negated]
        if not conditions:
            return "No coded diagnosis in approved data for this visit.", [], []

        lines = []
        history_ids_used = []

        for e in conditions:
            code = e.coded_entity
            review = code.human_review if code else None
            code_str = f" ({review.final_code} - {review.final_description})" if review and review.final_code else ""
            line = f"{e.normalized_term.capitalize()}{code_str}"

            # Only reference history the caller ALREADY retrieved and
            # passed in -- never fetched independently here.
            trend_points = relevant_history.trend_summary.get(e.normalized_term)
            if trend_points:
                past_severities = [p["severity"] for p in trend_points if p.get("severity")]
                if past_severities:
                    line += f" -- prior documented severity: {', '.join(past_severities)}"
                    # Mark which mandatory_safety_context entries backed this trend statement
                    for h in relevant_history.mandatory_safety_context:
                        if h.normalized_term == e.normalized_term:
                            history_ids_used.append(h.entity_id)

            lines.append(line)

        return "; ".join(lines) + ".", [e.id for e in conditions], history_ids_used

    # ---------- Plan: deliberately NEVER fabricated ----------

    def _build_plan(self, entities: List[m.ExtractedEntityORM]):
        """
        There is no structured treatment/plan data source anywhere in this
        pipeline (Modules 1-5 extract findings and diagnoses, not
        treatment decisions). Rather than have an LLM invent a plausible-
        sounding plan, this deliberately returns a placeholder that flags
        the gap -- the clinician fills this in themselves. This is the
        literal, structural enforcement of "must not invent clinical
        facts when information is absent."
        """
        return (
            "[No structured plan data available from approved entities -- "
            "clinician to complete Plan section directly.]",
            "Plan section intentionally left as a placeholder: no treatment/medication "
            "data source exists in the current pipeline to draw from.",
        )
