import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aws_cred_manager import app_paths, config_store


def test_update_profile_expiration_does_not_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    profile = config_store.ManagedProfile(
        profile_name="p1", account_id="123", account_name="Acct", role_name="Role", region="us-east-1"
    )
    config_store.save_profiles([profile])

    # This nests a save_profiles() call inside an already-held lock. With a
    # plain (non-reentrant) threading.Lock this call would hang forever.
    config_store.update_profile_expiration("p1", 1234567890000)

    reloaded = config_store.load_profiles()
    assert reloaded[0].last_expiration_epoch == 1234567890000


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    settings = config_store.Settings(sso_start_url="https://example.awsapps.com/start", sso_region="us-east-1")
    config_store.save_settings(settings)
    reloaded = config_store.load_settings()
    assert reloaded.sso_start_url == settings.sso_start_url
    assert reloaded.is_configured()


def test_load_settings_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    settings = config_store.load_settings()
    assert settings.sso_start_url == ""
    assert not settings.is_configured()


def test_load_profiles_corrupt_json_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    (tmp_path / "managed_profiles.json").write_text("{not valid json")
    assert config_store.load_profiles() == []


def test_set_profile_paused_toggles_only_that_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    profiles = [
        config_store.ManagedProfile(profile_name="a", account_id="1", account_name="A", role_name="R", region="us-east-1"),
        config_store.ManagedProfile(profile_name="b", account_id="2", account_name="B", role_name="R", region="us-east-1"),
    ]
    config_store.save_profiles(profiles)

    result = config_store.set_profile_paused("a", True)
    by_name = {p.profile_name: p for p in result}
    assert by_name["a"].paused is True
    assert by_name["b"].paused is False

    result = config_store.set_profile_paused("a", False)
    by_name = {p.profile_name: p for p in result}
    assert by_name["a"].paused is False


def test_update_profiles_after_refresh_does_not_clobber_concurrent_pause_toggle(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    profile = config_store.ManagedProfile(
        profile_name="a", account_id="1", account_name="A", role_name="R", region="us-east-1"
    )
    config_store.save_profiles([profile])

    # Simulate a refresh that was kicked off using an in-memory profile
    # loaded *before* the user paused it in the UI. The refresher's own
    # copy has paused=False baked in -- if update_profiles_after_refresh
    # naively overwrote the whole list with that stale copy, the pause
    # toggle below would be lost.
    stale_copy = config_store.ManagedProfile(
        profile_name="a", account_id="1", account_name="A", role_name="R",
        region="us-east-1", paused=False,
    )
    stale_copy.last_expiration_epoch = 1234567890000
    stale_copy.last_error = None

    config_store.set_profile_paused("a", True)  # user pauses it mid-refresh
    merged = config_store.update_profiles_after_refresh({"a": stale_copy})

    result = merged[0]
    assert result.last_expiration_epoch == 1234567890000  # refresh result applied
    assert result.paused is True  # concurrent pause toggle survived


def test_set_all_profiles_paused_bulk_sets_every_row(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    profiles = [
        config_store.ManagedProfile(profile_name="a", account_id="1", account_name="A", role_name="R", region="us-east-1"),
        config_store.ManagedProfile(profile_name="b", account_id="2", account_name="B", role_name="R", region="us-east-1", paused=True),
    ]
    config_store.save_profiles(profiles)

    result = config_store.set_all_profiles_paused(True)
    assert all(p.paused for p in result)

    result = config_store.set_all_profiles_paused(False)
    assert all(not p.paused for p in result)


def test_individual_resume_after_bulk_pause_is_indistinguishable_from_never_bulk_paused(tmp_path, monkeypatch):
    # This is the actual behavior the user asked for: pause everything in
    # one click, then resume just one row -- and that row should behave
    # exactly like a normally-active row from then on, not like it's
    # still somehow tied to the bulk pause.
    monkeypatch.setattr(app_paths, "app_data_dir", lambda: tmp_path)
    profiles = [
        config_store.ManagedProfile(profile_name="a", account_id="1", account_name="A", role_name="R", region="us-east-1"),
        config_store.ManagedProfile(profile_name="b", account_id="2", account_name="B", role_name="R", region="us-east-1"),
    ]
    config_store.save_profiles(profiles)

    config_store.set_all_profiles_paused(True)
    result = config_store.set_profile_paused("a", False)

    by_name = {p.profile_name: p for p in result}
    assert by_name["a"].paused is False
    assert by_name["b"].paused is True
