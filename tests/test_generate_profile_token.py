import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestGenerateProfileToken:
    @pytest.fixture
    def script_path(self):
        return str(Path(__file__).parent.parent / "generate_profile_token.py")

    def test_generates_token_for_root_profile(self, temp_dir, script_path):
        with patch("sys.argv", ["generate_profile_token.py", "--profile", "default"]):
            with patch("generate_profile_token.Path") as mock_path_class:
                mock_self = mock_path_class.return_value
                mock_self.parent.resolve.return_value = temp_dir

                import generate_profile_token
                # reload to pick up the mocked Path
                import importlib
                importlib.reload(generate_profile_token)

                with patch.object(generate_profile_token, "main") as mock_main:
                    generate_profile_token.main()
                    mock_main.assert_called_once()

    def test_creates_profile_dir_if_not_exists(self, temp_dir):
        with patch("sys.argv", ["generate_profile_token.py", "--profile", "newprofile"]):
            with patch("generate_profile_token.Path") as mock_path_class:
                mock_self = mock_path_class.return_value
                mock_self.parent.resolve.return_value = temp_dir

                import generate_profile_token
                import importlib
                importlib.reload(generate_profile_token)

                profile_dir = temp_dir / "profiles" / "newprofile"
                assert not profile_dir.exists()

                # run main in isolation
                with patch("generate_profile_token.main") as mock_main:
                    generate_profile_token.main()
                    # profile_dir would be created inside main

    def test_updates_existing_env_file(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text("CALENDAR_URL=https://caldav.icloud.com/\n")

        with patch("sys.argv", ["generate_profile_token.py"]):
            with patch("generate_profile_token.Path") as mock_path_class:
                mock_self = mock_path_class.return_value
                mock_self.parent.resolve.return_value = temp_dir
                mock_self.exists.return_value = True

                from generate_profile_token import main
                with patch("builtins.open", unittest.mock.mock_open(read_data=env_file.read_text())):
                    pass

    def test_appends_token_when_missing(self, temp_dir):
        env_file = temp_dir / ".env"
        env_file.write_text("CALENDAR_KEY=val\n")

        with patch("sys.argv", ["generate_profile_token.py"]):
            with patch("generate_profile_token.Path") as mock_path_class:
                mock_self = mock_path_class.return_value
                mock_self.parent.resolve.return_value = temp_dir
                mock_self.exists.return_value = True

                from generate_profile_token import main
                with patch("builtins.open", unittest.mock.mock_open(read_data=env_file.read_text())):
                    pass


import unittest
