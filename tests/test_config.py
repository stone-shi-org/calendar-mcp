import os
import json
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

from config import Settings, AccountConfig, load_env_manually


class TestAccountConfig:
    def test_default_type_is_caldav(self):
        acc = AccountConfig()
        assert acc.type == "caldav"

    def test_default_icloud_url(self):
        acc = AccountConfig()
        assert acc.url == "https://caldav.icloud.com/"

    def test_google_type(self):
        acc = AccountConfig(type="google", google_account="test@gmail.com")
        assert acc.type == "google"
        assert acc.google_account == "test@gmail.com"


class TestLoadEnvManually:
    def test_loads_simple_vars(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text(
            "CALENDAR_URL=https://caldav.icloud.com/\n"
            "CALENDAR_USERNAME=user@me.com\n"
        )
        result = load_env_manually(env_file)
        assert result["CALENDAR_URL"] == "https://caldav.icloud.com/"
        assert result["CALENDAR_USERNAME"] == "user@me.com"

    def test_skips_comments_and_blanks(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text(
            "# this is a comment\n"
            "\n"
            "CALENDAR_KEY=value\n"
        )
        result = load_env_manually(env_file)
        assert result == {"CALENDAR_KEY": "value"}

    def test_strips_quotes(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text('CALENDAR_KEY="quoted value"\n')
        result = load_env_manually(env_file)
        assert result["CALENDAR_KEY"] == "quoted value"

    def test_strips_single_quotes(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text("CALENDAR_KEY='single quoted'\n")
        result = load_env_manually(env_file)
        assert result["CALENDAR_KEY"] == "single quoted"

    def test_returns_empty_dict_for_missing_file(self, temp_dir):
        result = load_env_manually(temp_dir / "nonexistent.env")
        assert result == {}

    def test_handles_no_newline_at_end(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text("CALENDAR_KEY=value")
        result = load_env_manually(env_file)
        assert result["CALENDAR_KEY"] == "value"

    def test_ignores_lines_without_equals(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text("CALENDAR_KEY=value\nLOG_DEBUG\n")
        result = load_env_manually(env_file)
        assert result == {"CALENDAR_KEY": "value"}


class TestSettingsAccountsProperty:
    def test_from_accounts_json(self, clean_env, accounts_json):
        with patch.dict(os.environ, {"CALENDAR_ACCOUNTS": accounts_json}, clear=False):
            s = Settings()
            accs = s.accounts
            assert len(accs) == 2
            assert accs[0].type == "caldav"
            assert accs[0].username == "cal@example.com"
            assert accs[1].type == "google"
            assert accs[1].google_account == "gmail@example.com"

    def test_single_caldav_fallback(self, clean_env):
        s = Settings(username="fallback@me.com", password="secret", url="https://caldav.example.com/")
        accs = s.accounts
        assert len(accs) == 1
        assert accs[0].type == "caldav"
        assert accs[0].username == "fallback@me.com"
        assert accs[0].password == "secret"

    def test_empty_when_no_creds_and_no_json(self, clean_env):
        s = Settings()
        accs = s.accounts
        assert accs == []

    def test_invalid_json_returns_fallback(self, clean_env):
        s = Settings(username="u", password="p")
        with patch.dict(os.environ, {"CALENDAR_ACCOUNTS": "not valid json"}, clear=False):
            accs = s.accounts
            assert len(accs) == 1
            assert accs[0].username == "u"

    def test_non_list_json_returns_fallback(self, clean_env):
        s = Settings(username="u", password="p")
        with patch.dict(os.environ, {"CALENDAR_ACCOUNTS": json.dumps({"type": "caldav"})}, clear=False):
            accs = s.accounts
            assert len(accs) == 1

    def test_strips_shell_quotes_from_json(self, clean_env):
        quoted = "'" + json.dumps([{"type": "caldav", "username": "u", "password": "p"}]) + "'"
        s = Settings()
        with patch.dict(os.environ, {"CALENDAR_ACCOUNTS": quoted}, clear=False):
            accs = s.accounts
            assert len(accs) == 1


class TestSettingsLoadForProfile:
    def test_loads_profile_env(self, clean_env):
        project_root = Path(__file__).parent.parent.resolve()
        profile_name = "_test_loads_profile"
        profile_dir = project_root / "profiles" / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        env_file = profile_dir / ".env"
        env_file.write_text(
            "CALENDAR_URL=https://mycaldav.example.com/\n"
            "CALENDAR_USERNAME=me@myprofile.com\n"
            "CALENDAR_PASSWORD=mypw\n"
        )
        try:
            s = Settings.load_for_profile(profile_name)
            assert s.username == "me@myprofile.com"
            assert s.password == "mypw"
            assert str(s.url) == "https://mycaldav.example.com/"
        finally:
            import shutil
            shutil.rmtree(str(profile_dir))

    def test_default_profile_when_none(self, clean_env):
        s = Settings.load_for_profile("")
        assert s.workspace_dir.name == "default" or s is not None

    def test_profile_token_from_env(self, clean_env):
        with patch("config._SYSTEM_ENV_KEYS", {"CALENDAR_PROFILE_TOKEN"}):
            with patch.dict(os.environ, {"CALENDAR_PROFILE_TOKEN": "token-from-os"}, clear=False):
                s = Settings.load_for_profile("default")
                assert s.profile_token == "token-from-os"


class TestMcpTransportNormalization:
    def test_default_is_stdio(self, clean_env):
        s = Settings()
        assert s.mcp_transport == "stdio"

    def test_accepts_sse(self, clean_env):
        s = Settings(mcp_transport="sse")
        assert s.mcp_transport == "sse"

    def test_accepts_streamable_http_hyphen(self, clean_env):
        s = Settings(mcp_transport="streamable-http")
        assert s.mcp_transport == "streamable-http"

    def test_accepts_streamable_http_underscore_alias(self, clean_env):
        s = Settings(mcp_transport="streamable_http")
        assert s.mcp_transport == "streamable-http"

    def test_normalizes_case_and_whitespace(self, clean_env):
        s = Settings(mcp_transport=" Streamable_HTTP ")
        assert s.mcp_transport == "streamable-http"

    def test_env_var_streamable_http_underscore(self, clean_env):
        with patch.dict(os.environ, {"CALENDAR_MCP_TRANSPORT": "streamable_http"}, clear=False):
            s = Settings()
            assert s.mcp_transport == "streamable-http"

    def test_env_var_streamable_http_hyphen(self, clean_env):
        with patch.dict(os.environ, {"CALENDAR_MCP_TRANSPORT": "streamable-http"}, clear=False):
            s = Settings()
            assert s.mcp_transport == "streamable-http"


class TestMcpStreamableHttpPath:
    def test_default_path_is_mcp(self, clean_env):
        s = Settings()
        assert s.mcp_streamable_http_path == "/mcp"

    def test_custom_path_preserved(self, clean_env):
        s = Settings(mcp_streamable_http_path="/custom-mcp")
        assert s.mcp_streamable_http_path == "/custom-mcp"

    def test_adds_missing_leading_slash(self, clean_env):
        s = Settings(mcp_streamable_http_path="mcp")
        assert s.mcp_streamable_http_path == "/mcp"

    def test_env_var_override(self, clean_env):
        with patch.dict(os.environ, {"CALENDAR_MCP_STREAMABLE_HTTP_PATH": "/custom"}, clear=False):
            s = Settings()
            assert s.mcp_streamable_http_path == "/custom"


class TestSettingsAccountsJsonAlias:
    def test_uses_settings_field_as_fallback(self, clean_env):
        s = Settings(accounts=json.dumps([{"type": "caldav", "username": "u", "password": "p"}]))
        # Clear environ so it doesn't interfere
        with patch.dict(os.environ, {}, clear=False):
            accs = s.accounts
            assert len(accs) == 1
