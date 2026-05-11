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
from pathlib import Path

import requests
import yaml
from icalendar import Calendar

CONFIG_PATH = Path("calendars.yaml")
OUTPUT_PATH = Path("docs/combined.ics")
TIMEOUT_SECONDS = 30
USER_AGENT = "ICS-Aggregator/1.0 (+https://github.com/)"


def fetch_ics(url_or_path: str) -> bytes:
    """Load ICS bytes from a URL or a local file path.

    Supports:
      - https://...
      - webcal://...      (converted to https)
      - file:./path.ics   (read from disk)
      - ./path.ics        (relative path on disk)
    """
    # Local file forms
    if url_or_path.startswith("file:"):
        return Path(url_or_path[len("file:"):]).read_bytes()
    if url_or_path.startswith(("./", "/")) or not "://" in url_or_path:
        # Treat as local path (e.g. "docs/maison-de-la-danse.ics")
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


def merge_calendar(combined: Calendar, source_name: str, prefix: str | None,
                   raw_ics: bytes) -> int:
    """Parse a source calendar and add its VEVENT components to `combined`.
    Returns the number of events added."""
    source_cal = Calendar.from_ical(raw_ics)
    count = 0
    for component in source_cal.walk():
        if component.name != "VEVENT":
            continue

        # Optional: prefix the event title to identify the source
        if prefix:
            original_summary = component.get("SUMMARY", "")
            component["SUMMARY"] = f"[{prefix}] {original_summary}"

        # Make UIDs globally unique by namespacing them with the source name.
        # Without this, two sources with overlapping UIDs would collide and
        # subscribers would only see one of the events.
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
        name = source.get("name", "Unknown")
        url = source.get("url")
        prefix = source.get("prefix")

        if not url:
            errors.append(f"{name}: missing 'url'")
            print(f"  SKIP {name}: missing 'url'", file=sys.stderr)
            continue

        print(f"Fetching: {name}", file=sys.stderr)
        try:
            raw = fetch_ics(url)
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

    print(
        f"\nWrote {total_events} events from {len(sources) - len(errors)}/"
        f"{len(sources)} sources to {OUTPUT_PATH}",
        file=sys.stderr,
    )

    if errors:
        print(f"\n{len(errors)} source(s) failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        # Don't exit non-zero — partial success still produces a useful file.

    return 0


if __name__ == "__main__":
    sys.exit(main())
