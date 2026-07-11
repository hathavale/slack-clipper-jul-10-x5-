"""Attach to the user's running Chrome over CDP and locate the Slack tab."""

from __future__ import annotations

import time

from playwright.sync_api import Browser, Page, Playwright

SLACK_URL_FRAGMENT = "app.slack.com/client"

LAUNCH_HELP = """\
Could not connect to Chrome's debugging port.

Launch a dedicated Chrome instance with remote debugging enabled (recent Chrome
refuses CDP on your default profile, so give it its own user-data-dir and sign
in to Slack there once):

  macOS:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
        --remote-debugging-port=9222 --user-data-dir="$HOME/.slack-clipper-chrome"
  Linux:
    google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.slack-clipper-chrome"

Then open your Slack workspace in that window and re-run this command.
"""


def connect(pw: Playwright, cdp_url: str) -> Browser:
    try:
        return pw.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        raise ConnectionError(f"{LAUNCH_HELP}\n(underlying error: {exc})") from exc


def pages(browser: Browser) -> list[Page]:
    return [p for ctx in browser.contexts for p in ctx.pages]


def find_slack_page(browser: Browser) -> Page | None:
    for page in pages(browser):
        if SLACK_URL_FRAGMENT in page.url:
            return page
    return None


def open_channel(page: Page, channel: str, header_selectors: list[str],
                 timeout: float = 15.0) -> None:
    """Navigate to a channel via Slack's quick switcher (Cmd/Ctrl+K)."""
    is_mac = page.evaluate("() => navigator.platform.toLowerCase().includes('mac')")
    page.keyboard.press("Meta+k" if is_mac else "Control+k")
    page.wait_for_timeout(600)
    page.keyboard.type(channel.lstrip("#"), delay=40)
    page.wait_for_timeout(900)  # let the switcher rank results
    page.keyboard.press("Enter")

    wanted = channel.lstrip("#").lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for css in header_selectors:
            el = page.query_selector(css)
            if el and wanted in (el.inner_text() or "").lower():
                return
        page.wait_for_timeout(300)
    raise TimeoutError(
        f"opened the quick switcher for {channel!r} but never saw it in the channel "
        f"header — it may have picked a different result; open the channel manually "
        f"and re-run without --channel")
