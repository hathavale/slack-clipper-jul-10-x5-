"""The scroll → settle → extract → dedupe loop over Slack's virtualized message list."""

from __future__ import annotations

import time

from playwright.sync_api import Page, JSHandle

from .models import Capture, Message
from .selectors import Selectors

# Extracts the currently mounted messages from a pane root.
# Non-message rows (day dividers, "X joined", unread markers) either lack a
# ts-shaped data-item-key or lack a message-text block, and are skipped.
_JS_EXTRACT = """
(root, cfg) => {
  const pick = (el, sels) => { for (const s of sels) { const hit = el.querySelector(s); if (hit) return hit; } return null; };
  const out = [];
  for (const item of root.querySelectorAll(cfg.listItem)) {
    const key = item.getAttribute(cfg.itemKeyAttr) || "";
    if (!/^\\d{9,}\\.\\d+$/.test(key)) continue;
    const textEl = pick(item, cfg.messageText);
    if (!textEl) continue;
    const senderEl = pick(item, cfg.sender);
    const replyEl = pick(item, cfg.replyBar);
    let replyCount = 0;
    if (replyEl) { const m = (replyEl.textContent || "").match(/\\d+/); replyCount = m ? Number(m[0]) : 0; }
    out.push({
      ts: key,
      author: senderEl ? senderEl.textContent.trim() : null,
      text: textEl.innerText.replace(/\\s+$/, ""),
      edited: !!pick(item, cfg.editedLabel),
      replyCount,
    });
  }
  return out;
}
"""

# The scrollable ancestor of the list items — found structurally rather than by
# class name, since Slack's scrollbar wrappers are the most churn-prone part of its DOM.
_JS_FIND_SCROLLER = """
(root, cfg) => {
  let el = root.querySelector(cfg.listItem) || root;
  while (el && el !== document.documentElement) {
    const style = getComputedStyle(el);
    if (el.scrollHeight > el.clientHeight + 10 && /(auto|scroll)/.test(style.overflowY)) return el;
    el = el.parentElement;
  }
  return root;
}
"""

# Cheap signature of the pane's current render state, used for the settle wait.
_JS_SIGNATURE = """
(root, cfg) => {
  const keys = [...root.querySelectorAll(cfg.listItem)].map(el => el.getAttribute(cfg.itemKeyAttr)).join("|");
  return keys + "#" + root.scrollHeight;
}
"""


class PaneNotFound(RuntimeError):
    pass


def _js_cfg(sel: Selectors) -> dict:
    return {
        "listItem": sel.list_item,
        "itemKeyAttr": sel.item_key_attr,
        "messageText": sel.message_text,
        "sender": sel.sender,
        "editedLabel": sel.edited_label,
        "replyBar": sel.reply_bar,
    }


def find_pane(page: Page, candidates: list[str], timeout: float = 10.0) -> JSHandle:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for css in candidates:
            handle = page.query_selector(css)
            if handle:
                return handle
        time.sleep(0.25)
    raise PaneNotFound(f"no pane matched any of: {candidates}")


class PaneCapturer:
    """Runs the capture loop inside one pane (main channel view or a thread flexpane)."""

    def __init__(self, page: Page, pane: JSHandle, selectors: Selectors,
                 settle_interval: float = 0.25, settle_timeout: float = 10.0):
        self.page = page
        self.pane = pane
        self.sel = selectors
        self.cfg = _js_cfg(selectors)
        self.settle_interval = settle_interval
        self.settle_timeout = settle_timeout
        self.scroller = pane.evaluate_handle(
            "(root, cfg) => (" + _JS_FIND_SCROLLER + ")(root, cfg)", self.cfg)

    def wait_settled(self) -> None:
        """Wait until the mounted message set and scroll height stop changing —
        the hydration wait for newly loaded history."""
        prev, stable = None, 0
        deadline = time.monotonic() + self.settle_timeout
        while time.monotonic() < deadline:
            sig = self.pane.evaluate("(root, cfg) => (" + _JS_SIGNATURE + ")(root, cfg)", self.cfg)
            if sig == prev:
                stable += 1
                if stable >= 2:
                    return
            else:
                prev, stable = sig, 0
            time.sleep(self.settle_interval)

    def extract(self) -> list[Message]:
        raw = self.pane.evaluate("(root, cfg) => (" + _JS_EXTRACT + ")(root, cfg)", self.cfg)
        return [Message(ts=r["ts"], author=r["author"], text=r["text"],
                        edited=r["edited"], reply_count=r["replyCount"]) for r in raw]

    # -- scrolling ----------------------------------------------------------

    def scroll_to_bottom(self) -> None:
        self.scroller.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        self.wait_settled()

    def scroll_up_one_step(self) -> bool:
        """Scroll up ~85% of a viewport. Returns False when already pinned to the top."""
        return self.scroller.evaluate(
            "el => { const before = el.scrollTop;"
            " el.scrollTop = Math.max(0, before - el.clientHeight * 0.85);"
            " return el.scrollTop !== before; }")

    def at_top(self) -> bool:
        return self.scroller.evaluate("el => el.scrollTop <= 1")

    # -- the loop -----------------------------------------------------------

    def collect(self, capture: Capture, since: float | None = None,
                max_messages: int | None = None, on_progress=None,
                max_stale_rounds: int = 3, should_stop=None) -> Capture:
        """Scroll from the current position toward older history, merging frames
        into `capture` until `since` is crossed, `max_messages` is reached, the
        top of the conversation is hit, or `should_stop()` turns true (abort —
        everything merged so far is kept)."""
        stale_rounds = 0
        while True:
            if should_stop is not None and should_stop():
                break
            self.wait_settled()
            new = capture.merge(self.extract())
            if on_progress:
                on_progress(len(capture.messages), new)

            if max_messages is not None and len(capture.messages) >= max_messages:
                break
            if since is not None and capture.oldest_ts is not None and capture.oldest_ts <= since:
                break

            moved = self.scroll_up_one_step()
            if not moved and self.at_top():
                stale_rounds = stale_rounds + 1 if new == 0 else 0
                if stale_rounds >= max_stale_rounds:
                    break
            else:
                stale_rounds = 0
        return capture

    def scroll_until_visible(self, ts: str, max_rounds: int = 200, should_stop=None) -> bool:
        """Bring the message with data-item-key == ts into the mounted window."""
        for _ in range(max_rounds):
            if should_stop is not None and should_stop():
                return False
            self.wait_settled()
            keys = self.pane.evaluate(
                "(root, cfg) => [...root.querySelectorAll(cfg.listItem)]"
                ".map(el => el.getAttribute(cfg.itemKeyAttr)).filter(k => /^\\d/.test(k || ''))",
                self.cfg)
            if ts in keys:
                return True
            numeric = [float(k) for k in keys]
            if not numeric:
                return False
            target = float(ts)
            if target < min(numeric):
                if not self.scroll_up_one_step() and self.at_top():
                    return False
            else:
                moved = self.scroller.evaluate(
                    "el => { const before = el.scrollTop;"
                    " el.scrollTop = Math.min(el.scrollHeight, before + el.clientHeight * 0.85);"
                    " return el.scrollTop !== before; }")
                if not moved:
                    return False
        return False


def capture_channel(page: Page, selectors: Selectors, channel: str | None = None,
                    since: float | None = None, until: float | None = None,
                    max_messages: int | None = None, threads: bool = True,
                    on_progress=None, log=print, settle_interval: float = 0.25,
                    should_stop=None) -> Capture:
    """Top-level capture: main pane first, then each thread found along the way.

    `should_stop` is a callable polled between scroll rounds; when it turns true
    the capture is aborted cooperatively — everything collected so far is still
    filtered, author-filled, and returned."""
    pane = find_pane(page, selectors.main_pane)
    capturer = PaneCapturer(page, pane, selectors, settle_interval=settle_interval)
    capture = Capture(channel=channel)

    capturer.scroll_to_bottom()
    capturer.collect(capture, since=since, max_messages=max_messages,
                     on_progress=on_progress, should_stop=should_stop)

    if until is not None:
        capture.messages = [m for m in capture.messages if m.ts_float <= until]
    if since is not None:
        capture.messages = [m for m in capture.messages if m.ts_float >= since]
    if max_messages is not None and len(capture.messages) > max_messages:
        capture.messages = capture.messages[-max_messages:]
    capture.inherit_authors()

    if threads:
        parents = [m for m in capture.messages if m.reply_count > 0]
        for parent in parents:
            if should_stop is not None and should_stop():
                break
            replies = _capture_thread(page, capturer, selectors, parent.ts, log=log,
                                      settle_interval=settle_interval, should_stop=should_stop)
            if replies:
                capture.threads[parent.ts] = replies
    return capture


def _capture_thread(page: Page, main: PaneCapturer, selectors: Selectors,
                    parent_ts: str, log=print, settle_interval: float = 0.25,
                    should_stop=None) -> list[Message]:
    if not main.scroll_until_visible(parent_ts, should_stop=should_stop):
        log(f"  ! could not scroll back to message {parent_ts}; skipping its thread")
        return []
    row = main.pane.evaluate_handle(
        "(root, args) => [...root.querySelectorAll(args.cfg.listItem)]"
        ".find(el => el.getAttribute(args.cfg.itemKeyAttr) === args.ts)",
        {"cfg": main.cfg, "ts": parent_ts})
    bar = None
    for css in selectors.reply_bar:
        bar = row.evaluate_handle(f"el => el.querySelector({css!r})")
        if bar.evaluate("el => !!el"):
            break
        bar = None
    if bar is None:
        log(f"  ! no reply bar on {parent_ts}; skipping")
        return []
    bar.as_element().click()

    try:
        pane = find_pane(page, selectors.thread_pane, timeout=10.0)
    except PaneNotFound:
        log(f"  ! thread pane never opened for {parent_ts}")
        return []

    thread_capturer = PaneCapturer(page, pane, selectors, settle_interval=settle_interval)
    thread = Capture(channel=None)
    thread_capturer.scroll_to_bottom()
    thread_capturer.collect(thread, should_stop=should_stop)
    thread.inherit_authors()

    page.keyboard.press("Escape")  # close the flexpane
    replies = [m for m in thread.messages if m.ts != parent_ts]
    for m in replies:
        m.thread_ts = parent_ts
    return replies
