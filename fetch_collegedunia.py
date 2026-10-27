#!/usr/bin/env python3
"""fetch_collegedunia.py -- Populate data/seed_colleges.csv from CollegeDunia for ANY Indian State.

Usage:
    python fetch_collegedunia.py                                # scrape default (Karnataka)
    python fetch_collegedunia.py --state "Tamil Nadu"           # scrape Tamil Nadu colleges
    python fetch_collegedunia.py --state "Maharashtra" --limit 50
    python fetch_collegedunia.py --state "Kerala" --dry-run
    python fetch_collegedunia.py --list-states                  # list all supported Indian states
    python fetch_collegedunia.py --limit 0                      # scrape all available colleges
    python fetch_collegedunia.py --delay 1.5                    # custom delay between requests (seconds)

The script is safe to re-run: existing entries in the CSV are preserved and
only new colleges (matched by website URL) are appended.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent))

from discovery.collegedunia_scraper import (
    INDIAN_STATES,
    get_state_listing_url,
    get_state_slug,
    scrape_colleges,
)

log = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent / "data" / "seed_colleges.csv"
CSV_FIELDS = ["name", "website", "state", "city", "institution_type"]


def _load_existing_websites(path: Path) -> set:
    """Return the set of website URLs already in the CSV (normalised to lowercase)."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return {(r.get("website") or "").strip().lower() for r in reader if r.get("website")}


def update_seed_csv(colleges: list, csv_path: Path, default_state: str = "Karnataka", dry_run: bool = False) -> dict:
    """Merge scraped colleges into the seed CSV.

    Args:
        colleges: List of dicts from the scraper.
        csv_path: Path to the CSV file.
        default_state: Fallback state name if not provided in college dict.
        dry_run: If True, print what would be written but don't touch the file.

    Returns:
        {"added": int, "skipped": int, "total": int}
    """
    existing = _load_existing_websites(csv_path)
    new_rows = []

    for college in colleges:
        website = (college.get("website") or "").strip()
        if not website:
            continue
        if website.lower() in existing:
            continue
        new_rows.append({
            "name": college.get("name", ""),
            "website": website,
            "state": college.get("state") or default_state,
            "city": college.get("city", ""),
            "institution_type": college.get("institution_type", ""),
        })
        existing.add(website.lower())  # prevent dupes within the new batch

    stats = {"added": len(new_rows), "skipped": len(colleges) - len(new_rows), "total": len(colleges)}

    if dry_run:
        print("\n[DRY RUN] Would add %d new colleges to %s" % (stats["added"], csv_path))
        for row in new_rows[:20]:
            print("  + %s | %s | %s | %s" % (row["name"], row["website"], row["city"], row["state"]))
        if len(new_rows) > 20:
            print("  ... and %d more" % (len(new_rows) - 20))
        return stats

    if not new_rows:
        print("[OK] Nothing new to add -- CSV is already up to date.")
        return stats

    # Ensure the CSV exists with header
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    print("\n[OK] Added %d new colleges to %s" % (stats["added"], csv_path))
    print("   Skipped %d already-present colleges" % stats["skipped"])
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch colleges from CollegeDunia for any Indian State")
    parser.add_argument(
        "--state",
        type=str,
        default="Karnataka",
        help="Indian state to scrape (e.g. 'Karnataka', 'Tamil Nadu', 'Maharashtra', 'Kerala', 'Delhi NCR', etc. Default: Karnataka)",
    )
    parser.add_argument(
        "--list-states",
        action="store_true",
        help="List all supported Indian states and exit",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max colleges to scrape (0 = all available on listing, default: 100)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between HTTP requests (default: 1.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to CSV",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(CSV_PATH),
        help="Output CSV path (default: data/seed_colleges.csv)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-college progress output",
    )
    args = parser.parse_args()

    if args.list_states:
        print("Supported Indian States and Union Territories:")
        for s in INDIAN_STATES:
            print(f"  - {s} ({get_state_listing_url(s)})")
        return 0

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    verbose = not args.quiet
    limit = args.limit if args.limit > 0 else None  # 0 means no limit
    state_display = args.state.strip().title()
    listing_url = get_state_listing_url(args.state)

    print("=" * 60)
    print("  Shortfundly - CollegeDunia India College Fetcher")
    print("=" * 60)
    print("  Target State: %s" % state_display)
    print("  Listing URL:  %s" % listing_url)
    if limit:
        print("  Scraping top %d colleges (use --limit 0 for all)" % limit)
    else:
        print("  Scraping ALL available colleges for this state")
    print("  Delay: %.1fs between requests" % args.delay)
    print("  Output: %s" % args.output)
    if args.dry_run:
        print("  [DRY RUN] No files will be written")
    print("=" * 60)

    colleges = scrape_colleges(state=args.state, limit=limit, delay=args.delay, verbose=verbose)

    if not colleges:
        print("[ERROR] No colleges found for '%s' -- check network or state name" % state_display)
        return 1

    stats = update_seed_csv(colleges, Path(args.output), default_state=state_display, dry_run=args.dry_run)

    print("\nSummary:")
    print("   State: %s" % state_display)
    print("   Scraped: %d colleges from CollegeDunia" % stats["total"])
    print("   Added to CSV: %d new entries" % stats["added"])
    print("   Skipped (already in CSV): %d" % stats["skipped"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
