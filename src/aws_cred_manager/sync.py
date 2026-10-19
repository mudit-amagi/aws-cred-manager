"""Glue between config_store (what the user configured), aws_sso (talking to
AWS), and creds_file (writing ~/.aws/credentials).

Design note on removal: refresh/add are purely additive to
~/.aws/credentials -- they upsert one [profile x] section per currently
managed profile, and never delete anything on their own. Deleting a
profile's section is always an explicit, separate call (remove_profiles /
teardown_all) that names exactly the header to remove. This avoids ever
having to infer "what used to be there" from a config_store snapshot that
may already have been overwritten by the time we look -- the caller always
knows exactly what it's removing because it just decided to remove it.

Design note on ~/.aws/config: this app does NOT write anything there.
Earlier versions mirrored every managed profile into ~/.aws/config as an
sso_session-based profile (on top of writing static credentials into
~/.aws/credentials for the same profile name), on the theory that this
would let the raw `aws` CLI also refresh credentials for these profiles
via SSO. In practice, having an sso_session-based profile in config AND
static access/secret/session keys for the *same* profile name in
credentials confuses botocore/aws CLI's credential resolution -- it can
end up preferring (or getting stuck between) the SSO source and the
static keys, which surfaces as intermittent auth failures/conflicts for
anything using that profile. So: only ~/.aws/credentials is written now,
and cleanup_legacy_config_sections() removes any sections an older
version of this app already left behind in ~/.aws/config, so upgrading
actually fixes the conflict rather than just stopping it from getting
worse.

refresh_credentials() is written so that one profile failing (expired
permission, deleted role, transient network error) does not prevent the
other profiles from refreshing -- each profile is attempted independently
and errors are collected instead of aborting the whole batch.
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Dict, List

from . import app_paths, aws_sso, config_store, creds_file


def cleanup_legacy_config_sections(settings: config_store.Settings, profiles: List[config_store.ManagedProfile]) -> None:
    """Remove any [profile x] / [sso-session ...] sections a previous
    version of this app wrote into ~/.aws/config for the given profiles.
    Safe and cheap to call unconditionally -- removing a header that
    isn't present is a no-op (see creds_file.upsert_sections).
    """
    config_path = app_paths.aws_config_path()
    if not config_path.exists():
        return
    headers = [f"profile {p.profile_name}" for p in profiles]
    headers.append(f"sso-session {settings.sso_session_name}")
    creds_file.upsert_sections(config_path, {}, remove_headers=headers)


def add_profile(settings: config_store.Settings, new_profile: config_store.ManagedProfile) -> List[config_store.ManagedProfile]:
    profiles = config_store.load_profiles()
    profiles = [p for p in profiles if p.profile_name != new_profile.profile_name] + [new_profile]
    config_store.save_profiles(profiles)
    cleanup_legacy_config_sections(settings, profiles)
    return profiles


def remove_profiles(profile_names: List[str]) -> List[config_store.ManagedProfile]:
    profiles = config_store.load_profiles()
    remaining = [p for p in profiles if p.profile_name not in profile_names]
    config_store.save_profiles(remaining)

    creds_file.upsert_sections(
        app_paths.aws_config_path(), {}, remove_headers=[f"profile {n}" for n in profile_names]
    )
    creds_file.upsert_sections(
        app_paths.aws_credentials_path(), {}, remove_headers=list(profile_names)
    )
    return remaining


@dataclasses.dataclass
class RefreshResult:
    profiles: List[config_store.ManagedProfile]
    errors: Dict[str, str]  # profile_name -> error message, only for profiles that failed this round


def refresh_credentials(
    settings: config_store.Settings,
    profiles: List[config_store.ManagedProfile],
    on_device_auth: Callable[[aws_sso.DeviceAuthInfo], None],
    force_login: bool = False,
) -> RefreshResult:
    if not profiles:
        return RefreshResult(profiles=profiles, errors={})

    # Legacy-config cleanup covers every given profile, paused or not --
    # it's just removing stale sections an older version left behind, so
    # there's no reason to gate it on whether this profile is currently
    # being refreshed.
    cleanup_legacy_config_sections(settings, profiles)

    active = [p for p in profiles if not p.paused]
    if not active:
        return RefreshResult(profiles=profiles, errors={})

    session_name = settings.sso_session_name
    if force_login:
        token = aws_sso.login(settings.sso_start_url, settings.sso_region, session_name, on_device_auth)
    else:
        token = aws_sso.ensure_valid_token(settings.sso_start_url, settings.sso_region, session_name, on_device_auth)

    cred_sections = {}
    errors: Dict[str, str] = {}
    for p in active:
        try:
            creds = aws_sso.get_role_credentials(token, p.account_id, p.role_name)
        except Exception as exc:  # noqa: BLE001 -- one bad profile must not sink the batch
            errors[p.profile_name] = str(exc)
            p.last_error = str(exc)
            continue
        cred_sections[p.profile_name] = creds_file.build_credentials_block(
            creds.access_key_id, creds.secret_access_key, creds.session_token
        )
        p.last_expiration_epoch = creds.expiration_epoch_ms
        p.last_error = None

    if cred_sections:
        creds_file.upsert_sections(app_paths.aws_credentials_path(), cred_sections)

    # Merge results back by name instead of overwriting the whole on-disk
    # list with our own (possibly now-stale) copy -- see
    # config_store.update_profiles_after_refresh for why.
    config_store.update_profiles_after_refresh({p.profile_name: p for p in active})
    return RefreshResult(profiles=profiles, errors=errors)


def teardown_all() -> None:
    """Remove every profile this app has ever written, for a clean uninstall."""
    profiles = config_store.load_profiles()
    session_name = config_store.load_settings().sso_session_name
    config_headers = [f"profile {p.profile_name}" for p in profiles] + [f"sso-session {session_name}"]
    cred_headers = [p.profile_name for p in profiles]
    creds_file.upsert_sections(app_paths.aws_config_path(), {}, remove_headers=config_headers)
    creds_file.upsert_sections(app_paths.aws_credentials_path(), {}, remove_headers=cred_headers)
    config_store.save_profiles([])
