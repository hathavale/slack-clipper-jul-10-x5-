import glob
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURE = Path(__file__).parent / "fixture_slack.html"


def _chromium_executable() -> str | None:
    """Prefer a preinstalled Chromium if the exact playwright-pinned build is absent."""
    hits = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return hits[-1] if hits else None


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception:
            b = pw.chromium.launch(executable_path=_chromium_executable())
        yield b
        b.close()


@pytest.fixture()
def slack_page(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(FIXTURE.as_uri())
    yield page
    page.close()
