import pytest
from fastapi.testclient import TestClient

from autonomous_intelligence.api import app
from autonomous_intelligence.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient wired to a fresh tmp SQLite DB + tmp output dir per test,
    with a demo AI provider so no real API calls are made. `api.app`'s
    routes and `lifespan` all call `get_settings()` at request/startup time
    (not import time), so clearing the cache after patching env vars is
    enough - no module reload needed."""
    monkeypatch.setenv("AI_PROVIDER", "demo")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DAILY_CONTENT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("BRAND_ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("DAILY_TRENDS_COUNT", "1")
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


@pytest.fixture
def signup():
    """Returns a `signup(client, ...)` helper - a fixture (not a plain
    importable function) so tests don't need `tests` to be an importable
    package."""

    def _signup(
        client: TestClient, *, company_name="Acme Inc.", email="owner@acme.test", password="hunter22"
    ):
        response = client.post(
            "/auth/signup",
            json={"company_name": company_name, "email": email, "password": password},
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _signup
