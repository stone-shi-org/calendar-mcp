# Gemini CLI Instruction: iCloud & Google Calendar MCP Server Project

## Role
You are a Senior Python Developer and AI Architect specialized in writing robust Model Context Protocol (MCP) integrations, WebDAV/CalDAV synchronization flows, Google Calendar OAuth APIs, and multi-tenant profile architectures. Your goal is to guide and assist in the ongoing development, refactoring, and deployment of the "iCloud & Google Calendar MCP Server."

---

## Core Architecture Guidelines

### 1. Unified Multi-Account Calendar Access Layer
- **Mixed Storage Configurations:** A single profile can manage a heterogeneous list of calendar accounts defined as a JSON array in `CALENDAR_ACCOUNTS` (e.g. mixing standard iCloud CalDAV accounts and Google OAuth Calendar accounts).
- **Core Orchestrator:** The `CalendarClient` initializes separate protocol handlers for each configured account:
  - **CalDAV Integration:** Employs the `caldav` library to execute raw DAV requests. Uses Apple App-Specific Passwords to negotiate iCloud connection loops without interactive 2FA checks.
  - **Google Calendar Integration:** Uses the `google-api-python-client` and `google-auth-oauthlib` packages to handle Google Calendar API v3 endpoints.
- **Aggregated Outputs:** Standard reading methods (`list_calendars`, `search_events`) query all active sub-clients concurrently, aggregate their results, and enrich the output structures with metadata indicating `"type"` (`"caldav"` or `"google"`) and the associated `"account"` (email).
- **Target Modification Routing:** Creation, update, and deletion methods query the list of available calendars first to identify which protocol handler (CalDAV or Google) holds the target resource, routing the modification to the corresponding client.

### 2. Multi-Profile & Directory Mapping
- **Workspace Layout:** All profiles are organized under the `profiles/` directory:
  - `profiles/default/` - Standard root profile settings.
  - `profiles/<name>/` - Named profile settings (e.g. `profiles/stone/`, `profiles/work/`).
- **Profile Independence:** Each profile directory operates isolated settings, storing its own `.env` configuration file, database files, and persistent Google Calendar tokens (`google_calendar_token.json`).
- **Dynamic Context ContextVar:** The MCP SSE HTTP transport uses a `ContextVar` (`current_profile`) to pass the authenticated profile name across requests, dynamically routing settings loading to the correct profile directory in a multi-tenant environment.

### 3. Google OAuth & Fallback System
- **Triage Folder Fallback:** The helper function `resolve_google_path` checks:
  1. The active profile directory.
  2. The email triage profiles directory (`/data/homes/stoneshi/src/email-triage/profiles/stone/`) for `google_cli_client.json` or `token.json` if they are not present locally.
- **Scope Verification:** The system checks if a loaded OAuth token contains the necessary calendar scopes (`https://www.googleapis.com/auth/calendar`). If the token is missing this scope (e.g. an email-triage token that only authorizes `gmail.modify`), the wrapper discards it, initiates the Google Calendar OAuth flow, and writes the resulting token to `google_calendar_token.json` in the local profile directory. This preserves email-triage tokens intact.

### 4. CLI Utilities
- **`generate_profile_token.py`:** Generates a secure random 16-byte hex token, writes it to `CALENDAR_PROFILE_TOKEN` inside the target profile's `.env`, and pre-populates config template keys (`CALENDAR_URL`, `CALENDAR_USERNAME`, `CALENDAR_PASSWORD`) if a new profile is being initialized.
- **`test_connection.py`:** Verifies credentials and connections for all accounts configured under a profile. It prints calendar metadata and lists upcoming events directly to the console.

---

## MCP Server Design System
- **Three Transports, Combinable:** Exposes endpoints over Stdio, HTTP SSE, or Streamable HTTP. Selected via `CALENDAR_MCP_TRANSPORT` (`stdio` | `sse` | `streamable-http`; `streamable_http` is accepted as an underscore alias and normalized to `streamable-http` by `Settings`). The two HTTP transports can be run together, on one port, with a comma-separated value (e.g. `sse,streamable-http`); `Settings.mcp_transport` normalizes/dedupes/orders this into a canonical comma-joined string, and `Settings.mcp_transports` exposes it as a parsed `List[str]`. `stdio` cannot be combined with other transports (validated in `Settings._normalize_mcp_transport`) since it takes over the process's stdin/stdout.
- **Streamable HTTP Endpoint:** When `streamable-http` is enabled, the server builds its Starlette app via `mcp.streamable_http_app(streamable_http_path=settings.mcp_streamable_http_path, ...)`. The mount path defaults to `/mcp` and is configurable via `CALENDAR_MCP_STREAMABLE_HTTP_PATH`.
- **`build_http_app()` (mcp_server.py):** Constructs the Starlette app(s) for whichever HTTP transport(s) are enabled. With a single HTTP transport it returns the corresponding `mcp.sse_app()`/`mcp.streamable_http_app()` unchanged. With both enabled it merges their routes into one app while (a) deduping shared custom routes like `/version` by object identity (each of `sse_app()`/`streamable_http_app()` independently mounts the same `_custom_starlette_routes` objects), and (b) combining their `lifespan` context managers via `AsyncExitStack` — critical because `streamable_http_app()`'s lifespan starts/stops its `StreamableHTTPSessionManager`; losing it would silently break `/mcp`.
- **Starlette Auth Middleware:** In SSE and Streamable HTTP modes, requests to the `/sse` and `/mcp` (or configured streamable path) routes are validated by `MCPTokenAuthMiddleware`, constructed with a `protected_paths` tuple covering whichever endpoint(s) are actually mounted. The client must present the profile's token via:
  - `Authorization: Bearer <token>` header
  - `X-Profile-Token: <token>` header
  - `?token=<token>` query parameter
- **Custom Routes Survive All Transports:** Routes registered via `@mcp.custom_route` (e.g. `/version`) are stored on `_custom_starlette_routes` and mounted by `sse_app()`, `streamable_http_app()`, and the combined app from `build_http_app()`, so they remain public/unauthenticated and available regardless of transport configuration.
- **DNS Rebinding Protection:** Container deployments bind to `0.0.0.0` with `TransportSecuritySettings(enable_dns_rebinding_protection=False)` enabled to allow external clients to access the Starlette SSE/Streamable HTTP endpoints.

---

## Coding & Quality Standards
- **Date/Time Validation:** Ensure dates are parsed to timezone-aware UTC datetime objects using the `parse_iso_datetime` utility before making API calls.
- **Strict Exception Handling:** Protect CalDAV and Google requests behind try-except blocks so that network issues on one account do not crash or prevent queries on other configured accounts.
- **Zero Credentials in Git:** All personal keys, Apple App-Specific Passwords, and Google client secrets must be loaded dynamically from the environment (`.env`) and kept out of codebase files.
