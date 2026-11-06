"""Private JSON response storage; no legacy diskcache database is opened or migrated."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, cast

from blue_data_planner.types import InvalidSettingError

_FORMAT = "blue-data-planner.llm-response.v1"
_MAX_CACHE_BYTES = 1024**3
_ENTRY_NAME = re.compile(r"blue-data-planner-v1-[0-9a-f]{64}\.json")


def cache_directory(configured: Path | None) -> Path:
    """Resolve trusted deployment configuration once, independently of call properties/CWD."""
    value = (
        configured if configured is not None else os.environ.get("BLUE_DATA_PLANNER_LLM_CACHE_DIR")
    )
    directory = (
        Path(value).expanduser()
        if value is not None
        else Path.home() / ".cache" / "blue-data-planner" / "llm-v1"
    )
    if not directory.is_absolute():
        raise InvalidSettingError("the LLM cache directory must be an absolute deployment path")
    return directory


def _filename(key: str) -> str:
    digest = hashlib.sha256(f"{_FORMAT}\0{key}".encode()).hexdigest()
    return f"blue-data-planner-v1-{digest}.json"


class JSONResponseCache:
    """Owner-only storage with atomic writes and directory-relative, no-follow file access."""

    def __init__(self, directory: Path) -> None:
        if os.name != "posix":
            raise InvalidSettingError("private LLM caching requires POSIX file permissions")
        self.directory = directory
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as error:
            raise InvalidSettingError(f"cannot create the LLM cache directory: {error}") from error
        with self._directory():
            pass

    @contextmanager
    def _directory(self) -> Generator[int, None, None]:
        try:
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:
            raise InvalidSettingError(f"cannot open the LLM cache directory: {error}") from error
        try:
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise InvalidSettingError(
                    "the LLM cache directory must be owned by the current user with mode 0700"
                )
            yield descriptor
        finally:
            os.close(descriptor)

    def get(self, key: str) -> dict[str, Any] | None:
        """Read JSON only; corrupt, foreign, linked, and non-regular entries are cache misses."""
        with self._directory() as directory:
            try:
                descriptor = os.open(
                    _filename(key),
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, encoding="utf-8") as stream:
                    info = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_size > _MAX_CACHE_BYTES
                        # Another writer may have replaced this opened entry, leaving zero
                        # links. Its descriptor still holds a complete, private JSON file.
                        or info.st_nlink > 1
                    ):
                        return None
                    record: Any = json.load(stream)
            except (OSError, UnicodeError, ValueError, RecursionError):
                return None
        if not isinstance(record, dict):
            return None
        entry = cast("dict[str, Any]", record)
        response: Any = entry.get("response")
        if entry.get("format") != _FORMAT or not isinstance(response, dict):
            return None
        return cast("dict[str, Any]", response)

    def set(self, key: str, response: dict[str, Any]) -> None:
        """Publish one complete JSON file, never overwriting through an existing link."""
        try:
            encoded = json.dumps({"format": _FORMAT, "response": response}, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            # A provider may attach non-JSON metadata. Return its successful response as usual,
            # but do not cache it or fall back to a general object serializer.
            return
        if len(encoded) > _MAX_CACHE_BYTES:
            return
        with self._directory() as directory:
            temporary = f".{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(encoded)
                os.replace(temporary, _filename(key), src_dir_fd=directory, dst_dir_fd=directory)
                _trim(directory, _filename(key))
            except OSError as error:
                raise InvalidSettingError(
                    f"cannot write the LLM response cache: {error}"
                ) from error
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=directory)


def _trim(directory: int, protected: str) -> None:
    """Keep up to 1 GiB of responses, retiring older entries after atomic publication."""
    entries: list[tuple[int, str, int]] = []
    with os.scandir(directory) as children:
        for child in children:
            if not _ENTRY_NAME.fullmatch(child.name):
                continue
            try:
                info = child.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                entries.append((info.st_mtime_ns, child.name, info.st_size))
    total = sum(size for _, _, size in entries)
    for _, name, size in sorted(entries):
        if total <= _MAX_CACHE_BYTES:
            break
        if name != protected:
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=directory)
            total -= size
