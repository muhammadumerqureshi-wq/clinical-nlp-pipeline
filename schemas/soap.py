"""
Pydantic schemas for Module 6 — SOAP Generation.

The generator consumes ONLY:
  1. Today's approved/edited entities for a specific analysis_id
     (Module 4's persisted layer, scoped to ONE visit, not the whole patient)
  2. A RelevantHistoryResult ALREADY computed by Module 5 and passed in by
     the caller -- this module never calls the history retrieval service
     itself, and never queries the patient's full history directly.

Result is always status="draft" -- this module does not implement
clinician approval/editing; that's Module 3's job, reused unchanged for
whatever comes out of here later.
"""

from enum import Enum
from typing import List, Dict
from datetime import datetime
from pydantic import BaseModel, Field


class GenerationMethod(str, Enum):
    TEMPLATE_COMPOSER = "template_composer"  # deterministic, zero-fabrication assembly (current implementation)
    LLM = "llm"  # reserved for a future real LLM-backed implementation, same output contract


class SOAPDraft(BaseModel):
    analysis_id: str
    status: str = "draft"  # always "draft" -- no approval/editing performed here

    subjective: str
    objective: str
    assessment: str
    plan: str

    # Traceability: exactly which entity_ids (today's approved data, or
    # retrieved history entries) contributed text to each section. An
    # empty list for a section means nothing available -- see
    # `source_entity_ids` and `history_entity_ids_used`.
    source_entity_ids: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="section name -> list of TODAY's approved entity_ids that produced that section's text"
    )
    history_entity_ids_used: List[str] = Field(
        default_factory=list,
        description="entity_ids from the retrieved history that were referenced (e.g. in a trend statement)"
    )

    generation_method: GenerationMethod = GenerationMethod.TEMPLATE_COMPOSER
    generated_at: datetime
    warnings: List[str] = Field(
        default_factory=list,
        description="Explicit notes about missing data the generator deliberately did NOT fabricate "
                    "(e.g. no plan/treatment data available)."
    )
