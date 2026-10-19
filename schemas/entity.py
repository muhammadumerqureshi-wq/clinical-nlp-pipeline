"""
Pydantic schemas for the Clinical Entity Extraction module.
Matches the architecture designed for the Optikode clinical NLP pipeline:
Raw Note -> Entity Extraction -> Attribute Extraction -> Context Analysis -> Normalization
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class EntityType(str, Enum):
    SYMPTOM = "symptom"
    CONDITION = "condition"
    FINDING = "finding"
    MEASUREMENT = "measurement"
    MEDICATION = "medication"


class TemporalStatus(str, Enum):
    CURRENT = "current"
    PAST = "past"
    RESOLVED = "resolved"


class Subject(str, Enum):
    PATIENT = "patient"
    FAMILY = "family"


class Trend(str, Enum):
    IMPROVING = "improving"
    WORSENING = "worsening"
    STABLE = "stable"


class EntityAttributes(BaseModel):
    """Descriptive characteristics of the entity, all optional since not
    every entity (e.g. a medication) will have a location or severity."""
    location: Optional[str] = None
    duration: Optional[str] = None
    severity: Optional[str] = None


class EntityContext(BaseModel):
    """Contextual interpretation of the entity -- this is the layer that
    prevents an AI system from making dangerous mistakes (e.g. coding a
    negated or family-history symptom as an active patient condition)."""
    negated: bool = False
    temporal_status: TemporalStatus = TemporalStatus.CURRENT
    subject: Subject = Subject.PATIENT
    trend: Optional[Trend] = None


class ExtractedEntity(BaseModel):
    """A single structured entity pulled from a clinical note."""
    entity_id: str
    analysis_id: Optional[str] = None
    entity_type: EntityType
    raw_text: str = Field(..., description="Exact phrase as it appeared in the note")
    normalized_term: str = Field(..., description="Standardized clinical term")
    attributes: EntityAttributes = Field(default_factory=EntityAttributes)
    context: EntityContext = Field(default_factory=EntityContext)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    source_note_id: Optional[str] = None


class ExtractionResult(BaseModel):
    """Full response returned by the extraction engine for one note."""
    note_id: Optional[str] = None
    raw_text: str
    entities: List[ExtractedEntity]
    entity_count: int

    @classmethod
    def from_entities(cls, raw_text: str, entities: List[ExtractedEntity], note_id: Optional[str] = None):
        return cls(note_id=note_id, raw_text=raw_text, entities=entities, entity_count=len(entities))
