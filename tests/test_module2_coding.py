"""Module 2 -- ICD-10-CM code mapping and its safety gates."""

import pytest

from schemas.coding import MappingMethod
from tests.helpers import SAMPLE_NOTE, coded_by_term


@pytest.fixture()
def coding(extractor, code_mapper):
    extraction = extractor.extract(SAMPLE_NOTE, note_id="N-1", analysis_id="A-1")
    return code_mapper.map_entities(extraction.entities, note_id="N-1", analysis_id="A-1")


def test_counts_account_for_every_entity(coding):
    assert coding.coded_count == 3
    assert coding.skipped_count == 3
    assert len(coding.coded_entities) == 6  # nothing silently dropped


def test_exact_match_codes(coding):
    sob = coded_by_term(coding, "shortness of breath")
    wheezing = coded_by_term(coding, "wheezing")
    assert (sob.selected_code, sob.mapping_method) == ("R06.02", MappingMethod.EXACT_MATCH)
    assert (wheezing.selected_code, wheezing.mapping_method) == ("R06.2", MappingMethod.EXACT_MATCH)


def test_severity_refines_to_specific_subcode(coding):
    asthma = coded_by_term(coding, "asthma")
    assert asthma.selected_code == "J45.30"
    assert asthma.mapping_method == MappingMethod.ATTRIBUTE_REFINED
    # The full ranked shortlist is preserved for audit, best first.
    scores = [c.match_score for c in asthma.candidate_codes]
    assert scores == sorted(scores, reverse=True)
    assert {"J45.30", "J45.909", "J45.40", "J45.50"} <= {c.code for c in asthma.candidate_codes}


def test_unspecified_severity_falls_back_to_default_code(extractor, code_mapper):
    extraction = extractor.extract("Patient has asthma.")
    coding = code_mapper.map_entities(extraction.entities)
    asthma = coded_by_term(coding, "asthma")
    assert asthma.selected_code == "J45.909"
    assert asthma.mapping_method == MappingMethod.EXACT_MATCH


def test_negated_entity_is_never_coded(coding):
    fever = coded_by_term(coding, "fever")
    assert fever.selected_code is None
    assert fever.mapping_method == MappingMethod.NOT_CODED_NEGATED


def test_regression_negated_family_history_preserves_both_reasons(coding):
    """Primary reason is family history; negation must not be silently lost."""
    entity = coded_by_term(coding, "respiratory issues")
    assert entity.selected_code is None
    assert entity.mapping_method == MappingMethod.NOT_CODED_FAMILY_HISTORY
    assert set(entity.exclusion_reasons) == {
        MappingMethod.NOT_CODED_FAMILY_HISTORY, MappingMethod.NOT_CODED_NEGATED,
    }


def test_finding_is_not_codeable_by_design_not_a_coding_gap(coding):
    finding = coded_by_term(coding, "bilateral expiratory wheeze")
    assert finding.selected_code is None
    assert finding.mapping_method == MappingMethod.NOT_CODEABLE_TYPE


def test_codeable_type_without_dictionary_match_is_unmapped(extractor, code_mapper):
    """A symptom the extractor recognizes but the code dictionary lacks is a
    genuine coding gap (UNMAPPED) -- distinct from a finding, which is never
    eligible for coding at all (NOT_CODEABLE_TYPE)."""
    from data import code_mappings
    saved = code_mappings.CODE_MAPPINGS.pop("headache")
    try:
        extraction = extractor.extract("Patient has a headache.")
        coding = code_mapper.map_entities(extraction.entities)
        headache = coded_by_term(coding, "headache")
        assert headache.mapping_method == MappingMethod.UNMAPPED
        assert headache.selected_code is None
    finally:
        code_mappings.CODE_MAPPINGS["headache"] = saved


def test_source_entity_is_preserved_unmodified(extractor, code_mapper):
    extraction = extractor.extract(SAMPLE_NOTE)
    coding = code_mapper.map_entities(extraction.entities)
    originals = {e.entity_id: e for e in extraction.entities}
    for c in coding.coded_entities:
        assert c.entity_id == c.source_entity.entity_id
        assert c.source_entity == originals[c.entity_id]


def test_mapping_confidence_combines_extraction_and_match_confidence(coding):
    asthma = coded_by_term(coding, "asthma")
    assert asthma.mapping_confidence == round(asthma.source_entity.confidence_score * 0.95, 2)
    assert coded_by_term(coding, "fever").mapping_confidence == 0.0
