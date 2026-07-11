"""Background capture jobs with pollable progress state."""

from __future__ import annotations

import threading
import uuid

from playwright.sync_api import sync_playwright

from .. import browser as br
from ..capture import PaneNotFound, capture_channel, find_pane
from ..output import write
from ..selectors import Selectors

TERMINAL_STATES = ("done", "error")


class JobManager:
    """One capture at a time (they all drive the same Chrome window)."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._active: str | None = None

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def start(self, *, cdp_url: str, selectors: Selectors, link: str | None,
              since: float | None, out_dir: str, threads: bool,
              settle_interval: float = 0.25) -> dict:
        with self._lock:
            if self._active and self._jobs[self._active]["state"] not in TERMINAL_STATES:
                raise RuntimeError("a capture is already running — wait for it to finish")
            job = {
                "id": uuid.uuid4().hex[:12],
                "state": "queued",       # queued → connecting → navigating →
                "detail": "",            # waiting_for_messages → capturing → writing → done
                "messages": 0,
                "thread_replies": 0,
                "files": [],
                "error": None,
            }
            self._jobs[job["id"]] = job
            self._active = job["id"]

        worker = threading.Thread(
            target=self._run, daemon=True,
            args=(job, cdp_url, selectors, link, since, out_dir, threads, settle_interval))
        worker.start()
        return job

    def _run(self, job: dict, cdp_url: str, selectors: Selectors, link: str | None,
             since: float | None, out_dir: str, threads: bool, settle_interval: float):
        try:
            job["state"] = "connecting"
            with sync_playwright() as pw:
                chrome = br.connect(pw, cdp_url)
                page = br.find_slack_page(chrome)

                if link:
                    job["state"] = "navigating"
                    job["detail"] = link
                    if page is None:
                        ctx = chrome.contexts[0] if chrome.contexts else chrome.new_context()
                        page = ctx.new_page()
                    page.goto(link, wait_until="domcontentloaded")
                elif page is None:
                    raise RuntimeError(
                        "no Slack tab is open in the debug Chrome — paste a conversation "
                        "link, or open the conversation there and retry")

                page.bring_to_front()
                job["state"] = "waiting_for_messages"
                job["detail"] = "waiting for the message pane to render"
                try:
                    find_pane(page, selectors.main_pane, timeout=45.0)
                except PaneNotFound:
                    raise RuntimeError(
                        "Slack's message pane never appeared — check that you are signed "
                        "in in the debug Chrome window and the link points to a "
                        "conversation") from None

                job["state"] = "capturing"
                job["detail"] = "scrolling back through history"

                def progress(total: int, new: int) -> None:
                    job["messages"] = total

                capture = capture_channel(
                    page, selectors, channel=page.title(), since=since, threads=threads,
                    on_progress=progress, settle_interval=settle_interval,
                    log=lambda msg: job.__setitem__("detail", str(msg)))

                job["state"] = "writing"
                json_path, md_path = write(capture, out_dir)
                job["messages"] = len(capture.messages)
                job["thread_replies"] = sum(len(v) for v in capture.threads.values())
                job["files"] = [str(json_path), str(md_path)]
                job["detail"] = ""
                job["state"] = "done"
        except Exception as exc:  # surfaced to the UI verbatim
            job["error"] = str(exc)
            job["state"] = "error"
