"""AWS IAM Identity Center (SSO) OIDC device-authorization flow.

Covers: registering an OIDC client, starting + polling device authorization,
caching the resulting SSO token exactly the way botocore/aws-cli expect it
(so `aws` CLI commands using the same sso-session keep working), listing the
accounts/roles a signed-in user has access to, and minting short-lived role
credentials.

Token cache format: modern sso-session-based AWS config (which is what this
app writes into ~/.aws/config) makes the CLI/SDK look up cached SSO tokens
by sha1(session_name) -- NOT sha1(start_url), which is the older, pre
sso-session cache key. Using the wrong key means `aws` commands report
"Token ... does not exist" even though this app is signed in just fine.
"""
from __future__ import annotations

import calendar
import dataclasses
import hashlib
import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from . import app_paths

_BOTO_CONFIG = BotoConfig(retries={"max_attempts": 5, "mode": "standard"})


class SsoError(Exception):
    pass


class LoginCancelled(SsoError):
    pass


class LoginTimedOut(SsoError):
    pass


@dataclasses.dataclass
class DeviceAuthInfo:
    verification_uri: str
    verification_uri_complete: str
    user_code: str
    device_code: str
    interval: int
    expires_in: int


@dataclasses.dataclass
class SsoToken:
    session_name: str
    access_token: str
    expires_at_epoch: float
    region: str


@dataclasses.dataclass
class AccountRole:
    account_id: str
    account_name: str
    role_name: str


def _oidc_client(region: str):
    return boto3.client("sso-oidc", region_name=region, config=_BOTO_CONFIG)


def _sso_client(region: str):
    return boto3.client("sso", region_name=region, config=_BOTO_CONFIG)


def _cache_path(session_name: str) -> Path:
    key = hashlib.sha1(session_name.encode("utf-8")).hexdigest()
    return app_paths.sso_cache_dir() / f"{key}.json"


def _parse_expires_at(value: str) -> float:
    """Parse an RFC3339 UTC timestamp like '2026-07-10T18:30:00Z' into a
    UTC epoch. calendar.timegm treats the struct_time as UTC directly,
    which is the correct, DST-safe way to do this -- manually subtracting
    time.timezone (as a naive approach might) silently gets the offset
    wrong for half the year whenever DST is in effect locally.
    """
    parsed = time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return float(calendar.timegm(parsed))


def _load_cached_token(session_name: str) -> Optional[SsoToken]:
    path = _cache_path(session_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return SsoToken(
            session_name=session_name,
            access_token=data["accessToken"],
            expires_at_epoch=_parse_expires_at(data["expiresAt"]),
            region=data["region"],
        )
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None


def _save_cached_token(token: SsoToken, client_id: str, client_secret: str, client_expires_at: float) -> None:
    path = _cache_path(token.session_name)
    payload = {
        "startUrl": None,
        "region": token.region,
        "accessToken": token.access_token,
        "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(token.expires_at_epoch)),
        "clientId": client_id,
        "clientSecret": client_secret,
        "clientSecretExpiresAt": str(int(client_expires_at)),
    }
    path.write_text(json.dumps(payload, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def get_cached_token(session_name: str) -> Optional[SsoToken]:
    token = _load_cached_token(session_name)
    if token is None:
        return None
    if token.expires_at_epoch - app_paths.SSO_TOKEN_EXPIRY_BUFFER_SECONDS <= time.time():
        return None
    return token


def login(
    start_url: str,
    region: str,
    session_name: str,
    on_device_auth: Callable[[DeviceAuthInfo], None],
    poll_timeout_seconds: int = 600,
    cancel_event: Optional[threading.Event] = None,
) -> SsoToken:
    """Run the full OIDC device-authorization flow, blocking until the user
    approves (or it times out / is denied). Safe to call from a background
    thread; on_device_auth is the caller's hook to surface the user code
    (notification, dialog, etc.) -- this function does not touch any UI
    toolkit itself.

    cancel_event: if given and set while this call is waiting for approval,
    raises LoginCancelled immediately instead of waiting out the full
    poll_timeout_seconds. Without this, a sign-in the user ignored (closed
    the browser tab, or just clicked Refresh Now again) stays outstanding
    on AWS's side -- the device code is neither approved nor denied, so the
    poll loop below just keeps getting AuthorizationPendingException -- and
    blocks the single worker thread, and therefore every subsequent Refresh
    Now click, for up to poll_timeout_seconds (10 minutes by default).
    """
    oidc = _oidc_client(region)

    client = oidc.register_client(clientName="aws-cred-manager", clientType="public")
    client_id = client["clientId"]
    client_secret = client["clientSecret"]
    # RegisterClient returns clientSecretExpiresAt as an absolute epoch
    # timestamp already -- NOT a duration to add to clientIdIssuedAt (there
    # is no clientSecretExpiresIn field in this API's response at all;
    # using it was a bug: every login attempt raised
    # KeyError: 'clientSecretExpiresIn' before ever reaching the browser).
    client_expires_at = client["clientSecretExpiresAt"]

    auth = oidc.start_device_authorization(
        clientId=client_id,
        clientSecret=client_secret,
        startUrl=start_url,
    )
    info = DeviceAuthInfo(
        verification_uri=auth["verificationUri"],
        verification_uri_complete=auth["verificationUriComplete"],
        user_code=auth["userCode"],
        device_code=auth["deviceCode"],
        interval=max(auth.get("interval", 5), 1),
        expires_in=auth.get("expiresIn", poll_timeout_seconds),
    )
    on_device_auth(info)
    try:
        webbrowser.open(info.verification_uri_complete)
    except Exception:
        pass  # on_device_auth already surfaced the code/URL to the user

    deadline = time.time() + min(info.expires_in, poll_timeout_seconds)
    interval = info.interval
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise LoginCancelled("Sign-in was replaced by a newer request.")
        if time.time() >= deadline:
            raise LoginTimedOut("Sign-in was not approved in time.")
        if cancel_event is not None:
            if cancel_event.wait(timeout=interval):
                raise LoginCancelled("Sign-in was replaced by a newer request.")
        else:
            time.sleep(interval)
        try:
            token_resp = oidc.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=info.device_code,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "AuthorizationPendingException":
                continue
            if code == "SlowDownException":
                interval += 5
                continue
            if code == "AccessDeniedException":
                raise LoginCancelled("Sign-in was denied.") from exc
            if code == "ExpiredTokenException":
                raise LoginTimedOut("The sign-in code expired.") from exc
            raise SsoError(f"Sign-in failed: {exc}") from exc

        expires_at = time.time() + token_resp["expiresIn"]
        token = SsoToken(
            session_name=session_name,
            access_token=token_resp["accessToken"],
            expires_at_epoch=expires_at,
            region=region,
        )
        _save_cached_token(token, client_id, client_secret, client_expires_at)
        return token


def ensure_valid_token(
    start_url: str,
    region: str,
    session_name: str,
    on_device_auth: Callable[[DeviceAuthInfo], None],
    cancel_event: Optional[threading.Event] = None,
) -> SsoToken:
    token = get_cached_token(session_name)
    if token is not None:
        return token
    return login(start_url, region, session_name, on_device_auth, cancel_event=cancel_event)


def list_accounts_and_roles(token: SsoToken) -> List[AccountRole]:
    sso = _sso_client(token.region)
    results: List[AccountRole] = []
    account_paginator = sso.get_paginator("list_accounts")
    for account_page in account_paginator.paginate(accessToken=token.access_token):
        for account in account_page.get("accountList", []):
            account_id = account["accountId"]
            account_name = account.get("accountName", account_id)
            role_paginator = sso.get_paginator("list_account_roles")
            for role_page in role_paginator.paginate(accessToken=token.access_token, accountId=account_id):
                for role in role_page.get("roleList", []):
                    results.append(AccountRole(account_id=account_id, account_name=account_name, role_name=role["roleName"]))
    results.sort(key=lambda r: (r.account_name.lower(), r.role_name.lower()))
    return results


@dataclasses.dataclass
class RoleCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration_epoch_ms: int


def get_role_credentials(token: SsoToken, account_id: str, role_name: str) -> RoleCredentials:
    sso = _sso_client(token.region)
    resp = sso.get_role_credentials(roleName=role_name, accountId=account_id, accessToken=token.access_token)
    creds = resp["roleCredentials"]
    return RoleCredentials(
        access_key_id=creds["accessKeyId"],
        secret_access_key=creds["secretAccessKey"],
        session_token=creds["sessionToken"],
        expiration_epoch_ms=creds["expiration"],
    )


def is_expired(expiration_epoch_ms: int) -> bool:
    remaining = expiration_epoch_ms / 1000.0 - time.time()
    return remaining <= app_paths.CREDENTIAL_EXPIRY_BUFFER_SECONDS
