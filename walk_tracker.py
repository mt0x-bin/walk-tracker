"""
Walk Tracker — Notion → JSON + Summary
Reads walking data from a Notion page code block, parses it,
writes a formatted summary back, and exports data.json for the web dashboard.

Designed to run daily at 22:00 GMT+7 via GitHub Actions.
"""

import os
import re
import json
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional
import urllib.request
import urllib.error

# ─── Config ─────────────────────────────────────────────────────────────────

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
PAGE_ID = os.environ.get("NOTION_PAGE_ID", "")
USER_HEIGHT = float(os.environ.get("USER_HEIGHT", "1.67"))
NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"
TZ_OFFSET = timezone(timedelta(hours=7))  # GMT+7

GOALS = {
    0: 5.0,  # Monday
    1: 5.0,  # Tuesday
    2: 5.0,  # Wednesday
    3: 5.0,  # Thursday
    4: 5.0,  # Friday
    5: 5.0,  # Saturday
    6: 7.0,  # Sunday
}

JSON_OUTPUT = "docs/data.json"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


# ─── Notion API ──────────────────────────────────────────────────────────────

def notion_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make a request to the Notion API with retry logic."""
    url = f"{BASE_URL}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data else None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            if e.code == 429 or e.code >= 500:
                # Rate limit or server error → retry
                wait = RETRY_DELAY * attempt
                print(f"  ⚠ Notion {e.code}, retry {attempt}/{MAX_RETRIES} in {wait}s...")
                _time.sleep(wait)
                continue
            print(f"❌ Notion API error {e.code}: {error_body}")
            sys.exit(1)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_RETRIES:
                print(f"  ⚠ Network error, retry {attempt}/{MAX_RETRIES}...")
                _time.sleep(RETRY_DELAY * attempt)
                continue
            print(f"❌ Network error after {MAX_RETRIES} retries: {e}")
            sys.exit(1)

    print(f"❌ Failed after {MAX_RETRIES} retries")
    sys.exit(1)


def get_page_blocks(page_id: str) -> list:
    """Get all child blocks from a Notion page, handling pagination."""
    blocks = []
    cursor = None
    while True:
        endpoint = f"blocks/{page_id}/children?page_size=100"
        if cursor:
            endpoint += f"&start_cursor={cursor}"
        result = notion_request("GET", endpoint)
        blocks.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return blocks


def extract_code_blocks(blocks: list) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Find code blocks in the page.
    - Summary block: identified by caption containing 'summary'
    - Raw data block: the first code block that is NOT the summary and has content
    Returns (raw_block_id, raw_text, summary_block_id)
    """
    raw_id, raw_text, summary_id = None, None, None

    for block in blocks:
        if block.get("type") != "code":
            continue

        captions = block["code"].get("caption", [])
        caption = "".join(c.get("plain_text", "") for c in captions).lower().strip()
        rich_texts = block["code"].get("rich_text", [])
        text = "".join(rt.get("plain_text", "") for rt in rich_texts)

        if "summary" in caption:
            summary_id = block["id"]
        elif raw_id is None and text.strip():
            raw_id = block["id"]
            raw_text = text

    return raw_id, raw_text, summary_id


# ─── Parsing ─────────────────────────────────────────────────────────────────

DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}(/\d{2,4})?$")


def now_gmt7() -> datetime:
    """Get current time in GMT+7."""
    return datetime.now(TZ_OFFSET)


def parse_time_to_minutes(s: str) -> Optional[float]:
    """
    Parse mm:ss → total minutes as float.
    Example: '109:31' → 109.5167
    Returns None if invalid.
    """
    s = s.strip()
    parts = s.split(":")
    if len(parts) != 2:
        return None
    try:
        mins = int(parts[0])
        secs = int(parts[1])
        if mins < 0 or secs < 0 or secs >= 60:
            return None
        return mins + secs / 60
    except ValueError:
        return None


def format_time_mss(total_minutes: float) -> str:
    """Format minutes → 'mm:ss'."""
    mins = int(total_minutes)
    secs = round((total_minutes - mins) * 60)
    if secs >= 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}"


def format_time_long(total_minutes: float) -> str:
    """Format minutes → 'Xh YYm' or 'Ym'."""
    h = int(total_minutes // 60)
    m = round(total_minutes % 60)
    if m >= 60:
        h += 1
        m = 0
    return f"{h}h{m:02d}m" if h > 0 else f"{m}m"


def parse_date(s: str) -> Optional[datetime]:
    """
    Parse date string: dd/mm, dd/mm/yy, dd/mm/yyyy.
    If no year: uses current year, or previous year if date would be in the future.
    Returns None if invalid.
    """
    s = s.strip()
    parts = s.split("/")
    now = now_gmt7()

    try:
        if len(parts) == 3:
            day, month = int(parts[0]), int(parts[1])
            year = int(parts[2])
            if year < 100:
                year += 2000
            return datetime(year, month, day, tzinfo=TZ_OFFSET)

        if len(parts) == 2:
            day, month = int(parts[0]), int(parts[1])
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            year = now.year
            try:
                candidate = datetime(year, month, day, tzinfo=TZ_OFFSET)
            except ValueError:
                return None  # Invalid date like 31/02
            if candidate.date() > now.date() + timedelta(days=1):
                year -= 1
                candidate = datetime(year, month, day, tzinfo=TZ_OFFSET)
            return candidate

    except (ValueError, OverflowError):
        return None

    return None


def get_goal(dt: datetime) -> float:
    """Get distance goal for a given date."""
    return GOALS.get(dt.weekday(), 5.0)


def _try_dot_as_time(tok: str) -> Optional[float]:
    """
    Treat 'mm.ss' as 'mm:ss' — common typo (e.g. '100.09' meant '100:09').
    Strict rules to avoid false positives:
    - Minutes part must be >= 4 (walking sessions are almost always > 4 min)
    - Seconds part must be 0-59
    - Seconds string must be exactly 2 digits (e.g. '100.09', not '7.3')
      This prevents '7.03' (which is a km value) from being read as time.
    """
    if "." not in tok:
        return None
    parts = tok.split(".")
    if len(parts) != 2:
        return None
    try:
        mins = int(parts[0])
        secs_str = parts[1]
        if len(secs_str) != 2:
            return None  # Must be exactly 2 digits like .09, .31
        secs = int(secs_str)
        if mins < 4 or secs < 0 or secs >= 60:
            return None
        return mins + secs / 60
    except ValueError:
        return None


def parse_entry_line(line: str) -> Optional[dict]:
    """
    Parse a single data line containing 3 values: time (mm:ss), distance (km), calories.
    Values can be in any order — time is identified by ':', then smaller number = distance,
    larger number = calories.
    Fallback: if no ':' found, tries to detect 'mm.ss' as a time typo.
    Returns entry dict or None if unparseable.
    """
    tokens = line.split()
    if len(tokens) < 3:
        return None

    # Find the time token (contains ':')
    time_idx = None
    for i, tok in enumerate(tokens):
        if ":" in tok and parse_time_to_minutes(tok) is not None:
            time_idx = i
            break

    # Fallback: try mm.ss dot-notation (common typo)
    # Pick the candidate with the highest minutes value (most likely to be time)
    if time_idx is None:
        best_idx, best_mins = None, 0
        for i, tok in enumerate(tokens):
            mins = _try_dot_as_time(tok)
            if mins is not None and mins > best_mins:
                best_idx = i
                best_mins = mins
        if best_idx is not None:
            time_idx = best_idx

    if time_idx is None:
        return None

    time_str = tokens[time_idx]
    minutes = parse_time_to_minutes(time_str)
    if minutes is None:
        # Was detected via dot fallback
        minutes = _try_dot_as_time(time_str)
        time_str = f"{int(minutes)}:{round((minutes % 1) * 60):02d}"  # Normalize to mm:ss

    # Remaining tokens → numeric values
    nums = []
    for i, tok in enumerate(tokens):
        if i == time_idx:
            continue
        try:
            nums.append(float(tok))
        except ValueError:
            continue

    if len(nums) < 2:
        return None

    # Smaller = distance (km), larger = calories
    nums.sort()
    distance = round(nums[0], 2)
    calories = round(nums[1])

    # Sanity checks
    if distance <= 0 or calories <= 0 or minutes <= 0:
        return None
    if distance > 100:  # > 100km single walk? Probably error
        return None

    speed = distance / (minutes / 60)
    cal_per_km = calories / distance if distance > 0 else 0

    return {
        "time": time_str,
        "time_minutes": round(minutes, 2),
        "distance": distance,
        "calories": calories,
        "speed": round(speed, 2),
        "cal_per_km": round(cal_per_km, 2),
    }


def parse_raw_data(raw: str) -> list[dict]:
    """
    Parse raw walking data text into structured days.
    Format: date line (dd/mm) followed by data lines.
    Returns list of {date: datetime, entries: [...]}, sorted by date.
    """
    lines = raw.strip().split("\n")
    days = []
    cur_date = None
    cur_entries = []

    def flush():
        nonlocal cur_entries
        if cur_date is not None and cur_entries:
            days.append({"date": cur_date, "entries": list(cur_entries)})
        cur_entries = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Check if this is a date line
        if DATE_RE.match(line):
            parsed_date = parse_date(line)
            if parsed_date:
                flush()
                cur_date = parsed_date
            continue

        # Try to parse as data line
        entry = parse_entry_line(line)
        if entry:
            cur_entries.append(entry)

    flush()

    # Sort chronologically
    days.sort(key=lambda d: d["date"])

    # Deduplicate: merge entries for the same date
    merged = {}
    for day in days:
        key = day["date"].strftime("%Y-%m-%d")
        if key in merged:
            merged[key]["entries"].extend(day["entries"])
        else:
            merged[key] = day
    days = sorted(merged.values(), key=lambda d: d["date"])

    return days


# ─── Aggregation helpers ─────────────────────────────────────────────────────

def day_totals(day: dict) -> dict:
    """Compute aggregated totals for a single day."""
    entries = day["entries"]
    dist = sum(e["distance"] for e in entries)
    cal = sum(e["calories"] for e in entries)
    time = sum(e["time_minutes"] for e in entries)
    speed = dist / (time / 60) if time > 0 else 0
    goal = get_goal(day["date"])
    return {
        "distance": round(dist, 2),
        "calories": round(cal),
        "time_minutes": round(time, 2),
        "speed": round(speed, 2),
        "goal": goal,
        "goal_met": dist >= goal,
        "sessions": len(entries),
    }


def overall_totals(days: list[dict]) -> dict:
    """Compute totals across all days."""
    all_entries = [e for d in days for e in d["entries"]]
    if not all_entries:
        return {"total_dist": 0, "total_cal": 0, "total_time": 0,
                "avg_speed": 0, "avg_dist": 0, "avg_cal_per_km": 0,
                "total_sessions": 0, "total_days": 0}

    td = sum(e["distance"] for e in all_entries)
    tc = sum(e["calories"] for e in all_entries)
    tt = sum(e["time_minutes"] for e in all_entries)
    return {
        "total_dist": round(td, 2),
        "total_cal": round(tc),
        "total_time": round(tt, 2),
        "avg_speed": round(td / (tt / 60), 2) if tt > 0 else 0,
        "avg_dist": round(td / len(all_entries), 2),
        "avg_cal_per_km": round(tc / td, 2) if td > 0 else 0,
        "total_sessions": len(all_entries),
        "total_days": len(days),
    }


# ─── Notion summary output ──────────────────────────────────────────────────

DAY_NAMES = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def format_notion_summary(days: list[dict]) -> str:
    """Format parsed data into a readable Notion summary."""
    lines = []
    totals = overall_totals(days)

    for day in days:
        dt = day["date"]
        dt_totals = day_totals(day)
        date_str = dt.strftime("%d/%m/%Y")
        day_name = DAY_NAMES[dt.weekday()]
        icon = "✅" if dt_totals["goal_met"] else "❌"

        lines.append(f"📅 {date_str} ({day_name})")
        lines.append("─" * 34)

        for e in day["entries"]:
            lines.append(
                f"  ⏱ {e['time']:>7s}  "
                f"📏 {e['distance']:>5.2f} km  "
                f"🔥 {e['calories']:>3d} cal  "
                f"🏃 {e['speed']:.1f} km/h"
            )

        lines.append(
            f"  ▸ Tổng: {format_time_mss(dt_totals['time_minutes'])} | "
            f"{dt_totals['distance']:.2f} km | "
            f"{dt_totals['calories']} cal | "
            f"{dt_totals['speed']:.1f} km/h "
            f"{icon} ({dt_totals['distance']:.1f}/{dt_totals['goal']:.0f} km)"
        )
        lines.append("")

    now = now_gmt7()
    lines.append("=" * 38)
    lines.append(f"📊 TỔNG KẾT ({totals['total_days']} ngày, {totals['total_sessions']} buổi)")
    lines.append(f"  📏 Quãng đường : {totals['total_dist']:.2f} km")
    lines.append(f"  🔥 Calo        : {totals['total_cal']:,} cal")
    lines.append(f"  ⏱  Thời gian   : {format_time_long(totals['total_time'])}")
    lines.append(f"  🏃 Tốc độ TB   : {totals['avg_speed']:.2f} km/h")
    if totals["total_sessions"]:
        lines.append(f"  📊 TB/buổi     : {totals['avg_dist']:.2f} km")
    lines.append(f"  📅 Cập nhật    : {now.strftime('%d/%m/%Y %H:%M')}")
    lines.append("=" * 38)

    return "\n".join(lines)


def make_rich_text(text: str) -> list:
    """Split text into Notion rich_text chunks (max 2000 chars each)."""
    chunks = []
    while text:
        chunks.append({"type": "text", "text": {"content": text[:2000]}})
        text = text[2000:]
    return chunks


def update_summary_block(page_id: str, summary_text: str, summary_block_id: Optional[str]):
    """Update existing summary block or create a new one."""
    caption = [{"type": "text", "text": {"content": "summary — auto-updated"}}]
    code_block = {
        "rich_text": make_rich_text(summary_text),
        "language": "plain text",
        "caption": caption,
    }

    if summary_block_id:
        notion_request("PATCH", f"blocks/{summary_block_id}", {"code": code_block})
        print("  ✅ Updated summary block")
    else:
        notion_request("PATCH", f"blocks/{page_id}/children", {
            "children": [
                {"object": "block", "type": "divider", "divider": {}},
                {"object": "block", "type": "heading_2", "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "📊 Tổng kết tự động"}}]
                }},
                {"object": "block", "type": "code", "code": code_block},
            ]
        })
        print("  ✅ Created new summary block")


# ─── JSON export ─────────────────────────────────────────────────────────────

def export_json(days: list[dict], output_path: str):
    """Export parsed data as JSON for the web dashboard."""
    json_days = []
    for day in days:
        dt = day["date"]
        json_days.append({
            "date": dt.strftime("%Y-%m-%d"),
            "dateStr": dt.strftime("%d/%m"),
            "dateFull": dt.strftime("%d/%m/%Y"),
            "dayOfWeek": dt.weekday(),
            "goal": get_goal(dt),
            "entries": [
                {
                    "time": e["time"],
                    "timeMinutes": e["time_minutes"],
                    "distance": e["distance"],
                    "calories": e["calories"],
                    "speed": e["speed"],
                    "calPerKm": e["cal_per_km"],
                }
                for e in day["entries"]
            ],
        })

    totals = overall_totals(days)
    now = now_gmt7()

    output = {
        "updatedAt": now.isoformat(),
        "totalDays": totals["total_days"],
        "totalSessions": totals["total_sessions"],
        "days": json_days,
        "summary": {
            "totalDistance": totals["total_dist"],
            "totalCalories": totals["total_cal"],
            "totalTimeMinutes": totals["total_time"],
            "avgSpeed": totals["avg_speed"],
            "avgDistPerSession": totals["avg_dist"],
            "avgCalPerKm": totals["avg_cal_per_km"],
        },
        "config": {
            "height": USER_HEIGHT,
            "goals": {DAY_NAMES[k]: v for k, v in GOALS.items()},
        },
    }

    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  ✅ Exported {output_path} ({totals['total_sessions']} entries, {totals['total_days']} days)")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if not NOTION_TOKEN:
        print("❌ NOTION_TOKEN not set")
        sys.exit(1)
    if not PAGE_ID:
        print("❌ NOTION_PAGE_ID not set")
        sys.exit(1)

    now = now_gmt7()
    print(f"🚀 Walk Tracker — {now.strftime('%d/%m/%Y %H:%M:%S')} (GMT+7)")
    print(f"   Page: {PAGE_ID[:8]}...")

    # 1. Read blocks from Notion page
    print("\n📖 Reading Notion page...")
    blocks = get_page_blocks(PAGE_ID)
    print(f"  Found {len(blocks)} blocks")

    # 2. Extract code blocks
    raw_id, raw_text, summary_id = extract_code_blocks(blocks)
    if not raw_text:
        print("❌ No code block with walking data found")
        print("   Make sure you have a Plain Text code block with your walking data")
        sys.exit(1)
    print(f"  Raw data: {len(raw_text)} chars (block {raw_id[:8]}...)")

    # 3. Parse raw data
    print("\n🔍 Parsing data...")
    days = parse_raw_data(raw_text)
    totals = overall_totals(days)
    print(f"  {totals['total_days']} days, {totals['total_sessions']} entries")
    print(f"  Date range: {days[0]['date'].strftime('%d/%m/%Y')} → {days[-1]['date'].strftime('%d/%m/%Y')}" if days else "  No data")

    if not days:
        print("\n⚠️ No valid walking data found — exiting")
        sys.exit(0)

    # 4. Write summary to Notion
    print("\n📝 Writing summary to Notion...")
    summary = format_notion_summary(days)
    update_summary_block(PAGE_ID, summary, summary_id)

    # 5. Export JSON for dashboard
    print("\n📊 Exporting dashboard data...")
    export_json(days, JSON_OUTPUT)

    # 6. Print summary
    print(f"\n{'=' * 40}")
    print(summary)
    print(f"{'=' * 40}")
    print("\n🎉 Done!")


if __name__ == "__main__":
    main()
