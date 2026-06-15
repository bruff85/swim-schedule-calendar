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


def load_config():
    """Load configuration from config.json"""
    with open('config.json', 'r') as f:
        return json.load(f)


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
  - Start time (ALWAYS afternoon/evening, 14:00 or later in 24-hour format)
  - End time
  - Coach/Location name (the text in each cell like "Niyoosha", "SS", "Yoga", "Kailee", etc.)
  - If a day shows "OFF", skip it (no practice that day)

IMPORTANT CONSTRAINTS:
- Swim practice times are NEVER before 2 PM (14:00)
- If you see "AM" times or times before 14:00, REJECT THE ENTIRE SCHEDULE and respond with: {{"error": "Schedule contains invalid AM times"}}
- Use 24-hour format ONLY (14:30 for 2:30 PM, never "2:30 AM")

Please respond with ONLY valid JSON in this exact format (unless there's an error):

{{
  "week_start": "5/18/2026",
  "classes": {{
    "Sharknado 2": {{
      "Monday": [{{"start": "17:15", "end": "18:00", "coach": "Niyoosha"}}],
      "Tuesday": [{{"start": "16:30", "end": "17:30", "coach": "Niyoosha"}}],
      "Wednesday": [{{"start": "17:30", "end": "18:30", "coach": "Yoga"}}],
      "Thursday": [{{"start": "18:15", "end": "19:00", "coach": "SS"}}],
      "Friday": [],
      "Saturday": [{{"start": "13:45", "end": "15:00", "coach": "Kailee"}}],
      "Sunday": []
    }},
    "Sharknado 3": {{
      "Monday": [{{"start": "16:30", "end": "17:15", "coach": "Niyoosha"}}],
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
                "model": "claude-opus-4-1",
                "max_tokens": 2000,
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
    
    # Parse JSON from response
    # Remove markdown code blocks if present
    json_text = response_text.strip()
    if json_text.startswith('```'):
        # Remove ```json and ``` markers
        json_text = re.sub(r'^```json\s*', '', json_text)
        json_text = re.sub(r'```\s*$', '', json_text)
        json_text = json_text.strip()
    
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
            
            # Parse time strings (already in 24-hour format like "17:15")
            start_hour, start_min = map(int, start_time_str.split(':'))
            end_hour, end_min = map(int, end_time_str.split(':'))
            
            # VALIDATION: Swim practice should NEVER be before 2 PM (14:00)
            # Reject if start time is in early morning/AM
            if start_hour < 14:
                print(f"  WARNING: Rejecting unrealistic practice time {start_time_str} for {coach} on {day_name}")
                print(f"    Swim practice times should be PM (14:00 or later, 24-hour format)")
                continue
            
            start_dt = day_date.replace(hour=start_hour, minute=start_min, second=0)
            end_dt = day_date.replace(hour=end_hour, minute=end_min, second=0)
            
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
                f"DESCRIPTION:Spartans Swim Team Practice - {class_name}",
                "TRANSP:OPAQUE",
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


def load_existing_events(filepath):
    """Load events from existing ICS file"""
    events_by_date = {}
    
    if not os.path.exists(filepath):
        return events_by_date
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract all VEVENT blocks
        event_blocks = re.findall(r'BEGIN:VEVENT.*?END:VEVENT', content, re.DOTALL)
        
        for event_block in event_blocks:
            # Extract DTSTART date
            dtstart_match = re.search(r'DTSTART[^:]*:(\d{8})', event_block)
            if dtstart_match:
                date_str = dtstart_match.group(1)
                events_by_date[date_str] = event_block.strip()
    
    except Exception as e:
        print(f"  Warning: Could not load existing events: {e}")
    
    return events_by_date


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
    print(f"\nSchedule week starting: {week_start.strftime('%Y-%m-%d')}")
    
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
        
        # Load existing events
        filename = f"{slugify(class_name)}.ics"
        filepath = os.path.join('docs', filename)
        existing_events = load_existing_events(filepath)
        
        # Calculate cutoff (keep events from last 14 days)
        cutoff_date = week_start - timedelta(days=14)
        cutoff_str = cutoff_date.strftime('%Y%m%d')
        
        # Parse existing events and filter to keep only recent ones
        lines = ics_content.split('\r\n')
        header_lines = []
        event_lines = []
        in_event = False
        current_event = []
        
        for line in lines:
            if line.startswith('BEGIN:VEVENT'):
                in_event = True
                current_event = [line]
            elif line.startswith('END:VEVENT'):
                current_event.append(line)
                event_lines.append('\r\n'.join(current_event))
                in_event = False
                current_event = []
            elif in_event:
                current_event.append(line)
            elif not in_event and not line.startswith('END:VCALENDAR'):
                header_lines.append(line)
        
        # Add existing events that are still within the window
        for date_str, event_block in existing_events.items():
            if date_str >= cutoff_str:  # Keep if after cutoff date
                # Check if this event is already in new events
                if event_block not in event_lines:
                    event_lines.append(event_block)
        
        # Rebuild ICS with all events (old within window + new)
        footer = ["END:VCALENDAR"]
        merged_ics = '\r\n'.join(header_lines + event_lines + footer)
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(merged_ics)
        
        event_count = len(event_lines)
        print(f"  Generated {filename} with {event_count} events (including 2-week history)")
    
    print("\n" + "=" * 60)
    print("Done! ✅")
    print("=" * 60)
    print("\nSubscribe URLs:")
    for class_name in classes:
        filename = f"{slugify(class_name)}.ics"
        print(f"  {class_name}: https://bruff85.github.io/swim-schedule-calendar/{filename}")


if __name__ == "__main__":
    main()
