"""
Module 2 — Clinical Terminology Normalization / Code Mapping Engine.

Consumes ExtractedEntity objects produced by Module 1 and maps them to
ICD-10-CM codes, while:
  - preserving the full original entity for traceability (source_entity)
  - refusing to code entities that are negated or belong to family history
    (a naive mapper would code these, which would be a real patient-safety
    bug -- see the reasoning in _map_single_entity)
  - narrowing to a more specific code when severity is known (attribute-
    refined mapping), falling back to a default/unspecified code otherwise
  - distinguishing entities that were NEVER eligible for coding (findings,
    measurements -- MappingMethod.NOT_CODEABLE_TYPE) from entities that
    WERE eligible but had no dictionary match (MappingMethod.UNMAPPED).
    This distinction matters downstream: Module 3 treats both as still
    fully reviewable/approvable as clinical facts, just never coded.
"""

import uuid
from typing import List

from schemas.entity import ExtractedEntity, EntityType
from schemas.coding import CodedEntity, CodeCandidate, CodingResult, MappingMethod, CodeSystem
from data.code_mappings import CODE_MAPPINGS

# Which entity types are eligible for ICD-10-CM diagnosis coding. Findings
# and measurements are real, clinically approvable facts -- they just
# don't get a billing code, because ICD-10-CM codes diagnoses/symptoms,
# not exam observations. See MappingMethod.NOT_CODEABLE_TYPE.
CODEABLE_TYPES = {EntityType.SYMPTOM, EntityType.CONDITION}


class ClinicalCodeMapper:

    def map_entities(self, entities: List[ExtractedEntity],
                      note_id=None, analysis_id=None) -> CodingResult:
        coded = [self._map_single_entity(e) for e in entities]
        return CodingResult.from_coded_entities(coded, note_id=note_id, analysis_id=analysis_id)

    def _map_single_entity(self, entity: ExtractedEntity) -> CodedEntity:
        # Collect ALL applicable exclusion reasons first, rather than
        # returning on the first match -- an entity can be BOTH negated
        # AND family-history at once (e.g. "No family history of X"), and
        # the earlier bug was that checking negation first meant the
        # family-history reason was silently lost even though it's the
        # more fundamental fact (whose history is this, before whether
        # it's affirmed or denied).
        reasons: List[MappingMethod] = []

        if entity.context.subject.value == "family":
            reasons.append(MappingMethod.NOT_CODED_FAMILY_HISTORY)
        if entity.context.negated:
            reasons.append(MappingMethod.NOT_CODED_NEGATED)

        if reasons:
            # Family history is the primary reason when both apply: it's a
            # structural fact about WHOSE record this is, and negation is
            # a modifier on top of that -- "no family history" is still
            # fundamentally a family-history entity, just a denied one.
            primary = (MappingMethod.NOT_CODED_FAMILY_HISTORY
                       if MappingMethod.NOT_CODED_FAMILY_HISTORY in reasons
                       else MappingMethod.NOT_CODED_NEGATED)
            return self._skip(entity, primary, all_reasons=reasons)

        # --- Entity type is not eligible for ICD-10-CM coding at all ---
        # (finding/measurement) -- this is NOT an error and does NOT mean
        # the entity isn't clinically real. It stays fully eligible for
        # clinician review and persistence downstream (Module 3); it just
        # never gets a billing code, by design.
        if entity.entity_type not in CODEABLE_TYPES:
            return self._skip(entity, MappingMethod.NOT_CODEABLE_TYPE)

        # --- Entity type IS codeable, but nothing matched in our dictionary ---
        # (a genuine coding gap, distinct from the above)
        mapping = CODE_MAPPINGS.get(entity.normalized_term)
        if mapping is None:
            return self._skip(entity, MappingMethod.UNMAPPED)

        return self._map_found_entity(entity, mapping)

    def _map_found_entity(self, entity: ExtractedEntity, mapping: dict) -> CodedEntity:
        candidates: List[CodeCandidate] = []

        default_code, default_desc = mapping["default"]
        candidates.append(CodeCandidate(code=default_code, description=default_desc, match_score=0.7))

        severity_map = mapping.get("severity_map", {})
        for sev_keyword, (code, desc) in severity_map.items():
            score = 0.95 if entity.attributes.severity == sev_keyword else 0.5
            candidates.append(CodeCandidate(code=code, description=desc, match_score=score))

        # Rank candidates by match_score, best first
        candidates.sort(key=lambda c: c.match_score, reverse=True)
        best = candidates[0]

        used_severity_refinement = (
            entity.attributes.severity is not None
            and entity.attributes.severity in severity_map
            and best.code != default_code
        )
        method = MappingMethod.ATTRIBUTE_REFINED if used_severity_refinement else MappingMethod.EXACT_MATCH

        # Combine the upstream extraction confidence with this stage's own
        # match confidence -- a low-confidence extraction should never
        # produce a falsely high-confidence code.
        mapping_confidence = round(entity.confidence_score * best.match_score, 2)

        return CodedEntity(
            coding_id=str(uuid.uuid4()),
            entity_id=entity.entity_id,
            source_entity=entity,
            code_system=CodeSystem.ICD10CM,
            candidate_codes=candidates,
            selected_code=best.code,
            selected_description=best.description,
            mapping_method=method,
            mapping_confidence=mapping_confidence,
        )

    @staticmethod
    def _skip(entity: ExtractedEntity, reason: MappingMethod, all_reasons=None) -> CodedEntity:
        """Entity is intentionally NOT coded, but still fully preserved and
        returned -- traceability means every input entity produces an
        output record, even the ones we deliberately decline to code."""
        return CodedEntity(
            coding_id=str(uuid.uuid4()),
            entity_id=entity.entity_id,
            source_entity=entity,
            code_system=CodeSystem.ICD10CM,
            candidate_codes=[],
            selected_code=None,
            selected_description=None,
            mapping_method=reason,
            exclusion_reasons=all_reasons if all_reasons is not None else [reason],
            mapping_confidence=0.0,
        )
