import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock, mock_open

import pytest

from mcp.server.transport_security import TransportSecuritySettings

from config import Settings
from mcp_server import (
    RobustMCPServer,
    load_token_profile_map,
    MCPTokenAuthMiddleware,
    get_resources,
    get_version_info,
    build_http_app,
    current_profile,
    mcp,
    SERVER_INSTRUCTIONS,
)


# ---------------------------------------------------------------------------
# RobustMCPServer
# ---------------------------------------------------------------------------

class TestRobustMCPServer:
    @pytest.fixture
    def mcp_instance(self):
        return RobustMCPServer("test")

    @pytest.mark.asyncio
    async def test_strips_calendar_mcp_prefix(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True}):
            with patch("mcp.server.mcpserver.MCPServer.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("calendar_mcp__list_calendars", {})
                super_call.assert_called_once_with("list_calendars", {}, None)

    @pytest.mark.asyncio
    async def test_strips_calendar_mcp_hyphen_prefix(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True}):
            with patch("mcp.server.mcpserver.MCPServer.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("calendar-mcp__list_calendars", {})
                super_call.assert_called_once_with("list_calendars", {}, None)

    @pytest.mark.asyncio
    async def test_fuzzy_matches_prefix(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True, "search_events": True}):
            with patch("mcp.server.mcpserver.MCPServer.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("list_cal", {})
                super_call.assert_called_once_with("list_calendars", {}, None)

    @pytest.mark.asyncio
    async def test_passes_through_if_no_match(self, mcp_instance):
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True}):
            with patch("mcp.server.mcpserver.MCPServer.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("other_tool", {})
                super_call.assert_called_once_with("other_tool", {}, None)

    @pytest.mark.asyncio
    async def test_forwards_context_through_fuzzy_match(self, mcp_instance):
        sentinel_context = object()
        with patch.object(mcp_instance._tool_manager, "_tools", {"list_calendars": True}):
            with patch("mcp.server.mcpserver.MCPServer.call_tool", new_callable=AsyncMock) as super_call:
                await mcp_instance.call_tool("list_cal", {}, sentinel_context)
                super_call.assert_called_once_with("list_calendars", {}, sentinel_context)


# ---------------------------------------------------------------------------
# Server initialize() instructions field (CM-1)
# ---------------------------------------------------------------------------

class TestServerInstructions:
    def test_module_server_has_non_empty_instructions(self):
        # Populates InitializeResult.instructions in the MCP handshake.
        assert isinstance(mcp.instructions, str)
        assert mcp.instructions.strip() != ""
        assert mcp.instructions == SERVER_INSTRUCTIONS

    def test_instructions_mentions_tool_ordering_and_profile_caveat(self):
        text = SERVER_INSTRUCTIONS.lower()
        assert "list_calendars" in text
        assert "search_events" in text
        assert "profile" in text

    def test_constructor_forwards_instructions_to_low_level_server(self):
        instance = RobustMCPServer("test", instructions="hello world")
        assert instance.instructions == "hello world"


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

    # -- Streamable HTTP ("/mcp") coverage (CM-2) --

    @pytest.mark.asyncio
    async def test_protects_mcp_path_by_default(self, token_map, mock_load_token_map):
        app = AsyncMock()
        send = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [],
            "query_string": b"",
        }
        await middleware(scope, None, send)

        # No token provided -> rejected, app never invoked
        send.assert_called()
        app.assert_not_called()

    @pytest.mark.asyncio
    async def test_authorizes_mcp_path_with_bearer_token(self, token_map, mock_load_token_map):
        app = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer abc123")],
            "query_string": b"",
        }
        await middleware(scope, None, AsyncMock())
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_sets_profile_context_var_for_mcp_path(self, token_map, mock_load_token_map):
        context_values = []

        async def check_context(scope, receive, send):
            context_values.append(current_profile.get())
            await send({})

        middleware = MCPTokenAuthMiddleware(check_context, token_map)

        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer def456")],
            "query_string": b"",
        }
        await middleware(scope, None, AsyncMock())

        assert context_values == ["stone"]

    @pytest.mark.asyncio
    async def test_rejects_invalid_token_on_mcp_path(self, token_map, mock_load_token_map):
        app = AsyncMock()
        send = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map)

        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer invalid-token")],
            "query_string": b"",
        }
        await middleware(scope, None, send)

        send.assert_called()
        app.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_protected_paths_override(self, token_map, mock_load_token_map):
        # A custom protected_paths tuple (e.g. a configured, non-default
        # streamable HTTP endpoint) should be honored instead of the default.
        app = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map, protected_paths=("/custom-mcp",))

        # Default "/mcp" path is no longer protected under the custom config.
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [],
            "query_string": b"",
        }
        await middleware(scope, None, AsyncMock())
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_protected_paths_still_require_token(self, token_map, mock_load_token_map):
        app = AsyncMock()
        send = AsyncMock()
        middleware = MCPTokenAuthMiddleware(app, token_map, protected_paths=("/custom-mcp",))

        scope = {
            "type": "http",
            "path": "/custom-mcp",
            "headers": [],
            "query_string": b"",
        }
        await middleware(scope, None, send)

        send.assert_called()
        app.assert_not_called()


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


# ---------------------------------------------------------------------------
# Streamable HTTP transport app (CM-2)
# ---------------------------------------------------------------------------

class TestStreamableHttpApp:
    def test_builds_starlette_app_with_configured_path(self):
        app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")
        # Should be a Starlette app exposing the /mcp mount.
        paths = [getattr(r, "path", None) for r in app.routes]
        assert any(p and p.startswith("/mcp") for p in paths)

    def test_custom_route_version_survives_on_streamable_http_app(self):
        # /version is registered via @mcp.custom_route and must keep working
        # regardless of which HTTP transport app it's mounted on.
        from starlette.testclient import TestClient

        app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")
        client = TestClient(app)
        response = client.get("/version")
        assert response.status_code == 200

    def test_honors_custom_streamable_http_path(self):
        from starlette.testclient import TestClient

        app = mcp.streamable_http_app(streamable_http_path="/custom-mcp", host="0.0.0.0")
        client = TestClient(app)
        response = client.get("/version")
        assert response.status_code == 200
        paths = [getattr(r, "path", None) for r in app.routes]
        assert any(p and p.startswith("/custom-mcp") for p in paths)


class TestStreamableHttpEndToEndAuth:
    """Integration test: MCPTokenAuthMiddleware wrapping the real
    streamable_http_app, exercising the full request path used when
    CALENDAR_MCP_TRANSPORT=streamable-http."""

    def test_rejects_unauthenticated_mcp_request(self):
        from starlette.testclient import TestClient

        app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")
        with patch("mcp_server.load_token_profile_map", return_value={"tok": "default"}):
            app.add_middleware(MCPTokenAuthMiddleware, token_map={"tok": "default"}, protected_paths=("/sse", "/mcp"))
            client = TestClient(app)
            response = client.post("/mcp", json={})
            assert response.status_code == 401

    def test_version_route_unaffected_by_auth_middleware(self):
        from starlette.testclient import TestClient

        app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")
        with patch("mcp_server.load_token_profile_map", return_value={"tok": "default"}):
            app.add_middleware(MCPTokenAuthMiddleware, token_map={"tok": "default"}, protected_paths=("/sse", "/mcp"))
            client = TestClient(app)
            response = client.get("/version")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# build_http_app: running SSE and Streamable HTTP together (CM-2 follow-up)
# ---------------------------------------------------------------------------

class TestBuildHttpApp:
    @pytest.fixture
    def security(self):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    @pytest.fixture
    def base_settings(self, clean_env):
        return Settings(mcp_host="0.0.0.0")

    def test_sse_only_returns_unwrapped_sub_app(self, base_settings, security):
        app, protected_paths = build_http_app(["sse"], base_settings, security)
        assert protected_paths == ("/sse",)
        paths = [r.path for r in app.routes]
        assert "/sse" in paths
        assert "/mcp" not in paths
        assert "/version" in paths

    def test_streamable_http_only_returns_unwrapped_sub_app(self, base_settings, security):
        app, protected_paths = build_http_app(["streamable-http"], base_settings, security)
        assert protected_paths == ("/mcp",)
        paths = [r.path for r in app.routes]
        assert "/mcp" in paths
        assert "/sse" not in paths
        assert "/version" in paths

    def test_combined_transports_expose_both_endpoints(self, base_settings, security):
        app, protected_paths = build_http_app(["sse", "streamable-http"], base_settings, security)
        assert protected_paths == ("/sse", "/mcp")
        paths = [r.path for r in app.routes]
        assert "/sse" in paths
        assert "/messages" in paths
        assert "/mcp" in paths
        assert "/version" in paths

    def test_combined_transports_do_not_duplicate_custom_routes(self, base_settings, security):
        # /version is registered via @mcp.custom_route and is mounted by both
        # sse_app() and streamable_http_app() independently; the combined app
        # must dedupe it rather than registering it twice.
        app, _ = build_http_app(["sse", "streamable-http"], base_settings, security)
        version_routes = [r for r in app.routes if r.path == "/version"]
        assert len(version_routes) == 1

    def test_combined_transports_respect_custom_streamable_http_path(self, security, clean_env):
        s = Settings(mcp_host="0.0.0.0", mcp_streamable_http_path="/custom-mcp")
        app, protected_paths = build_http_app(["sse", "streamable-http"], s, security)
        assert protected_paths == ("/sse", "/custom-mcp")
        paths = [r.path for r in app.routes]
        assert "/custom-mcp" in paths

    def test_combined_app_version_route_reachable(self, base_settings, security):
        from starlette.testclient import TestClient

        app, _ = build_http_app(["sse", "streamable-http"], base_settings, security)
        client = TestClient(app)
        response = client.get("/version")
        assert response.status_code == 200

    def test_combined_app_starts_streamable_http_session_manager(self, base_settings, security):
        # The combined app's lifespan must still enter streamable_http_app()'s
        # own lifespan, which starts the StreamableHTTPSessionManager. If that
        # lifespan were dropped, POSTs to /mcp would fail outright (e.g. 500)
        # instead of reaching normal MCP protocol-level handling.
        from starlette.testclient import TestClient

        app, _ = build_http_app(["sse", "streamable-http"], base_settings, security)
        with TestClient(app) as client:
            response = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "ping", "id": 1},
                headers={"accept": "application/json, text/event-stream"},
            )
            # A running session manager rejects this as a bad MCP request
            # (400/406-ish) rather than erroring out with a 500 from a
            # missing/never-started session manager.
            assert response.status_code < 500

    def test_combined_app_auth_middleware_protects_both_endpoints(self, base_settings, security):
        from starlette.testclient import TestClient

        app, protected_paths = build_http_app(["sse", "streamable-http"], base_settings, security)
        with patch("mcp_server.load_token_profile_map", return_value={"tok": "default"}):
            app.add_middleware(MCPTokenAuthMiddleware, token_map={"tok": "default"}, protected_paths=protected_paths)
            with TestClient(app) as client:
                mcp_response = client.post("/mcp", json={})
                assert mcp_response.status_code == 401

                version_response = client.get("/version")
                assert version_response.status_code == 200
