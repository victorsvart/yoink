import sqlite3
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from yoink.auth import SessionStore, import_browser_session
from yoink.browser_cookies import CookieBrowser, read_browser_cookies
from yoink.errors import YoinkError


def firefox_profile(directory: Path, *, expires: int) -> None:
    directory.mkdir()
    with sqlite3.connect(directory / "cookies.sqlite") as database:
        database.execute("""CREATE TABLE moz_cookies (
            host TEXT, name TEXT, value TEXT, path TEXT, expiry INTEGER, isSecure INTEGER
        )""")
        database.executemany(
            "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?)",
            [
                (".x.com", name, "synthetic-session", "/", expires, 1)
                for name in ("auth_token", "ct0")
            ]
            + [("unrelated.test", "tracking", "other-site-secret", "/", expires, 1)],
        )


def test_real_reader_imports_only_x_from_synthetic_profile(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = tmp_path / "synthetic-firefox"
    firefox_profile(profile, expires=int(time.time()) + 3600)
    store = SessionStore(tmp_path / "private")
    import_browser_session(store, CookieBrowser.FIREFOX, profile=str(profile))
    cookies = store.load()
    assert cookies is not None
    assert {cookie.name for cookie in cookies} == {"auth_token", "ct0"}
    assert "other-site-secret" not in store.path.read_text()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_expired_import_preserves_existing_session(tmp_path: Path) -> None:
    profile = tmp_path / "synthetic-firefox"
    firefox_profile(profile, expires=int(time.time()) + 3600)
    store = SessionStore(tmp_path / "private")
    import_browser_session(store, CookieBrowser.FIREFOX, profile=str(profile))
    saved = store.path.read_bytes()
    with sqlite3.connect(profile / "cookies.sqlite") as database:
        database.execute("UPDATE moz_cookies SET expiry = 1")
    with pytest.raises(YoinkError, match="No active X session"):
        import_browser_session(store, CookieBrowser.FIREFOX, profile=str(profile))
    assert store.path.read_bytes() == saved


def test_browser_permission_failure_is_redacted() -> None:
    with patch("yoink.browser_cookies.import_module") as module:
        module.return_value.extract_cookies_from_browser.side_effect = PermissionError("SECRET")
        with pytest.raises(YoinkError, match="OS access permissions") as error:
            read_browser_cookies(CookieBrowser.CHROME, None)
    assert "SECRET" not in str(error.value)


def test_safari_permission_failure_explains_manual_access() -> None:
    with patch("yoink.browser_cookies.import_module") as module:
        module.return_value.extract_cookies_from_browser.side_effect = PermissionError("SECRET")
        with pytest.raises(YoinkError, match="Full Disk Access") as error:
            read_browser_cookies(CookieBrowser.SAFARI, None)
    assert "cannot grant this permission itself" in str(error.value)
    assert "SECRET" not in str(error.value)


def test_rejects_invalid_browser_reader_result() -> None:
    with patch("yoink.browser_cookies.import_module") as module:
        module.return_value.extract_cookies_from_browser.return_value = object()
        with pytest.raises(YoinkError, match="invalid data"):
            read_browser_cookies(CookieBrowser.CHROME, None)
