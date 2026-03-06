#!/usr/bin/env python3
"""
seed_archive.py  —  Paging Dr. Oh  —  One-Time Archive Seeder
==============================================================
Run this ONCE before your first daily generation to pre-populate
the archive with ~12 months of retrospective What's New items.

This makes the Archive tab useful from day one instead of waiting
30 days for items to roll out of the current window.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python seed_archive.py

It writes to:
    archive.json          (retrospective items organized by month)
    whats_new_current.json (a small set of "recent" items for the past 30 days)
    history.json          (marks seeded items so generate.py won't duplicate them)
"""

import os, sys, json, traceback
from datetime import datetime, timedelta, timezone

try:
    from anthropic import Anthropic
except ImportError:
    print("ERROR: 'anthropic' package not installed.  Run:  pip install anthropic")
    sys.exit(1)

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    print("ERROR: Set the ANTHROPIC_API_KEY environment variable.")
    sys.exit(1)

CLIENT = Anthropic(api_key=API_KEY)
MODEL  = "claude-sonnet-4-20250514"
TODAY  = datetime.now(timezone.utc)

# File paths
ARCHIVE_PATH = "archive.json"
WN_PATH      = "whats_new_current.json"
HISTORY_PATH = "history.json"


def call_claude(prompt, max_tokens=8000):
    """Call Claude API (no web search — we want historically accurate items)."""
    response = CLIENT.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def parse_json_response(text):
    """Extract JSON array from API response."""
    import re
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    start = text.find("[")
    if start == -1:
        raise ValueError("No JSON array found")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
        if depth == 0:
            return json.loads(text[start : i + 1])
    raise ValueError("Malformed JSON array")


def generate_month_items(year, month):
    """Generate 3-5 notable medical updates for a given month."""
    month_name = datetime(year, month, 1).strftime("%B %Y")
    prompt = f"""You are a medical education content curator. Generate 3-5 notable medical
updates that were published in {month_name}. These should be realistic items that
a hospitalist/internal medicine physician would find important.

Include a mix of:
- RCTs from major journals (NEJM, JAMA, Lancet, BMJ, Annals)
- Updated clinical guidelines
- FDA actions (approvals, warnings)

For each item, provide accurate or highly plausible details. Use real journal names,
real medical organizations, and realistic statistics.

Return ONLY a valid JSON array (no markdown fences):
[
  {{
    "id": "unique-slug-{year}-{month:02d}",
    "type": "RCT" or "Meta-Analysis" or "Guideline" or "FDA Action",
    "specialty": "e.g. Critical Care",
    "source": "Journal or Organization Name",
    "date": "Month Day, {year}",
    "date_iso": "{year}-{month:02d}-XX",
    "title": "Full title of the study/guideline/action",
    "study_design": "Brief design description",
    "key_findings": "Key results with statistics",
    "bottom_line": "One-sentence clinical takeaway",
    "confidence": "high" or "moderate" or "preliminary",
    "source_url": "https://doi.org/..."
  }}
]

Replace XX in date_iso with the actual day number. Make each id unique."""

    try:
        raw = call_claude(prompt, max_tokens=6000)
        items = parse_json_response(raw)
        # Fix date_iso format
        for item in items:
            if "date_iso" in item and "XX" in item.get("date_iso", ""):
                # Replace XX with 15 as default day
                item["date_iso"] = item["date_iso"].replace("XX", "15")
        return items
    except Exception as e:
        print(f"  WARNING: Failed to generate items for {month_name}: {e}")
        return []


def main():
    print("=" * 60)
    print("  Paging Dr. Oh — Archive Seeder (One-Time)")
    print("=" * 60)
    print()
    print("This will generate ~12 months of retrospective medical")
    print("updates to populate the Archive tab.")
    print()

    # Generate items for each of the past 12 months (skipping the current month)
    archive_items = []
    current_items = []
    all_ids = []

    # Current month items go into whats_new_current.json (past 30 days)
    # Older items go into archive.json
    thirty_days_ago = TODAY - timedelta(days=30)

    for months_back in range(12, 0, -1):  # 12 months ago → 1 month ago
        target_date = TODAY - timedelta(days=months_back * 30)
        year = target_date.year
        month = target_date.month

        month_name = datetime(year, month, 1).strftime("%B %Y")
        print(f"  Generating items for {month_name}...", end=" ", flush=True)

        items = generate_month_items(year, month)
        print(f"got {len(items)} items")

        for item in items:
            all_ids.append(item.get("id", ""))
            # Items from last 30 days → current, older → archive
            item_date_str = item.get("date_iso", "")
            try:
                item_date = datetime.strptime(item_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if item_date >= thirty_days_ago:
                    current_items.append(item)
                else:
                    archive_items.append(item)
            except (ValueError, TypeError):
                archive_items.append(item)

    # Also generate a few items for "this month" (current 30-day window)
    print(f"  Generating items for {TODAY.strftime('%B %Y')} (current)...", end=" ", flush=True)
    current_month_items = generate_month_items(TODAY.year, TODAY.month)
    print(f"got {len(current_month_items)} items")
    for item in current_month_items:
        all_ids.append(item.get("id", ""))
        current_items.append(item)

    # Save files
    print()
    print(f"  Archive items:  {len(archive_items)}")
    print(f"  Current items:  {len(current_items)}")

    with open(ARCHIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(archive_items, f, indent=2, ensure_ascii=False)
    print(f"  Wrote {ARCHIVE_PATH}")

    with open(WN_PATH, "w", encoding="utf-8") as f:
        json.dump(current_items, f, indent=2, ensure_ascii=False)
    print(f"  Wrote {WN_PATH}")

    # Update history.json to include seeded IDs (prevents duplicates)
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = {"diseases_shown": [], "landmark_studies_shown": [], "whats_new_ids": [], "last_run": None}

    history["whats_new_ids"] = list(set(history.get("whats_new_ids", []) + all_ids))
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"  Updated {HISTORY_PATH}")

    print()
    print("Done!  Your archive is now pre-populated.")
    print("You can run generate.py to create today's index.html.")
    print("=" * 60)


if __name__ == "__main__":
    main()
