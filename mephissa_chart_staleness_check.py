#!/usr/bin/env python3
"""Scheduled staleness check for Mephissa's cached Anghami chart data.

Deliberately does NOT touch Anghami itself -- that's the whole point. A
fully automated refresh job hitting Anghami on a timer is exactly the kind
of repeated bot-like access pattern that triggered their anti-scraping
shuffle defense in the first place (see arabic_charts_cache.json's design
notes in cli.py). This script only reads the local cache file, checks its
age, and writes a flag to the same shared state.json statusline.py already
reads -- a human still has to actually run the refresh.

Meant to run on a schedule (Task Scheduler / cron), not interactively.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STALE_AFTER_DAYS = 7

CACHE_PATH = Path(os.path.expanduser(r"~\.claude\mephissa\arabic_charts_cache.json"))
STATE_PATH = Path(os.path.expanduser(r"~\.claude\mephissa\state.json"))

CHART_KEYS = ("arabic_hits", "lebanese_hits", "top_anghami", "top_weekly")


def check_staleness() -> dict:
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    now = time.time()
    stale_charts = []
    never_refreshed = []
    for key in CHART_KEYS:
        entry = cache.get(key)
        if not entry or not entry.get("entries"):
            never_refreshed.append(key)
            continue
        age_days = (now - entry.get("last_updated", 0)) / 86400
        if age_days >= STALE_AFTER_DAYS:
            stale_charts.append((key, round(age_days, 1)))

    return {
        "stale": bool(stale_charts) or bool(never_refreshed),
        "stale_charts": stale_charts,
        "never_refreshed": never_refreshed,
        "checked_at": now,
    }


def write_state(result: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if STATE_PATH.exists():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["charts_stale"] = result["stale"]
        data["charts_stale_checked_at"] = result["checked_at"]
        STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    result = check_staleness()
    write_state(result)
    if result["stale"]:
        print(f"[chart-staleness] STALE — charts need a manual refresh:")
        for key, age in result["stale_charts"]:
            print(f"  {key}: {age} days old (threshold: {STALE_AFTER_DAYS})")
        for key in result["never_refreshed"]:
            print(f"  {key}: never refreshed from a real pull")
    else:
        print("[chart-staleness] all charts fresh, nothing to do")


if __name__ == "__main__":
    main()
