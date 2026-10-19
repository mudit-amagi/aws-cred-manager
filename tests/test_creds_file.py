import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aws_cred_manager import creds_file


def test_new_file_created(tmp_path):
    path = tmp_path / "config"
    creds_file.upsert_sections(path, {"profile a": [("region", "us-east-1")]})
    text = path.read_text()
    assert "[profile a]" in text
    assert "region = us-east-1" in text


def test_unmanaged_sections_survive_multiple_rewrites(tmp_path):
    path = tmp_path / "config"
    path.write_text("[profile hand-written]\nregion = eu-west-1\naws_access_key_id = ABC\n")

    creds_file.upsert_sections(path, {"profile managed-1": [("region", "us-east-1")]})
    creds_file.upsert_sections(path, {"profile managed-1": [("region", "us-west-2")]})
    creds_file.upsert_sections(path, {"profile managed-2": [("region", "ap-south-1")]})

    text = path.read_text()
    assert "[profile hand-written]" in text
    assert "aws_access_key_id = ABC" in text
    assert "region = eu-west-1" in text
    assert "[profile managed-1]" in text
    assert "region = us-west-2" in text  # overwritten, not duplicated
    assert text.count("[profile managed-1]") == 1
    assert "[profile managed-2]" in text


def test_removed_profiles_fully_deleted(tmp_path):
    path = tmp_path / "credentials"
    creds_file.upsert_sections(path, {"a": [("aws_access_key_id", "1")], "b": [("aws_access_key_id", "2")]})
    creds_file.upsert_sections(path, {}, remove_headers=["a"])
    text = path.read_text()
    assert "[a]" not in text
    assert "[b]" in text


def test_file_permissions_restricted(tmp_path):
    path = tmp_path / "credentials"
    creds_file.upsert_sections(path, {"a": [("aws_access_key_id", "1")]})
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_existing_section_names(tmp_path):
    path = tmp_path / "config"
    path.write_text("[profile a]\nregion = us-east-1\n\n[profile b]\nregion = us-west-2\n")
    assert creds_file.existing_section_names(path) == ["profile a", "profile b"]


def test_existing_section_names_missing_file(tmp_path):
    assert creds_file.existing_section_names(tmp_path / "nope") == []


def test_preamble_before_first_section_preserved(tmp_path):
    path = tmp_path / "config"
    path.write_text("# a stray comment\n\n[profile a]\nregion = us-east-1\n")
    creds_file.upsert_sections(path, {"profile a": [("region", "us-west-2")]})
    text = path.read_text()
    assert text.startswith("# a stray comment")


def test_atomic_write_no_partial_file_visible_to_concurrent_reader(tmp_path):
    import threading
    import time

    from aws_cred_manager.creds_file import _atomic_write_text

    path = tmp_path / "credentials"
    path.write_text("[a]\naws_access_key_id = ORIGINAL\n")

    observed_empty_or_partial = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                text = path.read_text()
            except FileNotFoundError:
                continue
            if text != "" and "[a]" not in text and "[b]" not in text:
                observed_empty_or_partial.append(text)
            if len(text) > 0 and not text.rstrip().endswith(("ORIGINAL", "NEWVALUE")):
                observed_empty_or_partial.append(text)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    for _ in range(200):
        _atomic_write_text(path, "[b]\naws_access_key_id = NEWVALUE\n")
        _atomic_write_text(path, "[a]\naws_access_key_id = ORIGINAL\n")
    stop.set()
    reader_thread.join(timeout=2)

    assert observed_empty_or_partial == [], f"Reader observed a torn write: {observed_empty_or_partial}"


def test_atomic_write_sets_restrictive_permissions(tmp_path):
    from aws_cred_manager.creds_file import _atomic_write_text

    path = tmp_path / "credentials"
    _atomic_write_text(path, "[a]\naws_access_key_id = X\n")
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
