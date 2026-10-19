"""Non-destructive editing of ~/.aws/config and ~/.aws/credentials.

We can't use configparser here: round-tripping through it reformats the
whole file (reorders keys, strips comments, normalizes whitespace), which
would silently mangle anything a user or another tool put in these files
that we don't own. Instead this is a small hand-rolled, section-aware
editor: it splits the file into ordered (header, body_lines) sections,
replaces only the sections we manage, and leaves every other byte alone.

"Managed" sections are identified purely by header name, tracked by the
caller (see sync.py) via config_store's managed_profiles.json -- this file
has no opinion about which sections are "ours" beyond what it's told.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

Section = Tuple[str, List[str]]  # (header line without brackets, body lines)


def _split_sections(text: str) -> List[Section]:
    """Parse into an ordered list of (header, body_lines).

    A leading section with header "" holds any content before the first
    [section] header (blank lines, stray comments) -- this is preserved
    verbatim and never touched by upsert/remove.
    """
    sections: List[Section] = []
    header = ""
    body: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            sections.append((header, body))
            header = stripped[1:-1].strip()
            body = []
        else:
            body.append(line)
    sections.append((header, body))
    return sections


def _join_sections(sections: List[Section]) -> str:
    parts: List[str] = []
    for header, body in sections:
        if header:
            parts.append(f"[{header}]")
        parts.extend(body)
    text = "\n".join(parts).strip("\n")
    return text + "\n" if text else ""


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to `path` without ever leaving it truncated or partially
    written for a concurrent reader to observe.

    Path.write_text() truncates the file in place before writing the new
    content -- any process reading ~/.aws/credentials at exactly the wrong
    moment (the aws CLI, boto3 in another process, Cyberduck's own
    credential reader, ...) can see an empty or half-written file and fail
    to parse it. Writing to a temp file in the same directory and then
    os.replace()-ing it into place is atomic on both POSIX and Windows: a
    concurrent reader always sees either the old complete file or the new
    complete file, never something in between.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)  # 0600 -- these files hold secrets
        os.replace(tmp_name, path)  # atomic on POSIX and Windows
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def existing_section_names(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [header for header, _ in _split_sections(path.read_text()) if header]


def read_section(path: Path, header: str) -> Optional[Dict[str, str]]:
    """Parse one section's "key = value" lines into a dict. Returns None if
    the file or the section doesn't exist. Used to read back credentials
    this app already wrote (e.g. for handing off to another tool) without
    keeping a separate, potentially-stale in-memory copy.
    """
    if not path.exists():
        return None
    for h, body in _split_sections(path.read_text()):
        if h != header:
            continue
        result: Dict[str, str] = {}
        for line in body:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            result[key.strip()] = value.strip()
        return result
    return None


def upsert_sections(path: Path, new_sections: Dict[str, List[str]], remove_headers: List[str] = ()) -> None:
    """Replace/add the given sections (by exact header name) and drop any
    section whose header is in remove_headers. Every other section --
    managed or not, matched or not -- is left exactly as it was, in its
    original position. New sections are appended at the end in the order
    given.
    """
    existing = _split_sections(path.read_text()) if path.exists() else [("", [])]
    remove_set = set(remove_headers)

    result: List[Section] = []
    seen = set()
    for header, body in existing:
        if header == "":
            result.append((header, body))
            continue
        if header in remove_set:
            continue
        if header in new_sections:
            result.append((header, [f"{k} = {v}" for k, v in _pairs(new_sections[header])]))
            seen.add(header)
        else:
            result.append((header, body))

    for header, pairs in new_sections.items():
        if header not in seen:
            result.append((header, [f"{k} = {v}" for k, v in _pairs(pairs)]))

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _join_sections(result))


def _pairs(value) -> List[Tuple[str, str]]:
    # Accept either a dict or an already-ordered list of (key, value) pairs.
    if isinstance(value, dict):
        return list(value.items())
    return list(value)


def build_credentials_block(access_key_id: str, secret_access_key: str, session_token: str) -> List[Tuple[str, str]]:
    return [
        ("aws_access_key_id", access_key_id),
        ("aws_secret_access_key", secret_access_key),
        ("aws_session_token", session_token),
    ]
