#!/usr/bin/env python3
"""
ICS Calendar Aggregator
=======================

Reads a list of ICS calendar URLs from `calendars.yaml`, downloads each one,
and merges all events into a single combined ICS file written to
`docs/combined.ics`. That file can be served by GitHub Pages and subscribed
to from Outlook, Google Calendar, Apple Calendar, etc.

Usage:
    python aggregate.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yaml
from icalendar import Calendar, vDate


CONFIG_PATH = Path("calendars.yaml")
OUTPUT_PATH = Path("docs/combined.ics")
TIMEOUT_SECONDS = 30
USER_AGENT = "ICS-Aggregator/1.0 (+https://github.com/)"


def fetch_ics(url_or_path: str) -> bytes:
    """Load ICS bytes from a URL or a local file path."""
    if url_or_path.startswith("file:"):
        return Path(url_or_path[len("file:"):]).read_bytes()
    if url_or_path.startswith(("./", "/")) or "://" not in url_or_path:
        return Path(url_or_path).read_bytes()
    if url_or_path.startswith("webcal://"):
        url_or_path = "https://" + url_or_path[len("webcal://"):]
    response = requests.get(
        url_or_path,
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.content


def normalize_allday(component) -> None:
    """Convert long daytime DATETIME events to VALUE=DATE all-day events.

    Heuristic: duration > 4h AND starts before noon.
    Catches museum exhibitions (10h-18h = 8h) without touching
    evening concerts (19h-22h = 3h) or late-night shows.
    """
    dtstart_prop = component.get("DTSTART")
    dtend_prop   = component.get("DTEND")
    if not dtstart_prop or not dtend_prop:
        return

    start = dtstart_prop.dt
    end   = dtend_prop.dt

    # Already a DATE-only event -- nothing to do
    if isinstance(start, date) and not isinstance(start, datetime):
        return

    try:
        duration = end - start
    except TypeError:
        return

    # Must be > 4h AND start before noon to avoid converting evening shows
    if duration.total_seconds() <= 4 * 3600:
        return
    start_hour = start.hour if isinstance(start, datetime) else 0
    if start_hour >= 12:
        return

    # Convert to VALUE=DATE
    start_d = start.date() if isinstance(start, datetime) else start
    end_d   = end.date()   if isinstance(end,   datetime) else end
    if end_d <= start_d:
        end_d = start_d + timedelta(days=1)
    del component["DTSTART"]
    del component["DTEND"]
    component.add("DTSTART", start_d)
    component.add("DTEND",   end_d)


def merge_calendar(combined: Calendar, source_name: str, prefix: str | None,
                   raw_ics: bytes) -> int:
    """Parse a source calendar and add its VEVENT components to `combined`.
    Returns the number of events added."""
    source_cal = Calendar.from_ical(raw_ics)
    count = 0
    for component in source_cal.walk():
        if component.name != "VEVENT":
            continue
        normalize_allday(component)
        if prefix:
            original_summary = component.get("SUMMARY", "")
            component["SUMMARY"] = f"[{prefix}] {original_summary}"
        original_uid = str(component.get("UID", f"event-{count}"))
        component["UID"] = f"{source_name}-{original_uid}"
        combined.add_component(component)
        count += 1
    return count


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"ERROR: config file not found at {CONFIG_PATH}", file=sys.stderr)
        return 1
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    sources = config.get("calendars", [])
    if not sources:
        print("ERROR: no calendars defined in calendars.yaml", file=sys.stderr)
        return 1
    combined = Calendar()
    combined.add("prodid", "-//ICS Aggregator//combined calendar//EN")
    combined.add("version", "2.0")
    combined.add("calscale", "GREGORIAN")
    combined.add("method", "PUBLISH")
    combined.add("x-wr-calname", config.get("name", "Combined Calendar"))
    combined.add("x-wr-timezone", config.get("timezone", "Europe/Paris"))
    total_events = 0
    errors: list[str] = []
    for source in sources:
        name   = source.get("name", "Unknown")
        url    = source.get("url")
        prefix = source.get("prefix")
        if not url:
            errors.append(f"{name}: missing 'url'")
            print(f"  SKIP {name}: missing 'url'", file=sys.stderr)
            continue
        print(f"Fetching: {name}", file=sys.stderr)
        try:
            raw   = fetch_ics(url)
            added = merge_calendar(combined, name, prefix, raw)
            print(f"  OK   {added} events", file=sys.stderr)
            total_events += added
        except requests.HTTPError as e:
            err = f"{name}: HTTP {e.response.status_code}"
            print(f"  FAIL {err}", file=sys.stderr)
            errors.append(err)
        except Exception as e:  # noqa: BLE001
            err = f"{name}: {type(e).__name__}: {e}"
            print(f"  FAIL {err}", file=sys.stderr)
            errors.append(err)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(combined.to_ical())
    print(f"\nWrote {total_events} events from {len(sources)-len(errors)}/{len(sources)} sources to {OUTPUT_PATH}", file=sys.stderr)
    if errors:
        print(f"\n{len(errors)} source(s) failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
