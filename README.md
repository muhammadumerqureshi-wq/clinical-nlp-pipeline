# Clinical NLP Pipeline

[![tests](https://github.com/muhammadumerqureshi-wq/clinical-nlp-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/muhammadumerqureshi-wq/clinical-nlp-pipeline/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.12-blue)
![license](https://img.shields.io/badge/license-MIT-green)

A FastAPI backend that turns free-text clinical notes into **structured, clinician-reviewed, ICD-10-CM-coded data**, retrieves a patient's relevant history, and drafts a **SOAP note** with full source traceability.

It is built around one idea: in a clinical setting, *what the system refuses to do matters as much as what it does.* Negated findings are never coded as diagnoses. Nothing reaches the database without a clinician's decision. A treatment plan is never invented.

> **Disclaimer.** Portfolio/educational project. All clinical text in this repo is synthetic. It is not a medical device, has no authentication, and must not be used with real patient data.

## Pipeline

```
 raw clinical note
        │
        ▼
┌─────────────────┐   spaCy PhraseMatcher + sentence-scoped context analysis
│ 1. Extraction   │   (negation, family history, temporal status, trend, attributes)
└────────┬────────┘
         ▼
┌─────────────────┐   ICD-10-CM mapping with severity refinement; refuses to code
│ 2. Coding       │   negated / family-history entities
└────────┬────────┘
         ▼
┌─────────────────┐   approve / edit / reject per entity; nothing is final until a
│ 3. Review       │   clinician decides
└────────┬────────┘
         ▼
┌─────────────────┐   only approved/edited data is written; AI proposal and clinician
│ 4. Persistence  │   decision are stored side by side (audit trail)
└────────┬────────┘
         ▼
┌─────────────────┐   two lanes: deterministic SQL exact-match (safety-critical) +
│ 5. Retrieval    │   BM25 fuzzy match (supplementary). Reads approved data only.
└────────┬────────┘
         ▼
┌─────────────────┐   draft S/O/A/P assembled only from approved data + the bounded
│ 6. SOAP draft   │   retrieved context; every sentence traceable to entity IDs
└─────────────────┘
```

## Design principles

| Principle | How it is enforced |
|---|---|
| **Never code what isn't true of the patient** | Negated (`Denies fever`) and family-history entities are excluded in Module 2. When both apply, the primary reason is *family history* and `exclusion_reasons` records both. |
| **Clinically approved ≠ billable** | Findings and measurements are never ICD-coded (`not_codeable_type`) but are fully reviewable, persistable, retrievable and used in SOAP Objective. "No code" is not "nothing to review". |
| **Human in the loop** | An entity with no decision stays `pending`; the analysis stays `needs_review` until every reviewable entity is decided. Unreviewed is never silently approved. |
| **Auditability** | The AI's proposed code (`selected_code`) and the clinician's final decision (`final_code`) are both persisted. Pipeline IDs are reused as primary keys, so a database row traces back to the exact in-memory entity. |
| **Bounded context** | Retrieval never feeds a patient's whole history downstream. The SQL lane guarantees inclusion of exact matches; BM25 is supplementary and never re-surfaces what SQL already returned. |
| **Never fabricate** | The Plan section is a placeholder + warning, because no structured plan data exists in the pipeline. SOAP assembly uses only fields that exist on approved entities. |

## Quick start

```bash
git clone https://github.com/muhammadumerqureshi-wq/clinical-nlp-pipeline.git
cd clinical-nlp-pipeline

python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m spacy download en_core_web_sm
```

Run the end-to-end demo (two synthetic visits, no server needed):

```bash
python examples/demo.py
```

Run the API (interactive docs at http://127.0.0.1:8000/docs):

```bash
uvicorn main:app --reload
```

Run the tests:

```bash
python -m pytest
```

### Demo output

```text
======================================================================
VISIT 1 (2026-06-01): extraction + coding
======================================================================
  'asthma'                               condition  code=J45.30   attribute_refined

  review -> approved; persisted 1 approved entities, skipped 0

======================================================================
VISIT 2 (2026-07-20): extraction + coding
======================================================================
  'shortness of breath'                  symptom    code=R06.02   exact_match
  'wheezing'                             symptom    code=R06.2    exact_match
  'fever'                                symptom    code=-        not_coded_negated  [negated]
  'Asthma'                               condition  code=J45.40   attribute_refined
  'respiratory issues'                   condition  code=-        not_coded_family_history  [negated, family history]
  'expiratory wheeze on auscultation'    finding    code=-        not_codeable_type

  review -> approved; persisted 4 approved entities, skipped 2

======================================================================
Relevant history retrieved for visit 2 (bounded, approved data only)
======================================================================
  [sql_exact] asthma (severity=mild, seen 2026-06-01)
  fuzzy (BM25) matches: 0

======================================================================
DRAFT SOAP note for visit 2
======================================================================
  status : DRAFT  (method: template_composer)
  S: Patient reports shortness of breath for 3 days; wheezing for 3 days.
  O: Exam notable for bilateral expiratory wheeze (lungs).
  A: Asthma (J45.40 - Moderate persistent asthma, uncomplicated) -- prior documented severity: mild.
  P: [No structured plan data available from approved entities -- clinician to complete Plan section directly.]
  ! Plan section intentionally left as a placeholder: no treatment/medication data source exists in the current pipeline to draw from.
```

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/extract` | Module 1 only: note → structured entities |
| `POST` | `/code` | Modules 1+2: note → entities + ICD-10-CM codes |
| `POST` | `/review` | Module 3: apply approve/edit/reject decisions |
| `POST` | `/patients` | Create patient |
| `GET` | `/patients/{id}` | Fetch patient |
| `POST` | `/patients/{id}/encounters` | Create encounter |
| `POST` | `/encounters/{id}/clinical-notes` | Store a note and open a `pending` analysis |
| `POST` | `/analyses/{id}/approved-results` | Module 4: persist **only** approved/edited entities |
| `GET` | `/analyses/{id}` | Read back the persisted, approved record |
| `GET` | `/patients/{id}/approved-entities` | All approved entities for a patient |
| `POST` | `/patients/{id}/relevant-history` | Module 5: two-lane bounded history |
| `POST` | `/analyses/{id}/soap-draft` | Module 6: draft SOAP from approved data + supplied history |
| `GET` | `/health` | Liveness |

Typical flow: create patient → encounter → note → `/code` → `/review` → `/approved-results` → `/relevant-history` → `/soap-draft`. Missing parents return `404`.

## Data model

```
Patient ─1:N─ Encounter ─1:N─ ClinicalNote ─1:N─ Analysis ─1:N─ ExtractedEntity ─1:1─ CodedEntity ─1:1─ HumanReview
```

Foreign keys are enforced (including on SQLite, via `PRAGMA foreign_keys=ON`). Only entities with an `approved`/`edited` decision get `ExtractedEntity`/`CodedEntity`/`HumanReview` rows. Re-persisting the same analysis is idempotent.

## Testing

85 tests across all modules plus HTTP-level tests for every endpoint. They run against a throwaway SQLite file; `conftest.py` overrides `DATABASE_URL` unconditionally so tests can never touch a real database.

Every bug found during development has a named regression test, and those tests were verified to **fail when the bug is re-introduced** (not just to pass on the fixed code). Examples:

- negation / duration / location leaking across sentence boundaries
- overlapping dictionary terms double-counting one finding
- a negated *family-history* entity losing one of its two exclusion reasons
- findings being auto-excluded from review because they have no ICD code
- BM25 returning a misleadingly empty result on a tiny corpus (see below)
- "worse at night" being read as a *worsening trend* (situational variation ≠ progression)

### The BM25 small-corpus quirk

Classic BM25's IDF can go **negative** when a term appears in most or all documents of a very small corpus, so a genuine match scores below zero and is silently filtered out. Retrieval therefore skips the BM25 lane below `MIN_CORPUS_SIZE_FOR_BM25` candidates instead of returning an empty list that could be mistaken for "nothing relevant exists".

## Known limitations

Stated plainly, so nobody has to discover them:

- **Extraction is dictionary-based** (19 concepts / 42 surface forms), not a trained clinical NER model. It is explainable and deterministic, but it only finds what the dictionary knows.
- **ICD-10-CM coverage is a curated subset** (13 codes), not the full ~70,000-code set.
- **Context analysis is rule-based and sentence-scoped** (negation cues, family cues, trend keywords), not dependency-parsed.
- **SOAP generation is a deterministic template composer, not an LLM.** That guarantees no fabricated content by construction. `GenerationMethod.LLM` is reserved in the schema; any future LLM layer would have to preserve the same "never invent, always flag gaps" contract.
- **Retrieval is keyword-based.** BM25 uses whitespace tokenization with no stemming ("breathing" ≠ "breath").
- **Measurements have no numeric `value`/`unit` yet**, so lab/vital trends cannot be compared.
- **No authentication/authorization and no audit-log hardening** — unsuitable for real PHI.
- **No migrations.** Tables are created with `create_all`; a production deployment would add Alembic.
- **Only SQLite is exercised by the test suite.** PostgreSQL/MySQL are supported through `DATABASE_URL` (all access is via SQLAlchemy) but are untested here.

## Roadmap

- Numeric measurement `value`/`unit` extraction
- Cross-encounter progress / trend analysis (deterministic comparison first, severity-aware, with an explicit `insufficient_evidence` outcome)
- Optional LLM-assisted narrative that may *rephrase* but never override deterministic clinical data
- Authentication, Alembic migrations, PostgreSQL in CI

## Project layout

```
.
├── main.py                  # FastAPI app (wires modules to endpoints)
├── core/
│   ├── extractor.py         # Module 1
│   ├── code_mapper.py       # Module 2
│   ├── review_manager.py    # Module 3
│   ├── persistence_service.py  # Module 4
│   ├── history_retrieval.py # Module 5
│   └── soap_generator.py    # Module 6
├── schemas/                 # Pydantic contracts between modules
├── db/                      # SQLAlchemy engine + ORM models
├── data/                    # clinical term dictionary, ICD-10-CM mappings
├── examples/demo.py         # runnable end-to-end demo
├── tests/                   # pytest suite
└── .github/workflows/ci.yml # runs the suite on every push / PR
```

## License

MIT — see [LICENSE](LICENSE).
