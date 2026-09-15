#!/usr/bin/env python3
"""
Model Context Protocol (MCP) Server for iCloud Calendar.
Exposes CalDAV calendar operations to AI clients over stdio or SSE transport.
"""

import logging
import sys
import contextvars
from typing import List, Dict, Any, Optional
from pathlib import Path

# 1. Force stderr-only logging before importing other modules
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("calendar_mcp.mcp_server")

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    logger.error(
        "The 'mcp' SDK is not installed in the current virtual environment, "
        "or is an incompatible major version (this server targets the mcp 2.x "
        "'MCPServer' API; see requirements.txt)."
    )
    sys.exit(1)

# Import local client modules
from config import Settings, settings
from calendar_client import CalendarClient

# Initialize MCPServer; transport-specific settings (host/port/transport_security)
# are supplied later, to run()/sse_app() rather than the constructor (mcp 2.x API).
from mcp.server.transport_security import TransportSecuritySettings

class RobustMCPServer(MCPServer):
    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        cleaned_name = name
        # Resolve potential tool name prefixes or truncations
        while True:
            if cleaned_name.startswith("calendar_mcp__"):
                cleaned_name = cleaned_name[len("calendar_mcp__"):]
            elif cleaned_name.startswith("calendar-mcp__"):
                cleaned_name = cleaned_name[len("calendar-mcp__"):]
            else:
                break

        tools = getattr(self._tool_manager, "_tools", {})
        if cleaned_name in tools:
            return await super().call_tool(cleaned_name, arguments, context)

        # Fallback: check if any registered tool starts with cleaned_name
        matching_tools = sorted(
            [t_name for t_name in tools if t_name.startswith(cleaned_name)],
            key=len
        )
        if matching_tools:
            logger.info("Fuzzy matched tool call '%s' (cleaned: '%s') to registered tool '%s'", name, cleaned_name, matching_tools[0])
            return await super().call_tool(matching_tools[0], arguments, context)

        return await super().call_tool(cleaned_name, arguments, context)

security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

SERVER_INSTRUCTIONS = (
    "iCloud/Google Calendar MCP server. Tools: list_calendars, search_events "
    "(read-only) and create_event, update_event, delete_event (mutating). "
    "Call list_calendars first to discover valid `profile` and calendar names, "
    "then search_events to check for existing/conflicting events before calling "
    "a mutating tool -- this server does not dedupe or confirm destructive calls "
    "for you. Most tools take a `profile` argument (default \"default\") that "
    "selects which CalDAV/Google account credentials to use; passing the wrong "
    "profile silently operates on the wrong calendar. See each tool's own "
    "schema/description for parameter details."
)

mcp = RobustMCPServer(
    "iCloud Calendar MCP",
    warn_on_duplicate_tools=False,
    instructions=SERVER_INSTRUCTIONS
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.requests import Request

# ContextVar to store the authenticated profile name for the current request
current_profile = contextvars.ContextVar("current_profile", default="default")

def load_token_profile_map() -> Dict[str, str]:
    """Scans root .env and all profile .env files to build a token-to-profile map."""
    token_map = {}
    workspace_root = Path(__file__).parent.resolve()
    
    # 1. Check root .env for default token
    root_env = workspace_root / ".env"
    if root_env.exists():
        try:
            with open(root_env, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "CALENDAR_PROFILE_TOKEN":
                            token_map[v.strip()] = "default"
        except Exception:
            pass
            
    # 2. Check profiles/ directories
    profiles_dir = workspace_root / "profiles"
    if profiles_dir.exists():
        for p_path in profiles_dir.iterdir():
            if p_path.is_dir():
                profile_name = p_path.name
                profile_env = p_path / ".env"
                if profile_env.exists():
                    try:
                        with open(profile_env, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#") and "=" in line:
                                    k, v = line.split("=", 1)
                                    if k.strip() == "CALENDAR_PROFILE_TOKEN":
                                        token_map[v.strip()] = profile_name
                    except Exception:
                        pass
    return token_map

class MCPTokenAuthMiddleware:
    def __init__(self, app, token_map: Dict[str, str]):
        self.app = app
        self.token_map = token_map

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            from starlette.datastructures import Headers, QueryParams
            
            headers = Headers(scope=scope)
            path = scope.get("path", "")
            
            if path.startswith("/sse"):
                self.token_map = load_token_profile_map()
                token = None
                auth_header = headers.get("authorization")
                if auth_header and auth_header.lower().startswith("bearer "):
                    token = auth_header[7:].strip()
                if not token:
                    token = headers.get("x-profile-token")
                if not token:
                    query_params = QueryParams(scope.get("query_string", b"").decode("utf-8"))
                    token = query_params.get("token")
                    
                if not token or token not in self.token_map:
                    body = b'{"error":"Unauthorized: Invalid or missing profile token"}'
                    await send({
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("utf-8")),
                        ]
                    })
                    await send({
                        "type": "http.response.body",
                        "body": body,
                        "more_body": False
                    })
                    return
                
                profile = self.token_map[token]
                token_t = current_profile.set(profile)
                try:
                    await self.app(scope, receive, send)
                    return
                finally:
                    current_profile.reset(token_t)
        
        await self.app(scope, receive, send)

def get_resources(profile_name: str = "default"):
    # Override profile name with the one mapped from the SSE token context
    mapped_profile = current_profile.get("default")
    if mapped_profile != "default":
        profile_name = mapped_profile

    # Load profile settings
    profile_settings = Settings.load_for_profile(profile_name)
    logger.info("Loading resource client for profile '%s' (context: %s)", profile_name, mapped_profile)
    
    # Initialize CalDAV client
    client = CalendarClient(settings_instance=profile_settings)
    return client, profile_settings

# =====================================================================
# TOOLS SECTION
# =====================================================================

@mcp.tool()
def list_calendars(profile: str = "default") -> List[Dict[str, Any]]:
    """
    Lists metadata (name and URL) of all available calendars.

    :param profile: Target profile name (default: "default").
    :return: A list of calendar metadata dicts.
    """
    client, _ = get_resources(profile)
    return client.list_calendars()

@mcp.tool()
def search_events(
    query: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    calendar_type: Optional[str] = None,
    profile: str = "default"
) -> List[Dict[str, Any]]:
    """
    Searches calendars for events matching specific criteria.

    :param query: Optional text query (matches summary, description, or location).
    :param start_date: Optional ISO-8601 start date/time (e.g., YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ). Defaults to 30 days ago.
    :param end_date: Optional ISO-8601 end date/time. Defaults to 365 days from now.
    :param calendar_type: Optional calendar type filter ('caldav' or 'google').
    :param profile: Target profile name (default: "default").
    :return: A list of matched event dictionaries.
    """
    client, _ = get_resources(profile)
    return client.search_events(
        query=query,
        start_date=start_date,
        end_date=end_date,
        calendar_type=calendar_type
    )


@mcp.tool()
def create_event(
    calendar_name_or_url: str,
    summary: str,
    start: str,
    end: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    profile: str = "default"
) -> Dict[str, Any]:
    """
    Creates a new event in a specified calendar.

    :param calendar_name_or_url: Name or URL of the target calendar.
    :param summary: Summary/title of the event.
    :param start: ISO-8601 start date/time (e.g., YYYY-MM-DDTHH:MM:SSZ).
    :param end: ISO-8601 end date/time (e.g., YYYY-MM-DDTHH:MM:SSZ).
    :param description: Optional description of the event.
    :param location: Optional location of the event.
    :param profile: Target profile name (default: "default").
    :return: Dictionary containing the created event details.
    """
    client, _ = get_resources(profile)
    return client.create_event(
        calendar_name_or_url=calendar_name_or_url,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location
    )

@mcp.tool()
def update_event(
    calendar_name_or_url: str,
    event_uid: str,
    summary: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    profile: str = "default"
) -> Dict[str, Any]:
    """
    Updates properties of an existing event. Only provided fields are updated.

    :param calendar_name_or_url: Name or URL of the calendar containing the event.
    :param event_uid: The UID of the event to update.
    :param summary: New summary/title.
    :param start: New ISO-8601 start date/time.
    :param end: New ISO-8601 end date/time.
    :param description: New description.
    :param location: New location.
    :param profile: Target profile name (default: "default").
    :return: Dictionary containing the updated event details.
    """
    client, _ = get_resources(profile)
    return client.update_event(
        calendar_name_or_url=calendar_name_or_url,
        event_uid=event_uid,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location
    )

@mcp.tool()
def delete_event(
    calendar_name_or_url: str,
    event_uid: str,
    profile: str = "default"
) -> Dict[str, Any]:
    """
    Deletes an event from the calendar.

    :param calendar_name_or_url: Name or URL of the calendar containing the event.
    :param event_uid: The UID of the event to delete.
    :param profile: Target profile name (default: "default").
    :return: A status dictionary containing confirmation and deleted details.
    """
    client, _ = get_resources(profile)
    return client.delete_event(
        calendar_name_or_url=calendar_name_or_url,
        event_uid=event_uid
    )

def get_version_info() -> str:
    version_file = Path(__file__).parent.resolve() / "version.txt"
    if version_file.exists():
        try:
            return version_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            return f"Error reading version.txt: {e}"
    return "unknown build: dev"

@mcp.custom_route("/version", methods=["GET"])
async def get_version(request: Request) -> PlainTextResponse:
    return PlainTextResponse(get_version_info())

if __name__ == "__main__":
    if settings.mcp_transport == "sse":
        import uvicorn
        import anyio
        
        token_map = load_token_profile_map()
        masked_map = {k[:4] + "...": v for k, v in token_map.items()}
        logger.info("Starting SSE MCP server. Loaded profile token mappings: %s", masked_map)
        
        # Get standard MCPServer SSE Starlette app
        app = mcp.sse_app(host=settings.mcp_host, transport_security=security)
        
        # Add token validation middleware
        app.add_middleware(MCPTokenAuthMiddleware, token_map=token_map)
        
        async def run_server():
            config = uvicorn.Config(
                app,
                host=settings.mcp_host,
                port=settings.mcp_port,
                log_level="info",
            )
            server = uvicorn.Server(config)
            await server.serve()
            
        anyio.run(run_server)
    else:
        logger.info("Starting Stdio MCP server on stdin/stdout.")
        mcp.run(transport=settings.mcp_transport)
