#!/usr/bin/env python3
"""
Swim Schedule Calendar Generator - Anthropic API Version

Uses Claude to read the swim schedule image and extract practice times.
Generates individual ICS files for each tracked class.
"""

import os
import re
import json
import base64
import uuid
from datetime import datetime, timedelta
from io import BytesIO
import requests


# Pool location mappings
LOCATIONS = {
    "LCHS": {
        "name": "La Canada High School",
        "address": "4463 Oak Grove Dr, La Cañada Flintridge, CA 91011"
    },
    "GHS": {
        "name": "Glendale High School",
        "address": "1440 E Broadway, Glendale, CA 91205"
    }
}

# Public practice-schedule page linked from every event's Notes/DESCRIPTION
SCHEDULE_PAGE_URL = ("https://www.gomotionapp.com/team/spartansla/"
                     "page/our-team/practice-schedule-and-monthly-fees")

# State file used to track which week we've already generated calendars for.
# This is what prevents a manual run + the scheduled overnight run from
# both processing the same week and creating duplicate events.
STATE_FILE = os.path.join('docs', '.last_processed_week.json')


def load_config():
    """Load configuration from config.json"""
    with open('config.json', 'r') as f:
        return json.load(f)


def save_last_processed_week(week_key):
    """Record that we've successfully processed this week (for logging/debugging only —
    the actual skip decision is based on real ICS content, see week_has_events_for_class)."""
    os.makedirs('docs', exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump({
            'week_start': week_key,
            'updated_at': datetime.utcnow().isoformat() + 'Z'
        }, f, indent=2)


def week_has_events_for_class(filepath, week_start):
    """Check the ACTUAL committed ICS file to see if it already contains at least one
    event whose DTSTART falls within this week (Monday..Sunday).

    This is deliberately based on the real calendar content rather than a separate
    'last run' record, so it can't drift out of sync with what's actually published,
    and it reuses the exact same DTSTART date strings the rolling-window trim uses.
    """
    if not os.path.exists(filepath):
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    week_end = week_start + timedelta(days=6)
    start_str = week_start.strftime('%Y%m%d')
    end_str = week_end.strftime('%Y%m%d')
    
    dtstarts = re.findall(r'DTSTART[^:]*:(\d{8})', content)
    return any(start_str <= d <= end_str for d in dtstarts)


def find_schedule_image_url(html_content):
    """Extract the schedule image URL from the HTML"""
    pattern = r'src="(/spartansla/UserFiles/Image/QuickUpload/[^"]+\.(?:jpg|jpeg|png))"'
    matches = re.findall(pattern, html_content, re.IGNORECASE)
    
    if not matches:
        raise ValueError("Could not find schedule image in HTML")
    
    return "https://www.spartanswim.com" + matches[0]


def download_image(url):
    """Download image and return base64 encoded data"""
    print(f"Downloading schedule image from: {url}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    
    # Determine media type from URL
    if url.lower().endswith('.png'):
        media_type = "image/png"
    elif url.lower().endswith(('.jpg', '.jpeg')):
        media_type = "image/jpeg"
    else:
        media_type = "image/jpeg"  # default
    
    # Encode to base64
    image_data = base64.standard_b64encode(response.content).decode('utf-8')
    
    return image_data, media_type


def extract_json_from_text(response_text):
    """Pull a JSON object out of Claude's response, tolerating stray commentary or
    markdown fences before/after it (rather than assuming the response is pure JSON).
    """
    text = response_text.strip()
    
    # First choice: a fenced ```json ... ``` or ``` ... ``` block, wherever it appears
    fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    
    # Second choice: the outermost { ... } in the whole response
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace:last_brace + 1]
    
    raise ValueError(
        f"Could not find any JSON in Claude's response. Raw response started with:\n"
        f"{text[:500]}"
    )


def extract_schedule_with_claude(image_data, media_type, classes):
    """Use Anthropic API to extract schedule from image"""
    print("Sending image to Claude for schedule extraction...")
    
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    
    # Debug: confirm API key is loaded
    print(f"API Key loaded: {api_key[:20]}..." if api_key else "NO API KEY")
    print(f"API Key format valid: {api_key.startswith('sk-ant-') if api_key else False}")
    
    # Build the prompt
    classes_list = ', '.join(classes)
    prompt = f"""Please analyze this swim practice schedule image and extract the practice times for these specific classes: {classes_list}

CRITICAL: This schedule MUST be released on or after today (Sunday evening). If the schedule date appears to be from the PAST week, REJECT IT and respond with: {{"error": "Schedule is from previous week"}}

For each class, I need:
- The week date range (e.g., "5/18 - 5/24") — MUST BE THIS WEEK OR NEXT WEEK, NOT PAST
- For each day of the week (Monday through Sunday), list all practice sessions with:
  - Start time in 24-hour format (see TIME RULES below)
  - End time
  - Coach name (the text in each cell like "Niyoosha", "SS", "Yoga", "Kailee", etc.)
  - Location: Check if the cell contains "@GHS" or "GHS" text. If yes, set location to "GHS". Otherwise set to "LCHS" (default)
  - If a day shows "OFF", skip it (no practice that day)

IMPORTANT TIME RULES:
- Times written without AM/PM are afternoon/evening — convert to 24-hour PM (e.g. "4:30-6:00" means 16:30-18:00, "1:45-3:00" means 13:45-15:00)
- Monday-Friday practices for these classes ALWAYS start at 2 PM (14:00) or later. A weekday time before 14:00 is not a practice for these classes — skip it.
- Saturday and Sunday practices may be in the morning (e.g. "7:50-10:00AM" means 07:50-10:00), but never run later than 8 PM (20:00).
- The schedule occasionally has AM/PM typos: a weekend time marked "PM" that would start after 8 PM (e.g. "9:30-11:30PM") is really a morning practice — extract it as AM (09:30-11:30).
- Use 24-hour format ONLY (14:30 for 2:30 PM)
- Location must be either "GHS" or "LCHS" (normalize to these codes)

Your ENTIRE response must be nothing but the JSON object itself — no preamble, no commentary, no explanation of what week it is, no markdown code fences. Do not write a sentence before the JSON. The very first character of your response must be {{ and the very last character must be }}.

Respond with ONLY valid JSON in this exact format (unless there's an error):

{{
  "week_start": "5/18/2026",
  "classes": {{
    "Sharknado 2": {{
      "Monday": [{{"start": "17:15", "end": "18:00", "coach": "Niyoosha", "location": "LCHS"}}],
      "Tuesday": [{{"start": "16:30", "end": "17:30", "coach": "Niyoosha", "location": "LCHS"}}],
      "Wednesday": [{{"start": "17:30", "end": "18:30", "coach": "Yoga", "location": "GHS"}}],
      "Thursday": [{{"start": "18:15", "end": "19:00", "coach": "SS", "location": "LCHS"}}],
      "Friday": [],
      "Saturday": [{{"start": "13:45", "end": "15:00", "coach": "Kailee", "location": "LCHS"}}],
      "Sunday": []
    }},
    "Sharknado 3": {{
      "Monday": [{{"start": "16:30", "end": "17:15", "coach": "Niyoosha", "location": "LCHS"}}],
      ...
    }}
  }}
}}

Return ONLY the JSON, no other text"""

    # Call Anthropic API
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-opus-5",
                "max_tokens": 2000,
                "thinking": {"type": "disabled"},
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            },
            timeout=60
        )
        
        # Debug output
        print(f"API Response Status: {response.status_code}")
        if response.status_code != 200:
            print(f"API Response Body: {response.text}")
        
        response.raise_for_status()
        result = response.json()
        
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: API request failed with status {response.status_code}")
        print(f"Response: {response.text}")
        raise
    
    # Extract the text response
    response_text = result['content'][0]['text']
    
    print("\nClaude's response:")
    print(response_text[:500] + "..." if len(response_text) > 500 else response_text)
    
    # Parse JSON from response. Claude is instructed to return ONLY JSON, but models
    # sometimes add a sentence of commentary before it anyway — so we look for the
    # JSON wherever it appears rather than assuming it's the very first character.
    json_text = extract_json_from_text(response_text)
    schedule_data = json.loads(json_text)
    
    # Check for error responses from Claude
    if 'error' in schedule_data:
        raise ValueError(f"Claude validation error: {schedule_data['error']}")
    
    return schedule_data


def parse_week_start(week_start_str):
    """Parse week start date from string like '6/15/2026' or '6/15/2024' (OCR error)"""
    try:
        parsed_date = datetime.strptime(week_start_str, '%m/%d/%Y')
    except ValueError:
        # Fallback to current Monday
        today = datetime.now()
        return today - timedelta(days=today.weekday())
    
    today = datetime.now()
    
    # VALIDATION: If year is wrong by 1-2 years, assume it's an OCR error and correct it
    if parsed_date.year != today.year:
        if abs(parsed_date.year - today.year) <= 2:
            # Likely OCR error, correct the year
            original_date = parsed_date
            parsed_date = parsed_date.replace(year=today.year)
            print(f"  Year corrected from {original_date.strftime('%m/%d/%Y')} to {parsed_date.strftime('%m/%d/%Y')} (likely OCR error)")
        else:
            raise ValueError(
                f"Year seems way off: Extracted {parsed_date.year} but today is {today.year}. "
                f"This is likely an image reading error."
            )
    
    # Check that date is current/recent (not more than 7 days in past)
    days_difference = (today - parsed_date).days
    if days_difference > 7:
        raise ValueError(
            f"Schedule date is too old! Extracted: {parsed_date.strftime('%m/%d/%Y')}, "
            f"Today: {today.strftime('%m/%d/%Y')}. "
            f"This schedule is {days_difference} days old. Aborting to prevent old data."
        )
    
    return parsed_date


def ics_escape(text):
    """Escape text for use as an ICS property value (RFC 5545)."""
    return (text.replace("\\", "\\\\")   # backslash FIRST, or it double-escapes
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("\n", "\\n"))


def build_practice_description():
    """Build the Notes/DESCRIPTION text: a link to the practice schedule page
    plus a weekly-change disclaimer. Same for every event, so no details needed."""
    parts = [
        "Full practice schedule (times, locations & weekly changes):",
        SCHEDULE_PAGE_URL,
        "",
        "Schedule changes weekly; confirm with your coach.",
    ]
    return ics_escape("\n".join(parts))


def generate_ics_for_class(class_name, schedule, week_start, timezone):
    """Generate ICS calendar file for a single class with 2-week rolling history"""
    
    day_offset = {
        'Monday': 0,
        'Tuesday': 1,
        'Wednesday': 2,
        'Thursday': 3,
        'Friday': 4,
        'Saturday': 5,
        'Sunday': 6
    }
    
    events = []
    
    # Calculate cutoff date (keep events from last 14 days)
    cutoff_date = week_start - timedelta(days=14)
    
    for day_name, sessions in schedule.items():
        if not sessions:
            continue
            
        day_date = week_start + timedelta(days=day_offset.get(day_name, 0))
        
        for session in sessions:
            start_time_str = session['start']
            end_time_str = session['end']
            coach = session.get('coach', 'Practice')
            location_code = session.get('location', 'LCHS')  # Default to LCHS
            
            # Parse time strings (already in 24-hour format like "17:15")
            start_hour, start_min = map(int, start_time_str.split(':'))
            end_hour, end_min = map(int, end_time_str.split(':'))
            
            # VALIDATION: weekday practices always start 2 PM or later; weekend
            # practices may be in the morning (6 AM floor as a sanity check) but
            # never run at/after 8 PM. Guards against AM/PM misreads and typos.
            is_weekend = day_name in ('Saturday', 'Sunday')
            time_ok = (6 <= start_hour < 20) if is_weekend else (start_hour >= 14)
            if not time_ok:
                print(f"  WARNING: Rejecting implausible practice time {start_time_str} for {coach} on {day_name}")
                print(f"    (Mon-Fri practices start 14:00 or later; Sat/Sun between 06:00 and 20:00)")
                continue
            
            start_dt = day_date.replace(hour=start_hour, minute=start_min, second=0)
            end_dt = day_date.replace(hour=end_hour, minute=end_min, second=0)
            
            # Get location info
            location_info = LOCATIONS.get(location_code, LOCATIONS['LCHS'])
            location_name = location_info['name']
            location_address = location_info['address']
            
            # Generate UID
            uid = str(uuid.uuid5(uuid.NAMESPACE_DNS, 
                                 f"swim-{class_name}-{day_date.isoformat()}-{start_time_str}"))
            
            now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            
            event = [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now}",
                f"DTSTART;TZID={timezone}:{start_dt.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID={timezone}:{end_dt.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{class_name} - {coach}",
                f"DESCRIPTION:{build_practice_description()}",
                f"LOCATION:{ics_escape(location_address)}",
                "TRANSP:TRANSPARENT",
                "END:VEVENT"
            ]
            events.append('\r\n'.join(event))
    
    # Build complete ICS with rolling history
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Spartans Swim Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Spartans - {class_name}",
        f"X-WR-TIMEZONE:{timezone}",
        "X-PUBLISHED-TTL:P1W"
    ]
    
    footer = ["END:VCALENDAR"]
    
    return '\r\n'.join(header + events + footer)


def extract_uid(event_block):
    """Pull the UID line out of a VEVENT block"""
    match = re.search(r'UID:([^\r\n]+)', event_block)
    return match.group(1) if match else None


def load_existing_events(filepath):
    """Load events from existing ICS file, keyed by UID (falls back to DTSTART if no UID)"""
    events_by_uid = {}
    
    if not os.path.exists(filepath):
        return events_by_uid
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract all VEVENT blocks
        event_blocks = re.findall(r'BEGIN:VEVENT.*?END:VEVENT', content, re.DOTALL)
        
        for event_block in event_blocks:
            uid = extract_uid(event_block)
            key = uid if uid else event_block  # fallback so we never silently drop an event
            events_by_uid[key] = event_block.strip()
    
    except Exception as e:
        print(f"  Warning: Could not load existing events: {e}")
    
    return events_by_uid


def slugify(text):
    """Convert class name to filename-safe slug"""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def main():
    print("=" * 60)
    print("Spartans Swim Schedule Calendar Generator")
    print("Powered by Anthropic Claude API")
    print("=" * 60)
    
    # Load config
    config = load_config()
    classes = config['classes']
    schedule_url = config['schedule_url']
    timezone = config['timezone']
    
    print(f"Tracking {len(classes)} classes: {', '.join(classes)}")
    print(f"Schedule URL: {schedule_url}")
    
    # Fetch the schedule page
    print("\nFetching schedule page...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    response = requests.get(schedule_url, headers=headers, timeout=30)
    response.raise_for_status()
    html_content = response.text
    
    # Find and download schedule image
    image_url = find_schedule_image_url(html_content)
    image_data, media_type = download_image(image_url)
    
    # Extract schedule using Claude
    schedule_data = extract_schedule_with_claude(image_data, media_type, classes)
    
    # Parse week start
    week_start = parse_week_start(schedule_data['week_start'])
    week_key = week_start.strftime('%Y-%m-%d')
    print(f"\nSchedule week starting: {week_key}")
    
    # --- Duplicate-run guard ---
    # If this week's events are already present in every tracked class's ICS file
    # (e.g. you ran the workflow manually earlier and the scheduled Sunday-night
    # run is now firing too), skip regeneration entirely so we don't waste an API
    # call or touch the files. This checks the REAL committed calendar content
    # (not a separate log file), so it can't get out of sync with what's actually
    # published, and it can't misjudge the rolling window since it's reading the
    # same DTSTART dates the window trim itself uses.
    force = os.environ.get('FORCE_REGENERATE', '').strip().lower() == 'true'
    
    if not force:
        already_processed = all(
            week_has_events_for_class(os.path.join('docs', f"{slugify(c)}.ics"), week_start)
            for c in classes
        )
        if already_processed:
            print(f"\nWeek {week_key} already has events in every tracked class's calendar.")
            print("Skipping regeneration to avoid duplicate events / an unnecessary API call.")
            print("To force it anyway (e.g. to correct a bad prior run), set FORCE_REGENERATE=true.")
            print("\n" + "=" * 60)
            print("Skipped (no changes made) ✅")
            print("=" * 60)
            return
    else:
        print("\nFORCE_REGENERATE=true — regenerating even though this week may already be processed.")
    
    # Generate ICS for each class
    os.makedirs('docs', exist_ok=True)
    
    for class_name in classes:
        print(f"\nGenerating calendar for: {class_name}")
        
        class_schedule = schedule_data['classes'].get(class_name, {})
        
        if not class_schedule:
            print(f"  WARNING: No schedule found for {class_name}")
            continue
        
        # Generate ICS for new week
        ics_content = generate_ics_for_class(class_name, class_schedule, week_start, timezone)
        
        # Load existing events (keyed by UID)
        filename = f"{slugify(class_name)}.ics"
        filepath = os.path.join('docs', filename)
        existing_events_by_uid = load_existing_events(filepath)
        
        # Calculate cutoff (keep events from last 14 days)
        cutoff_date = week_start - timedelta(days=14)
        cutoff_str = cutoff_date.strftime('%Y%m%d')
        
        # Parse newly generated events into a dict keyed by UID.
        # New events always take precedence over old ones with the same UID
        # (this is what prevents duplicates when a manual run and the
        # scheduled run both generate the same week's events).
        lines = ics_content.split('\r\n')
        header_lines = []
        merged_by_uid = {}
        in_event = False
        current_event = []
        
        for line in lines:
            if line.startswith('BEGIN:VEVENT'):
                in_event = True
                current_event = [line]
            elif line.startswith('END:VEVENT'):
                current_event.append(line)
                block = '\r\n'.join(current_event)
                uid = extract_uid(block)
                merged_by_uid[uid if uid else block] = block
                in_event = False
                current_event = []
            elif in_event:
                current_event.append(line)
            elif not line.startswith('END:VCALENDAR'):
                header_lines.append(line)
        
        # Add existing events that are still within the window and not
        # already superseded by a freshly generated event with the same UID.
        for uid, event_block in existing_events_by_uid.items():
            dtstart_match = re.search(r'DTSTART[^:]*:(\d{8})', event_block)
            date_str = dtstart_match.group(1) if dtstart_match else None
            if date_str and date_str >= cutoff_str:
                if uid not in merged_by_uid:
                    merged_by_uid[uid] = event_block
        
        # Rebuild ICS with all events (old within window + new, deduped by UID)
        footer = ["END:VCALENDAR"]
        merged_ics = '\r\n'.join(header_lines + list(merged_by_uid.values()) + footer)
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(merged_ics)
        
        event_count = len(merged_by_uid)
        print(f"  Generated {filename} with {event_count} events (including 2-week history)")
    
    # Record that this week has been processed so a later duplicate run can skip it
    save_last_processed_week(week_key)
    
    print("\n" + "=" * 60)
    print("Done! ✅")
    print("=" * 60)
    print("\nSubscribe URLs:")
    for class_name in classes:
        filename = f"{slugify(class_name)}.ics"
        print(f"  {class_name}: https://bruff85.github.io/swim-schedule-calendar/{filename}")


if __name__ == "__main__":
    main()
