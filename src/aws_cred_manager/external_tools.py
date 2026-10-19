"""Hand off a managed profile's live, currently-valid credentials to two
external tools -- opening the AWS Console in a browser, and opening an S3
bucket listing in Cyberduck. Both read credentials fresh from
~/.aws/credentials (the single place this app keeps them up to date) --
neither keeps its own copy, so there's nothing here that can go stale.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict
from xml.sax.saxutils import escape as xml_escape

from . import app_paths, creds_file

logger = logging.getLogger(__name__)

FEDERATION_ENDPOINT = "https://signin.aws.amazon.com/federation"
CONSOLE_HOME = "https://console.aws.amazon.com/console/home"


class ExternalToolError(Exception):
    pass


def _read_profile_credentials(profile_name: str) -> Dict[str, str]:
    creds = creds_file.read_section(app_paths.aws_credentials_path(), profile_name)
    if not creds or "aws_access_key_id" not in creds:
        raise ExternalToolError(
            f"No credentials on file for '{profile_name}' yet -- refresh first."
        )
    return creds


def _open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        import os

        os.startfile(str(path))  # noqa: S606 -- opening a file we just wrote, not user input
    else:
        raise ExternalToolError("Not supported on this OS.")


# -- AWS Management Console --------------------------------------------------

def open_aws_console(profile_name: str, region: str) -> None:
    """Open the AWS Management Console in the default browser, signed in as
    this profile's role, using AWS's official console federation endpoint:
    https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_enable-console-custom-url.html
    """
    import webbrowser

    creds = _read_profile_credentials(profile_name)
    session = json.dumps({
        "sessionId": creds["aws_access_key_id"],
        "sessionKey": creds["aws_secret_access_key"],
        "sessionToken": creds.get("aws_session_token", ""),
    })
    # No SessionDuration parameter here: it's only valid for credentials
    # minted via GetFederationToken. These are AssumeRole-derived (SSO role
    # credentials) -- AWS rejects the federation request if it's included.
    token_url = f"{FEDERATION_ENDPOINT}?{urllib.parse.urlencode({'Action': 'getSigninToken', 'Session': session})}"

    try:
        with urllib.request.urlopen(token_url, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ExternalToolError(f"Could not reach AWS's console federation endpoint: {exc}") from exc

    signin_token = payload.get("SigninToken")
    if not signin_token:
        raise ExternalToolError(f"AWS did not return a sign-in token: {payload}")

    destination = f"{CONSOLE_HOME}?region={region}" if region else "https://console.aws.amazon.com/"
    login_url = f"{FEDERATION_ENDPOINT}?{urllib.parse.urlencode({'Action': 'login', 'Issuer': 'AWS Credential Manager', 'Destination': destination, 'SigninToken': signin_token})}"
    webbrowser.open(login_url)


# -- Cyberduck ----------------------------------------------------------

# Verbatim copy of Cyberduck's own, officially documented connection
# profile "S3 (Credentials from AWS Command Line Interface)"
# (https://github.com/iterate-ch/profiles -- linked from
# https://docs.cyberduck.io/protocols/s3/#connecting-using-credentials-from-aws-command-line-interface).
# This is not something we invented: it's Cyberduck's own supported
# mechanism for reading aws_access_key_id / aws_secret_access_key /
# aws_session_token directly from a named profile in ~/.aws/credentials,
# so Cyberduck always sees whatever this app most recently refreshed --
# no credentials are ever copied into a bookmark file or anywhere else.
_CYBERDUCK_PROFILE_NAME = "S3 (Credentials from AWS Command Line Interface)"
_CYBERDUCK_PROFILE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
    <dict>
        <key>Protocol</key>
        <string>s3</string>
        <key>Vendor</key>
        <string>s3-cli</string>
        <key>Description</key>
        <string>S3 (Credentials from AWS Command Line Interface)</string>
        <key>Default Nickname</key>
        <string>S3 (Credentials from AWS Command Line Interface)</string>
        <key>Username Placeholder</key>
        <string>Profile Name in ~/.aws/credentials</string>
        <key>Password Configurable</key>
        <false/>
        <key>Token Configurable</key>
        <true/>
        <key>Anonymous Configurable</key>
        <false/>
    </dict>
</plist>
"""


def _cyberduck_profile_path() -> Path:
    return app_paths.app_data_dir() / f"{_CYBERDUCK_PROFILE_NAME}.cyberduckprofile"


def _cyberduck_profile_marker() -> Path:
    return app_paths.app_data_dir() / ".cyberduck_profile_installed"


def _cyberduck_bookmark_xml(profile_name: str, nickname: str) -> str:
    # This key is "Provider", NOT "Vendor" -- verified directly against
    # Cyberduck's open-source deserializer
    # (HostDictionary.deserialize(), core/src/main/java/ch/cyberduck/core/serializer/HostDictionary.java):
    # it reads "Protocol" and "Provider" from a bookmark and resolves them
    # via ProtocolFactory.forName(identifier, provider), matching a
    # third-party profile only when provider equals that profile's Vendor
    # string ("s3-cli", installed above). A bookmark with no "Provider" key
    # falls through to state 1 ("matching bundled first with identifier
    # match") -- i.e. silently the *default* bundled S3 protocol, which is
    # exactly the "enter your Secret Access Key" prompt this produced
    # before this fix: "Vendor" is the connection *profile's own*
    # self-description field (confirmed from iterate-ch/profiles' actual
    # .cyberduckprofile source), not what a bookmark uses to reference it.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
    <dict>
        <key>Protocol</key>
        <string>s3</string>
        <key>Provider</key>
        <string>s3-cli</string>
        <key>Nickname</key>
        <string>{xml_escape(nickname)}</string>
        <key>Username</key>
        <string>{xml_escape(profile_name)}</string>
    </dict>
</plist>
"""


def _bookmark_path(profile_name: str) -> Path:
    bookmarks_dir = app_paths.app_data_dir() / "cyberduck_bookmarks"
    bookmarks_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in profile_name)
    return bookmarks_dir / f"{safe_name}.duck"


def open_in_cyberduck(profile_name: str, nickname: str) -> None:
    if sys.platform not in ("darwin", "win32"):
        raise ExternalToolError("Cyberduck integration is only available on macOS and Windows.")

    _read_profile_credentials(profile_name)  # fail fast with a clear message if not refreshed yet

    marker = _cyberduck_profile_marker()
    if not marker.exists():
        # First use only: install Cyberduck's CLI-credentials connection
        # profile. Cyberduck shows its own "install this profile?"
        # confirmation dialog for this -- that's a one-time, user-visible
        # step we can't and shouldn't skip (same reasoning as not bypassing
        # Gatekeeper). We deliberately do NOT chain straight into opening
        # the bookmark afterwards: that would race the user's click on
        # Cyberduck's own dialog. Ask them to click "Cyberduck" a second
        # time once they've approved it.
        profile_path = _cyberduck_profile_path()
        profile_path.write_text(_CYBERDUCK_PROFILE_XML)
        _open_path(profile_path)
        marker.write_text(str(time.time()))
        raise ExternalToolError(
            "Cyberduck just opened to install its \"S3 (Credentials from AWS CLI)\" profile -- "
            "approve/install it in Cyberduck's dialog, then click Cyberduck here again."
        )

    bookmark = _bookmark_path(profile_name)
    bookmark.write_text(_cyberduck_bookmark_xml(profile_name, nickname))
    _open_path(bookmark)
