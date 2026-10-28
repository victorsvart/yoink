"""Interactive login and a private, cookie-only session store outside Git."""

import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_path
from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from yoink.browser_cookies import CookieBrowser, read_browser_cookies
from yoink.errors import YoinkError

_DOMAINS = {"x.com", ".x.com", "twitter.com", ".twitter.com"}
_LOGIN_LIMIT = re.compile(r"temporarily limited your login", re.IGNORECASE)


@dataclass(frozen=True)
class SessionCookie:
    domain: str
    path: str
    secure: bool
    expires: int | None
    name: str
    value: str = field(repr=False)
    http_only: bool = False

    def restore(self, context: BrowserContext) -> None:
        context.add_cookies(
            [
                {
                    "domain": self.domain,
                    "path": self.path,
                    "secure": self.secure,
                    "name": self.name,
                    "value": self.value,
                    "httpOnly": self.http_only,
                    "expires": float(self.expires) if self.expires is not None else -1,
                }
            ]
        )

    def to_netscape(self) -> str:
        domain = f"#HttpOnly_{self.domain}" if self.http_only else self.domain
        fields = (
            domain,
            "TRUE" if self.domain.startswith(".") else "FALSE",
            self.path,
            "TRUE" if self.secure else "FALSE",
            str(self.expires or 0),
            self.name,
            self.value,
        )
        if any(any(char in part for char in "\t\r\n\0") for part in fields):
            raise YoinkError("Invalid session cookie. Try logging in again.")
        return "\t".join(fields)


def _validate(cookies: tuple[SessionCookie, ...]) -> None:
    active = {
        cookie.name
        for cookie in cookies
        if cookie.domain in _DOMAINS
        and cookie.path == "/"
        and cookie.value
        and (cookie.expires is None or cookie.expires > time.time())
    }
    if not {"auth_token", "ct0"} <= active:
        raise YoinkError("The saved X session is expired or incomplete. Run 'yoink login' again.")


def _decode(text: str) -> tuple[SessionCookie, ...]:
    cookies: list[SessionCookie] = []
    try:
        for line in text.splitlines():
            if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            domain, _, path, secure, expiration, name, value = line.split("\t")
            http_only = domain.startswith("#HttpOnly_")
            domain = domain.removeprefix("#HttpOnly_")
            if domain not in _DOMAINS or not path.startswith("/"):
                raise ValueError("Invalid scope")
            expires = int(expiration)
            cookies.append(
                SessionCookie(
                    domain,
                    path,
                    secure == "TRUE",
                    expires if expires > 0 else None,
                    name,
                    value,
                    http_only,
                )
            )
    except ValueError:
        raise YoinkError("The saved session is damaged. Run 'yoink login' again.") from None
    result = tuple(cookies)
    _validate(result)
    return result


class SessionStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or user_data_path("yoink", appauthor=False)
        self.path = self.directory / "cookies.txt"

    def _secure_directory(self) -> None:
        if self.directory.is_symlink():
            raise YoinkError("The session directory must not be a symlink.")
        resolved = self.directory.resolve()
        if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
            raise YoinkError("Session data must be stored outside a Git repository.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def load(self) -> tuple[SessionCookie, ...] | None:
        if not self.directory.exists():
            return None
        self._secure_directory()
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise YoinkError("The saved session must be a regular file.")
            os.fchmod(stream.fileno(), 0o600)
            try:
                return _decode(stream.read())
            except UnicodeError:
                raise YoinkError("The saved session is damaged. Run 'yoink login' again.") from None

    def save(self, cookies: tuple[SessionCookie, ...]) -> None:
        cookies = tuple(cookie for cookie in cookies if cookie.domain in _DOMAINS)
        _validate(cookies)
        self._secure_directory()
        content = (
            "# Netscape HTTP Cookie File\n"
            + "\n".join(cookie.to_netscape() for cookie in cookies if cookie.domain in _DOMAINS)
            + "\n"
        )
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=".session-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def logout(self) -> bool:
        if not self.directory.exists():
            return False
        self._secure_directory()
        existed = self.path.exists() or self.path.is_symlink()
        self.path.unlink(missing_ok=True)
        return existed


def import_browser_session(
    store: SessionStore,
    browser: CookieBrowser,
    *,
    profile: str | None = None,
) -> None:
    """Copy only X cookies; do not attempt a login or export the whole cookie jar."""
    jar = read_browser_cookies(browser, profile)
    cookies = tuple(
        SessionCookie(
            cookie.domain,
            cookie.path,
            cookie.secure,
            cookie.expires,
            cookie.name,
            cookie.value or "",
            cookie.has_nonstandard_attr("HttpOnly") or cookie.name == "auth_token",
        )
        for cookie in jar
        if cookie.domain in _DOMAINS
    )
    try:
        _validate(cookies)
    except YoinkError:
        raise YoinkError(
            "No active X session was found in that browser/profile. Check that X works there "
            "and select the signed-in profile with --profile if needed."
        ) from None

    store.save(cookies)


def wait_for_login(page: Page, *, timeout: int) -> None:
    home = page.get_by_test_id("AppTabBar_Home_Link")
    limited = page.get_by_text(_LOGIN_LIMIT)
    # Hidden elements must not mask either a visible refusal or the home page.
    visible_outcome = home.or_(limited).and_(page.locator(":visible"))
    visible_outcome.first.wait_for(state="visible", timeout=timeout * 1000)
    if home.is_visible():
        return

    raise YoinkError(
        "X temporarily limited this login. Stop login attempts and try later; "
        "X advises waiting about an hour for temporary lockouts. If you already "
        "have a working browser session, use 'yoink login --from-browser BROWSER'."
    )


def login(store: SessionStore, *, timeout: int = 300) -> None:
    """Wait for the user to finish login/2FA; never read password form fields."""
    try:
        previous = store.load()
    except YoinkError:
        previous = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            try:
                context = browser.new_context()
                if previous:
                    for cookie in previous:
                        cookie.restore(context)
                page = context.new_page()
                page.goto("https://x.com/login", wait_until="domcontentloaded")
                wait_for_login(page, timeout=timeout)
                cookies = tuple(
                    SessionCookie(
                        cookie.get("domain", ""),
                        cookie.get("path", "/"),
                        cookie.get("secure", True),
                        int(cookie.get("expires", -1)) if cookie.get("expires", -1) > 0 else None,
                        cookie.get("name", ""),
                        cookie.get("value", ""),
                        cookie.get("httpOnly", False),
                    )
                    for cookie in context.cookies()
                    if cookie.get("domain") in _DOMAINS
                )
                store.save(cookies)
            finally:
                browser.close()
    except PlaywrightTimeoutError:
        raise YoinkError(
            "X login timed out before the signed-in home page appeared. If X limits your login, "
            "stop retrying and wait. You can reuse a working session with "
            "'yoink login --from-browser BROWSER', or increase --timeout for manual login/2FA."
        ) from None
    except PlaywrightError:
        raise YoinkError(
            "Browser login did not complete. Run 'uv run playwright install chromium' if needed, "
            "then retry 'yoink login' and finish login/2FA before the timeout."
        ) from None
