"""Persistence for app settings and the list of profiles the user has configured.

Two small JSON files live in the app data dir (see app_paths.py):

  settings.json          -- { sso_start_url, sso_region, default_region, start_on_login }
  managed_profiles.json  -- [ { profile_name, account_id, account_name, role_name,
                                 region, last_expiration_epoch } ]

managed_profiles.json is the single source of truth for "which sections in
~/.aws/config and ~/.aws/credentials does this app own". Anything not listed
here is never touched, no matter what it looks like.

All reads/writes go through a single re-entrant lock. It's re-entrant
(threading.RLock, not Lock) deliberately: update_profile_expiration() reads
the current list and writes it back in one call, and the write path must be
safely callable from within a block that's already holding the lock.
"""
from __future__ import annotations

import dataclasses
import json
import threading
from typing import Dict, List, Optional

from . import app_paths
from .creds_file import _atomic_write_text

_lock = threading.RLock()


@dataclasses.dataclass
class Settings:
    sso_start_url: str = ""
    sso_region: str = "us-east-1"
    default_region: str = "us-east-1"
    start_on_login: bool = False

    @property
    def sso_session_name(self) -> str:
        # Stable, filesystem/ini-safe session name. Must match whatever is
        # written into ~/.aws/config's [sso-session ...] block, since
        # botocore's SSOTokenProvider caches tokens keyed by this name.
        return "aws-cred-manager"

    def is_configured(self) -> bool:
        return bool(self.sso_start_url and self.sso_region)


@dataclasses.dataclass
class ManagedProfile:
    profile_name: str
    account_id: str
    account_name: str
    role_name: str
    region: str
    last_expiration_epoch: Optional[int] = None  # ms since epoch, from GetRoleCredentials
    last_error: Optional[str] = None
    # Per-row pause: while True, the background worker skips this profile
    # entirely (no GetRoleCredentials calls, no credentials-file writes)
    # even though it stays in the table. Existing credentials on disk are
    # left alone -- pausing only stops further refreshing, it doesn't
    # revoke anything already valid.
    paused: bool = False

    def key(self) -> str:
        return self.profile_name


def _default(field_name: str):
    return getattr(_DEFAULT_SETTINGS, field_name)


_DEFAULT_SETTINGS = Settings()


def load_settings() -> Settings:
    path = app_paths.settings_file()
    with _lock:
        if not path.exists():
            return Settings()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, TypeError):
            return Settings()
        kwargs = {f.name: data.get(f.name, _default(f.name)) for f in dataclasses.fields(Settings)}
        try:
            return Settings(**kwargs)
        except TypeError:
            return Settings()


def save_settings(settings: Settings) -> None:
    with _lock:
        _atomic_write_text(app_paths.settings_file(), json.dumps(dataclasses.asdict(settings), indent=2))


def load_profiles() -> List[ManagedProfile]:
    path = app_paths.state_file()
    with _lock:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
            return [ManagedProfile(**item) for item in raw]
        except (json.JSONDecodeError, OSError, TypeError):
            return []


def save_profiles(profiles: List[ManagedProfile]) -> None:
    with _lock:
        _atomic_write_text(
            app_paths.state_file(), json.dumps([dataclasses.asdict(p) for p in profiles], indent=2)
        )


def update_profile_expiration(profile_name: str, expiration_epoch_ms: Optional[int], error: Optional[str] = None) -> None:
    with _lock:
        profiles = load_profiles()
        for p in profiles:
            if p.profile_name == profile_name:
                p.last_expiration_epoch = expiration_epoch_ms
                p.last_error = error
        save_profiles(profiles)


def update_profiles_after_refresh(updates: "Dict[str, ManagedProfile]") -> List[ManagedProfile]:
    """Merge refresh results (last_expiration_epoch / last_error) for the
    given profile names into the CURRENT on-disk profile list, rather than
    blindly overwriting the whole list with whatever copy the caller
    happened to load before the refresh started.

    Why this matters: a refresh cycle can take several seconds (network
    calls to AWS). If the user toggles a profile's `paused` flag (or adds
    /removes a row) while a refresh is in flight, a naive
    save_profiles(the_refresher's_own_list) at the end would silently
    clobber that concurrent change, because the refresher's in-memory
    copy was loaded before the change happened. Re-reading fresh here and
    only touching the two fields the refresh actually produced closes
    that race.
    """
    with _lock:
        current = load_profiles()
        by_name = {p.profile_name: p for p in current}
        for name, updated in updates.items():
            if name in by_name:
                by_name[name].last_expiration_epoch = updated.last_expiration_epoch
                by_name[name].last_error = updated.last_error
        merged = list(by_name.values())
        save_profiles(merged)
        return merged


def set_profile_paused(profile_name: str, paused: bool) -> List[ManagedProfile]:
    """Read-modify-write just the `paused` flag for one profile. Uses the
    same fresh-read-under-lock pattern as update_profiles_after_refresh()
    so this can never lose a concurrent refresh's expiration/error update,
    and vice versa -- the two only ever touch disjoint fields.
    """
    with _lock:
        current = load_profiles()
        for p in current:
            if p.profile_name == profile_name:
                p.paused = paused
        save_profiles(current)
        return current


def set_all_profiles_paused(paused: bool) -> List[ManagedProfile]:
    """Bulk version of set_profile_paused() for the toolbar's Pause
    All / Resume All button. Deliberately just sets every row's own
    `paused` flag rather than being a separate global switch -- that way
    a row that's been bulk-paused is indistinguishable from one paused
    individually, and can be resumed individually afterward with the
    row's own button exactly the same way. There is no separate
    "everything is globally paused" state to get out of sync with that.
    """
    with _lock:
        current = load_profiles()
        for p in current:
            p.paused = paused
        save_profiles(current)
        return current
