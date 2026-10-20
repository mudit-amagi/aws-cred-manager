import calendar
import sys
import threading
import time
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aws_cred_manager import aws_sso


def test_parse_expires_at_is_dst_safe():
    # A timestamp inside a period where local time (if this machine were,
    # say, US/Eastern) would be under DST. The old implementation computed
    # `time.mktime(...) - time.timezone`, which uses the *standard* (non-DST)
    # offset unconditionally and is off by one hour whenever DST is active
    # locally. calendar.timegm treats the struct as UTC directly and is
    # correct regardless of the local machine's timezone or DST state.
    value = "2026-07-10T18:30:00Z"
    parsed = aws_sso._parse_expires_at(value)
    expected = calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    assert parsed == expected


def test_is_expired_uses_buffer():
    now_ms = int(time.time() * 1000)
    # Expires in 60 seconds -- inside the 300s buffer, so should read as expired.
    assert aws_sso.is_expired(now_ms + 60_000)
    # Expires in 1 hour -- comfortably outside the buffer.
    assert not aws_sso.is_expired(now_ms + 3_600_000)


class _NeverApprovedOidcClient:
    """Stands in for the boto3 sso-oidc client: registers fine, starts a
    device authorization fine, but create_token always reports the code as
    still pending -- i.e. nobody has approved (or denied) it in the browser,
    which is exactly the state a closed/ignored sign-in tab leaves behind.
    """

    def register_client(self, **kwargs):
        return {
            "clientId": "test-client-id",
            "clientSecret": "test-client-secret",
            "clientSecretExpiresAt": time.time() + 3600,
        }

    def start_device_authorization(self, **kwargs):
        return {
            "verificationUri": "https://example.com/start",
            "verificationUriComplete": "https://example.com/start?code=ABC-123",
            "userCode": "ABC-123",
            "deviceCode": "test-device-code",
            "interval": 1,
            "expiresIn": 600,
        }

    def create_token(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "AuthorizationPendingException", "Message": "pending"}},
            "CreateToken",
        )


def test_login_cancel_event_interrupts_a_stale_pending_signin(monkeypatch):
    # Regression test for: clicking "Refresh Now" while an earlier sign-in
    # is still waiting on browser approval (e.g. the user closed the tab
    # instead of approving it) used to do nothing -- the worker thread was
    # stuck inside this same poll loop and only cancel_event lets a newer
    # request interrupt it instead of waiting out the full timeout.
    monkeypatch.setattr(aws_sso, "_oidc_client", lambda region: _NeverApprovedOidcClient())
    monkeypatch.setattr(aws_sso.webbrowser, "open", lambda url: None)

    cancel_event = threading.Event()
    # Fires shortly after login() starts polling, simulating a second
    # Refresh Now click arriving while the first attempt is still pending.
    threading.Timer(0.1, cancel_event.set).start()

    started = time.time()
    with pytest.raises(aws_sso.LoginCancelled):
        aws_sso.login(
            "https://example.awsapps.com/start",
            "us-east-1",
            "test-session",
            on_device_auth=lambda info: None,
            poll_timeout_seconds=5,
            cancel_event=cancel_event,
        )
    elapsed = time.time() - started

    # Cancelled almost immediately -- well short of poll_timeout_seconds,
    # which is what a caller with no cancel_event (or the pre-fix code)
    # would have to wait out via LoginTimedOut instead.
    assert elapsed < 1.0


def test_login_without_cancel_event_still_times_out(monkeypatch):
    # No cancel_event given (e.g. the Add Row dialog's own sign-in flow) --
    # behaves exactly as before: polls until poll_timeout_seconds, then
    # raises LoginTimedOut rather than hanging forever.
    monkeypatch.setattr(aws_sso, "_oidc_client", lambda region: _NeverApprovedOidcClient())
    monkeypatch.setattr(aws_sso.webbrowser, "open", lambda url: None)
    with pytest.raises(aws_sso.LoginTimedOut):
        aws_sso.login(
            "https://example.awsapps.com/start",
            "us-east-1",
            "test-session",
            on_device_auth=lambda info: None,
            poll_timeout_seconds=0,
        )
