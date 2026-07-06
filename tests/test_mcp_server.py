import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock, mock_open

import pytest

from mcp_server import (
    RobustFastMCP,
    load_token_profile_map,
    MCPTokenAuthMiddleware,
    get_resources,
    get_version_info,
    current_profile,
)


# ---------------------------------------------------------------------------
# RobustFastMCP
# ---------------------------------------------------------------------------

class TestRobustFastMCP:
    @pytest.fixture
    def mcp_instance(self):
        return RobustFastMCP("test")

    @pytest.mark.asyncio
    async def test_strips_calendar_mcp_prefix(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True}):
            with patch("mcp.server.fastmcp.FastMCP.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("calendar_mcp__list_calendars", {})
                super_call.assert_called_once_with("list_calendars", {})

    @pytest.mark.asyncio
    async def test_strips_calendar_mcp_hyphen_prefix(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True}):
            with patch("mcp.server.fastmcp.FastMCP.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("calendar-mcp__list_calendars", {})
                super_call.assert_called_once_with("list_calendars", {})

    @pytest.mark.asyncio
    async def test_fuzzy_matches_prefix(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True, "search_events": True}):
            with patch("mcp.server.fastmcp.FastMCP.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("list_cal", {})
                super_call.assert_called_once_with("list_calendars", {})

    @pytest.mark.asyncio
    async def test_passes_through_if_no_match(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True}):
            with patch("mcp.server.fastmcp.FastMCP.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("other_tool", {})
                super_call.assert_called_once_with("other_tool", {})


# ---------------------------------------------------------------------------
# load_token_profile_map
# ---------------------------------------------------------------------------

class TestLoadTokenProfileMap:
    def test_parses_root_env(self, temp_dir):
        env_content = (
            "CALENDAR_PROFILE_TOKEN=root-token\n"
            "CALENDAR_URL=https://caldav.icloud.com/\n"
        )
        profiles_dir = temp_dir / "profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(Path, "parent") as parent_mock:
            parent_mock.resolve.return_value = temp_dir
            with patch.object(Path, "exists", return_value=True):
                with patch("builtins.open", mock_open(read_data=env_content)):
                    result = load_token_profile_map()
                    assert result.get("root-token") == "default"

    def test_parses_profile_envs(self, temp_dir):
        profiles_dir = temp_dir / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "stone").mkdir()
        (profiles_dir / "work").mkdir()
        (profiles_dir / "stone" / ".env").write_text("CALENDAR_PROFILE_TOKEN=stone-token\n")
        (profiles_dir / "work" / ".env").write_text("CALENDAR_PROFILE_TOKEN=work-token\n")

        with patch.object(Path, "parent") as parent_mock:
            parent_mock.resolve.return_value = temp_dir
            result = load_token_profile_map()
            assert result.get("stone-token") == "stone"
            assert result.get("work-token") == "work"

    def test_handles_missing_root_env(self, temp_dir):
        profiles_dir = temp_dir / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "prof").mkdir()
        (profiles_dir / "prof" / ".env").write_text("CALENDAR_PROFILE_TOKEN=prof-token\n")

        with patch.object(Path, "parent") as parent_mock:
            parent_mock.resolve.return_value = temp_dir
            result = load_token_profile_map()
            assert result.get("prof-token") == "prof"

    def test_skips_lines_without_equals(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text(
            "CALENDAR_PROFILE_TOKEN=valid\n"
            "MALFORMED_LINE\n"
        )
        profiles_dir = temp_dir / "profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(Path, "parent") as parent_mock:
            parent_mock.resolve.return_value = temp_dir
            result = load_token_profile_map()
            assert result.get("valid") == "default"


# ---------------------------------------------------------------------------
# MCPTokenAuthMiddleware
# ---------------------------------------------------------------------------

class TestMCPTokenAuthMiddleware:
    @pytest.fixture
    def token_map(self):
        return {"abc123": "default", "def456": "stone"}

    @pytest.fixture
    def mock_load_token_map(self, token_map):
        with patch("mcp_server.load_token_profile_map", return_value=token_map) as m:
            yield m

    @pytest.mark.asyncio
    async def test_authorizes_with_bearer_token(self, token_map, mock_load_token_map):
        app = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/sse",
            "headers": [(b"authorization", b"Bearer abc123")],
            "query_string": b"",
        }
        await middleware(scope, None, AsyncMock())
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_authorizes_with_x_profile_token(self, token_map, mock_load_token_map):
        app = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/sse",
            "headers": [(b"x-profile-token", b"def456")],
            "query_string": b"",
        }
        await middleware(scope, None, AsyncMock())
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_authorizes_with_query_token(self, token_map, mock_load_token_map):
        app = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/sse",
            "headers": [],
            "query_string": b"token=abc123",
        }
        await middleware(scope, None, AsyncMock())
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_invalid_token(self, token_map, mock_load_token_map):
        app = AsyncMock()
        send = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/sse",
            "headers": [(b"authorization", b"Bearer invalid-token")],
            "query_string": b"",
        }
        await middleware(scope, None, send)

        # Should have sent 401 response
        send.assert_called()
        app.assert_not_called()

    @pytest.mark.asyncio
    async def test_sets_profile_context_var(self, token_map, mock_load_token_map):
        context_values = []

        async def check_context(scope, receive, send):
            context_values.append(current_profile.get())
            await send({})

        middleware = MCPTokenAuthMiddleware(check_context, token_map)

        scope = {
            "type": "http",
            "path": "/sse",
            "headers": [(b"authorization", b"Bearer def456")],
            "query_string": b"",
        }
        await middleware(scope, None, AsyncMock())

        assert context_values == ["stone"]

    @pytest.mark.asyncio
    async def test_passes_non_sse_paths_through(self, token_map, mock_load_token_map):
        app = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/version",
            "headers": [],
            "query_string": b"",
        }
        await middleware(scope, None, AsyncMock())
        app.assert_called_once()


# ---------------------------------------------------------------------------
# get_resources
# ---------------------------------------------------------------------------

class TestGetResources:
    def test_uses_context_profile(self):
        token = current_profile.set("stone")
        try:
            with patch("mcp_server.Settings.load_for_profile") as mock_load:
                with patch("mcp_server.CalendarClient") as mock_client:
                    result = get_resources("default")
                    mock_load.assert_called_once_with("stone")
                    mock_client.assert_called_once()
        finally:
            current_profile.reset(token)

    def test_falls_back_to_argument(self):
        token = current_profile.set("default")
        try:
            with patch("mcp_server.Settings.load_for_profile") as mock_load:
                with patch("mcp_server.CalendarClient") as mock_client:
                    result = get_resources("myprofile")
                    mock_load.assert_called_once_with("myprofile")
        finally:
            current_profile.reset(token)


# ---------------------------------------------------------------------------
# get_version_info
# ---------------------------------------------------------------------------

class TestGetVersionInfo:
    def test_reads_version_file(self, temp_dir):
        version_file = temp_dir / "version.txt"
        version_file.write_text("1.0.0\n")

        with patch.object(Path, "parent") as parent_mock:
            parent_mock.resolve.return_value = temp_dir
            assert get_version_info() == "1.0.0"

    def test_unknown_build_when_missing(self, temp_dir):
        with patch.object(Path, "parent") as parent_mock:
            parent_mock.resolve.return_value = temp_dir
            assert get_version_info() == "unknown build: dev"
