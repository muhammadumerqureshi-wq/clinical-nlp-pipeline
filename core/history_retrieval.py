"""
Module 5 — Relevant Patient History Retrieval.

Two-lane retrieval against Module 4's persisted (already clinician-
approved) entities ONLY -- never raw/unreviewed AI output, and never the
patient's current in-progress note against itself.

Lane 1 (mandatory_safety_context): deterministic SQL exact-match on
normalized_term. Guaranteed inclusion -- never subject to a ranking
algorithm, so a past condition can never be silently dropped just because
it scored low.

Lane 2 (relevant_historical_context): BM25 keyword search over past
entities' raw_text, for cases where today's complaint is phrased
differently from a past note (e.g. "breathing trouble" then, "shortness
of breath" now) and so shares no normalized_term match.
"""

from typing import List, Tuple
from collections import defaultdict
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from db import models as m
from schemas.entity import ExtractedEntity
from schemas.history import HistoryEntry, RelevantHistoryResult, TrendPoint

# Minimum BM25 score to bother surfacing -- pure noise below this.
BM25_MIN_SCORE = 0.1
BM25_TOP_K = 5
# Below this many candidate past entries, BM25's IDF can go negative for
# common terms (a word present in most/all of a tiny corpus isn't "rare"),
# silently suppressing real matches -- skip BM25 entirely rather than
# return a misleading empty result.
MIN_CORPUS_SIZE_FOR_BM25 = 3


def _tokenize(text: str) -> List[str]:
    return text.lower().split()


class HistoryRetrievalService:

    def get_relevant_history(self, db: Session, patient_id: str,
                              current_entities: List[ExtractedEntity],
                              exclude_encounter_id: str = None) -> RelevantHistoryResult:

        past = self._get_past_entities(db, patient_id, exclude_encounter_id)

        current_terms = {e.normalized_term for e in current_entities}

        # --- Lane 1: SQL exact match (deterministic, safety-critical) ---
        sql_matches = [row for row in past if row["normalized_term"] in current_terms]
        sql_matched_ids = {row["entity_id"] for row in sql_matches}
        mandatory = [self._to_entry(row, "sql_exact", 1.0) for row in sql_matches]

        # --- Lane 2: BM25 fuzzy match over the REMAINING entries only ---
        # (never re-surface something already guaranteed via exact match)
        remaining = [row for row in past if row["entity_id"] not in sql_matched_ids]
        bm25_hits = self._bm25_search(remaining, current_entities)
        relevant = [self._to_entry(row, "bm25_fuzzy", score) for row, score in bm25_hits]

        # --- Trend summary: chronological severity/trend for terms relevant to today ---
        trend_summary = self._build_trend_summary(past, current_terms)

        return RelevantHistoryResult(
            patient_id=patient_id,
            mandatory_safety_context=mandatory,
            relevant_historical_context=relevant,
            trend_summary=trend_summary,
        )

    # ---------- Data access ----------

    def _get_past_entities(self, db: Session, patient_id: str, exclude_encounter_id: str = None) -> List[dict]:
        """Every persisted (== clinician-approved) entity for this patient,
        joined with its encounter date, ordered chronologically. Only
        reads Module 4's approved layer -- see get_approved_entities_for_patient
        in persistence_service, this is the same underlying data."""
        query = (
            db.query(m.ExtractedEntityORM, m.EncounterORM)
            .join(m.AnalysisORM, m.ExtractedEntityORM.analysis_id == m.AnalysisORM.id)
            .join(m.ClinicalNoteORM, m.AnalysisORM.clinical_note_id == m.ClinicalNoteORM.id)
            .join(m.EncounterORM, m.ClinicalNoteORM.encounter_id == m.EncounterORM.id)
            .filter(m.EncounterORM.patient_id == patient_id)
        )
        if exclude_encounter_id:
            query = query.filter(m.EncounterORM.id != exclude_encounter_id)
        query = query.order_by(m.EncounterORM.created_at)

        results = []
        for entity, encounter in query.all():
            results.append({
                "entity_id": entity.id,
                "raw_text": entity.raw_text,
                "normalized_term": entity.normalized_term,
                "entity_type": entity.entity_type,
                "severity": entity.severity,
                "trend": entity.trend,
                "encounter_id": encounter.id,
                "encounter_date": encounter.encounter_date,
                "encounter_created_at": encounter.created_at,
            })
        return results

    # ---------- Lane 2: BM25 ----------

    def _bm25_search(self, remaining: List[dict],
                      current_entities: List[ExtractedEntity]) -> List[Tuple[dict, float]]:
        """
        NOTE: classic BM25's IDF formula can go NEGATIVE for a term that
        appears in a very small corpus (a term present in 1 of 1 documents
        is technically "common", not rare). With too few past entities,
        every score can come back negative and get filtered out below,
        even for a genuine match -- this isn't a bug, it's inherent to
        BM25 on tiny corpora. Below a minimum corpus size we skip BM25
        entirely rather than return misleadingly empty "no match" results
        that could be mistaken for "nothing relevant exists".
        """
        if not remaining or not current_entities:
            return []
        if len(remaining) < MIN_CORPUS_SIZE_FOR_BM25:
            return []

        corpus_tokens = [_tokenize(row["raw_text"]) for row in remaining]
        bm25 = BM25Okapi(corpus_tokens)

        # Combine all of today's entity texts into one query -- we want
        # "anything in today's note that resembles past history", not a
        # separate ranked list per entity.
        query_text = " ".join(e.raw_text for e in current_entities)
        scores = bm25.get_scores(_tokenize(query_text))

        max_score = max(scores) if len(scores) and max(scores) > 0 else 1.0
        scored = [
            (remaining[i], round(scores[i] / max_score, 2))
            for i in range(len(remaining))
            if scores[i] > 0 and (scores[i] / max_score) >= BM25_MIN_SCORE
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:BM25_TOP_K]

    # ---------- Trend summary ----------

    def _build_trend_summary(self, past: List[dict], current_terms: set) -> dict:
        by_term = defaultdict(list)
        for row in past:
            if row["normalized_term"] in current_terms:
                by_term[row["normalized_term"]].append(TrendPoint(
                    encounter_date=row["encounter_date"],
                    severity=row["severity"],
                    trend=row["trend"],
                ))
        # Already chronological since `past` was queried ordered by encounter date
        return {term: [p.model_dump() for p in points] for term, points in by_term.items()}

    # ---------- Shared ----------

    @staticmethod
    def _to_entry(row: dict, method: str, score: float) -> HistoryEntry:
        return HistoryEntry(
            entity_id=row["entity_id"],
            raw_text=row["raw_text"],
            normalized_term=row["normalized_term"],
            entity_type=row["entity_type"],
            severity=row["severity"],
            trend=row["trend"],
            encounter_date=row["encounter_date"],
            encounter_id=row["encounter_id"],
            retrieval_method=method,
            match_score=score,
        )
