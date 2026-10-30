"""Ghostline backend. One version for the whole repository: the file VERSION at the repository root, copied into the
image at /app/VERSION (GHOSTLINE_VERSION_FILE); GHOSTLINE_VERSION overrides it (tests)."""
from __future__ import annotations

import os
from pathlib import Path


def _read_version() -> str:
    env = os.environ.get("GHOSTLINE_VERSION")
    if env:
        return env.strip()
    for candidate in (os.environ.get("GHOSTLINE_VERSION_FILE"), Path(__file__).resolve().parents[2] / "VERSION"):
        if candidate and Path(candidate).is_file():
            text = Path(candidate).read_text(encoding="utf-8").strip()
            if text:
                return text
    return "0.0.0"


__version__ = _read_version()


def commit() -> str:
    """The commit the running image serves: GHOSTLINE_COMMIT, else SOURCE_COMMIT (Coolify sets it on the container at
    run time; its build passes no commit), else /app/COMMIT."""
    for name in ("GHOSTLINE_COMMIT", "SOURCE_COMMIT"):
        env = os.environ.get(name, "").strip()
        if env and env != "unknown":
            return env
    p = Path(os.environ.get("GHOSTLINE_COMMIT_FILE", "/app/COMMIT"))
    if p.is_file():
        text = p.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "unknown"
