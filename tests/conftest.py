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
    # The media agents fall back to real third-party providers whenever a key
    # is configured, and `Settings` reads the developer's own `.env` - so
    # without this the suite quietly billed/called Pexels, ElevenLabs, Runway
    # et al. on every `/daily-content/run`. That was slow (minutes per test,
    # dominated by network retries) and made results depend on whose machine
    # they ran on. Blanking the keys forces every agent down its offline
    # placeholder tier, which is what the assertions actually describe.
    for provider_key in (
        "LEONARDO_API_KEY",
        "PEXELS_API_KEY",
        "ELEVENLABS_API_KEY",
        "RUNWAY_API_KEY",
        "KLING_ACCESS_KEY",
        "KLING_SECRET_KEY",
        "HEYGEN_API_KEY",
        "HEYGEN_AVATAR_ID",
    ):
        monkeypatch.setenv(provider_key, "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DAILY_CONTENT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("BRAND_ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("DAILY_TRENDS_COUNT", "1")
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


@pytest.fixture
def api_client(client):
    """A second client against the same app/DB that carries no session
    cookie - the only way to prove an API key authenticates on its own
    rather than riding on `client`'s cookie. Depends on `client` so the env
    is already patched and `lifespan` has already initialized the DB; it is
    deliberately not entered as a context manager so a second scheduler
    task is not started."""
    return TestClient(app)


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
