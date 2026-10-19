"""
Shared pytest fixtures.

SAFETY: DATABASE_URL is overridden *unconditionally* with a throwaway SQLite
file before any application module is imported. Several tests call
`Base.metadata.drop_all()`, so they must never be able to run against a real
database that happens to be configured in the developer's environment.

Application modules are imported lazily inside fixtures so that a fixture only
requires the modules it actually uses.
"""

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP_DIR = tempfile.mkdtemp(prefix="clinical_nlp_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP_DIR, 'test.db')}"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def extractor():
    from core.extractor import ClinicalEntityExtractor
    return ClinicalEntityExtractor()  # spaCy model load is expensive: do it once


@pytest.fixture(scope="session")
def code_mapper():
    from core.code_mapper import ClinicalCodeMapper
    return ClinicalCodeMapper()


@pytest.fixture(scope="session")
def review_manager():
    from core.review_manager import ClinicianReviewManager
    return ClinicianReviewManager()


@pytest.fixture(scope="session")
def persistence():
    from core.persistence_service import PersistenceService
    return PersistenceService()


@pytest.fixture(scope="session")
def history_service():
    from core.history_retrieval import HistoryRetrievalService
    return HistoryRetrievalService()


@pytest.fixture(scope="session")
def soap_generator():
    from core.soap_generator import SOAPGenerator
    return SOAPGenerator()


@pytest.fixture()
def db():
    """Fresh schema + session for every test."""
    from db.database import Base, SessionLocal, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def services(request):
    """Bundle of all services for tests that drive the pipeline. Each service
    is resolved lazily, only when a test actually touches it."""
    class Services:
        extractor = property(lambda self: request.getfixturevalue("extractor"))
        code_mapper = property(lambda self: request.getfixturevalue("code_mapper"))
        review_manager = property(lambda self: request.getfixturevalue("review_manager"))
        persistence = property(lambda self: request.getfixturevalue("persistence"))
        history = property(lambda self: request.getfixturevalue("history_service"))
        soap = property(lambda self: request.getfixturevalue("soap_generator"))

    return Services()
