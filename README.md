# iCloud Calendar MCP Server

A Model Context Protocol (MCP) server that provides full Create, Read, Update, Delete (CRUD) operations and searching capabilities for your iCloud Calendar (or any standard CalDAV calendar server). It supports multi-profile configurations, token-based authentication, and can run over Standard Input/Output (Stdio), Server-Sent Events (SSE), or Streamable HTTP transport.

---

## Features

- **Standard CalDAV Support:** Connects directly to Apple iCloud Calendar (or any standard CalDAV server) over the secure web protocol.
- **Full CRUD & Search Tools:**
  - `list_calendars` - List names and URLs of your calendars.
  - `search_events` - Retrieve calendar events, filtering by date ranges or text queries.
  - `create_event` - Add new events to a specified calendar.
  - `update_event` - Edit existing events by UID.
  - `delete_event` - Remove events by UID.
- **Multi-Tenant Profile Management:** Load dynamic configurations (different Apple IDs or profiles) under the `profiles/` directory.
- **Multiple HTTP Transports:** Run over legacy SSE (`/sse`) or the newer Streamable HTTP transport (`/mcp` by default).
- **Token Authorization Middleware:** Secure your SSE and Streamable HTTP transport endpoints using profile-specific bearer tokens.
- **Docker Ready:** Built-in `Dockerfile`, `docker-compose.yml`, and `build.sh` supporting version tracking.

---

## Configuration

iCloud requires an **App-Specific Password** to connect to external clients like CalDAV. 

1. Log in to [appleid.apple.com](https://appleid.apple.com).
2. Go to **Sign-In and Security** > **App-Specific Passwords**.
3. Generate a new password (e.g., named "calendar-mcp") and copy it.

### 1. Default Profile Configuration

Create a `.env` file at the root of the project (or use the one automatically created during setup). This will be ignored by Git.

```bash
# iCloud Calendar CalDAV configurations
CALENDAR_URL=https://caldav.icloud.com/
CALENDAR_USERNAME=your-apple-id@gmail.com
CALENDAR_PASSWORD=xxxx-xxxx-xxxx-xxxx  # App-Specific Password

# MCP Server configurations
CALENDAR_MCP_TRANSPORT=stdio
CALENDAR_MCP_HOST=0.0.0.0
CALENDAR_MCP_PORT=8000

# Only used when CALENDAR_MCP_TRANSPORT=streamable-http (default shown)
CALENDAR_MCP_STREAMABLE_HTTP_PATH=/mcp
```

`CALENDAR_MCP_TRANSPORT` accepts `stdio`, `sse`, or `streamable-http` (the
underscore spelling `streamable_http` is also accepted as an alias).

### 2. Multi-Profile Configuration

Each profile has its own subdirectory under the `profiles/` folder containing an independent `.env` file. For example:
- `profiles/default/.env`
- `profiles/work/.env`

When calling tools, you can pass the `profile` argument (e.g., `profile="work"`). If not specified, the system defaults to the `"default"` profile.

---

## Command Line Utilities

### Generate Profile Access Tokens

To secure the HTTP (SSE or Streamable HTTP) server, you can generate random access tokens for your profiles using the bundled command-line utility.

Run the script to generate a token for the default profile:
```bash
./venv/bin/python generate_profile_token.py
```

Or target a specific named profile (this will automatically initialize the profile directory and `.env` template if they do not exist):
```bash
./venv/bin/python generate_profile_token.py --profile work
```

The script will print the generated token and save it under the profile's `CALENDAR_PROFILE_TOKEN` configuration key.

---

## Running the Server

Make sure to install dependencies in your virtual environment:
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### Option A: Running with Stdio Transport (Default)
Run directly from terminal. This is standard for local agent plugins (like Claude Desktop):
```bash
./venv/bin/python mcp_server.py
```

### Option B: Running with SSE Transport (HTTP Server)
Configure the transport option in your `.env` or set it in your environment:
```bash
export CALENDAR_MCP_TRANSPORT=sse
./venv/bin/python mcp_server.py
```
The server will start an HTTP service (defaulting to `http://0.0.0.0:8000`) exposing the SSE endpoint at `/sse`.

### Option C: Running with Streamable HTTP Transport (HTTP Server)
[Streamable HTTP](https://modelcontextprotocol.io/) is the newer bidirectional MCP transport (streaming responses over plain HTTP) and is the recommended option for new HTTP-based clients. Configure it via:
```bash
export CALENDAR_MCP_TRANSPORT=streamable-http
./venv/bin/python mcp_server.py
```
The server will start an HTTP service (defaulting to `http://0.0.0.0:8000`) exposing the Streamable HTTP endpoint at `/mcp` (configurable via `CALENDAR_MCP_STREAMABLE_HTTP_PATH`). As with SSE, requests must present a valid profile token (see below).

---

## Docker Deployment

Build, tag, and register the Docker image using the automated script:
```bash
./build.sh
```

Launch the service inside a Docker container using Docker Compose:
```bash
docker compose up -d
```
The compose file maps the container's port to local port `4004` and mounts a persistent volume for configurations.
