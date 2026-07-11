"""Run the UI: python -m slack_clipper.web [--port 5001]"""

import argparse

from . import create_app


def main() -> int:
    parser = argparse.ArgumentParser(prog="slack_clipper.web")
    parser.add_argument("--host", default="127.0.0.1")
    # 5001, not 5000: macOS AirPlay Receiver squats on 5000
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--cdp-url", default=None,
                        help="Chrome debugging endpoint (default: http://localhost:9222)")
    args = parser.parse_args()

    app = create_app(cdp_url=args.cdp_url)
    print(f"slack_clipper UI: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
