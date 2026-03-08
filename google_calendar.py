"""
Google Calendar integration for Rotaract Club Performance Tracker.

Setup instructions:
1. Go to https://console.cloud.google.com/
2. Create a new project (or select existing)
3. Enable the Google Calendar API
4. Create OAuth 2.0 credentials (Desktop App)
5. Download and save as 'credentials.json' in this directory
6. First run will open a browser for authentication and save 'token.json'

Set GOOGLE_CALENDAR_ID in your .env file (default: 'primary')
"""

import os
import json
from datetime import datetime, timedelta

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), 'credentials.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'token.json')
SCOPES = ['https://www.googleapis.com/auth/calendar']


def _get_service():
    """Build and return the Google Calendar service, or None if not configured."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        return None

    if not os.path.exists(CREDENTIALS_FILE):
        return None

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def get_upcoming_events(max_results=10, calendar_id=None):
    """
    Fetch upcoming events from Google Calendar.
    Returns a list of event dicts, or empty list if not configured.
    """
    service = _get_service()
    if not service:
        return []

    cal_id = calendar_id or os.environ.get('GOOGLE_CALENDAR_ID', 'primary')
    now = datetime.utcnow().isoformat() + 'Z'

    try:
        result = service.events().list(
            calendarId=cal_id,
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = []
        for item in result.get('items', []):
            start = item['start'].get('dateTime', item['start'].get('date'))
            end = item['end'].get('dateTime', item['end'].get('date'))
            events.append({
                'id': item.get('id'),
                'title': item.get('summary', 'Untitled'),
                'description': item.get('description', ''),
                'location': item.get('location', ''),
                'start': start,
                'end': end,
                'html_link': item.get('htmlLink', ''),
            })
        return events
    except Exception as e:
        print(f'Google Calendar error: {e}')
        return []


def sync_event_to_calendar(event, calendar_id=None):
    """
    Create a Google Calendar event from a local Event model instance.
    Returns the Google event ID on success, None on failure.
    """
    service = _get_service()
    if not service:
        return None

    cal_id = calendar_id or os.environ.get('GOOGLE_CALENDAR_ID', 'primary')

    body = {
        'summary': event.title,
        'description': event.description or '',
        'location': event.location or '',
        'start': {
            'dateTime': event.date.isoformat(),
            'timeZone': 'Asia/Kolkata',
        },
        'end': {
            'dateTime': (event.end_date or (event.date + timedelta(hours=2))).isoformat(),
            'timeZone': 'Asia/Kolkata',
        },
    }

    try:
        result = service.events().insert(calendarId=cal_id, body=body).execute()
        return result.get('id')
    except Exception as e:
        print(f'Failed to sync event to Google Calendar: {e}')
        return None


def is_calendar_connected():
    """Return True if Google Calendar credentials are configured."""
    if not os.path.exists(CREDENTIALS_FILE):
        return False
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        return True
    except ImportError:
        return False
