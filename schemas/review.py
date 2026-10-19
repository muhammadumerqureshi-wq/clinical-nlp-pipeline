"""
Pydantic schemas for Module 3 — Clinician Review.

Consumes CodedEntity objects from Module 2 and lets a clinician
Approve / Edit / Reject each ONE that actually had a code proposed.
Entities Module 2 never proposed a code for (negated, family history,
unmapped) are NOT sent for review at all -- there's nothing to approve.
"""

from enum import Enum
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

from schemas.coding import CodedEntity
from schemas.entity import ExtractedEntity


class ReviewDecision(str, Enum):
    APPROVED = "approved"           # clinician confirmed the AI's code as-is
    EDITED = "edited"                # clinician kept the entity but changed the code
    REJECTED = "rejected"            # clinician says this entity/code should not be used at all
    PENDING = "pending"              # had a code proposed, but no review submitted yet
    NOT_APPLICABLE = "not_applicable"  # Module 2 never proposed a code -- nothing to review


class EntityReviewInput(BaseModel):
    """What the clinician actually submits for ONE entity."""
    coding_id: str  # links back to the CodedEntity being reviewed
    decision: ReviewDecision
    edited_code: Optional[str] = None
    edited_description: Optional[str] = None
    reviewer_id: str
    reviewer_notes: Optional[str] = None


class ReviewSubmission(BaseModel):
    """
    Request body for POST /review.

    NOTE: in a production system with persistence, the client would only
    need to send `analysis_id` + `reviews`, and the server would fetch the
    stored CodedEntity list from the database. This MVP has no database
    layer yet (consistent with the rest of this project so far), so the
    client passes the original coded_entities back alongside the review
    decisions -- the server never trusts the client's own codes though,
    only the coding_id reference, matching against what Module 2 produced.
    """
    analysis_id: str
    note_id: Optional[str] = None
    coded_entities: List[CodedEntity]
    reviews: List[EntityReviewInput] = Field(default_factory=list)


class FinalEntity(BaseModel):
    """One entity's final state after the review stage."""
    coding_id: str
    entity_id: str
    source_entity: ExtractedEntity  # the original ExtractedEntity, carried through unmodified

    final_code: Optional[str] = None
    final_description: Optional[str] = None
    review_decision: ReviewDecision
    reviewer_id: Optional[str] = None
    reviewer_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class ReviewResult(BaseModel):
    """Response for POST /review -- the 'Approved Data' stage of the architecture."""
    analysis_id: str
    note_id: Optional[str] = None
    analysis_status: str  # "approved" | "needs_review" | "rejected"

    final_entities: List[FinalEntity]
    approved_count: int
    edited_count: int
    rejected_count: int
    pending_count: int
    not_applicable_count: int
