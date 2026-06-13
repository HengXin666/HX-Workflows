#!/usr/bin/env python3
"""Example task for the TG workflow orchestrator.

Replace this file or add new scripts next to it. Do not hard-code secrets here;
read them from environment variables injected by GitHub Actions Secrets.
"""

from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default=os.environ.get("TG_ACCOUNT_INDEX", "1"))
    args = parser.parse_args()

    session_present = bool(os.environ.get("TG_SESSION_STRING"))
    proxy_present = bool(os.environ.get("TG_PROXY"))

    print("TG example task executed")
    print(f"account={args.account}")
    print(f"TG_SESSION_STRING present={session_present}")
    print(f"TG_PROXY present={proxy_present}")
    print("Replace this script with real sign-in / forwarding logic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
