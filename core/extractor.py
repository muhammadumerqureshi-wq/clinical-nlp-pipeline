"""
Clinical Entity Extraction Engine.

Implements the pipeline stages discussed in the architecture design:
    Entity Extraction -> Attribute Extraction -> Context Analysis -> Normalization

Uses spaCy's PhraseMatcher for term recognition (fast, deterministic,
explainable -- good MVP choice) plus rule-based logic for attributes and
context. This is intentionally swappable: a production version could
replace `_find_raw_matches` with a fine-tuned clinical NER model without
changing anything downstream, because the contract is the ExtractedEntity
schema, not the matching method.
"""

import re
import uuid
from typing import List, Optional

import spacy
from spacy.matcher import PhraseMatcher

from schemas.entity import (
    ExtractedEntity, EntityAttributes, EntityContext,
    ExtractionResult, EntityType, TemporalStatus, Subject, Trend,
)
from data.clinical_terms import (
    CLINICAL_TERMS, BODY_LOCATIONS, SEVERITY_TERMS, FAMILY_CUES, PAST_CUES,
)

# Phrases like "worse at night" / "better with rest": situational variation,
# not progression. Neutralized before trend keywords are checked.
CONDITIONAL_VARIATION = re.compile(
    r"\b(?:worse|worsens|better)\s+(?:at|in|with|during|when|after|on|upon|by)\b"
)

# Negation cue words (a simplified version of what libraries like
# negspacy implement via dependency parsing)
NEGATION_CUES = ["no", "not", "denies", "denied", "without", "absent", "negative for", "ruled out"]


class ClinicalEntityExtractor:
    def __init__(self):
        self.nlp = spacy.load("en_core_web_sm")
        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self._term_lookup = {}  # surface form (lowercase) -> normalized term
        self._build_matcher()

    def _build_matcher(self):
        for normalized_term, (entity_type, synonyms) in CLINICAL_TERMS.items():
            patterns = [self.nlp.make_doc(s) for s in synonyms]
            self.matcher.add(normalized_term, patterns)
            for s in synonyms:
                self._term_lookup[s.lower()] = (normalized_term, entity_type)

    # ---------- Stage 1: Entity Extraction ----------
    def _find_raw_matches(self, doc):
        matches = self.matcher(doc)

        # Sort by span length (longest first) so that when two dictionary
        # entries overlap (e.g. "wheeze" inside "bilateral expiratory
        # wheeze"), we keep the more specific, longer match and drop the
        # shorter one nested inside it.
        raw = []
        for match_id, start, end in matches:
            span = doc[start:end]
            raw.append((span.start_char, span.end_char, span))
        raw.sort(key=lambda t: (t[1] - t[0]), reverse=True)

        results = []
        occupied = []  # list of (start_char, end_char) already claimed

        def overlaps(a_start, a_end):
            return any(not (a_end <= o_start or a_start >= o_end) for o_start, o_end in occupied)

        for start_char, end_char, span in raw:
            if overlaps(start_char, end_char):
                continue
            occupied.append((start_char, end_char))
            surface = span.text.lower()
            normalized_term, entity_type = self._term_lookup.get(surface, (span.text.lower(), EntityType.SYMPTOM))
            results.append({
                "span": span,
                "raw_text": span.text,
                "normalized_term": normalized_term,
                "entity_type": entity_type,
            })

        # Return in reading order rather than length order
        results.sort(key=lambda r: r["span"].start_char)
        return results

    # ---------- Stage 2: Attribute Extraction ----------
    def _extract_attributes(self, text: str, sentence_text: str) -> EntityAttributes:
        """Location/duration/severity modifiers are searched within the
        entity's own sentence only -- NOT a fixed character radius. A fixed
        radius previously caused two real bugs: "lungs" leaking from the
        next sentence onto "respiratory issues", and "3 days" leaking from
        an earlier clause onto "fever" even though "Denies fever" is its
        own separate sentence with no duration mentioned at all."""
        location = next((loc for loc in BODY_LOCATIONS if loc in sentence_text.lower()), None)
        severity = next((sev for sev in SEVERITY_TERMS if sev in sentence_text.lower()), None)
        duration = self._extract_duration(sentence_text)
        return EntityAttributes(location=location, duration=duration, severity=severity)

    @staticmethod
    def _extract_duration(sentence_text: str) -> Optional[str]:
        # Matches patterns like "3 days", "2 weeks", "1 month"
        match = re.search(r"\b(\d+)\s*(day|days|week|weeks|month|months|year|years)\b", sentence_text.lower())
        return match.group(0) if match else None

    # ---------- Stage 3: Context Analysis ----------
    def _analyze_context(self, window_text: str) -> EntityContext:
        lower = window_text.lower()

        negated = any(cue in lower for cue in NEGATION_CUES)
        subject = Subject.FAMILY if any(cue in lower for cue in FAMILY_CUES) else Subject.PATIENT
        temporal_status = TemporalStatus.PAST if any(cue in lower for cue in PAST_CUES) else TemporalStatus.CURRENT

        # "worse at night", "better with rest", "worse when lying flat" describe
        # WHEN or under WHAT CONDITIONS a symptom varies -- not a change over
        # time -- so they must not be read as a worsening/improving trend.
        lower = CONDITIONAL_VARIATION.sub(" ", lower)

        trend = None
        if "worsening" in lower or "worse" in lower:
            trend = Trend.WORSENING
        elif "improving" in lower or "better" in lower:
            trend = Trend.IMPROVING
        elif "stable" in lower or "unchanged" in lower:
            trend = Trend.STABLE

        return EntityContext(
            negated=negated,
            temporal_status=temporal_status,
            subject=subject,
            trend=trend,
        )

    # ---------- Stage 4: Normalization is handled inline (normalized_term) ----------

    @staticmethod
    def _get_sentence_context(span) -> str:
        """Context Analysis (negation, subject, temporal, trend) must be
        scoped to the entity's OWN sentence, not a fixed character radius.
        A fixed-radius window can leak cues from an adjacent sentence
        (e.g. 'Denies fever. Says her asthma...' would otherwise wrongly
        mark 'asthma' as negated because 'denies' falls within 40 chars).
        Using spaCy's sentence boundary detection fixes this."""
        return span.sent.text

    # ---------- Confidence scoring (simplified heuristic for MVP) ----------
    @staticmethod
    def _confidence_score(raw_text: str, normalized_term: str) -> float:
        # Exact dictionary match -> high confidence.
        # A production model would use actual model logits/probabilities here.
        if raw_text.lower() == normalized_term.lower():
            return 0.95
        return 0.85

    def extract(self, raw_text: str, note_id: Optional[str] = None,
                analysis_id: Optional[str] = None) -> ExtractionResult:
        doc = self.nlp(raw_text)
        raw_matches = self._find_raw_matches(doc)

        entities: List[ExtractedEntity] = []
        for m in raw_matches:
            sentence_text = self._get_sentence_context(m["span"])
            attributes = self._extract_attributes(m["raw_text"], sentence_text)
            context = self._analyze_context(sentence_text)
            confidence = self._confidence_score(m["raw_text"], m["normalized_term"])

            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                analysis_id=analysis_id,
                entity_type=m["entity_type"],
                raw_text=m["raw_text"],
                normalized_term=m["normalized_term"],
                attributes=attributes,
                context=context,
                confidence_score=confidence,
                source_note_id=note_id,
            ))

        return ExtractionResult.from_entities(raw_text=raw_text, entities=entities, note_id=note_id)
