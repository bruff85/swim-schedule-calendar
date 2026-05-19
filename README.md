# Spartans Swim Schedule Calendar

Automated swim practice schedule calendar generator for Spartans of La Cañada swim team.

## How It Works

1. Every Sunday at 11pm PT, a GitHub Action runs
2. Scrapes the Spartans swim schedule page
3. Uses OCR to extract the schedule from the image
4. Generates individual ICS calendar files for each tracked class
5. Publishes to GitHub Pages for calendar subscription

## Subscribe to Your Class

Each class has its own calendar subscription URL:

- **Sharknado 2**: `https://bruff85.github.io/swim-schedule-calendar/sharknado-2.ics`
- **Sharknado 3**: `https://bruff85.github.io/swim-schedule-calendar/sharknado-3.ics`

### On iPhone/iPad:
1. Open Safari and paste the URL
2. Tap "Subscribe" when prompted
3. The calendar syncs automatically every week

### On Mac:
1. Open Calendar app
2. File → New Calendar Subscription
3. Paste the URL
4. Set refresh frequency to "Every week"

### On Android/Google Calendar:
1. Open Google Calendar settings
2. Add calendar → From URL
3. Paste the `https://` URL (not `webcal://`)

## Adding More Classes

To track additional classes:

1. Edit `config.json`
2. Add the class name to the `classes` array (must match exactly as it appears on the schedule)
3. Commit and push
4. The next run will generate a calendar for that class

## Manual Schedule Updates

The script includes fallback manual schedule entries for when OCR parsing fails. These can be updated in `fetch_schedule.py` in the `manual_schedule_entry()` function.

## Running Manually

Trigger the workflow manually from the **Actions** tab to test or force an immediate update.

---

**Schedule Source**: https://www.spartanswim.com/team/spartansla/page/our-team/practice-schedule-and-monthly-fees
