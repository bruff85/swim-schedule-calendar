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
    
    # Build the prompt
    classes_list = ', '.join(classes)
    prompt = f"""Please analyze this swim practice schedule image and extract the practice times for these specific classes: {classes_list}

For each class, I need:
- The week date range (e.g., "5/18 - 5/24")
- For each day of the week (Monday through Sunday), list all practice sessions with:
  - Start time
  - End time  
  - Coach/Location name (the text in each cell like "Niyoosha", "SS", "Yoga", "Kailee", etc.)
  - If a day shows "OFF", skip it (no practice that day)

Please respond with ONLY valid JSON in this exact format:

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

Important: 
- Use 24-hour time format (e.g., 17:15 not 5:15 PM)
- Empty array [] for days with no practice
- Only include the classes I listed above
- Return ONLY the JSON, no other text"""

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
                "model": "claude-sonnet-4-20250514",
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
    return schedule_data


def parse_week_start(week_start_str):
    """Parse week start date from string like '5/18/2026'"""
    try:
        return datetime.strptime(week_start_str, '%m/%d/%Y')
    except ValueError:
        # Fallback to current Monday
        today = datetime.now()
        return today - timedelta(days=today.weekday())


def generate_ics_for_class(class_name, schedule, week_start, timezone):
    """Generate ICS calendar file for a single class"""
    
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
            
        day_date = week_start + timedelta(days=day_offset.get(day_name, 0))
        
        for session in sessions:
            start_time_str = session['start']
            end_time_str = session['end']
            coach = session.get('coach', 'Practice')
            
            # Parse time strings (already in 24-hour format like "17:15")
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
                f"SUMMARY:{class_name} - {coach}",
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
        "X-PUBLISHED-TTL:P1W"
    ]
    
    footer = ["END:VCALENDAR"]
    
    return '\r\n'.join(header + events + footer)


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
        
        # Generate ICS
        ics_content = generate_ics_for_class(class_name, class_schedule, week_start, timezone)
        
        # Save to file
        filename = f"{slugify(class_name)}.ics"
        filepath = os.path.join('docs', filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(ics_content)
        
        event_count = sum(len(sessions) for sessions in class_schedule.values())
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
