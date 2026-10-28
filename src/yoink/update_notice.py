"""checking releases in the background. a version notice shouldn't hold a download hostage."""

import fcntl
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self, cast

from yoink import __version__
from yoink.errors import YoinkError
from yoink.installation import managed_installation
from yoink.update import RELEASES_URL, download, release_version

CHECK_INTERVAL = 24 * 60 * 60
CACHE_FILE = "update-check.json"
LOCK_FILE = ".update-check.lock"


@dataclass(frozen=True)
class Cache:
    checked_at: float = 0
    latest: str | None = None


def read_cache(root: Path) -> Cache:
    try:
        descriptor = os.open(root / CACHE_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return Cache()
            content = stream.read(4097)
        if len(content) > 4096:
            return Cache()
        value: object = json.loads(content)
        if not isinstance(value, dict):
            return Cache()
        data = cast(dict[str, object], value)
        if set(data) != {"checked_at", "latest"}:
            return Cache()
        checked_at, latest = data.get("checked_at"), data.get("latest")
        if isinstance(checked_at, bool) or not isinstance(checked_at, (float, int)):
            return Cache()
        if not math.isfinite(checked_at) or checked_at < 0:
            return Cache()
        if latest is not None:
            if not isinstance(latest, str) or len(latest) > 64:
                return Cache()
            latest = release_version(latest.encode("ascii"))
        return Cache(float(checked_at), latest)
    except (OSError, ValueError, OverflowError, RecursionError, UnicodeError, YoinkError):
        return Cache()


def _lock(root: Path) -> int | None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            root / LOCK_FILE,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not a regular lock file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        return None


def _write_cache(root: Path, cache: Cache) -> None:
    descriptor, name = tempfile.mkstemp(dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(asdict(cache), stream)
        temporary.replace(root / CACHE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def record_latest(root: Path, latest: str, *, attempt: float | None = None) -> None:
    if not (root / "tools/yoink-cli").is_dir():
        return
    descriptor = _lock(root)
    if descriptor is None:
        return
    try:
        if not (root / "tools/yoink-cli").is_dir():
            return
        cache = read_cache(root)
        if attempt is not None and cache.checked_at != attempt:
            return
        _write_cache(root, Cache(time.time() if attempt is None else attempt, latest))
    except OSError:
        pass
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class UpdateNotice:
    root: Path

    @classmethod
    def start(cls) -> Self | None:
        if os.environ.get("YOINK_NO_UPDATE_CHECK") == "1" or not sys.stderr.isatty():
            return None
        try:
            root = Path(managed_installation().root)
            notice = cls(root)
            descriptor = _lock(root)
            if descriptor is None:
                return notice
            try:
                cache = read_cache(root)
                now = time.time()
                if cache.checked_at > 0 and 0 <= now - cache.checked_at < CHECK_INTERVAL:
                    return notice
                # reserving the check before spawning, so concurrent commands don't pile on
                _write_cache(root, Cache(now, cache.latest))
                subprocess.Popen(
                    [sys.executable, "-m", "yoink.update_notice"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                )
            finally:
                os.close(descriptor)
            return notice
        except (OSError, YoinkError):
            return None

    def show(self) -> None:
        if os.environ.get("YOINK_NO_UPDATE_CHECK") == "1" or not sys.stderr.isatty():
            return
        latest = read_cache(self.root).latest
        if latest is None:
            return
        if tuple(map(int, latest.split("."))) <= tuple(
            int(part) for part in __version__.split(".")
        ):
            return
        with suppress(OSError, ValueError):
            print(f"\nYoink {latest} is available. Run 'yoink update'.", file=sys.stderr)


def refresh() -> None:
    try:
        root = Path(managed_installation().root)
        cache = read_cache(root)
        if cache.checked_at <= 0:
            return
        latest = release_version(
            download(f"{RELEASES_URL}/latest/download/VERSION", limit=64, timeout=2),
        )
        record_latest(root, latest, attempt=cache.checked_at)
    except (OSError, YoinkError):
        pass


def clear_cache(root: Path) -> None:
    descriptor = _lock(root)
    if descriptor is None:
        return
    try:
        (root / CACHE_FILE).unlink(missing_ok=True)
        (root / LOCK_FILE).unlink(missing_ok=True)
    except OSError:
        pass
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    refresh()
