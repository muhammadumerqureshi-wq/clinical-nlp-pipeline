"""
Demo ICD-10-CM terminology dictionary for Module 2.

In production this would be backed by the full ~70,000-code ICD-10-CM
database (typically a proper terminology service / vector index), not a
hardcoded dict. For an MVP this curated subset covers exactly the terms
Module 1's dictionary can extract, which keeps the two modules honestly
in sync for demo/testing purposes.

Structure: normalized_term -> {
    "default": (code, description),
    "severity_map": { severity_keyword: (code, description) }  # optional
}
"""

CODE_MAPPINGS = {
    "asthma": {
        "default": ("J45.909", "Unspecified asthma, uncomplicated"),
        "severity_map": {
            "mild": ("J45.30", "Mild persistent asthma, uncomplicated"),
            "moderate": ("J45.40", "Moderate persistent asthma, uncomplicated"),
            "severe": ("J45.50", "Severe persistent asthma, uncomplicated"),
        },
    },
    "hypertension": {
        "default": ("I10", "Essential (primary) hypertension"),
    },
    "diabetes": {
        "default": ("E11.9", "Type 2 diabetes mellitus without complications"),
    },
    "shortness of breath": {
        "default": ("R06.02", "Shortness of breath"),
    },
    "wheezing": {
        "default": ("R06.2", "Wheezing"),
    },
    "cough": {
        "default": ("R05.9", "Cough, unspecified"),
    },
    "fever": {
        "default": ("R50.9", "Fever, unspecified"),
    },
    "chest pain": {
        "default": ("R07.9", "Chest pain, unspecified"),
    },
    "fatigue": {
        "default": ("R53.83", "Other fatigue"),
    },
    "headache": {
        "default": ("R51.9", "Headache, unspecified"),
    },
    # Note: "respiratory issues" is deliberately NOT mapped here -- it's a
    # vague, non-specific term that a real coder wouldn't code directly;
    # it's included in Module 1's dictionary only to demonstrate family
    # history / negation handling, not as a codeable diagnosis.

    # Findings and measurements generally support diagnoses rather than
    # being coded on their own in ICD-10-CM, so they are intentionally
    # left unmapped here (see mapping_method = "unmapped" for these).
}
