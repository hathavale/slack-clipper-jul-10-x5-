"""Capture job queue: up to 5 unfinished jobs, executed one at a time
(they all drive the same Chrome window), with cancel for queued jobs and a
cooperative abort for the running one."""

from __future__ import annotations

import threading
import uuid
from collections import deque

from playwright.sync_api import sync_playwright

from .. import browser as br
from ..capture import PaneNotFound, capture_channel, find_pane
from ..output import write
from ..selectors import Selectors

TERMINAL_STATES = ("done", "error", "cancelled")
MAX_UNFINISHED = 5


class JobManager:
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self._pending: deque = deque()          # (job, params) awaiting the worker
        self._cv = threading.Condition()
        self._worker: threading.Thread | None = None
        self._active_id: str | None = None
        self._abort = threading.Event()

    # -- public api ----------------------------------------------------------

    def list(self) -> list[dict]:
        return [self._jobs[jid] for jid in self._order]

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def start(self, *, request: dict, cdp_url: str, selectors: Selectors,
              link: str | None, since: float | None, out_dir: str, threads: bool,
              settle_interval: float = 0.25) -> dict:
        """Enqueue a capture. `request` is the display summary shown in the UI table."""
        with self._cv:
            unfinished = [j for j in self._jobs.values() if j["state"] not in TERMINAL_STATES]
            if len(unfinished) >= MAX_UNFINISHED:
                raise RuntimeError(f"the queue is full — up to {MAX_UNFINISHED} jobs may be "
                                   "queued or running; cancel one or wait")
            job = {
                "id": uuid.uuid4().hex[:12],
                "state": "queued",       # queued → connecting → navigating →
                "detail": "",            # waiting_for_messages → capturing → writing → done
                "messages": 0,           # (or: cancelled / error; aborted=True on partial done)
                "thread_replies": 0,
                "files": [],
                "error": None,
                "aborted": False,
                "request": request,
            }
            self._jobs[job["id"]] = job
            self._order.append(job["id"])
            params = dict(cdp_url=cdp_url, selectors=selectors, link=link, since=since,
                          out_dir=out_dir, threads=threads, settle_interval=settle_interval)
            self._pending.append((job, params))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._loop, daemon=True)
                self._worker.start()
            self._cv.notify()
        return job

    def cancel(self, job_id: str) -> dict:
        """Cancel a queued job. Running jobs must be aborted instead."""
        with self._cv:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "queued":
                raise RuntimeError(f"job is {job['state']} — only queued jobs can be "
                                   "cancelled (use abort for the running one)")
            job["state"] = "cancelled"
            return job

    def abort(self) -> str:
        """Ask the running job to stop scrolling; whatever it has captured so far
        is consolidated and written out as a partial transcript."""
        with self._cv:
            if self._active_id is None:
                raise RuntimeError("no capture is currently running")
            self._abort.set()
            return self._active_id

    # -- worker ---------------------------------------------------------------

    def _loop(self):
        while True:
            with self._cv:
                while not self._pending:
                    self._cv.wait()
                job, params = self._pending.popleft()
                if job["state"] == "cancelled":
                    continue
                self._active_id = job["id"]
                self._abort.clear()
            try:
                self._execute(job, **params)
            finally:
                with self._cv:
                    self._active_id = None

    def _execute(self, job: dict, cdp_url: str, selectors: Selectors, link: str | None,
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
                    should_stop=self._abort.is_set,
                    log=lambda msg: job.__setitem__("detail", str(msg)))

                job["state"] = "writing"
                json_path, md_path = write(capture, out_dir)
                job["messages"] = len(capture.messages)
                job["thread_replies"] = sum(len(v) for v in capture.threads.values())
                job["files"] = [str(json_path), str(md_path)]
                if self._abort.is_set():
                    job["aborted"] = True
                    job["detail"] = "aborted — partial transcript saved"
                else:
                    job["detail"] = ""
                job["state"] = "done"
        except Exception as exc:  # surfaced to the UI verbatim
            job["error"] = str(exc)
            job["state"] = "error"
