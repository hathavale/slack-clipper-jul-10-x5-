"""CSS selectors for the Slack web client DOM.

Slack ships DOM changes without notice, so every hook is a fallback chain and the
whole set can be overridden from a JSON file (--selectors) without touching code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Selectors:
    # roots of the two message panes
    main_pane: list[str] = field(default_factory=lambda: [
        '[data-qa="message_pane"]',
        ".p-workspace__primary_view_body",
        ".p-message_pane",
    ])
    thread_pane: list[str] = field(default_factory=lambda: [
        '[data-qa="threads_flexpane"]',
        ".p-threads_flexpane",
        ".p-flexpane",
    ])
    # one row of the virtualized list; data-item-key holds the message ts
    list_item: str = '[data-qa="virtual-list-item"]'
    item_key_attr: str = "data-item-key"
    # pieces inside a row
    message_text: list[str] = field(default_factory=lambda: [
        '[data-qa="message-text"]',
        ".c-message_kit__blocks",
    ])
    sender: list[str] = field(default_factory=lambda: [
        '[data-qa="message_sender_name"]',
        ".c-message__sender_button",
        ".c-message__sender",
    ])
    edited_label: list[str] = field(default_factory=lambda: [
        '[data-qa="message_edited_label"]',
        ".c-message__edited_label",
    ])
    reply_bar: list[str] = field(default_factory=lambda: [
        '[data-qa="reply_bar_count"]',
        ".c-message__reply_count",
    ])
    close_flexpane: list[str] = field(default_factory=lambda: [
        '[data-qa="close_flexpane"]',
        'button[aria-label="Close"]',
    ])
    channel_header: list[str] = field(default_factory=lambda: [
        '[data-qa="channel_name"]',
        ".p-view_header__channel_title",
    ])

    @classmethod
    def load(cls, path: str | Path | None) -> "Selectors":
        s = cls()
        if path:
            overrides = json.loads(Path(path).read_text())
            valid = {f.name for f in fields(cls)}
            for key, value in overrides.items():
                if key not in valid:
                    raise ValueError(f"unknown selector key in {path}: {key!r}")
                setattr(s, key, value)
        return s
