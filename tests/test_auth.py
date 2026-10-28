import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from playwright.sync_api import Error as PlaywrightError

from yoink.auth import SessionCookie, SessionStore, login
from yoink.errors import YoinkError


def session(*, expires: int | None = None) -> tuple[SessionCookie, ...]:
    return tuple(
        SessionCookie(".x.com", "/", True, expires, name, "test-secret", True)
        for name in ("auth_token", "ct0")
    )


def test_private_session_roundtrip_and_logout(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    cookies = session(expires=int(time.time()) + 3600)
    store.save(cookies)
    assert store.load() == cookies
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert "test-secret" not in repr(cookies)
    assert store.logout()
    assert not store.logout()
    assert store.load() is None


def test_restricts_existing_permissions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    store.save(session())
    store.directory.chmod(0o755)
    store.path.chmod(0o644)
    store.load()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700


def test_expired_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    expires = int(time.time()) + 3600
    store.save(session(expires=expires))
    store.path.write_text(store.path.read_text().replace(str(expires), "1"))
    with pytest.raises(YoinkError, match="expired"):
        store.load()


@pytest.mark.parametrize(
    "content", ["bad cookie", ".evil.test\tTRUE\t/\tTRUE\t0\tauth_token\tsecret"]
)
def test_corrupt_session_is_safe(tmp_path: Path, content: str) -> None:
    store = SessionStore(tmp_path / "private")
    store.directory.mkdir()
    store.path.write_text(content)
    with pytest.raises(YoinkError) as error:
        store.load()
    assert "secret" not in str(error.value)


def test_incomplete_session_not_saved(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    with pytest.raises(YoinkError):
        store.save(session()[:1])
    assert not store.path.exists()


def test_only_x_cookies_are_saved(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    other = SessionCookie("other.test", "/", True, None, "tracking", "unrelated-secret")
    store.save((*session(), other))
    assert "unrelated-secret" not in store.path.read_text()


def test_refuses_repository_storage(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    store = SessionStore(tmp_path / "session")
    with pytest.raises(YoinkError, match="outside a Git"):
        store.save(session())
    assert not store.path.exists()


def test_refuses_symlink_directory(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(YoinkError, match="symlink"):
        SessionStore(linked).save(session())


def test_refuses_cookie_symlink(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    store.directory.mkdir()
    target = tmp_path / "target"
    target.write_text("do not modify")
    store.path.symlink_to(target)
    with pytest.raises(OSError):
        store.load()
    assert target.read_text() == "do not modify"


def test_rejects_cookie_line_injection(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    injected = SessionCookie(".x.com", "/", True, None, "bad", "value\nnew-cookie")
    with pytest.raises(YoinkError, match="Invalid session cookie"):
        store.save((*session(), injected))
    assert not store.path.exists()


def test_browser_login_failure_preserves_saved_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    store.save(session())
    with patch("yoink.auth.sync_playwright") as playwright:
        browser = playwright.return_value.__enter__.return_value.chromium.launch.return_value
        context = browser.new_context.return_value
        context.new_page.return_value.get_by_test_id.return_value.wait_for.side_effect = (
            PlaywrightError("timeout with SECRET diagnostic")
        )
        with pytest.raises(YoinkError, match="did not complete") as error:
            login(store, timeout=1)
        browser.close.assert_called_once()
    assert store.load() == session()
    assert "SECRET" not in str(error.value)


def test_browser_login_saves_only_session_cookies(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "private")
    with patch("yoink.auth.sync_playwright") as playwright:
        browser = playwright.return_value.__enter__.return_value.chromium.launch.return_value
        context = browser.new_context.return_value
        context.cookies.return_value = [
            {
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "expires": -1,
                "name": name,
                "value": "test-secret",
                "httpOnly": True,
            }
            for name in ("auth_token", "ct0")
        ]
        login(store)
    assert store.load() == session()
    assert list(store.directory.iterdir()) == [store.path]
