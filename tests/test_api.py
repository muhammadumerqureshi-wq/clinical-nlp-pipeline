"""HTTP-level tests for every FastAPI endpoint, driving the full pipeline."""

import pytest
from fastapi.testclient import TestClient

from tests.helpers import SAMPLE_NOTE


@pytest.fixture()
def client(db):  # `db` fixture resets the schema for each test
    from main import app
    return TestClient(app)


def create_visit(client, note_text=SAMPLE_NOTE, date="2026-07-20", patient_id=None):
    if patient_id is None:
        patient_id = client.post("/patients", json={"name": "Ayesha Khan", "dob": "1990-04-12", "gender": "F"}).json()["id"]
    encounter_id = client.post(
        f"/patients/{patient_id}/encounters", json={"encounter_date": date, "encounter_type": "follow-up"}
    ).json()["id"]
    note = client.post(f"/encounters/{encounter_id}/clinical-notes", json={"raw_text": note_text}).json()
    return patient_id, encounter_id, note


def code_and_review(client, note, note_text=SAMPLE_NOTE, edit_asthma=True):
    coding = client.post("/code", json={
        "raw_text": note_text, "note_id": note["id"], "analysis_id": note["analysis_id"],
    }).json()
    reviews = []
    for c in coding["coded_entities"]:
        if c["mapping_method"] in ("not_coded_negated", "not_coded_family_history"):
            continue
        review = {"coding_id": c["coding_id"], "decision": "approved", "reviewer_id": "dr_ali"}
        if edit_asthma and c["source_entity"]["normalized_term"] == "asthma":
            review.update(decision="edited", edited_code="J45.40",
                          edited_description="Moderate persistent asthma, uncomplicated")
        reviews.append(review)
    review_result = client.post("/review", json={
        "analysis_id": note["analysis_id"], "note_id": note["id"],
        "coded_entities": coding["coded_entities"], "reviews": reviews,
    }).json()
    return coding, review_result


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_extract_endpoint(client):
    body = client.post("/extract", json={"raw_text": SAMPLE_NOTE, "note_id": "N-1"}).json()
    assert body["entity_count"] == 6
    assert body["note_id"] == "N-1"


def test_extract_validates_input(client):
    assert client.post("/extract", json={}).status_code == 422


def test_code_endpoint_returns_coded_and_skipped_counts(client):
    body = client.post("/code", json={"raw_text": SAMPLE_NOTE}).json()
    assert (body["coded_count"], body["skipped_count"]) == (3, 3)


def test_full_pipeline_over_http(client):
    patient_id, encounter_id, note = create_visit(client)
    assert note["analysis_status"] == "pending"

    coding, review_result = code_and_review(client, note)
    assert review_result["analysis_status"] == "approved"

    persisted = client.post(
        f"/analyses/{note['analysis_id']}/approved-results",
        json={"coding_result": coding, "review_result": review_result},
    ).json()
    assert persisted["analysis_status"] == "approved"
    assert persisted["persisted_count"] == 4  # SOB, wheezing, asthma, finding
    assert persisted["skipped_count"] == 2    # negated fever, family-history entity

    analysis = client.get(f"/analyses/{note['analysis_id']}").json()
    assert analysis["status"] == "approved"
    asthma = next(e for e in analysis["approved_entities"] if e["entity"]["normalized_term"] == "asthma")
    assert asthma["code"]["selected_code"] == "J45.30"      # AI proposal preserved
    assert asthma["review"]["final_code"] == "J45.40"       # clinician's edit
    finding = next(e for e in analysis["approved_entities"]
                   if e["entity"]["normalized_term"] == "bilateral expiratory wheeze")
    assert finding["review"]["final_code"] is None

    approved = client.get(f"/patients/{patient_id}/approved-entities").json()
    assert len(approved) == 4

    current = client.post("/extract", json={"raw_text": SAMPLE_NOTE}).json()["entities"]
    history = client.post(f"/patients/{patient_id}/relevant-history", json={
        "patient_id": patient_id, "current_entities": current, "exclude_encounter_id": encounter_id,
    })
    assert history.status_code == 200

    soap = client.post(f"/analyses/{note['analysis_id']}/soap-draft", json=history.json()).json()
    assert soap["status"] == "draft"
    assert "bilateral expiratory wheeze" in soap["objective"]
    assert "J45.40" in soap["assessment"]


def test_history_flows_into_soap_across_two_visits(client):
    patient_id, _, note1 = create_visit(client, "Patient has mild asthma.", date="2026-06-01")
    coding, review = code_and_review(client, note1, "Patient has mild asthma.", edit_asthma=False)
    client.post(f"/analyses/{note1['analysis_id']}/approved-results",
                json={"coding_result": coding, "review_result": review})

    note2_text = "Patient has moderate asthma, worsening."
    _, enc2, note2 = create_visit(client, note2_text, date="2026-07-20", patient_id=patient_id)
    coding2, review2 = code_and_review(client, note2, note2_text, edit_asthma=False)
    client.post(f"/analyses/{note2['analysis_id']}/approved-results",
                json={"coding_result": coding2, "review_result": review2})

    current = client.post("/extract", json={"raw_text": note2_text}).json()["entities"]
    history = client.post(f"/patients/{patient_id}/relevant-history", json={
        "patient_id": patient_id, "current_entities": current, "exclude_encounter_id": enc2,
    }).json()
    assert [e["severity"] for e in history["mandatory_safety_context"]] == ["mild"]

    soap = client.post(f"/analyses/{note2['analysis_id']}/soap-draft", json=history).json()
    assert "prior documented severity: mild" in soap["assessment"]


# ---------- error handling ----------

@pytest.mark.parametrize("method,url,body", [
    ("get", "/patients/nope", None),
    ("post", "/patients/nope/encounters", {}),
    ("post", "/encounters/nope/clinical-notes", {"raw_text": "x"}),   # was silently accepted before
    ("get", "/analyses/nope", None),
    ("get", "/patients/nope/approved-entities", None),
])
def test_unknown_resources_return_404(client, method, url, body):
    response = getattr(client, method)(url, **({"json": body} if body is not None else {}))
    assert response.status_code == 404


def test_soap_draft_for_unknown_analysis_is_404(client):
    empty_history = {"patient_id": "p", "mandatory_safety_context": [],
                     "relevant_historical_context": [], "trend_summary": {}}
    assert client.post("/analyses/nope/soap-draft", json=empty_history).status_code == 404


def test_persisting_to_unknown_analysis_is_404(client):
    _, _, note = create_visit(client)
    coding, review = code_and_review(client, note)
    response = client.post("/analyses/nope/approved-results",
                           json={"coding_result": coding, "review_result": review})
    assert response.status_code == 404


def test_relevant_history_rejects_mismatched_patient_id(client):
    patient_id, _, _ = create_visit(client)
    response = client.post(f"/patients/{patient_id}/relevant-history", json={
        "patient_id": "someone-else", "current_entities": [],
    })
    assert response.status_code == 400


def test_relevant_history_for_unknown_patient_is_404(client):
    response = client.post("/patients/nope/relevant-history", json={"patient_id": "nope", "current_entities": []})
    assert response.status_code == 404
