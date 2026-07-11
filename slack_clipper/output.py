"""Write a Capture out as JSON (source of truth) and Markdown (readable transcript)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Capture, Message


def _slug(name: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name or "conversation").strip("-").lower() or "conversation"


def write(capture: Capture, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = out / f"{_slug(capture.channel)}-{stamp}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(to_json(capture), encoding="utf-8")
    md_path.write_text(to_markdown(capture), encoding="utf-8")
    return json_path, md_path


def to_json(capture: Capture) -> str:
    doc = {
        "channel": capture.channel,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "message_count": len(capture.messages),
        "thread_reply_count": sum(len(v) for v in capture.threads.values()),
        "messages": [m.to_dict() for m in capture.messages],
        "threads": {ts: [m.to_dict() for m in replies] for ts, replies in capture.threads.items()},
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)


def _md_message(m: Message, indent: str = "") -> str:
    when = m.when.strftime("%H:%M")
    edited = " *(edited)*" if m.edited else ""
    body = m.text.replace("\n", f"\n{indent}  ")
    return f"{indent}- **{m.author or 'unknown'}** [{when}]{edited}: {body}"


def to_markdown(capture: Capture) -> str:
    lines = [f"# {capture.channel or 'Slack conversation'}", ""]
    current_day = None
    for m in capture.messages:
        day = m.when.strftime("%Y-%m-%d (%A)")
        if day != current_day:
            current_day = day
            lines += [f"## {day}", ""]
        lines.append(_md_message(m))
        for reply in capture.threads.get(m.ts, []):
            lines.append(_md_message(reply, indent="    "))
    lines.append("")
    return "\n".join(lines)
