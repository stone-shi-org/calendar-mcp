#!/usr/bin/env python3
"""
Simple CLI utility to test the mixed iCloud/Google connection and credentials.
"""

import sys
import logging
from pathlib import Path

# Setup logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

from config import Settings
from calendar_client import CalendarClient

def main():
    profile = "default"
    if len(sys.argv) > 1:
        profile = sys.argv[1]

    print(f"==================================================")
    print(f"Testing Calendar connection for profile: '{profile}'")
    print(f"==================================================")

    try:
        # Load profile settings
        settings = Settings.load_for_profile(profile)
        print(f"Config Source Folder: {settings.workspace_dir}")
        print("Configured Accounts:")
        for idx, acc in enumerate(settings.accounts, 1):
            if acc.type == "caldav":
                print(f"  {idx}. CalDAV: {acc.username} at {acc.url}")
            elif acc.type == "google":
                print(f"  {idx}. Google: {acc.google_account} (creds: {acc.credentials_path})")
        print("--------------------------------------------------")

        if not settings.accounts:
            print("[ERROR] No accounts configured. Please update your .env file or profile config.")
            sys.exit(1)

        print("Connecting to Calendar servers...")
        client = CalendarClient(settings_instance=settings)
        
        # Test 1: List calendars
        calendars = client.list_calendars()
        print(f"\n[SUCCESS] Connected! Found {len(calendars)} calendars across all accounts:")
        for cal in calendars:
            print(f"  - [{cal['type'].upper()}] {cal['name']}")
            print(f"    URL/ID:  {cal['url']}")
            print(f"    Account: {cal['account']}")
            print()
            
        # Test 2: Search events in default range
        print("Fetching events in default range...")
        events = client.search_events()
        print(f"[SUCCESS] Retrieved {len(events)} events.")
        
        if events:
            print("\nLatest 5 events:")
            for e in events[:5]:
                print(f"  * [{e['calendar_name']} - {e['account']}] {e['summary']}")
                print(f"    Time: {e['start']} to {e['end']}")
                print(f"    UID:  {e['uid']}")
                print(f"    Loc:  {e['location'] or 'None'}")
                print(f"    Desc: {e['description'][:60] if e['description'] else 'None'}...")
                print()

    except Exception as e:
        print(f"\n[ERROR] Connection failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
