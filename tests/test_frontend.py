"""Guards the single-page frontend served by `api.home()`.

It is ~1900 lines of HTML/CSS/JS living inside a Python string literal, so a
stray quote or a broken `\\'` escape silently breaks the whole app at runtime
with nothing else in the suite noticing. These tests check that it still
parses and that each feature's wiring is actually wired.
"""

import re
import shutil
import subprocess

import pytest

from autonomous_intelligence.api import home
from autonomous_intelligence.model_catalog import CONTENT_MODEL_CHOICES


@pytest.fixture(scope="module")
def page() -> str:
    return home()


@pytest.fixture(scope="module")
def page_js(page: str) -> str:
    blocks = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    assert blocks, "the page should carry an inline <script> block"
    return "\n".join(blocks)


def test_home_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text


def test_embedded_js_parses(page_js: str, tmp_path):
    """`node --check` on the extracted script - the only thing that catches a
    broken string escape before a user hits it in the browser. Skipped where
    node isn't installed rather than silently passing."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available to syntax-check the embedded script")
    js_file = tmp_path / "frontend.js"
    js_file.write_text(page_js, encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(js_file)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr


def test_delimiters_balance(page_js: str):
    """Dependency-free backstop for `test_embedded_js_parses` - catches the
    common truncation/paste error even when node is missing."""
    for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
        assert page_js.count(opener) == page_js.count(closer), f"unbalanced {opener}{closer}"


@pytest.mark.parametrize(
    "element_id",
    [
        # per-request model choice + multi-model comparison
        "ccModelSelect",
        "ccCompareToggle",
        "ccCompareModels",
        "ccCompareBtn",
        "ccCompareResults",
        # publishing calendar
        "calendarView",
        "calStatusFilter",
        "calResults",
        "calPlatformNote",
        # programmatic API keys
        "apiKeysCard",
        "apiKeyName",
        "apiKeyAddBtn",
        "apiKeyList",
        "apiKeyRevealBox",
        # outbound webhooks
        "webhooksCard",
        "webhookUrl",
        "webhookAddBtn",
        "webhookList",
        "webhookSecretBox",
        # pre-existing cards the new ones sit beside
        "billingCard",
        "libResults",
    ],
)
def test_feature_elements_present(page: str, element_id: str):
    assert f'id="{element_id}"' in page


@pytest.mark.parametrize(
    "endpoint",
    [
        "/custom-content/compare",
        "/auth/content-model-choices",
        "/webhooks",
        "/billing/status",
        "/api-keys",
        "/schedule",
        "/schedule/platforms",
    ],
)
def test_frontend_calls_endpoint(page_js: str, endpoint: str):
    assert f"'{endpoint}" in page_js


def test_export_links_cover_every_format(page_js: str):
    assert "libraryExportLinks" in page_js
    assert "'md', 'json', 'csv'" in page_js
    assert "/research/" in page_js and "/export" in page_js


def test_compare_checkbox_class_matches_selector(page_js: str):
    """The checkboxes are built in one place and read in another - a rename in
    only one of them would silently submit an empty model list."""
    assert page_js.count("cc-compare-model") >= 2


def test_model_picker_is_populated_from_the_catalog(page_js: str):
    """The picker is filled at runtime from `/auth/content-model-choices`, so
    assert the wiring rather than the ids - but the catalog must be non-empty
    for the feature to be reachable at all."""
    assert CONTENT_MODEL_CHOICES
    assert "loadContentModelChoices" in page_js
    assert "initCustomContentModels" in page_js


def test_calendar_tab_is_reachable(page: str, page_js: str):
    assert 'data-tab="calendar"' in page
    assert "loadCalendar" in page_js
    assert "calendarView" in page_js


def test_scheduling_is_offered_only_on_approved_content(page_js: str):
    """The button must be gated the same way the API is - offering it on a
    draft would produce a 403 the user cannot act on."""
    assert "scheduleControls" in page_js
    assert "s === 'approved' ? scheduleControls" in page_js


def test_schedule_submits_an_absolute_instant(page_js: str):
    """`datetime-local` has no timezone; the value must be converted to an
    absolute ISO instant so the server never guesses the user's offset."""
    assert "submitSchedule" in page_js
    assert "toISOString()" in page_js
    assert "scheduled_for" in page_js


def test_secrets_are_presented_as_shown_once(page_js: str):
    """Both the webhook signing secret and an API key are unrecoverable
    after creation - the UI has to say so, or users will lose them."""
    assert "cannot be shown again" in page_js
    assert "only time it is shown" in page_js
