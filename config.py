import os
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict
from pydantic import Field, BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Transport values accepted for CALENDAR_MCP_TRANSPORT. "streamable_http" is an
# underscore alias for "streamable-http" (the canonical value expected by the
# `mcp` SDK's MCPServer.run()/streamable_http_app()).
VALID_MCP_TRANSPORTS = ("stdio", "sse", "streamable-http")
_TRANSPORT_ALIASES = {"streamable_http": "streamable-http"}


logger = logging.getLogger("calendar_mcp.config")

# Capture initial system environment keys to avoid overwriting variables set explicitly via Docker/OS
_SYSTEM_ENV_KEYS = set(os.environ.keys())


class AccountConfig(BaseModel):
    type: str = "caldav"  # "caldav" or "google"
    name: Optional[str] = None
    
    # CalDAV fields
    username: Optional[str] = None
    password: Optional[str] = None
    url: Optional[str] = "https://caldav.icloud.com/"
    
    # Google fields
    token_path: Optional[str] = "google_calendar_token.json"
    credentials_path: Optional[str] = "google_cli_client.json"
    google_account: Optional[str] = None

def load_env_manually(env_file: Path) -> Dict[str, str]:
    """Helper to parse a .env file manually into a dictionary of key-value pairs."""
    env_vars = {}
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        # Strip enclosing quotes if present
                        if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
                            v = v[1:-1].strip()
                        env_vars[k] = v
        except Exception:
            pass
    return env_vars

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CALENDAR_",
        extra="ignore"
    )

    workspace_dir: Path = Field(default_factory=lambda: Path(__file__).parent.resolve())
    
    # CalDAV parameters (for single account fallback)
    url: str = "https://caldav.icloud.com/"
    username: str = ""
    password: str = ""
    
    # Optional JSON string representing list of accounts
    accounts_json: Optional[str] = Field(default=None, alias="accounts")
    
    # MCP Server settings
    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # Streamable HTTP endpoint path (used when mcp_transport is "streamable-http")
    mcp_streamable_http_path: str = "/mcp"

    # Profile Access Token (for SSE authorization)
    profile_token: str = ""

    @field_validator("mcp_transport", mode="before")
    @classmethod
    def _normalize_mcp_transport(cls, v):
        """Normalize CALENDAR_MCP_TRANSPORT so "streamable_http" (underscore) is
        accepted as an alias of the canonical "streamable-http" (hyphen) value
        expected by the mcp SDK, and so casing/whitespace don't matter."""
        if isinstance(v, str):
            normalized = v.strip().lower()
            return _TRANSPORT_ALIASES.get(normalized, normalized)
        return v

    @field_validator("mcp_streamable_http_path", mode="before")
    @classmethod
    def _normalize_streamable_http_path(cls, v):
        if isinstance(v, str) and v and not v.startswith("/"):
            return "/" + v
        return v

    @property
    def accounts(self) -> List[AccountConfig]:
        """Parses accounts_json or falls back to single credentials."""
        # Check direct env var first for reliability, fallback to settings field
        accounts_raw = os.getenv("CALENDAR_ACCOUNTS") or self.accounts_json
        
        if accounts_raw:
            accounts_raw = accounts_raw.strip()
            # Strip enclosing shell-style quotes if present
            if (accounts_raw.startswith("'") and accounts_raw.endswith("'")) or \
               (accounts_raw.startswith('"') and accounts_raw.endswith('"')):
                accounts_raw = accounts_raw[1:-1].strip()
                
            try:
                data = json.loads(accounts_raw)
                if isinstance(data, list):
                    return [AccountConfig(**item) for item in data]
            except Exception as e:
                logger.error("Failed to parse CALENDAR_ACCOUNTS JSON: %s. Raw data: %s", e, accounts_raw)

        # Fallback to single account credentials (CalDAV)
        if self.username and self.password:
            return [AccountConfig(
                type="caldav",
                username=self.username,
                password=self.password,
                url=self.url
            )]
        return []

    @classmethod
    def load_for_profile(cls, profile_name: str = "default") -> "Settings":
        workspace_root = Path(__file__).parent.resolve()
        
        if not profile_name:
            profile_name = "default"
            
        profile_dir = workspace_root / "profiles" / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine env file priority (profile env overrides root env)
        profile_env = profile_dir / ".env"
        env_file = profile_env if profile_env.exists() else workspace_root / ".env"
        
        # Clear any previously loaded profile/default environment variables to prevent contamination
        for k in list(os.environ.keys()):
            if k.startswith("CALENDAR_") or k == "CALENDAR_ACCOUNTS":
                if k not in _SYSTEM_ENV_KEYS:
                    del os.environ[k]
                    
        # Manually load and inject env variables from the active file
        env_vars = load_env_manually(env_file)
        for k, v in env_vars.items():
            if k.startswith("CALENDAR_") or k == "CALENDAR_ACCOUNTS":
                if k in _SYSTEM_ENV_KEYS:
                    # System/Docker environment variables have higher priority
                    continue
                os.environ[k] = v

        # Instantiate Settings with specific environment file
        s = cls(_env_file=env_file)
        s.workspace_dir = profile_dir
        
        # Double check OS environment directly as fallback if not caught by prefix mapping
        if not s.profile_token:
            s.profile_token = os.getenv("CALENDAR_PROFILE_TOKEN", "")
            
        return s

# Load standard default settings initially
settings = Settings.load_for_profile("default")

