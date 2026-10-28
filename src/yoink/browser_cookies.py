"""I read the chosen browser's cookies and keep yt-dlp's untyped bits here."""

from collections.abc import Callable
from enum import StrEnum
from http.cookiejar import CookieJar
from importlib import import_module
from typing import cast

from yoink.errors import YoinkError


class CookieBrowser(StrEnum):
    BRAVE = "brave"
    CHROME = "chrome"  # validated
    CHROMIUM = "chromium"
    EDGE = "edge"
    FIREFOX = "firefox"  # validated
    OPERA = "opera"
    SAFARI = "safari"  # validated
    VIVALDI = "vivaldi"
    WHALE = "whale"


class _QuietLogger:
    """I drop upstream logs. Leaking browser details would be a stupid feature."""

    def debug(self, message: object, *args: object, **kwargs: object) -> None:
        pass

    info = debug
    warning = debug
    error = debug


def read_browser_cookies(browser: CookieBrowser, profile: str | None) -> CookieJar:
    # yt-dlp gives me no types here. Lovely. I narrow the callable and check its
    # result before letting it anywhere near the rest of my code.
    try:
        extractor = cast(
            Callable[[str, str | None, _QuietLogger], object],
            import_module("yt_dlp.cookies").extract_cookies_from_browser,
        )
        result = extractor(browser.value, profile, _QuietLogger())
    except Exception:
        raise YoinkError(
            "Could not read the selected browser's cookies. Check the browser/profile and "
            "OS access permissions. On macOS, cookie access may require Keychain or "
            "Privacy & Security permission for the app running Yoink."
        ) from None

    if not isinstance(result, CookieJar):
        raise YoinkError("The browser cookie reader returned invalid data. Try updating yt-dlp.")

    return result
