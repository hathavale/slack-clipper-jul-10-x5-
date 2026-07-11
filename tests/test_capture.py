"""End-to-end tests of the capture loop against the virtualized-Slack fixture."""

import json

from slack_clipper.capture import capture_channel
from slack_clipper.models import Capture
from slack_clipper.output import to_json, to_markdown
from slack_clipper.selectors import Selectors

SEL = Selectors()
FAST = {"settle_interval": 0.08}
BASE_TS = 1720000000
N = 300


def ts_of(i: int) -> str:
    return f"{BASE_TS + i * 60}.000000"


def test_full_capture_dedupes_across_recycled_frames(slack_page):
    cap = capture_channel(slack_page, SEL, **FAST, channel="#fixture-channel", threads=False)
    assert len(cap.messages) == N
    keys = [m.ts for m in cap.messages]
    assert len(set(keys)) == N, "duplicate ts survived dedupe"
    assert keys == sorted(keys, key=float), "messages not in chronological order"
    assert cap.messages[0].text == "message #0 body"
    assert cap.messages[-1].text == f"message #{N - 1} body"


def test_authors_forward_filled_across_groups(slack_page):
    cap = capture_channel(slack_page, SEL, **FAST, threads=False)
    assert all(m.author for m in cap.messages), "grouped messages missing inherited author"
    # fixture groups 3 consecutive messages per author
    assert [m.author for m in cap.messages[:6]] == ["alice"] * 3 + ["bob"] * 3


def test_since_stops_scrolling_early(slack_page):
    since = float(ts_of(250))
    cap = capture_channel(slack_page, SEL, **FAST, threads=False, since=since)
    assert len(cap.messages) == 50
    assert all(m.ts_float >= since for m in cap.messages)


def test_max_messages_keeps_newest(slack_page):
    cap = capture_channel(slack_page, SEL, **FAST, threads=False, max_messages=40)
    assert len(cap.messages) == 40
    assert cap.messages[-1].ts == ts_of(N - 1)


def test_edited_flag_extracted(slack_page):
    cap = capture_channel(slack_page, SEL, **FAST, threads=False)
    edited = {m.ts for m in cap.messages if m.edited}
    expected = {ts_of(i) for i in range(1, N) if i % 37 == 0}
    assert edited == expected


def test_threads_captured_and_attached(slack_page):
    cap = capture_channel(slack_page, SEL, **FAST, threads=True, log=lambda *_: None)
    assert set(cap.threads) == {ts_of(50), ts_of(150)}
    for parent_ts, replies in cap.threads.items():
        assert len(replies) == 3
        assert all(r.thread_ts == parent_ts for r in replies)
        assert all(r.ts != parent_ts for r in replies), "parent leaked into replies"
    assert next(m for m in cap.messages if m.ts == ts_of(50)).reply_count == 3


def test_output_writers(slack_page):
    cap = capture_channel(slack_page, SEL, **FAST, channel="#fixture-channel", threads=True,
                          log=lambda *_: None)
    doc = json.loads(to_json(cap))
    assert doc["message_count"] == N
    assert doc["thread_reply_count"] == 6
    assert doc["messages"][0]["time_utc"].startswith("2024-07-03")

    md = to_markdown(cap)
    assert md.splitlines()[0] == "# #fixture-channel"
    assert "message #299 body" in md
    assert "reply 1 to #50" in md
    assert "*(edited)*" in md


def test_merge_updates_late_hydrating_metadata():
    from slack_clipper.models import Message
    cap = Capture(channel=None)
    cap.merge([Message(ts="1.000000", author=None, text="hi", reply_count=0)])
    cap.merge([Message(ts="1.000000", author="alice", text="hi", reply_count=4, edited=True)])
    assert len(cap.messages) == 1
    m = cap.messages[0]
    assert (m.author, m.reply_count, m.edited) == ("alice", 4, True)
