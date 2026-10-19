"""
Pydantic schemas for Module 2 — Clinical Terminology Normalization & Code Mapping.

Consumes ExtractedEntity objects from Module 1 (entity extraction) and
produces coded entities, preserving the original raw entity for full
traceability: raw text -> extracted entity -> candidate codes -> selected code.
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field

from schemas.entity import ExtractedEntity


class CodeSystem(str, Enum):
    ICD10CM = "ICD-10-CM"


class MappingMethod(str, Enum):
    EXACT_MATCH = "exact_match"            # normalized_term matched a dictionary entry directly
    ATTRIBUTE_REFINED = "attribute_refined"  # matched, then narrowed using severity/other attributes
    NOT_CODED_NEGATED = "not_coded_negated"  # entity was negated (e.g. "denies fever") - not coded as present
    NOT_CODED_FAMILY_HISTORY = "not_coded_family_history"  # entity belongs to family, not patient
    NOT_CODEABLE_TYPE = "not_codeable_type"  # entity type (finding/measurement) is never billed/coded in ICD-10-CM --
                                              # this is NOT an error, and does NOT mean the entity isn't clinically real.
                                              # Distinct from UNMAPPED: this entity was never eligible for coding at all.
    UNMAPPED = "unmapped"                   # entity type COULD be coded, but no matching dictionary entry was found
                                              # (a genuine coding gap, unlike NOT_CODEABLE_TYPE)


class CodeCandidate(BaseModel):
    """One possible code match, before final selection."""
    code: str
    description: str
    match_score: float = Field(..., ge=0.0, le=1.0)


class CodedEntity(BaseModel):
    """
    Output of Module 2 for a single entity.

    `source_entity` is the FULL original ExtractedEntity from Module 1 --
    preserved as-is, not just referenced by ID. This means a consumer of
    this schema never has to go looking elsewhere for the raw_text,
    negation status, or attributes that justified (or blocked) a code
    assignment -- everything needed to audit the decision travels together.
    """
    coding_id: str
    entity_id: str  # traceability: links back to source_entity.entity_id (redundant but explicit)
    source_entity: ExtractedEntity  # full original entity, unmodified

    code_system: CodeSystem = CodeSystem.ICD10CM
    candidate_codes: List[CodeCandidate] = Field(default_factory=list)
    selected_code: Optional[str] = None
    selected_description: Optional[str] = None
    mapping_method: MappingMethod  # primary reason (family history takes priority over negation)
    exclusion_reasons: List[MappingMethod] = Field(
        default_factory=list,
        description="ALL applicable reasons an entity was not coded (populated only "
                    "when the entity was skipped). E.g. a negated family-history "
                    "entity gets both NOT_CODED_FAMILY_HISTORY and NOT_CODED_NEGATED "
                    "here, even though mapping_method reports only the primary one."
    )
    mapping_confidence: float = Field(..., ge=0.0, le=1.0)


class CodingResult(BaseModel):
    """Full response for one note's worth of coded entities."""
    note_id: Optional[str] = None
    analysis_id: Optional[str] = None
    coded_entities: List[CodedEntity]
    coded_count: int  # entities that received a selected_code
    skipped_count: int  # entities intentionally not coded (negated / family history / unmapped)

    @classmethod
    def from_coded_entities(cls, coded_entities: List[CodedEntity],
                             note_id: Optional[str] = None,
                             analysis_id: Optional[str] = None):
        coded = sum(1 for c in coded_entities if c.selected_code is not None)
        skipped = len(coded_entities) - coded
        return cls(
            note_id=note_id,
            analysis_id=analysis_id,
            coded_entities=coded_entities,
            coded_count=coded,
            skipped_count=skipped,
        )
