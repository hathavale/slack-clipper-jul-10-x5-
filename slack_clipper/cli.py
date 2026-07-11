"""Command-line interface: `python -m slack_clipper capture ...`"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from . import browser as br
from .capture import capture_channel
from .output import write
from .selectors import Selectors


def _epoch(value: str) -> float:
    """Parse YYYY-MM-DD or full ISO datetime into a UTC epoch float."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slack_clipper",
                                     description="Capture coherent transcripts of Slack "
                                                 "conversations from a logged-in Chrome.")
    sub = parser.add_subparsers(dest="command", required=True)

    tabs = sub.add_parser("tabs", help="list tabs of the Chrome instance on the CDP port")
    tabs.add_argument("--cdp-url", default="http://localhost:9222")

    cap = sub.add_parser("capture", help="capture the currently open (or named) channel")
    cap.add_argument("--cdp-url", default="http://localhost:9222",
                     help="Chrome remote-debugging endpoint (default: %(default)s)")
    cap.add_argument("--channel", help="channel to open via the quick switcher; omit to "
                                       "capture whatever conversation is already on screen")
    cap.add_argument("--since", type=_epoch, metavar="DATE",
                     help="stop scrolling once messages older than this date are reached "
                          "(YYYY-MM-DD or ISO datetime, treated as UTC)")
    cap.add_argument("--until", type=_epoch, metavar="DATE",
                     help="drop messages newer than this date from the output")
    cap.add_argument("--max-messages", type=int,
                     help="stop after this many messages (newest kept)")
    cap.add_argument("--no-threads", action="store_true",
                     help="skip opening thread panes for messages with replies")
    cap.add_argument("--out", default="captures", help="output directory (default: %(default)s)")
    cap.add_argument("--selectors", help="JSON file overriding DOM selectors "
                                         "(escape hatch for Slack DOM changes)")
    return parser


def cmd_tabs(args) -> int:
    with sync_playwright() as pw:
        chrome = br.connect(pw, args.cdp_url)
        for page in br.pages(chrome):
            marker = "  <- slack" if br.SLACK_URL_FRAGMENT in page.url else ""
            print(f"{page.title()[:60]:60}  {page.url[:80]}{marker}")
    return 0


def cmd_capture(args) -> int:
    selectors = Selectors.load(args.selectors)
    with sync_playwright() as pw:
        chrome = br.connect(pw, args.cdp_url)
        page = br.find_slack_page(chrome)
        if page is None:
            print("No tab with app.slack.com/client is open in that Chrome instance.\n"
                  "Open your Slack workspace there first (`tabs` lists what I can see).",
                  file=sys.stderr)
            return 2
        page.bring_to_front()

        if args.channel:
            print(f"Opening {args.channel} via the quick switcher…")
            br.open_channel(page, args.channel, selectors.channel_header)

        def progress(total: int, new: int) -> None:
            print(f"\r  {total} messages captured…", end="", flush=True)

        print("Capturing (scrolling back through history)…")
        capture = capture_channel(
            page, selectors,
            channel=args.channel or page.title(),
            since=args.since, until=args.until, max_messages=args.max_messages,
            threads=not args.no_threads, on_progress=progress)
        print()

        json_path, md_path = write(capture, args.out)
        replies = sum(len(v) for v in capture.threads.values())
        print(f"Done: {len(capture.messages)} messages"
              + (f" + {replies} thread replies" if replies else ""))
        print(f"  {json_path}\n  {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {"tabs": cmd_tabs, "capture": cmd_capture}[args.command](args)
    except ConnectionError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
