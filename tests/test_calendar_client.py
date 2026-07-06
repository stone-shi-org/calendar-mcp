import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock, PropertyMock

import pytest

from calendar_client import (
    parse_iso_datetime,
    serialize_event,
    resolve_google_path,
    CalendarClient,
    GoogleCalendarClientWrapper,
)
from config import Settings, AccountConfig


# ---------------------------------------------------------------------------
# parse_iso_datetime
# ---------------------------------------------------------------------------

class TestParseIsoDatetime:
    def test_utc_z_suffix(self):
        dt = parse_iso_datetime("2025-06-15T10:30:00Z")
        assert dt.tzinfo == timezone.utc
        assert dt == datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_with_offset(self):
        dt = parse_iso_datetime("2025-06-15T10:30:00+05:00")
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(hours=5)

    def test_date_only(self):
        dt = parse_iso_datetime("2025-06-15")
        assert dt.tzinfo == timezone.utc
        assert dt == datetime(2025, 6, 15, tzinfo=timezone.utc)

    def test_naive_datetime_becomes_utc(self):
        dt = parse_iso_datetime("2025-06-15T10:30:00")
        assert dt.tzinfo == timezone.utc

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid date/time format"):
            parse_iso_datetime("not-a-date")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_iso_datetime("")


# ---------------------------------------------------------------------------
# serialize_event
# ---------------------------------------------------------------------------

class TestSerializeEvent:
    @pytest.fixture
    def mock_event(self):
        from icalendar import Event as ICalEvent
        import caldav

        comp = ICalEvent()
        comp.add("uid", "evt-001")
        comp.add("summary", "Test Event")
        comp.add("description", "A description")
        comp.add("location", "Conference Room")
        comp.add("dtstart", datetime(2025, 6, 15, 9, 0, 0, tzinfo=timezone.utc))
        comp.add("dtend", datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc))

        event = MagicMock(spec=caldav.Event)
        event.icalendar_component = comp
        event.url = "/cal/dav/evt-001.ics"
        return event

    def test_basic_serialization(self, mock_event):
        result = serialize_event(mock_event, "My Calendar", "me@example.com")
        assert result["uid"] == "evt-001"
        assert result["summary"] == "Test Event"
        assert result["description"] == "A description"
        assert result["location"] == "Conference Room"
        assert result["start"] is not None
        assert result["end"] is not None
        assert result["calendar_name"] == "My Calendar"
        assert result["account"] == "me@example.com"
        assert result["type"] == "caldav"

    def test_missing_fields_default_to_empty(self):
        from icalendar import Calendar as ICal, Event as ICalEvent
        import caldav

        comp = ICalEvent()
        comp.add("uid", "evt-002")
        comp.add("dtstart", datetime(2025, 6, 15, 9, 0, 0, tzinfo=timezone.utc))

        event = MagicMock(spec=caldav.Event)
        event.icalendar_component = comp
        event.url = "/cal/dav/evt-002.ics"

        result = serialize_event(event, "Cal", "acc")
        assert result["summary"] == ""
        assert result["description"] == ""
        assert result["location"] == ""


# ---------------------------------------------------------------------------
# resolve_google_path
# ---------------------------------------------------------------------------

class TestResolveGooglePath:
    def test_absolute_path_returned_as_is(self):
        p = resolve_google_path("/etc/passwd", Path("/tmp"))
        assert p == Path("/etc/passwd")

    def test_profile_dir_takes_priority(self, temp_dir):
        cred_file = temp_dir / "google_cli_client.json"
        cred_file.write_text("{}")
        result = resolve_google_path("google_cli_client.json", temp_dir)
        assert result == cred_file

    def test_workspace_root_fallback(self, temp_dir):
        result = resolve_google_path("nonexistent.json", temp_dir)
        expected = Path(__file__).parent.parent.resolve() / "nonexistent.json"
        assert result == expected

    def test_triage_fallback(self, temp_dir):
        original_exists = Path.exists
        def mocked_exists(path_self):
            path_str = str(path_self)
            if "email-triage" in path_str:
                return True
            if str(temp_dir) in path_str:
                return False
            return original_exists(path_self)
        with patch.object(Path, "exists", mocked_exists):
            result = resolve_google_path("google_cli_client.json", temp_dir)
            assert "email-triage" in str(result)


# ---------------------------------------------------------------------------
# GoogleCalendarClientWrapper (mocked)
# ---------------------------------------------------------------------------

class TestGoogleCalendarClientWrapper:
    @pytest.fixture
    def account_config(self):
        return AccountConfig(
            type="google",
            google_account="test@gmail.com",
            token_path="token.json",
            credentials_path="creds.json",
        )

    @pytest.fixture
    def mock_service(self):
        service = MagicMock()
        # mock calendarList().list().execute()
        cal_list_mock = MagicMock()
        cal_list_mock.execute.return_value = {
            "items": [
                {"id": "cal-1", "summary": "Primary"},
                {"id": "cal-2", "summary": "Work"},
            ]
        }
        service.calendarList.return_value.list.return_value = cal_list_mock

        # mock events().list().execute()
        events_result = MagicMock()
        events_result.execute.return_value = {
            "items": [
                {
                    "id": "evt-001",
                    "summary": "Meeting",
                    "description": "Discuss project",
                    "location": "Room 1",
                    "htmlLink": "https://calendar.google.com/event?id=evt-001",
                    "start": {"dateTime": "2025-06-15T09:00:00+00:00"},
                    "end": {"dateTime": "2025-06-15T10:00:00+00:00"},
                }
            ]
        }
        service.events.return_value.list.return_value = events_result

        # mock events().insert().execute()
        insert_result = MagicMock()
        insert_result.execute.return_value = {
            "id": "evt-new",
            "summary": "New Event",
            "description": "desc",
            "location": "loc",
            "htmlLink": "https://calendar.google.com/event?id=evt-new",
            "start": {"dateTime": "2025-07-01T09:00:00+00:00"},
            "end": {"dateTime": "2025-07-01T10:00:00+00:00"},
        }
        service.events.return_value.insert.return_value = insert_result

        # mock events().get().execute()
        get_result = MagicMock()
        get_result.execute.return_value = {
            "id": "evt-001",
            "summary": "Original Summary",
            "description": "",
            "location": "",
            "htmlLink": "",
            "start": {"dateTime": "2025-06-15T09:00:00+00:00"},
            "end": {"dateTime": "2025-06-15T10:00:00+00:00"},
        }
        service.events.return_value.get.return_value = get_result

        # mock events().update().execute()
        update_result = MagicMock()
        update_result.execute.return_value = {
            "id": "evt-001",
            "summary": "Updated Summary",
            "description": "Updated desc",
            "location": "Updated loc",
            "htmlLink": "",
            "start": {"dateTime": "2025-06-16T09:00:00+00:00"},
            "end": {"dateTime": "2025-06-16T10:00:00+00:00"},
        }
        service.events.return_value.update.return_value = update_result

        # mock events().delete().execute()
        delete_result = MagicMock()
        delete_result.execute.return_value = None
        service.events.return_value.delete.return_value = delete_result

        return service

    @pytest.fixture
    def wrapper(self, account_config, temp_dir, mock_service):
        with patch("calendar_client.GoogleCalendarClientWrapper._authenticate"):
            with patch("calendar_client.build", return_value=mock_service):
                w = GoogleCalendarClientWrapper(account_config, temp_dir)
                w.service = mock_service
                return w

    def test_list_calendars(self, wrapper):
        cals = wrapper.list_calendars()
        assert len(cals) == 2
        assert cals[0]["name"] == "Primary"
        assert cals[0]["url"] == "cal-1"
        assert cals[0]["account"] == "test@gmail.com"

    def test_search_events(self, wrapper):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 12, 31, tzinfo=timezone.utc)
        results = wrapper.search_events(start, end)
        # 2 calendars each return the same event
        assert len(results) == 2
        assert results[0]["summary"] == "Meeting"
        assert results[0]["type"] == "google"

    def test_search_events_with_query(self, wrapper):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 12, 31, tzinfo=timezone.utc)
        results = wrapper.search_events(start, end, query="Meeting")
        assert len(results) == 2

    def test_create_event(self, wrapper):
        start = datetime(2025, 7, 1, 9, 0, tzinfo=timezone.utc)
        end = datetime(2025, 7, 1, 10, 0, tzinfo=timezone.utc)
        result = wrapper.create_event("cal-1", "New Event", start, end)
        assert result["uid"] == "evt-new"
        assert result["summary"] == "New Event"

    def test_update_event(self, wrapper):
        start = datetime(2025, 6, 16, 9, 0, tzinfo=timezone.utc)
        end = datetime(2025, 6, 16, 10, 0, tzinfo=timezone.utc)
        result = wrapper.update_event("cal-1", "evt-001", summary="Updated Summary", start_dt=start, end_dt=end)
        assert result["summary"] == "Updated Summary"

    def test_delete_event(self, wrapper):
        result = wrapper.delete_event("cal-1", "evt-001")
        assert result["status"] == "success"

    def test_no_service_returns_empty(self, account_config, temp_dir):
        with patch("calendar_client.GoogleCalendarClientWrapper._authenticate"):
            w = GoogleCalendarClientWrapper(account_config, temp_dir)
            w.service = None
            assert w.list_calendars() == []
            assert w.search_events(
                datetime.now(timezone.utc), datetime.now(timezone.utc)
            ) == []

    def test_raises_if_no_google_libs(self, account_config, temp_dir):
        with patch("calendar_client.GOOGLE_LIBS_AVAILABLE", False):
            with pytest.raises(ImportError):
                GoogleCalendarClientWrapper(account_config, temp_dir)


# ---------------------------------------------------------------------------
# CalendarClient (mocked)
# ---------------------------------------------------------------------------

class TestCalendarClientInit:
    def test_raises_if_no_accounts(self, clean_env):
        s = Settings()
        with pytest.raises(ValueError, match="No CalDAV or Google accounts configured"):
            CalendarClient(s)

    def test_raises_if_all_clients_fail(self, clean_env):
        with patch.dict(os.environ, {"CALENDAR_ACCOUNTS": '[{"type": "caldav", "username": "", "password": ""}]'}):
            s = Settings()
            with pytest.raises(ValueError, match="Failed to initialize any"):
                CalendarClient(s)


class TestCalendarClientListCalendars:
    @pytest.fixture
    def mock_caldav_client(self):
        acc = AccountConfig(type="caldav", username="cal@me.com", password="pw", url="https://cal.example.com")
        client = MagicMock()
        principal = MagicMock()
        cal1 = MagicMock()
        cal1.name = "Personal"
        cal1.url = "/cal/personal/"
        cal2 = MagicMock()
        cal2.name = "Work"
        cal2.url = "/cal/work/"
        principal.calendars.return_value = [cal1, cal2]
        client.principal.return_value = principal
        return acc, client

    @pytest.fixture
    def mock_google_wrapper(self):
        acc = AccountConfig(type="google", google_account="g@me.com")
        wrapper = MagicMock()
        wrapper.list_calendars.return_value = [
            {"name": "Google Cal", "url": "google-cal-id", "account": "g@me.com"}
        ]
        return acc, wrapper

    def test_aggregates_caldav_and_google(self, clean_env, mock_caldav_client, mock_google_wrapper):
        s = Settings(username="dummy", password="dummy")
        cc = CalendarClient.__new__(CalendarClient)
        cc.settings = s
        cc.profile_dir = Path("/tmp")
        cc.accounts = [mock_caldav_client[0], mock_google_wrapper[0]]
        cc.caldav_clients = [mock_caldav_client]
        cc.google_clients = [mock_google_wrapper]

        results = cc.list_calendars()
        assert len(results) == 3
        types = [r["type"] for r in results]
        assert types.count("caldav") == 2
        assert types.count("google") == 1

    def test_error_isolation(self, clean_env, mock_caldav_client):
        bad_client = MagicMock()
        bad_client.principal.side_effect = Exception("Network error")
        bad_acc = AccountConfig(type="caldav", username="bad@me.com", password="pw")

        s = Settings(username="dummy", password="dummy")
        cc = CalendarClient.__new__(CalendarClient)
        cc.settings = s
        cc.profile_dir = Path("/tmp")
        cc.accounts = [mock_caldav_client[0], bad_acc]
        cc.caldav_clients = [mock_caldav_client, (bad_acc, bad_client)]
        cc.google_clients = []

        results = cc.list_calendars()
        # The good client should still return its calendars
        assert len(results) == 2


class TestCalendarClientFindCalendar:
    @pytest.fixture
    def cc(self, clean_env):
        s = Settings(username="u", password="p")
        cc = CalendarClient.__new__(CalendarClient)
        cc.settings = s
        cc.profile_dir = Path("/tmp")
        return cc

    def test_exact_url_match_caldav(self, cc):
        acc = AccountConfig(type="caldav", username="u", password="p")
        client = MagicMock()
        principal = MagicMock()
        cal = MagicMock()
        cal.name = "My Calendar"
        cal.url = "/cal/unique-id/"
        principal.calendars.return_value = [cal]
        client.principal.return_value = principal
        cc.caldav_clients = [(acc, client)]
        cc.google_clients = []

        result = cc._find_calendar("/cal/unique-id/")
        assert result[0] == "caldav"
        assert result[3] is cal

    def test_substring_name_match(self, cc):
        acc = AccountConfig(type="caldav", username="u", password="p")
        client = MagicMock()
        principal = MagicMock()
        cal = MagicMock()
        cal.name = "Personal Calendar"
        cal.url = "/cal/personal/"
        principal.calendars.return_value = [cal]
        client.principal.return_value = principal
        cc.caldav_clients = [(acc, client)]
        cc.google_clients = []

        result = cc._find_calendar("personal")
        assert result[0] == "caldav"
        assert result[3] is cal

    def test_google_match(self, cc):
        acc = AccountConfig(type="google", google_account="g@me.com")
        wrapper = MagicMock()
        wrapper.list_calendars.return_value = [
            {"name": "Work Calendar", "url": "work-cal-id", "account": "g@me.com"}
        ]
        cc.caldav_clients = []
        cc.google_clients = [(acc, wrapper)]

        result = cc._find_calendar("work-cal-id")
        assert result[0] == "google"
        assert result[3] == "work-cal-id"

    def test_not_found_raises(self, cc):
        cc.caldav_clients = []
        cc.google_clients = []
        with pytest.raises(ValueError, match="not found"):
            cc._find_calendar("nonexistent")


class TestCalendarClientSearchEvents:
    @pytest.fixture
    def cc_with_mocks(self, clean_env):
        s = Settings(username="u", password="p")
        cc = CalendarClient.__new__(CalendarClient)
        cc.settings = s
        cc.profile_dir = Path("/tmp")

        # CalDAV
        cal_acc = AccountConfig(type="caldav", username="cal@me.com", password="pw")
        cal_client = MagicMock()
        principal = MagicMock()
        cal = MagicMock()
        cal.name = "Test Cal"
        cal.search.return_value = []
        principal.calendars.return_value = [cal]
        cal_client.principal.return_value = principal
        cc.caldav_clients = [(cal_acc, cal_client)]

        # Google
        google_acc = AccountConfig(type="google", google_account="g@me.com")
        g_wrapper = MagicMock()
        g_wrapper.search_events.return_value = []
        cc.google_clients = [(google_acc, g_wrapper)]

        # Store refs for assertions
        cc._cal = cal
        cc._g_wrapper = g_wrapper
        return cc

    def test_default_date_range(self, cc_with_mocks):
        with patch("calendar_client.parse_iso_datetime", side_effect=lambda x: (
            datetime.now(timezone.utc) + timedelta(days=-30) if x is None else
            datetime.now(timezone.utc) + timedelta(days=365)
        )):
            pass
        results = cc_with_mocks.search_events()
        assert results == []

    def test_start_after_end_raises(self, cc_with_mocks):
        with pytest.raises(ValueError, match="Start date must be earlier"):
            cc_with_mocks.search_events(
                start_date="2025-12-31",
                end_date="2025-01-01"
            )

    def test_filter_by_type(self, cc_with_mocks):
        cc_with_mocks.search_events(calendar_type="caldav")
        cc_with_mocks._g_wrapper.search_events.assert_not_called()

        cc_with_mocks._cal.search.reset_mock()
        cc_with_mocks.search_events(calendar_type="google")
        cc_with_mocks._cal.search.assert_not_called()


class TestCalendarClientCreateEvent:
    def test_start_after_end_raises(self, clean_env):
        s = Settings(username="u", password="p")
        cc = CalendarClient.__new__(CalendarClient)
        cc.settings = s
        cc.profile_dir = Path("/tmp")
        cc.caldav_clients = []
        cc.google_clients = []

        with patch.object(CalendarClient, "_find_calendar", return_value=(
            "caldav",
            AccountConfig(type="caldav", username="u", password="p"),
            MagicMock(),
            MagicMock(),
        )):
            with pytest.raises(ValueError, match="Start date/time must be earlier"):
                cc.create_event(
                    calendar_name_or_url="any",
                    summary="Test",
                    start="2025-12-31T23:00:00Z",
                    end="2025-01-01T01:00:00Z"
                )


class TestCalendarClientUpdateEvent:
    def test_caldav_invalid_dates_raises(self, clean_env):
        s = Settings(username="u", password="p")
        cc = CalendarClient.__new__(CalendarClient)
        cc.settings = s
        cc.profile_dir = Path("/tmp")

        acc = AccountConfig(type="caldav", username="u", password="p")
        client = MagicMock()
        principal = MagicMock()
        cal = MagicMock()
        cal.name = "Cal"
        cal.url = "/cal/1/"
        principal.calendars.return_value = [cal]
        client.principal.return_value = principal
        cc.caldav_clients = [(acc, client)]
        cc.google_clients = []

        with patch.object(CalendarClient, "_find_calendar", return_value=("caldav", acc, client, cal)):
            with pytest.raises(ValueError, match="Start date/time must be earlier"):
                cc.update_event("Cal", "evt-1", start="2025-12-31", end="2025-01-01")


import os  # needed for clean_env
