"""Support for unattended runs: file-based configuration and single-instance locking.

A scheduled run has no terminal, no shell profile, and no user watching it. These
helpers let the CLI read its configuration from an owner-only file and refuse to
start a second copy while one is still working.
"""

from __future__ import annotations

import fcntl
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from stat import S_IMODE
from typing import Iterator

from email_triage.config import ConfigurationError


class LockBusy(RuntimeError):
    """Raised when another run of this project still holds the lock."""


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ConfigurationError(f"Invalid configuration on line {number}: expected KEY=VALUE")
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise ConfigurationError(f"Invalid configuration on line {number}: empty key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path) -> list[str]:
    """Load KEY=VALUE settings into the environment. Existing variables win."""

    if not path.exists():
        raise ConfigurationError(f"Configuration file does not exist: {path}")
    mode = S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigurationError(
            f"{path} is readable by other users (mode {mode:o}). Run: chmod 600 {path}"
        )
    loaded: list[str] = []
    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        if key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


@contextmanager
def single_instance_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock for the duration of one run, or raise LockBusy."""

    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LockBusy(f"another run already holds {path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        os.chmod(path, 0o600)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def is_interactive() -> bool:
    """True when a person can answer a device-code prompt."""

    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False
