"""Data model for captured Slack messages."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class Message:
    ts: str                     # Slack timestamp id, e.g. "1720000000.123456" — unique per channel
    author: str | None          # display name; None until group-inheritance fills it in
    text: str
    edited: bool = False
    reply_count: int = 0        # replies visible on the reply bar (0 = no thread)
    thread_ts: str | None = None  # parent ts when this message is a thread reply

    @property
    def ts_float(self) -> float:
        return float(self.ts)

    @property
    def when(self) -> datetime:
        return datetime.fromtimestamp(self.ts_float, tz=timezone.utc)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time_utc"] = self.when.isoformat()
        return d


@dataclass
class Capture:
    channel: str | None
    messages: list[Message] = field(default_factory=list)
    threads: dict[str, list[Message]] = field(default_factory=dict)  # parent ts -> replies

    def merge(self, batch: list[Message]) -> int:
        """Merge a batch into messages, deduping by ts. Returns count of new messages."""
        seen = {m.ts for m in self.messages}
        fresh = [m for m in batch if m.ts not in seen]
        # a later frame may reveal metadata (reply bar hydrates late); refresh known messages
        by_ts = {m.ts: m for m in batch}
        for existing in self.messages:
            update = by_ts.get(existing.ts)
            if update is not None:
                existing.reply_count = max(existing.reply_count, update.reply_count)
                existing.edited = existing.edited or update.edited
                if update.author and not existing.author:
                    existing.author = update.author
        self.messages.extend(fresh)
        self.messages.sort(key=lambda m: m.ts_float)
        return len(fresh)

    def inherit_authors(self) -> None:
        """Slack only renders the sender on the first message of a consecutive group;
        forward-fill authors in ts order."""
        last = None
        for m in self.messages:
            if m.author:
                last = m.author
            elif last:
                m.author = last

    @property
    def oldest_ts(self) -> float | None:
        return self.messages[0].ts_float if self.messages else None

    @property
    def newest_ts(self) -> float | None:
        return self.messages[-1].ts_float if self.messages else None
