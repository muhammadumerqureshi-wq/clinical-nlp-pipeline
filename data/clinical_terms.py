"""
Demo clinical term dictionary.

In a production system this would be replaced/augmented by a real medical
ontology (SNOMED CT, UMLS) and likely a trained clinical NER model
(e.g. a fine-tuned BERT/med7). For a portfolio-grade MVP, a curated
dictionary + spaCy PhraseMatcher is a legitimate, explainable starting
point and is easy to extend.

Structure: normalized_term -> (entity_type, [surface form synonyms])
"""

from schemas.entity import EntityType

CLINICAL_TERMS = {
    # Symptoms
    "shortness of breath": (EntityType.SYMPTOM, ["shortness of breath", "sob", "breathing difficulty", "trouble breathing", "dyspnea"]),
    "wheezing": (EntityType.SYMPTOM, ["wheezing", "wheeze"]),
    "cough": (EntityType.SYMPTOM, ["cough", "coughing"]),
    "fever": (EntityType.SYMPTOM, ["fever", "high temperature", "febrile"]),
    "chest pain": (EntityType.SYMPTOM, ["chest pain", "chest discomfort"]),
    "fatigue": (EntityType.SYMPTOM, ["fatigue", "tiredness", "exhaustion"]),
    "headache": (EntityType.SYMPTOM, ["headache", "head pain"]),

    # Conditions
    "asthma": (EntityType.CONDITION, ["asthma"]),
    "hypertension": (EntityType.CONDITION, ["hypertension", "high blood pressure"]),
    "diabetes": (EntityType.CONDITION, ["diabetes", "diabetes mellitus"]),
    "respiratory issues": (EntityType.CONDITION, ["respiratory issues", "respiratory problems"]),

    # Findings (exam observations)
    "bilateral expiratory wheeze": (EntityType.FINDING, ["bilateral expiratory wheeze", "expiratory wheeze on auscultation"]),
    "regular heart rate": (EntityType.FINDING, ["regular rate and rhythm", "normal heart sounds"]),
    "clear lungs": (EntityType.FINDING, ["lungs clear to auscultation", "clear breath sounds"]),

    # Measurements
    "oxygen saturation": (EntityType.MEASUREMENT, ["spo2", "oxygen saturation", "o2 sat"]),
    "blood pressure": (EntityType.MEASUREMENT, ["blood pressure", "bp"]),
    "temperature": (EntityType.MEASUREMENT, ["temperature", "temp"]),

    # Medications
    "albuterol": (EntityType.MEDICATION, ["albuterol", "salbutamol"]),
    "ibuprofen": (EntityType.MEDICATION, ["ibuprofen"]),
}

# Body locations - used for the `location` attribute
BODY_LOCATIONS = [
    "chest", "lungs", "left lung", "right lung", "bilateral", "abdomen",
    "head", "back", "lower back", "throat", "heart",
]

# Severity cue words
SEVERITY_TERMS = ["mild", "moderate", "severe", "worsening", "acute", "chronic"]

# Words that flip a following entity to "family" subject
FAMILY_CUES = ["mother", "father", "sister", "brother", "family history", "parent", "sibling"]

# Words that mark past/resolved status
PAST_CUES = ["history of", "previously", "resolved", "used to have", "in the past"]
