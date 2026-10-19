import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aws_cred_manager import config_store, refresh_worker


def _profile(name, paused=False, expiration_epoch_ms=None):
    return config_store.ManagedProfile(
        profile_name=name, account_id="1", account_name="A", role_name="R",
        region="us-east-1", paused=paused, last_expiration_epoch=expiration_epoch_ms,
    )


def test_any_profile_needs_refresh_ignores_paused_profiles():
    expired = int((time.time() - 60) * 1000)
    paused_and_expired = _profile("paused", paused=True, expiration_epoch_ms=expired)
    assert refresh_worker.any_profile_needs_refresh([paused_and_expired]) is False


def test_any_profile_needs_refresh_still_true_for_active_expired_profile():
    expired = int((time.time() - 60) * 1000)
    active_expired = _profile("active", paused=False, expiration_epoch_ms=expired)
    paused_and_expired = _profile("paused", paused=True, expiration_epoch_ms=expired)
    assert refresh_worker.any_profile_needs_refresh([paused_and_expired, active_expired]) is True


def test_run_once_reports_all_paused_status_when_every_profile_is_paused(tmp_path, monkeypatch):
    from aws_cred_manager import app_paths
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)

    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")
    config_store.save_settings(settings)
    # Bulk-paused via the toolbar's Pause All, or paused one at a time --
    # either way, every profile having paused=True looks identical to the
    # worker.
    config_store.save_profiles([_profile("a", paused=True), _profile("b", paused=True)])

    def fail_if_called(*a, **k):
        raise AssertionError("refresh_credentials must not run when every profile is paused")

    monkeypatch.setattr(refresh_worker.sync, "refresh_credentials", fail_if_called)

    statuses = []
    worker = refresh_worker.RefreshWorker(
        on_device_auth=lambda info: None, on_status=statuses.append,
    )
    worker._run_once(force_login=True)  # force_login=True must not override a paused row

    assert statuses == ["all_paused"]


def test_run_once_still_refreshes_non_paused_rows_when_some_are_paused(tmp_path, monkeypatch):
    from aws_cred_manager import app_paths
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)

    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")
    config_store.save_settings(settings)
    expired = int((time.time() - 60) * 1000)
    config_store.save_profiles([
        _profile("paused", paused=True, expiration_epoch_ms=expired),
        _profile("active", paused=False, expiration_epoch_ms=expired),
    ])

    calls = []
    monkeypatch.setattr(
        refresh_worker.sync, "refresh_credentials",
        lambda *a, **k: calls.append(1) or refresh_worker.sync.RefreshResult(profiles=[], errors={}),
    )

    statuses = []
    worker = refresh_worker.RefreshWorker(
        on_device_auth=lambda info: None, on_status=statuses.append,
    )
    worker._run_once(force_login=False)

    assert calls == [1]
    assert statuses == ["refreshing", "ok"]


def test_ignored_signin_is_not_reopened_by_periodic_polls(tmp_path, monkeypatch):
    from aws_cred_manager import app_paths
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    config_store.save_settings(config_store.Settings(sso_start_url="https://x.awsapps.com/start", sso_region="us-east-1"))
    config_store.save_profiles([_profile("a")])  # never fetched -> needs refresh

    calls = []

    def fake_refresh(*a, **k):
        calls.append(k.get("force_login"))
        raise refresh_worker.aws_sso.LoginTimedOut("nobody approved")

    monkeypatch.setattr(refresh_worker.sync, "refresh_credentials", fake_refresh)
    monkeypatch.setattr(refresh_worker.aws_sso, "get_cached_token", lambda name: None)

    statuses = []
    worker = refresh_worker.RefreshWorker(on_device_auth=lambda i: None, on_status=statuses.append)

    worker._run_once(force_login=False)   # first attempt opens a tab, times out
    worker._run_once(force_login=False)   # periodic polls must NOT retry
    worker._run_once(force_login=False)
    assert calls == [False]
    assert statuses[-1] == "needs_login"

    worker._run_once(force_login=True)    # explicit Refresh Now does retry
    assert calls == [False, True]
