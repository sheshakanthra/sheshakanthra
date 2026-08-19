#!/usr/bin/env python3
"""
fetch_contributions.py
----------------------
Pulls the public contribution calendar for a GitHub user and writes
`data/contributions.json`.

No authentication. No GitHub token. No third-party stats service.
It reads the same public HTML fragment the profile page itself renders:

    https://github.com/users/<username>/contributions

Markup contract (verified against the live page)
------------------------------------------------
Each day is a `<td class="ContributionCalendar-day">` carrying
`data-date`, `data-level` (0-4) and an `id` of the form
`contribution-day-component-<weekday>-<index>`.

The exact count is *not* on the cell — it lives in a sibling custom element:

    <tool-tip for="contribution-day-component-0-40">3 contributions on ...</tool-tip>

So counts are joined onto cells by that id. If GitHub ever drops the
tool-tips, the script falls back to a level->count estimate and still works.

Usage
    python scripts/fetch_contributions.py --user sheshakanthra
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://github.com/users/{user}/contributions"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "X-Requested-With": "XMLHttpRequest",
}

# Used only if tool-tips are unavailable.
LEVEL_FALLBACK = {0: 0, 1: 1, 2: 3, 3: 6, 4: 10}

WEEKS = 53


def fetch_html(user: str, timeout: int = 25, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(URL.format(user=user), headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as exc:  # network flake / rate limit
            last = exc
            print(f"  [fetch] attempt {attempt}/{retries} failed: {exc}")
    raise SystemExit(f"ERROR: could not fetch contributions for '{user}': {last}")


def parse_counts_from_tooltips(soup: BeautifulSoup) -> dict[str, int]:
    """Map cell-id -> exact contribution count, read from <tool-tip> text."""
    counts: dict[str, int] = {}
    for tip in soup.find_all("tool-tip"):
        target = tip.get("for")
        if not target:
            continue
        text = tip.get_text(" ", strip=True)
        if text.lower().startswith("no contribution"):
            counts[target] = 0
            continue
        m = re.match(r"([\d,]+)\s+contribution", text)
        if m:
            counts[target] = int(m.group(1).replace(",", ""))
    return counts


def parse_days(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tips = parse_counts_from_tooltips(soup)

    days: list[dict] = []
    for td in soup.select("td.ContributionCalendar-day"):
        d = td.get("data-date")
        if not d:
            continue
        level = int(td.get("data-level") or 0)
        cid = td.get("id") or ""
        count = tips.get(cid)
        if count is None:
            count = LEVEL_FALLBACK.get(level, 0)
        days.append({"date": d, "level": level, "count": count})

    if not days:
        raise SystemExit(
            "ERROR: no contribution cells found. GitHub may have changed its "
            "markup, or the username is wrong / has no public activity."
        )

    days.sort(key=lambda x: x["date"])
    if not tips:
        print("  [parse] WARNING: no tool-tips found — counts are level estimates")
    return days


def trim_to_window(days: list[dict], weeks: int = WEEKS) -> list[dict]:
    """
    Keep the last `weeks` full weeks, aligned so the grid starts on a Sunday
    (GitHub's calendar convention).
    """
    last = datetime.strptime(days[-1]["date"], "%Y-%m-%d").date()
    # Walk back to the Sunday that starts the window.
    end_of_week = last + timedelta(days=(6 - ((last.weekday() + 1) % 7)))
    start = end_of_week - timedelta(days=weeks * 7 - 1)
    return [
        d
        for d in days
        if start <= datetime.strptime(d["date"], "%Y-%m-%d").date() <= last
    ]


def derive_stats(days: list[dict]) -> dict:
    total = sum(d["count"] for d in days)

    best = max(days, key=lambda d: d["count"])
    best_day = {"date": best["date"], "count": best["count"]} if best["count"] else None

    # Longest streak across the window.
    longest = run = 0
    longest_end = None
    for d in days:
        if d["count"] > 0:
            run += 1
            if run > longest:
                longest, longest_end = run, d["date"]
        else:
            run = 0

    # Current streak, counted backwards from the most recent day. Today is
    # allowed to be empty without breaking the streak (the day isn't over).
    current = 0
    today = date.today().isoformat()
    for d in reversed(days):
        if d["count"] > 0:
            current += 1
        elif d["date"] == today:
            continue
        else:
            break

    monthly: dict[str, int] = defaultdict(int)
    for d in days:
        monthly[d["date"][:7]] += d["count"]

    active = sum(1 for d in days if d["count"] > 0)

    return {
        "total": total,
        "current_streak": current,
        "longest_streak": longest,
        "longest_streak_end": longest_end,
        "best_day": best_day,
        "active_days": active,
        "days_tracked": len(days),
        "average_per_day": round(total / max(len(days), 1), 2),
        "monthly": dict(sorted(monthly.items())),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch public GitHub contributions.")
    ap.add_argument("--user", default="sheshakanthra")
    ap.add_argument("--output", default="data/contributions.json")
    ap.add_argument("--weeks", type=int, default=WEEKS)
    a = ap.parse_args()

    print(f"[fetch] user: {a.user}")
    html = fetch_html(a.user)
    days = parse_days(html)
    days = trim_to_window(days, a.weeks)

    stats = derive_stats(days)
    payload = {
        "user": a.user,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "range": {"from": days[0]["date"], "to": days[-1]["date"], "weeks": a.weeks},
        "stats": stats,
        "days": days,
    }

    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"  [range] {days[0]['date']} -> {days[-1]['date']}  ({len(days)} days)")
    print(f"  [total] {stats['total']} contributions")
    print(
        f"  [streak] current {stats['current_streak']}d  "
        f"longest {stats['longest_streak']}d"
    )
    if stats["best_day"]:
        print(
            f"  [best]  {stats['best_day']['count']} on {stats['best_day']['date']}"
        )
    print(f"[fetch] wrote {out}")


if __name__ == "__main__":
    sys.exit(main())
