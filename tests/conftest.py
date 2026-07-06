import os
import json
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def profile_env(temp_dir: Path) -> Path:
    env_path = temp_dir / "profiles" / "testprofile" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        'CALENDAR_URL=https://caldav.icloud.com/\n'
        'CALENDAR_USERNAME=test@example.com\n'
        'CALENDAR_PASSWORD=app-pw-1234\n'
        'CALENDAR_PROFILE_TOKEN=abc123\n'
    )
    return env_path


@pytest.fixture
def accounts_json() -> str:
    return json.dumps([
        {"type": "caldav", "username": "cal@example.com", "password": "pw1", "url": "https://caldav.example.com/"},
        {"type": "google", "google_account": "gmail@example.com", "token_path": "token.json", "credentials_path": "creds.json"},
    ])


@pytest.fixture
def clean_env() -> Generator[None, None, None]:
    saved = {}
    for k in list(os.environ.keys()):
        if k.startswith("CALENDAR_"):
            saved[k] = os.environ.pop(k)
    yield
    for k in list(os.environ.keys()):
        if k.startswith("CALENDAR_"):
            os.environ.pop(k, None)
    os.environ.update(saved)
