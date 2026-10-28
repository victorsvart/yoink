"""backend failures, minus the credential leaks."""


class YoinkError(Exception):
    """errors that can actually be printed without exposing credentials."""


def extraction_error(diagnostic: str, *, authenticated: bool) -> YoinkError:
    """classifying failures because raw backend errors can leak session details."""
    # Best effort, definitely not ideal: matching human-readable diagnostics is
    # brittle and can misclassify errors when upstream wording changes or overlaps.
    # Prefer structured error codes if yt-dlp exposes them through this interface.
    text = diagnostic.lower()
    if any(word in text for word in ("requested format", "no video formats")):
        return YoinkError("No video matches this quality. Try --quality best or a higher limit.")
    if any(word in text for word in ("no video", "does not contain", "no media")):
        return YoinkError("This post does not contain a downloadable video.")
    if any(word in text for word in ("429", "rate limit", "too many requests")):
        return YoinkError("X is rate limiting requests. Wait a while and try again.")
    if any(word in text for word in ("not found", "does not exist", "deleted", "404")):
        return YoinkError("This post is unavailable or deleted. Check the link and your access.")
    if any(
        word in text
        for word in (
            "login",
            "log in",
            "sign in",
            "authorized",
            "protected",
            "private",
            "401",
            "403",
            "sensitive",
            "age-restricted",
            "guest token",
            "authentication",
        )
    ):
        if authenticated:
            return YoinkError(
                "X rejected the saved session or denied access to this post. "
                "Run 'yoink login' again and check that your account can view it."
            )
        return YoinkError(
            "X requires authentication or denied access to this post. "
            "Run 'yoink login' and try again."
        )
    if any(word in text for word in ("timed out", "timeout", "resolve", "connection", "network")):
        return YoinkError("Could not reach X or its media server. Check your connection and retry.")
    return YoinkError(
        "Extraction or download failed. Check the link and your access, then retry. "
        "If X changed, update yt-dlp with 'uv lock --upgrade-package yt-dlp && uv sync'."
    )
