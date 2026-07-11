"""Web UI tests: API validation plus a real end-to-end capture through CDP.

The e2e test launches a headless Chromium with --remote-debugging-port (the same
transport the app uses against the user's Chrome), points the Flask app at it,
and captures the virtualized-Slack fixture via the HTTP API.
"""

import glob
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from slack_clipper.web import create_app
from slack_clipper.web.chrome import find_chrome

FIXTURE = Path(__file__).parent / "fixture_slack.html"
CDP_PORT = 9777
CDP_URL = f"http://localhost:{CDP_PORT}"
BASE_TS = 1720000000
N = 300


def _chromium() -> str | None:
    exe = find_chrome()
    if exe:
        return exe
    hits = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return hits[-1] if hits else None


@pytest.fixture(scope="module")
def cdp_chrome(tmp_path_factory):
    exe = _chromium()
    if exe is None:
        pytest.skip("no Chrome/Chromium available for the CDP e2e test")
    profile = tmp_path_factory.mktemp("chrome-profile")
    proc = subprocess.Popen(
        [exe, f"--remote-debugging-port={CDP_PORT}", "--headless=new",
         f"--user-data-dir={profile}", "--no-sandbox", "--disable-gpu",
         "--no-first-run", FIXTURE.as_uri()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail("headless Chromium's CDP port never came up")
    yield CDP_URL
    proc.terminate()
    proc.wait(timeout=10)


def _wait_for_job(client, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.5)
    pytest.fail(f"job {job_id} did not finish within {timeout}s (last: {job})")


def test_e2e_capture_via_api(cdp_chrome, tmp_path):
    app = create_app(cdp_url=cdp_chrome, settle_interval=0.08)
    client = app.test_client()

    assert client.get("/api/status").get_json()["chrome_running"] is True

    out_dir = tmp_path / "clips"
    resp = client.post("/api/capture", json={
        "link": FIXTURE.as_uri(), "out_dir": str(out_dir), "threads": False})
    assert resp.status_code == 200, resp.get_json()

    job = _wait_for_job(client, resp.get_json()["job_id"])
    assert job["state"] == "done", job["error"]
    assert job["messages"] == N
    json_path, md_path = job["files"]
    doc = json.loads(Path(json_path).read_text())
    assert doc["message_count"] == N
    assert "message #299 body" in Path(md_path).read_text()


def test_e2e_last_n_days_limits_capture(cdp_chrome, tmp_path):
    app = create_app(cdp_url=cdp_chrome, settle_interval=0.08)
    client = app.test_client()

    # choose N-days so `since` lands midway between fixture messages #249 and
    # #250 — a few seconds of clock drift before the server computes `since`
    # can't cross a 30s midpoint
    days = (time.time() - (BASE_TS + 249 * 60 + 30)) / 86400
    resp = client.post("/api/capture", json={
        "link": FIXTURE.as_uri(), "out_dir": str(tmp_path), "threads": False,
        "last_days": days})
    assert resp.status_code == 200, resp.get_json()

    job = _wait_for_job(client, resp.get_json()["job_id"])
    assert job["state"] == "done", job["error"]
    assert job["messages"] == 50


def test_validation_errors():
    app = create_app(cdp_url="http://localhost:1")  # nothing listening
    client = app.test_client()

    resp = client.post("/api/capture", json={"last_days": "-3"})
    assert resp.status_code == 400
    assert "positive" in resp.get_json()["error"]

    resp = client.post("/api/capture", json={"link": "general"})
    assert resp.status_code == 400
    assert "URL" in resp.get_json()["error"]

    resp = client.post("/api/capture", json={})
    assert resp.status_code == 409  # chrome not running
    assert "not running" in resp.get_json()["error"]


def test_unknown_job_is_404():
    app = create_app(cdp_url="http://localhost:1")
    assert app.test_client().get("/api/jobs/nope").status_code == 404
