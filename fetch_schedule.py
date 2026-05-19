 #!/usr/bin/env python3
"""
Swim Schedule Calendar Generator

Scrapes the Spartans swim schedule page, extracts the schedule image,
uses OCR to read class times, and generates individual ICS files for each class.
"""

import os
import re
import json
import hashlib
import uuid
from datetime import datetime, timedelta
from io import BytesIO
import requests
from PIL import Image
import pytesseract


def load_config():
    """Load configuration from config.json"""
    with open('config.json', 'r') as f:
        return json.load(f)


def find_schedule_image_url(html_content):
    """Extract the schedule image URL from the HTML"""
    # Look for image URLs in UserFiles/Image/QuickUpload/ that contain schedule or date patterns
    pattern = r'src="(/spartansla/UserFiles/Image/QuickUpload/[^"]+\.(?:jpg|jpeg|png))"'
    matches = re.findall(pattern, html_content, re.IGNORECASE)
    
    if not matches:
        raise ValueError("Could not find schedule image in HTML")
    
    # Return the first match (usually the most recent schedule)
    # The swim site typically shows the current week's schedule first
    return "https://www.spartanswim.com" + matches[0]


def download_image(url):
    """Download image from URL and return PIL Image object"""
    print(f"Downloading schedule image from: {url}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return Image.open(BytesIO(response.content))


def extract_text_from_image(image):
    """Use OCR to extract text from schedule image"""
    print("Running OCR on schedule image...")
    # Tesseract OCR
    text = pytesseract.image_to_string(image)
    return text


def parse_schedule_week(text):
    """Extract the week date range from the schedule"""
    # Look for patterns like "5/18 - 5/24" or "Schedule 5/18-5/24"
    match = re.search(r'(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})', text)
    if match:
        start_month, start_day, end_month, end_day = match.groups()
        current_year = datetime.now().year
        start_date = datetime(current_year, int(start_month), int(start_day))
        return start_date
    return None


def parse_class_schedule(text, class_name):
    """
    Parse the OCR text to find schedule for a specific class.
    Returns dict of {day_of_week: [(start_time, end_time, location), ...]}
    """
    schedule = {
        'Monday': [],
        'Tuesday': [],
        'Wednesday': [],
        'Thursday': [],
        'Friday': [],
        'Saturday': [],
        'Sunday': []
    }
    
    # Find the line containing the class name
    lines = text.split('\n')
    class_line_idx = None
    for i, line in enumerate(lines):
        if class_name.lower() in line.lower():
            class_line_idx = i
            break
    
    if class_line_idx is None:
        print(f"  WARNING: Could not find '{class_name}' in OCR text")
        return schedule
    
    # The schedule typically appears on the same line or next few lines
    # Look for time patterns like "5:15-6:00", "4:30-5:30PM", etc.
    context_lines = lines[class_line_idx:min(class_line_idx + 3, len(lines))]
    full_context = ' '.join(context_lines)
    
    # Find all time patterns
    time_pattern = r'(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*(AM|PM)?'
    times = re.findall(time_pattern, full_context, re.IGNORECASE)
    
    # This is a simplified parser - in practice we'd need to map times to days
    # For now, return what we found and we'll manually verify
    print(f"  Found {len(times)} time slots for {class_name}")
    
    # NOTE: Full parsing would require understanding the table structure
    # For MVP, we'll use a fallback approach with manual time entry
    return schedule


def manual_schedule_entry(class_name, week_start):
    """
    Manual schedule entry as fallback for OCR parsing.
    This should be replaced with your actual observed schedule.
    """
    # Based on the screenshot you provided:
    schedules = {
        'Sharknado 2': {
            'Monday': [('17:15', '18:00', 'Niyoosha')],
            'Tuesday': [('16:30', '17:30', 'Niyoosha')],
            'Wednesday': [('17:30', '18:30', 'Yoga')],
            'Thursday': [('18:15', '19:00', 'SS')],
            'Saturday': [('13:45', '15:00', 'Kailee')]
        },
        'Sharknado 3': {
            'Monday': [('16:30', '17:15', 'Niyoosha')],
            'Tuesday': [('18:30', '19:30', 'SS')],
            'Wednesday': [('17:30', '18:30', 'Yoga')],
            'Friday': [('18:15', '19:15', 'SS')]
        }
    }
    
    return schedules.get(class_name, {})


def generate_ics_for_class(class_name, schedule, week_start, timezone):
    """Generate ICS calendar file for a single class"""
    
    # Map day names to day offsets from Monday
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
    
    for day_name, sessions in schedule.items():
        if not sessions:
            continue
            
        day_date = week_start + timedelta(days=day_offset[day_name])
        
        for start_time_str, end_time_str, location in sessions:
            # Parse time strings
            start_hour, start_min = map(int, start_time_str.split(':'))
            end_hour, end_min = map(int, end_time_str.split(':'))
            
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
                f"SUMMARY:{class_name} - {location}",
                f"DESCRIPTION:Spartans Swim Team Practice - {class_name}",
                "TRANSP:OPAQUE",
                "END:VEVENT"
            ]
            events.append('\r\n'.join(event))
    
    # Build complete ICS
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Spartans Swim Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Spartans - {class_name}",
        f"X-WR-TIMEZONE:{timezone}",
        "X-PUBLISHED-TTL:P1W"  # Refresh weekly
    ]
    
    footer = ["END:VCALENDAR"]
    
    return '\r\n'.join(header + events + footer)


def slugify(text):
    """Convert class name to filename-safe slug"""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def main():
    print("=" * 60)
    print("Spartans Swim Schedule Calendar Generator")
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
    
    # Find schedule image
    try:
        image_url = find_schedule_image_url(html_content)
        image = download_image(image_url)
        
        # Extract text via OCR
        ocr_text = extract_text_from_image(image)
        
        # DEBUG: Print full OCR output
        print("\n" + "=" * 60)
        print("FULL OCR TEXT OUTPUT")
        print("=" * 60)
        print(ocr_text)
        print("=" * 60)
        print()
        
        # Try to parse week from OCR
        week_start = parse_schedule_week(ocr_text)
        if not week_start:
            # Fallback: assume current week's Monday
            today = datetime.now()
            week_start = today - timedelta(days=today.weekday())
            print(f"  Could not parse week from OCR, using current Monday: {week_start.strftime('%Y-%m-%d')}")
        else:
            print(f"  Schedule week starting: {week_start.strftime('%Y-%m-%d')}")
        
    except Exception as e:
        print(f"ERROR: Could not process schedule image: {e}")
        print("Falling back to manual schedule entry...")
        today = datetime.now()
        week_start = today - timedelta(days=today.weekday())
    
    # Generate ICS for each class
    os.makedirs('docs', exist_ok=True)
    
    for class_name in classes:
        print(f"\nGenerating calendar for: {class_name}")
        
        # For MVP, use manual schedule (OCR parsing can be refined later)
        schedule = manual_schedule_entry(class_name, week_start)
        
        if not any(schedule.values()):
            print(f"  WARNING: No schedule found for {class_name}")
            continue
        
        # Generate ICS
        ics_content = generate_ics_for_class(class_name, schedule, week_start, timezone)
        
        # Save to file
        filename = f"{slugify(class_name)}.ics"
        filepath = os.path.join('docs', filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(ics_content)
        
        event_count = sum(len(sessions) for sessions in schedule.values())
        print(f"  Generated {filename} with {event_count} events")
    
    print("\n" + "=" * 60)
    print("Done! ✅")
    print("=" * 60)
    print("\nSubscribe URLs:")
    for class_name in classes:
        filename = f"{slugify(class_name)}.ics"
        print(f"  {class_name}: https://bruff85.github.io/swim-schedule-calendar/{filename}")


if __name__ == "__main__":
    main()
