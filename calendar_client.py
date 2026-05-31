import uuid
import logging
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

import caldav
from icalendar import Calendar, Event
from config import Settings, AccountConfig

# Google API imports
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

logger = logging.getLogger("calendar_mcp.client")

GOOGLE_CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']

def parse_iso_datetime(dt_str: str) -> datetime:
    """Parses ISO-8601 datetime or date string into a timezone-aware datetime object."""
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        try:
            # Fallback to date only
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise ValueError(
                f"Invalid date/time format '{dt_str}'. Please use ISO 8601 "
                "(e.g., YYYY-MM-DDTHH:MM:SSZ, YYYY-MM-DDTHH:MM:SS+HH:MM, or YYYY-MM-DD)"
            ) from e

def serialize_event(event: Any, calendar_name: str, account_name: str) -> Dict[str, Any]:
    """Extracts properties from a CalDAV event and returns a clean dictionary."""
    comp = event.icalendar_component
    
    dtstart = comp.get('dtstart')
    start_val = None
    if dtstart:
        dt_obj = dtstart.dt
        start_val = dt_obj.isoformat() if hasattr(dt_obj, 'isoformat') else str(dt_obj)
        
    dtend = comp.get('dtend')
    end_val = None
    if dtend:
        dt_obj = dtend.dt
        end_val = dt_obj.isoformat() if hasattr(dt_obj, 'isoformat') else str(dt_obj)
        
    return {
        "uid": str(comp.get('uid', '')),
        "url": str(event.url),
        "summary": str(comp.get('summary', '')),
        "description": str(comp.get('description', '')),
        "location": str(comp.get('location', '')),
        "start": start_val,
        "end": end_val,
        "calendar_name": calendar_name,
        "account": account_name,
        "type": "caldav"
    }

def resolve_google_path(path_str: str, profile_dir: Path) -> Path:
    """Helper to resolve Google OAuth credentials or tokens, checking relative paths, profile folders, and fallbacks."""
    p = Path(path_str)
    if p.is_absolute():
        return p
    # 1. Try profile directory
    if (profile_dir / p).exists():
        return profile_dir / p
    # 2. Try email-triage stone profile directory fallback
    triage_stone_dir = Path("/data/homes/stoneshi/src/email-triage/profiles/stone")
    if triage_stone_dir.exists() and (triage_stone_dir / p).exists():
        return triage_stone_dir / p
    # 3. Fallback to workspace root
    workspace_root = Path(__file__).parent.resolve()
    return workspace_root / p

class GoogleCalendarClientWrapper:
    """Helper client wrapping Google Calendar API interactions."""
    def __init__(self, account_config: AccountConfig, profile_dir: Path):
        if not GOOGLE_LIBS_AVAILABLE:
            raise ImportError("Google API client dependencies are not installed. Please check requirements.")
        self.config = account_config
        self.profile_dir = profile_dir
        self.account_name = account_config.google_account or "Google Calendar"
        self.service = None
        self._authenticate()

    def _authenticate(self):
        token_path = resolve_google_path(self.config.token_path, self.profile_dir)
        credentials_path = resolve_google_path(self.config.credentials_path, self.profile_dir)
        target_token_path = self.profile_dir / Path(self.config.token_path).name

        creds = None
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_CALENDAR_SCOPES)
            except Exception as e:
                logger.warning("Failed to load Google credentials from %s: %s", token_path, e)

        # Force re-authentication if the token does not contain calendar scopes
        if creds and not any('calendar' in s for s in creds.scopes):
            logger.warning("Loaded Google OAuth token does not contain calendar scopes. Re-authenticating...")
            creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing Google Calendar access token...")
                    creds.refresh(Request())
                except Exception as e:
                    logger.warning("Silent token refresh failed: %s. Re-running OAuth flow...", e)
                    creds = None
            
            if not creds:
                if not credentials_path.exists():
                    raise FileNotFoundError(
                        f"Google credentials secrets file not found at {credentials_path}. "
                        "Please place your google_cli_client.json credentials in your profile directory."
                    )
                
                logger.info("Initializing OAuth InstalledAppFlow for Google Calendar...")
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GOOGLE_CALENDAR_SCOPES)
                
                # Check for headless OAuth flow redirects
                try:
                    creds = flow.run_local_server(port=0, prompt='select_account')
                except Exception as e:
                    logger.info("Local HTTP redirect server failed (%s). Falling back to console code redirect...", e)
                    flow.redirect_uri = "http://localhost"
                    auth_url, _ = flow.authorization_url(access_type='offline', prompt='select_account')
                    
                    import sys
                    sys.stderr.write(f"\n[GOOGLE CALENDAR OAUTH REQUIRED]: Open this URL:\n👉 {auth_url}\n\n")
                    sys.stderr.write("Grant permissions, copy the landing URL, and paste it below:\n")
                    sys.stderr.write("Redirect URL response: ")
                    sys.stderr.flush()
                    
                    redirect_response = input()
                    flow.fetch_token(authorization_response=redirect_response.strip())
                    creds = flow.credentials

            # Write credentials back
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            with open(target_token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("Saved Google Calendar token to %s", target_token_path)

        self.service = build('calendar', 'v3', credentials=creds, cache_discovery=False)

    def list_calendars(self) -> List[Dict[str, Any]]:
        results = []
        if not self.service:
            return []
        try:
            calendar_list = self.service.calendarList().list().execute()
            for item in calendar_list.get('items', []):
                results.append({
                    "name": item.get("summary") or "Untitled Google Calendar",
                    "url": item["id"],
                    "account": self.account_name
                })
        except Exception as e:
            logger.error("Failed to list Google calendars for %s: %s", self.account_name, e)
        return results

    def search_events(self, start_dt: datetime, end_dt: datetime, query: Optional[str] = None) -> List[Dict[str, Any]]:
        results = []
        if not self.service:
            return []
        try:
            calendar_list = self.service.calendarList().list().execute()
            for cal in calendar_list.get('items', []):
                cal_id = cal['id']
                cal_name = cal.get('summary') or "Untitled Google Calendar"
                try:
                    kwargs = {
                        "calendarId": cal_id,
                        "timeMin": start_dt.isoformat(),
                        "timeMax": end_dt.isoformat(),
                        "singleEvents": True
                    }
                    if query:
                        kwargs["q"] = query
                    
                    events_result = self.service.events().list(**kwargs).execute()
                    for event in events_result.get('items', []):
                        start_info = event.get('start', {})
                        start_val = start_info.get('dateTime') or start_info.get('date')
                        end_info = event.get('end', {})
                        end_val = end_info.get('dateTime') or end_info.get('date')
                        
                        results.append({
                            "uid": event["id"],
                            "url": event.get("htmlLink", ""),
                            "summary": event.get("summary", ""),
                            "description": event.get("description", ""),
                            "location": event.get("location", ""),
                            "start": start_val,
                            "end": end_val,
                            "calendar_name": cal_name,
                            "account": self.account_name,
                            "type": "google"
                        })
                except Exception as e:
                    logger.warning("Failed to search events for Google calendar '%s': %s", cal_name, e)
        except Exception as e:
            logger.error("Failed to list Google calendars list: %s", e)
        return results

    def create_event(
        self,
        cal_id: str,
        summary: str,
        start_dt: datetime,
        end_dt: datetime,
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict[str, Any]:
        body = {
            "summary": summary,
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()}
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        event = self.service.events().insert(calendarId=cal_id, body=body).execute()
        
        start_info = event.get('start', {})
        start_val = start_info.get('dateTime') or start_info.get('date')
        end_info = event.get('end', {})
        end_val = end_info.get('dateTime') or end_info.get('date')

        return {
            "uid": event["id"],
            "url": event.get("htmlLink", ""),
            "summary": event.get("summary", ""),
            "description": event.get("description", ""),
            "location": event.get("location", ""),
            "start": start_val,
            "end": end_val,
            "calendar_name": cal_id,
            "account": self.account_name
        }

    def update_event(
        self,
        cal_id: str,
        event_uid: str,
        summary: Optional[str] = None,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict[str, Any]:
        event = self.service.events().get(calendarId=cal_id, eventId=event_uid).execute()
        
        if summary is not None:
            event["summary"] = summary
        if description is not None:
            event["description"] = description
        if location is not None:
            event["location"] = location
        if start_dt is not None:
            event["start"] = {"dateTime": start_dt.isoformat()}
        if end_dt is not None:
            event["end"] = {"dateTime": end_dt.isoformat()}

        updated = self.service.events().update(calendarId=cal_id, eventId=event_uid, body=event).execute()
        
        start_info = updated.get('start', {})
        start_val = start_info.get('dateTime') or start_info.get('date')
        end_info = updated.get('end', {})
        end_val = end_info.get('dateTime') or end_info.get('date')

        return {
            "uid": updated["id"],
            "url": updated.get("htmlLink", ""),
            "summary": updated.get("summary", ""),
            "description": updated.get("description", ""),
            "location": updated.get("location", ""),
            "start": start_val,
            "end": end_val,
            "calendar_name": cal_id,
            "account": self.account_name
        }

    def delete_event(self, cal_id: str, event_uid: str) -> Dict[str, Any]:
        self.service.events().delete(calendarId=cal_id, eventId=event_uid).execute()
        return {
            "status": "success",
            "message": f"Successfully deleted Google Calendar event '{event_uid}' from calendar '{cal_id}'"
        }

class CalendarClient:
    def __init__(self, settings_instance: Settings):
        self.settings = settings_instance
        self.profile_dir = settings_instance.workspace_dir
        self.accounts = self.settings.accounts

        if not self.accounts:
            raise ValueError("No CalDAV or Google accounts configured. Check settings/env.")

        self.caldav_clients = []
        self.google_clients = []

        for acc in self.accounts:
            if acc.type == "caldav":
                try:
                    if not acc.username or not acc.password:
                        raise ValueError(f"Caldav credentials missing for {acc.name or 'Caldav account'}")
                    cl = caldav.DAVClient(
                        url=acc.url,
                        username=acc.username,
                        password=acc.password
                    )
                    self.caldav_clients.append((acc, cl))
                except Exception as e:
                    logger.error("Failed to initialize CalDAV client for %s: %s", acc.username, e)
            elif acc.type == "google":
                try:
                    g_wrapper = GoogleCalendarClientWrapper(acc, self.profile_dir)
                    self.google_clients.append((acc, g_wrapper))
                except Exception as e:
                    logger.error("Failed to initialize Google Calendar client for %s: %s", acc.google_account, e)

        if not self.caldav_clients and not self.google_clients:
            raise ValueError("Failed to initialize any active CalDAV or Google calendar clients.")

    def list_calendars(self) -> List[Dict[str, Any]]:
        """Lists metadata of all available calendars across all configured accounts."""
        results = []
        # List CalDAV calendars
        for acc, client in self.caldav_clients:
            try:
                principal = client.principal()
                calendars = principal.calendars()
                for cal in calendars:
                    results.append({
                        "name": cal.name or "Untitled Calendar",
                        "url": str(cal.url),
                        "account": acc.username,
                        "type": "caldav"
                    })
            except Exception as e:
                logger.warning("Failed to fetch CalDAV calendars for account '%s': %s", acc.username, e)

        # List Google calendars
        for acc, g_wrapper in self.google_clients:
            try:
                google_cals = g_wrapper.list_calendars()
                for item in google_cals:
                    item["type"] = "google"
                    results.append(item)
            except Exception as e:
                logger.warning("Failed to fetch Google calendars for account '%s': %s", acc.google_account, e)
                
        return results

    def _find_calendar(self, name_or_url: str) -> Tuple[str, Any, Any, Any]:
        """
        Finds a specific calendar object matching name or URL substring.
        Returns Tuple: (type, account_config, client_obj, cal_ref)
        """
        name_or_url_lower = name_or_url.lower().strip()
        all_caldav_calendars = []
        all_google_calendars = []

        # Gather CalDAV
        for acc, client in self.caldav_clients:
            try:
                principal = client.principal()
                calendars = principal.calendars()
                for cal in calendars:
                    all_caldav_calendars.append((acc, client, cal))
            except Exception as e:
                logger.warning("Failed lookup CalDAV: %s", e)

        # Gather Google
        for acc, g_wrapper in self.google_clients:
            try:
                cals = g_wrapper.list_calendars()
                for item in cals:
                    all_google_calendars.append((acc, g_wrapper, item))
            except Exception as e:
                logger.warning("Failed lookup Google: %s", e)

        # 1. Exact match on URL
        for acc, client, cal in all_caldav_calendars:
            if str(cal.url).strip() == name_or_url:
                return "caldav", acc, client, cal
        for acc, g_wrapper, item in all_google_calendars:
            if item["url"] == name_or_url:
                return "google", acc, g_wrapper, item["url"]

        # 2. Substring match on name (exact)
        for acc, client, cal in all_caldav_calendars:
            cal_name = (cal.name or "").lower().strip()
            if cal_name == name_or_url_lower:
                return "caldav", acc, client, cal
        for acc, g_wrapper, item in all_google_calendars:
            cal_name = item["name"].lower().strip()
            if cal_name == name_or_url_lower:
                return "google", acc, g_wrapper, item["url"]

        # 3. Substring match on URL/UUID
        for acc, client, cal in all_caldav_calendars:
            if name_or_url_lower in str(cal.url).lower():
                return "caldav", acc, client, cal
        for acc, g_wrapper, item in all_google_calendars:
            if name_or_url_lower in item["url"].lower():
                return "google", acc, g_wrapper, item["url"]

        # 4. Fallback search (name prefix or substring)
        for acc, client, cal in all_caldav_calendars:
            cal_name = (cal.name or "").lower().strip()
            if name_or_url_lower in cal_name:
                return "caldav", acc, client, cal
        for acc, g_wrapper, item in all_google_calendars:
            cal_name = item["name"].lower().strip()
            if name_or_url_lower in cal_name:
                return "google", acc, g_wrapper, item["url"]

        raise ValueError(f"Calendar matching name or URL '{name_or_url}' not found. Use list_calendars to view available calendars.")

    def search_events(
        self,
        query: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        calendar_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Searches all calendars across configured accounts (CalDAV & Google) for events, optionally filtering by type."""
        start_dt = parse_iso_datetime(start_date) if start_date else datetime.now(timezone.utc) - timedelta(days=30)
        end_dt = parse_iso_datetime(end_date) if end_date else datetime.now(timezone.utc) + timedelta(days=365)
        
        if start_dt >= end_dt:
            raise ValueError("Start date must be earlier than End date.")

        results = []

        # Query CalDAV
        if calendar_type is None or calendar_type.lower() == "caldav":
            for acc, client in self.caldav_clients:
                try:
                    principal = client.principal()
                    calendars = principal.calendars()
                    for cal in calendars:
                        cal_name = cal.name or "Untitled Calendar"
                        try:
                            events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
                            for event in events:
                                serialized = serialize_event(event, cal_name, acc.username)
                                if query:
                                    query_lower = query.lower()
                                    if not (query_lower in serialized["summary"].lower() or 
                                            query_lower in serialized["description"].lower() or 
                                            query_lower in serialized["location"].lower()):
                                        continue
                                results.append(serialized)
                        except Exception as e:
                            logger.warning("Failed to search CalDAV calendar '%s': %s", cal_name, e)
                except Exception as e:
                    logger.warning("Failed to fetch CalDAV calendars: %s", e)

        # Query Google
        if calendar_type is None or calendar_type.lower() == "google":
            for acc, g_wrapper in self.google_clients:
                try:
                    google_events = g_wrapper.search_events(start_dt, end_dt, query)
                    results.extend(google_events)
                except Exception as e:
                    logger.warning("Failed to search Google events for '%s': %s", acc.google_account, e)

        # Sort results by start date
        results.sort(key=lambda x: x["start"] or "")
        return results

    def create_event(
        self,
        calendar_name_or_url: str,
        summary: str,
        start: str,
        end: str,
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates a new calendar event in the target calendar."""
        cal_type, acc, client, cal_ref = self._find_calendar(calendar_name_or_url)
        start_dt = parse_iso_datetime(start)
        end_dt = parse_iso_datetime(end)
        
        if start_dt >= end_dt:
            raise ValueError("Start date/time must be earlier than End date/time.")

        if cal_type == "caldav":
            uid = str(uuid.uuid4())
            ical = Calendar()
            ical.add('prodid', '-//Calendar MCP Server//')
            ical.add('version', '2.0')
            event = Event()
            event.add('summary', summary)
            event.add('dtstart', start_dt)
            event.add('dtend', end_dt)
            event.add('dtstamp', datetime.now(timezone.utc))
            event.add('uid', uid)
            if description:
                event.add('description', description)
            if location:
                event.add('location', location)
            ical.add_component(event)
            ical_str = ical.to_ical().decode('utf-8')
            
            logger.info("Adding new CalDAV event '%s' to calendar '%s'", summary, cal_ref.name)
            new_event = cal_ref.add_event(ical_str)
            return serialize_event(new_event, cal_ref.name or "Untitled Calendar", acc.username)
            
        elif cal_type == "google":
            logger.info("Adding new Google Calendar event '%s' to calendar '%s'", summary, cal_ref)
            return client.create_event(
                cal_id=cal_ref,
                summary=summary,
                start_dt=start_dt,
                end_dt=end_dt,
                description=description,
                location=location
            )

    def update_event(
        self,
        calendar_name_or_url: str,
        event_uid: str,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict[str, Any]:
        """Updates properties of an existing event."""
        cal_type, acc, client, cal_ref = self._find_calendar(calendar_name_or_url)
        
        start_dt = parse_iso_datetime(start) if start else None
        end_dt = parse_iso_datetime(end) if end else None

        if cal_type == "caldav":
            event = cal_ref.get_event_by_uid(event_uid)
            comp = event.icalendar_component
            
            if summary is not None:
                comp['summary'] = summary
            if description is not None:
                comp['description'] = description
            if location is not None:
                comp['location'] = location
                
            # Date checking
            curr_start = comp.get('dtstart').dt if comp.get('dtstart') else None
            curr_end = comp.get('dtend').dt if comp.get('dtend') else None
            eff_start = start_dt if start_dt else curr_start
            eff_end = end_dt if end_dt else curr_end
            if eff_start and eff_end and eff_start >= eff_end:
                raise ValueError("Start date/time must be earlier than End date/time.")
                
            if start_dt:
                comp['dtstart'] = start_dt
            if end_dt:
                comp['dtend'] = end_dt
            comp['dtstamp'] = datetime.now(timezone.utc)
            
            logger.info("Saving CalDAV changes to event '%s'", event_uid)
            event.save()
            return serialize_event(event, cal_ref.name or "Untitled Calendar", acc.username)
            
        elif cal_type == "google":
            logger.info("Saving Google changes to event '%s'", event_uid)
            return client.update_event(
                cal_id=cal_ref,
                event_uid=event_uid,
                summary=summary,
                start_dt=start_dt,
                end_dt=end_dt,
                description=description,
                location=location
            )

    def delete_event(self, calendar_name_or_url: str, event_uid: str) -> Dict[str, Any]:
        """Deletes an event from the target calendar."""
        cal_type, acc, client, cal_ref = self._find_calendar(calendar_name_or_url)

        if cal_type == "caldav":
            event = cal_ref.get_event_by_uid(event_uid)
            serialized_details = serialize_event(event, cal_ref.name or "Untitled Calendar", acc.username)
            logger.info("Deleting CalDAV event '%s'", event_uid)
            event.delete()
            return {
                "status": "success",
                "message": f"Successfully deleted CalDAV event '{serialized_details['summary']}'",
                "deleted_event": serialized_details
            }
        elif cal_type == "google":
            logger.info("Deleting Google event '%s'", event_uid)
            return client.delete_event(cal_id=cal_ref, event_uid=event_uid)
