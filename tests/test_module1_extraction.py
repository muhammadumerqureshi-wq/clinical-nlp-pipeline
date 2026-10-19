"""Module 1 -- clinical entity extraction."""

import pytest

from schemas.entity import EntityType, Subject, TemporalStatus, Trend
from tests.helpers import SAMPLE_NOTE


def by_term(result, term):
    return next(e for e in result.entities if e.normalized_term == term)


def test_extracts_expected_entities_in_reading_order(extractor):
    result = extractor.extract(SAMPLE_NOTE)
    assert [e.normalized_term for e in result.entities] == [
        "shortness of breath", "wheezing", "fever", "asthma",
        "respiratory issues", "bilateral expiratory wheeze",
    ]
    assert result.entity_count == 6


def test_entity_types(extractor):
    result = extractor.extract(SAMPLE_NOTE)
    assert by_term(result, "shortness of breath").entity_type == EntityType.SYMPTOM
    assert by_term(result, "asthma").entity_type == EntityType.CONDITION
    assert by_term(result, "bilateral expiratory wheeze").entity_type == EntityType.FINDING


def test_synonyms_are_normalized_but_raw_text_is_preserved(extractor):
    result = extractor.extract("Patient has SOB.")
    entity = result.entities[0]
    assert entity.raw_text == "SOB"
    assert entity.normalized_term == "shortness of breath"


def test_traceability_ids_propagate(extractor):
    result = extractor.extract(SAMPLE_NOTE, note_id="N-1", analysis_id="A-1")
    assert result.note_id == "N-1"
    assert all(e.source_note_id == "N-1" and e.analysis_id == "A-1" for e in result.entities)
    assert len({e.entity_id for e in result.entities}) == len(result.entities)


def test_confidence_scores_are_valid_probabilities(extractor):
    result = extractor.extract(SAMPLE_NOTE)
    assert all(0.0 <= e.confidence_score <= 1.0 for e in result.entities)


def test_empty_note_yields_no_entities(extractor):
    result = extractor.extract("")
    assert result.entities == []
    assert result.entity_count == 0


# ---------- context analysis ----------

def test_negated_symptom_is_flagged(extractor):
    assert by_term(extractor.extract(SAMPLE_NOTE), "fever").context.negated is True


def test_family_history_is_flagged_and_negated(extractor):
    ctx = by_term(extractor.extract(SAMPLE_NOTE), "respiratory issues").context
    assert ctx.subject == Subject.FAMILY
    assert ctx.negated is True
    assert ctx.temporal_status == TemporalStatus.PAST


def test_severity_and_duration_are_extracted(extractor):
    result = extractor.extract(SAMPLE_NOTE)
    assert by_term(result, "asthma").attributes.severity == "mild"
    assert by_term(result, "shortness of breath").attributes.duration == "3 days"
    assert by_term(result, "wheezing").attributes.duration == "3 days"


# ---------- regression tests for bugs found during development ----------

def test_regression_negation_does_not_leak_across_sentences(extractor):
    """'Denies fever. Says her asthma...' must not negate asthma."""
    assert by_term(extractor.extract(SAMPLE_NOTE), "asthma").context.negated is False


def test_regression_duration_does_not_leak_across_sentences(extractor):
    """'Denies fever.' is its own sentence and must not inherit '3 days'."""
    assert by_term(extractor.extract(SAMPLE_NOTE), "fever").attributes.duration is None


def test_regression_location_does_not_leak_across_sentences(extractor):
    """'Lungs:' belongs to the next sentence, not to 'respiratory issues'."""
    result = extractor.extract(SAMPLE_NOTE)
    assert by_term(result, "respiratory issues").attributes.location is None
    assert by_term(result, "bilateral expiratory wheeze").attributes.location == "lungs"


def test_regression_overlapping_dictionary_terms_keep_longest_match(extractor):
    """'wheeze' nested inside 'expiratory wheeze on auscultation' must not
    produce additional duplicate entities."""
    result = extractor.extract("Lungs: bilateral expiratory wheeze on auscultation.")
    assert len(result.entities) == 1
    assert result.entities[0].normalized_term == "bilateral expiratory wheeze"


@pytest.mark.parametrize("note,term,expected", [
    # situational variation -- NOT a change over time
    ("Shortness of breath for 3 days, worse at night.", "shortness of breath", None),
    ("Cough is better with rest.", "cough", None),
    ("Wheezing, worse when lying flat.", "wheezing", None),
    # genuine progression
    ("Asthma has been getting worse compared to last month.", "asthma", Trend.WORSENING),
    ("Asthma, worse than last visit.", "asthma", Trend.WORSENING),
    ("Cough is better than last week.", "cough", Trend.IMPROVING),
    ("Asthma is stable.", "asthma", Trend.STABLE),
])
def test_regression_trend_distinguishes_situational_variation_from_progression(extractor, note, term, expected):
    """'worse at night' once produced trend=worsening, which later surfaced as a
    clinically wrong 'worsening' statement in generated SOAP drafts."""
    entity = by_term(extractor.extract(note), term)
    assert entity.context.trend == expected


def test_sample_note_trends(extractor):
    result = extractor.extract(SAMPLE_NOTE)
    assert by_term(result, "shortness of breath").context.trend is None
    assert by_term(result, "wheezing").context.trend is None
    assert by_term(result, "asthma").context.trend == Trend.WORSENING
