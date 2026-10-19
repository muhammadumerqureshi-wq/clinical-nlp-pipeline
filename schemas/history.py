"""
Pydantic schemas for Module 5 — Relevant Patient History Retrieval.

Implements the two-lane retrieval design: mandatory_safety_context (exact,
deterministic SQL match on normalized_term -- never subject to ranking)
and relevant_historical_context (BM25 keyword search -- for paraphrased
past complaints that don't share an exact normalized_term).
"""

from typing import Optional, List
from pydantic import BaseModel

from schemas.entity import ExtractedEntity


class HistoryEntry(BaseModel):
    """One past, ALREADY-APPROVED entity being surfaced as relevant history.
    Sourced exclusively from Module 4's persisted layer -- never raw/unreviewed
    AI output."""
    entity_id: str
    raw_text: str
    normalized_term: str
    entity_type: str
    severity: Optional[str] = None
    trend: Optional[str] = None
    encounter_date: Optional[str] = None
    encounter_id: str
    retrieval_method: str  # "sql_exact" | "bm25_fuzzy"
    match_score: float = 1.0  # 1.0 for exact SQL match; BM25 score (0-1 normalized) for fuzzy


class TrendPoint(BaseModel):
    encounter_date: Optional[str] = None
    severity: Optional[str] = None
    trend: Optional[str] = None


class RelevantHistoryRequest(BaseModel):
    """
    current_entities are Module 1's raw extraction output for the note
    being worked on RIGHT NOW -- not yet coded or reviewed. History
    retrieval only reads the PATIENT's PAST persisted (approved) entities,
    never the current unreviewed ones.
    """
    patient_id: str
    current_entities: List[ExtractedEntity]
    exclude_encounter_id: Optional[str] = None  # avoid matching the current visit against itself


class RelevantHistoryResult(BaseModel):
    patient_id: str
    mandatory_safety_context: List[HistoryEntry]
    relevant_historical_context: List[HistoryEntry]
    trend_summary: dict  # normalized_term -> List[TrendPoint], chronological
