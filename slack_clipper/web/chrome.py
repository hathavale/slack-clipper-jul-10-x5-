"""Find, launch, and health-check the dedicated debug Chrome instance."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

MAC_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
LINUX_NAMES = ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]


def find_chrome() -> str | None:
    override = os.environ.get("SLACK_CLIPPER_CHROME")
    if override:
        return override
    if sys.platform == "darwin":
        for path in MAC_PATHS:
            if os.path.exists(path):
                return path
    for name in LINUX_NAMES:
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def is_running(cdp_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=timeout):
            return True
    except Exception:
        return False


def launch(cdp_url: str, profile_dir: str, url: str | None = None,
           wait: float = 25.0) -> None:
    """Start Chrome with remote debugging on the cdp_url port and wait for the
    port to come up. Uses a dedicated profile dir — recent Chrome refuses CDP
    on the default profile."""
    exe = find_chrome()
    if exe is None:
        raise RuntimeError("could not find a Chrome/Chromium executable — set the "
                           "SLACK_CLIPPER_CHROME environment variable to its full path")
    port = urlparse(cdp_url).port or 9222
    args = [exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={os.path.expanduser(profile_dir)}",
            "--no-first-run", "--no-default-browser-check"]
    if url:
        args.append(url)
    subprocess.Popen(args, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_running(cdp_url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Chrome started but its debugging port ({port}) never came up")
