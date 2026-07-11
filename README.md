# slack_clipper

Capture coherent, deduplicated transcripts of Slack conversations from your own
logged-in Chrome — no Slack API token, no extension. A Python app attaches to
Chrome over the DevTools protocol (CDP), drives Slack's virtualized message list
(scroll → wait for hydration → extract → dedupe by each message's stable `ts`
id), walks into thread panes, and writes the result as structured JSON plus a
readable Markdown transcript.

## How it works

Slack's message pane is a virtualized list: only the visible rows (plus a small
buffer) exist in the DOM, and nodes are recycled as you scroll. So the capture
is a loop, not a screenshot:

1. Find the message pane and its scrollable ancestor (located structurally, not
   by brittle class names).
2. Scroll up ~85% of a viewport, then wait until the set of mounted messages and
   the scroll height stop changing (the hydration wait).
3. Extract every mounted message — `ts` id, author, text, edited flag, reply
   count — and merge into the capture, deduping by `ts`.
4. Repeat until the top of the conversation, a `--since` date, or
   `--max-messages` is reached.
5. For each message with a reply bar, scroll back to it, open the thread
   flexpane, and run the same loop inside the pane.

Authors are forward-filled afterward (Slack only renders the sender on the
first message of a consecutive group). Everything is deterministic — no model,
no OCR, no pixel stitching.

## Setup

```bash
pip install -r requirements.txt
```

Launch a dedicated Chrome with remote debugging (recent Chrome refuses CDP on
your default profile, so give it its own profile directory and sign in to Slack
there once — the login persists across restarts):

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 --user-data-dir="$HOME/.slack-clipper-chrome"

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.slack-clipper-chrome"
```

Open your Slack workspace (`https://app.slack.com/client/...`) in that window.

## Web UI

The easiest way to use the app — a local Flask UI that manages the debug Chrome
for you:

```bash
python -m slack_clipper.web
# then open http://127.0.0.1:5001
```

From the page you can:

- **Launch Chrome** — starts the dedicated debug Chrome (own profile, remote
  debugging enabled) if it isn't already running; sign in to Slack there the
  first time.
- **Slack conversation link** — paste the URL from the address bar
  (`https://app.slack.com/client/T…/C…` or a DM link); the app navigates the
  debug Chrome straight to it. Leave empty to capture whatever conversation is
  already on screen.
- **Last N days** — how far back to capture (empty = full history).
- **Target directory** — where the transcript `.json` + `.md` land
  (default `~/SlackClips`).
- A live progress indicator: connecting → navigating → capturing (with a
  running message count) → done, showing the written file paths — or the error
  if something went wrong.

Flags: `--port` (default 5001 — 5000 collides with macOS AirPlay), `--host`,
`--cdp-url`. Environment overrides: `SLACK_CLIPPER_CHROME` (path to the Chrome
binary), `SLACK_CLIPPER_CHROME_PROFILE`, `SLACK_CLIPPER_CDP_URL`,
`SLACK_CLIPPER_SELECTORS`.

## Usage

```bash
# sanity check: what tabs can the app see?
python -m slack_clipper tabs

# capture the conversation currently on screen
python -m slack_clipper capture

# open a channel via the quick switcher, capture back to July 1, threads included
python -m slack_clipper capture --channel general --since 2026-07-01

# cap the volume, skip threads
python -m slack_clipper capture --max-messages 500 --no-threads
```

Output lands in `captures/` as a `.json` file (source of truth: every message
with `ts`, author, UTC time, text, edited flag, thread structure) and a `.md`
transcript grouped by day with thread replies nested under their parent.

Options: `--cdp-url` (default `http://localhost:9222`), `--since` / `--until`
(YYYY-MM-DD, UTC), `--max-messages`, `--no-threads`, `--out DIR`, and
`--selectors FILE` — a JSON file overriding any DOM selector (see
`slack_clipper/selectors.py` for the keys), the escape hatch for when Slack
ships a DOM change.

## Tests

The capture loop is exercised end-to-end against a local fixture
(`tests/fixture_slack.html`) that reproduces Slack's tricky behaviors: node
recycling, delayed hydration after scroll, sender-only-on-first-of-group, and
an asynchronously opening thread flexpane.

```bash
pip install pytest
python -m pytest tests/
```

## Caveats

- Selectors track today's Slack web DOM; when Slack changes markup, fix it with
  a `--selectors` JSON override (no code change needed).
- Scraping the web client sits outside Slack's ToS, and capturing corporate
  workspace content may violate employer data-handling policy — intended for
  personal workspaces.
- `--channel` uses the quick switcher and verifies the channel header after
  navigating; if it picks the wrong result it aborts rather than capturing the
  wrong channel.

## Roadmap (v2)

A local-LLM layer on top of the JSON output — Ollama-based transcript cleanup,
thread/topic reconstruction, summaries, and embedding into a vector store for
search. Deliberately deferred: it consumes the JSON this tool produces and
needs no changes to the capture engine.
