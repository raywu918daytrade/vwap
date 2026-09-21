"""Dispatch the offline-signal workflow from the always-on Oracle host."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TW = timezone(timedelta(hours=8))
TOKEN = (
    os.environ.get("GITHUB_ACTIONS_TRIGGER_TOKEN", "").strip()
    or os.environ.get("GITHUB_DAY_TRADE", "").strip()
)
REPOSITORY = os.environ.get("GITHUB_ACTIONS_REPOSITORY", "raywu918daytrade/vwap").strip()
WORKFLOW = os.environ.get("GITHUB_ACTIONS_WORKFLOW", "build-pattern-scan.yml").strip()
REF = os.environ.get("GITHUB_ACTIONS_REF", "main").strip()
HOUR = int(os.environ.get("GITHUB_ACTIONS_TRIGGER_HOUR", "14"))
MINUTE = int(os.environ.get("GITHUB_ACTIONS_TRIGGER_MIN", "0"))
RETRY_MINUTES = max(1, int(os.environ.get("GITHUB_ACTIONS_TRIGGER_RETRY_MINUTES", "10")))


def dispatch(date: str) -> None:
    url = f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches"
    payload = json.dumps({"ref": REF, "inputs": {"date": date, "force": False}}).encode()
    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "vwap-oracle-scheduler",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 204:
            raise RuntimeError(f"unexpected GitHub status {response.status}")


def main() -> None:
    if not TOKEN:
        print(
            "Oracle GHA trigger disabled: configure GITHUB_ACTIONS_TRIGGER_TOKEN or GITHUB_DAY_TRADE",
            flush=True,
        )
        while True:
            time.sleep(3600)

    last_success = ""
    next_attempt: datetime | None = None
    print(f"Oracle GHA trigger ready: weekdays {HOUR:02d}:{MINUTE:02d} Asia/Taipei", flush=True)
    while True:
        now = datetime.now(TW)
        date = now.strftime("%Y-%m-%d")
        scheduled = now.replace(hour=HOUR, minute=MINUTE, second=0, microsecond=0)
        due = now.weekday() < 5 and now >= scheduled and date != last_success
        if due and (next_attempt is None or now >= next_attempt):
            try:
                dispatch(date)
                last_success = date
                next_attempt = None
                print(f"Dispatched {WORKFLOW} for {date}", flush=True)
            except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
                next_attempt = now + timedelta(minutes=RETRY_MINUTES)
                print(f"Dispatch failed for {date}: {exc}; retry at {next_attempt:%H:%M}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
