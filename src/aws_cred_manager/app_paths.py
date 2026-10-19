"""Filesystem locations and small platform helpers used across the app.

Every path the app reads or writes lives behind a function here so there is
exactly one place that knows the per-OS app-data directory, the frozen vs.
source-checkout distinction, and the well-known AWS config file locations.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AWSCredManager"

DEFAULT_REFRESH_INTERVAL_SECONDS = 60
CREDENTIAL_EXPIRY_BUFFER_SECONDS = 300
SSO_TOKEN_EXPIRY_BUFFER_SECONDS = 120

# Single-instance lock / local IPC (loopback only, never exposed externally).
IPC_HOST = "127.0.0.1"
IPC_PORT = 51823


def is_frozen() -> bool:
    """True when running as a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def app_data_dir() -> Path:
    """Per-user, per-app writable directory. Created on first access."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        path = base / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return app_data_dir() / "settings.json"


def state_file() -> Path:
    return app_data_dir() / "managed_profiles.json"


def log_file() -> Path:
    return app_data_dir() / "app.log"


def sso_cache_dir() -> Path:
    path = Path.home() / ".aws" / "sso" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def aws_dir() -> Path:
    path = Path.home() / ".aws"
    path.mkdir(parents=True, exist_ok=True)
    return path


def aws_config_path() -> Path:
    return aws_dir() / "config"


def aws_credentials_path() -> Path:
    return aws_dir() / "credentials"
