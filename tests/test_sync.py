import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aws_cred_manager import app_paths, config_store, sync


def _isolate(tmp_path, monkeypatch):
    """Point every path this app touches at a throwaway tmp_path tree
    instead of the real ~/.aws and app-data directories.
    """
    aws_dir = tmp_path / "aws"
    data_dir = tmp_path / "appdata"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_paths, "aws_dir", lambda: aws_dir)
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: data_dir)
    return aws_dir


def test_add_profile_does_not_write_to_config(tmp_path, monkeypatch):
    aws_dir = _isolate(tmp_path, monkeypatch)
    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")
    profile = config_store.ManagedProfile(
        profile_name="prod-admin", account_id="111122223333", account_name="Prod",
        role_name="AdministratorAccess", region="us-east-1",
    )

    sync.add_profile(settings, profile)

    config_path = aws_dir / "config"
    assert not config_path.exists() or "prod-admin" not in config_path.read_text()


def test_cleanup_removes_legacy_config_sections_left_by_older_versions(tmp_path, monkeypatch):
    aws_dir = _isolate(tmp_path, monkeypatch)
    aws_dir.mkdir(parents=True, exist_ok=True)
    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")

    # Simulate what an older version of this app used to write into
    # ~/.aws/config -- an sso-session block plus one sso_session-based
    # profile per managed row, alongside static keys for the same profile
    # name in ~/.aws/credentials. That combination is exactly what caused
    # the "conflict" this fix addresses.
    config_path = aws_dir / "config"
    config_path.write_text(
        "[sso-session aws-cred-manager]\n"
        "sso_start_url = https://example.awsapps.com/start\n"
        "sso_region = us-east-1\n"
        "\n"
        "[profile prod-admin]\n"
        "sso_session = aws-cred-manager\n"
        "sso_account_id = 111122223333\n"
        "sso_role_name = AdministratorAccess\n"
        "region = us-east-1\n"
        "\n"
        "[profile hand-written]\n"
        "region = eu-west-1\n"
    )

    profile = config_store.ManagedProfile(
        profile_name="prod-admin", account_id="111122223333", account_name="Prod",
        role_name="AdministratorAccess", region="us-east-1",
    )

    sync.cleanup_legacy_config_sections(settings, [profile])

    text = config_path.read_text()
    assert "[profile prod-admin]" not in text
    assert "[sso-session aws-cred-manager]" not in text
    # Sections this app doesn't own are always left alone.
    assert "[profile hand-written]" in text
    assert "region = eu-west-1" in text


def test_refresh_credentials_only_writes_credentials_file(tmp_path, monkeypatch):
    aws_dir = _isolate(tmp_path, monkeypatch)
    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")
    profile = config_store.ManagedProfile(
        profile_name="prod-admin", account_id="111122223333", account_name="Prod",
        role_name="AdministratorAccess", region="us-east-1",
    )

    class FakeToken:
        pass

    class FakeCreds:
        access_key_id = "AKIAFAKE"
        secret_access_key = "secretfake"
        session_token = "tokenfake"
        expiration_epoch_ms = 9999999999999

    monkeypatch.setattr(sync.aws_sso, "ensure_valid_token", lambda *a, **k: FakeToken())
    monkeypatch.setattr(sync.aws_sso, "get_role_credentials", lambda *a, **k: FakeCreds())

    sync.refresh_credentials(settings, [profile], on_device_auth=lambda info: None)

    creds_text = (aws_dir / "credentials").read_text()
    assert "[prod-admin]" in creds_text
    assert "aws_access_key_id = AKIAFAKE" in creds_text

    config_path = aws_dir / "config"
    assert not config_path.exists() or "prod-admin" not in config_path.read_text()


def test_paused_profile_is_skipped_by_refresh(tmp_path, monkeypatch):
    aws_dir = _isolate(tmp_path, monkeypatch)
    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")
    active = config_store.ManagedProfile(
        profile_name="prod-admin", account_id="111122223333", account_name="Prod",
        role_name="AdministratorAccess", region="us-east-1",
    )
    paused = config_store.ManagedProfile(
        profile_name="staging-deploy", account_id="444455556666", account_name="Staging",
        role_name="DeployRole", region="us-east-1", paused=True,
    )

    calls = []

    class FakeToken:
        pass

    class FakeCreds:
        access_key_id = "AKIAFAKE"
        secret_access_key = "secretfake"
        session_token = "tokenfake"
        expiration_epoch_ms = 9999999999999

    def fake_get_role_credentials(token, account_id, role_name):
        calls.append(account_id)
        return FakeCreds()

    monkeypatch.setattr(sync.aws_sso, "ensure_valid_token", lambda *a, **k: FakeToken())
    monkeypatch.setattr(sync.aws_sso, "get_role_credentials", fake_get_role_credentials)

    sync.refresh_credentials(settings, [active, paused], on_device_auth=lambda info: None)

    # Only the non-paused profile should ever have hit AWS...
    assert calls == ["111122223333"]

    creds_text = (aws_dir / "credentials").read_text()
    assert "[prod-admin]" in creds_text
    # ...and the paused one must not have gotten fresh credentials written.
    assert "[staging-deploy]" not in creds_text


def test_refresh_credentials_noop_when_everything_paused(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")
    paused = config_store.ManagedProfile(
        profile_name="staging-deploy", account_id="444455556666", account_name="Staging",
        role_name="DeployRole", region="us-east-1", paused=True,
    )

    def fail_if_called(*a, **k):
        raise AssertionError("should never attempt a login when every profile is paused")

    monkeypatch.setattr(sync.aws_sso, "ensure_valid_token", fail_if_called)
    monkeypatch.setattr(sync.aws_sso, "login", fail_if_called)

    result = sync.refresh_credentials(settings, [paused], on_device_auth=lambda info: None)
    assert result.errors == {}
