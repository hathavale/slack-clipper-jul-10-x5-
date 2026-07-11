"""Flask UI wrapper around slack_clipper.

Serves a single-page UI to launch the debug Chrome, start a capture of a
Slack conversation (by pasted link), and watch progress until the transcript
files are written.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ..selectors import Selectors
from . import chrome
from .jobs import JobManager

DEFAULT_CDP_URL = "http://localhost:9222"
DEFAULT_PROFILE = "~/.slack-clipper-chrome"


def create_app(cdp_url: str | None = None, profile_dir: str | None = None,
               settle_interval: float = 0.25) -> Flask:
    app = Flask(__name__)
    cdp = cdp_url or os.environ.get("SLACK_CLIPPER_CDP_URL", DEFAULT_CDP_URL)
    profile = os.path.expanduser(
        profile_dir or os.environ.get("SLACK_CLIPPER_CHROME_PROFILE", DEFAULT_PROFILE))
    selectors = Selectors.load(os.environ.get("SLACK_CLIPPER_SELECTORS") or None)
    jobs = JobManager()

    @app.get("/")
    def index():
        return render_template("index.html", cdp_url=cdp)

    @app.get("/api/status")
    def status():
        return jsonify(chrome_running=chrome.is_running(cdp), cdp_url=cdp)

    @app.post("/api/launch-chrome")
    def launch_chrome():
        data = request.get_json(silent=True) or {}
        if chrome.is_running(cdp):
            return jsonify(chrome_running=True, note="already running")
        try:
            chrome.launch(cdp, profile, url=data.get("link") or "https://app.slack.com/client")
        except Exception as exc:
            return jsonify(chrome_running=False, error=str(exc)), 500
        return jsonify(chrome_running=True,
                       note="Chrome launched — sign in to Slack there if you haven't yet")

    @app.post("/api/capture")
    def start_capture():
        data = request.get_json(silent=True) or {}

        link = (data.get("link") or "").strip() or None
        if link and not link.startswith(("http://", "https://", "file://")):
            return jsonify(error="the Slack link must be a URL "
                                 "(e.g. https://app.slack.com/client/T…/C…)"), 400

        since = None
        days_raw = data.get("last_days")
        if days_raw not in (None, ""):
            try:
                days = float(days_raw)
                if days <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify(error="'last N days' must be a positive number"), 400
            since = time.time() - days * 86400

        client_save = bool(data.get("client_save"))
        if client_save:
            # browser-picked folder (File System Access API): its real path is
            # never exposed to the page, so the server writes to a temp dir and
            # the browser copies the finished files into the picked folder
            out_dir = tempfile.mkdtemp(prefix="slack_clipper_")
        else:
            out_dir = os.path.expanduser((data.get("out_dir") or "captures").strip() or "captures")

        if not chrome.is_running(cdp):
            return jsonify(error="the debug Chrome is not running — launch it first"), 409

        request_summary = {
            "link": link or "conversation on screen",
            "days": days_raw if days_raw not in (None, "") else "all",
            "target": "browser folder" if client_save else out_dir,
            "threads": bool(data.get("threads", True)),
            "client_save": client_save,
        }
        try:
            job = jobs.start(request=request_summary, cdp_url=cdp, selectors=selectors,
                             link=link, since=since, out_dir=out_dir,
                             threads=bool(data.get("threads", True)),
                             settle_interval=settle_interval)
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(job_id=job["id"])

    @app.get("/api/jobs")
    def all_jobs():
        return jsonify(jobs=jobs.list())

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id: str):
        try:
            return jsonify(jobs.cancel(job_id))
        except KeyError:
            return jsonify(error="unknown job"), 404
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409

    @app.post("/api/abort")
    def abort_running():
        try:
            return jsonify(aborted_job=jobs.abort())
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            return jsonify(error="unknown job"), 404
        return jsonify(job)

    @app.get("/api/jobs/<job_id>/files")
    def job_files(job_id: str):
        """Transcript contents for browser-side saving into a picked folder."""
        job = jobs.get(job_id)
        if job is None:
            return jsonify(error="unknown job"), 404
        if job["state"] != "done":
            return jsonify(error="job is not finished"), 409
        files = [{"name": Path(p).name, "content": Path(p).read_text(encoding="utf-8")}
                 for p in job["files"]]
        return jsonify(files=files)

    return app
