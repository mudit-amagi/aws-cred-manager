import calendar
import sys
import time
from pathlib import Path

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
