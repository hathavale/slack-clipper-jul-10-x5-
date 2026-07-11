"""Web UI tests: API validation plus a real end-to-end capture through CDP.

The e2e test launches a headless Chromium with --remote-debugging-port (the same
transport the app uses against the user's Chrome), points the Flask app at it,
and captures the virtualized-Slack fixture via the HTTP API.
"""

import glob
import json
import subprocess
import tempfile
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

    job_id = resp.get_json()["job_id"]
    job = _wait_for_job(client, job_id)
    assert job["state"] == "done", job["error"]
    assert job["messages"] == N
    json_path, md_path = job["files"]
    doc = json.loads(Path(json_path).read_text())
    assert doc["message_count"] == N
    assert "message #299 body" in Path(md_path).read_text()

    # the files endpoint serves content for browser-side saving (picked folder)
    files = client.get(f"/api/jobs/{job_id}/files").get_json()["files"]
    assert [f["name"] for f in files] == [Path(json_path).name, Path(md_path).name]
    assert json.loads(files[0]["content"])["message_count"] == N


def test_e2e_client_save_writes_to_temp_dir(cdp_chrome):
    """client_save mode: no out_dir needed; server stages files in a temp dir
    and hands their content to the browser via the files endpoint."""
    app = create_app(cdp_url=cdp_chrome, settle_interval=0.08)
    client = app.test_client()

    resp = client.post("/api/capture", json={
        "link": FIXTURE.as_uri(), "client_save": True, "threads": False,
        "folder_name": "MyClips"})
    assert resp.status_code == 200, resp.get_json()
    job_id = resp.get_json()["job_id"]

    # the queue table shows the picked folder's name, not a generic label
    assert client.get(f"/api/jobs/{job_id}").get_json()["request"]["target"] == "MyClips 📁"

    # not finished yet -> files endpoint refuses
    assert client.get(f"/api/jobs/{job_id}/files").status_code == 409

    job = _wait_for_job(client, job_id)
    assert job["state"] == "done", job["error"]
    assert all(p.startswith(tempfile.gettempdir()) for p in job["files"])
    files = client.get(f"/api/jobs/{job_id}/files").get_json()["files"]
    assert len(files) == 2 and "message #299 body" in files[1]["content"]


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
    assert app.test_client().get("/api/jobs/nope/files").status_code == 404
    assert app.test_client().post("/api/jobs/nope/cancel").status_code == 404


def test_abort_without_running_job_is_409():
    app = create_app(cdp_url="http://localhost:1")
    resp = app.test_client().post("/api/abort")
    assert resp.status_code == 409
    assert "no capture" in resp.get_json()["error"]


def test_queue_limit_and_cancel_rules(monkeypatch):
    """Queue mechanics without a browser: 5-job cap, cancel only while queued,
    cancelled jobs are skipped by the worker."""
    import threading

    from slack_clipper.web.jobs import JobManager

    release = threading.Event()

    def fake_execute(self, job, **params):
        job["state"] = "capturing"
        release.wait(timeout=30)
        job["state"] = "done"

    monkeypatch.setattr(JobManager, "_execute", fake_execute)
    jm = JobManager()

    def enqueue():
        return jm.start(request={}, cdp_url="x", selectors=None, link=None,
                        since=None, out_dir="out", threads=False)

    jobs = [enqueue() for _ in range(5)]
    with pytest.raises(RuntimeError, match="full"):
        enqueue()

    deadline = time.monotonic() + 5
    while jobs[0]["state"] != "capturing" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert jobs[0]["state"] == "capturing"

    with pytest.raises(RuntimeError, match="only queued"):
        jm.cancel(jobs[0]["id"])          # running -> not cancellable
    jm.cancel(jobs[2]["id"])              # queued -> cancellable
    assert jobs[2]["state"] == "cancelled"
    assert jm.abort() == jobs[0]["id"]    # abort targets the running job

    release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(j["state"] in ("done", "cancelled") for j in jobs):
            break
        time.sleep(0.05)
    assert jobs[2]["state"] == "cancelled"
    assert [j["state"] for i, j in enumerate(jobs) if i != 2] == ["done"] * 4

    with pytest.raises(RuntimeError, match="no capture"):
        jm.abort()                        # nothing running any more


def test_e2e_abort_consolidates_partial_capture(cdp_chrome, tmp_path):
    """Abort mid-scroll: the job finishes as done+aborted with a partial
    transcript written out, and a queued job behind it is cancellable."""
    app = create_app(cdp_url=cdp_chrome, settle_interval=0.35)  # slow scroll rounds
    client = app.test_client()

    first = client.post("/api/capture", json={
        "link": FIXTURE.as_uri(), "out_dir": str(tmp_path), "threads": False})
    job_id = first.get_json()["job_id"]
    queued = client.post("/api/capture", json={
        "link": FIXTURE.as_uri(), "out_dir": str(tmp_path), "threads": False})
    queued_id = queued.get_json()["job_id"]

    assert client.post(f"/api/jobs/{queued_id}/cancel").status_code == 200
    assert client.get(f"/api/jobs/{queued_id}").get_json()["state"] == "cancelled"

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["state"] == "capturing" and job["messages"] >= 30:
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"job never got deep into capturing (last: {job})")

    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409  # running
    assert client.post("/api/abort").status_code == 200

    job = _wait_for_job(client, job_id)
    assert job["state"] == "done" and job["aborted"] is True
    assert 0 < job["messages"] < N, "abort should have stopped before full history"
    doc = json.loads(Path(job["files"][0]).read_text())
    assert doc["message_count"] == job["messages"]  # partial capture consolidated
