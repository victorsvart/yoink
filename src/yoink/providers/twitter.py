import re
from urllib.parse import urlsplit

from yoink.errors import YoinkError
from yoink.models import Post

_HOSTS = {
    f"{prefix}{host}"
    for prefix in ("", "www.", "m.", "mobile.")
    for host in ("x.com", "twitter.com")
}
_POST_PATH = re.compile(
    r"/(?:[A-Za-z0-9_]{1,15}/status|i/web/status|i/status|statuses)/"
    r"([1-9][0-9]*)(?:/(?:video|photo)/[1-9][0-9]*)?/?"
)


def parse_post(url: str) -> Post:
    """I accept post links and strip the tracking junk and media index."""
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"https", "http"}
            and parsed.hostname in _HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
            and not any(char.isspace() or ord(char) < 32 for char in url)
        )
    except ValueError:
        raise YoinkError("Use an individual https://x.com/USER/status/ID post URL.") from None
    if not valid:
        raise YoinkError("Use an individual https://x.com/USER/status/ID post URL.")
    match = _POST_PATH.fullmatch(parsed.path)
    if match is None:
        raise YoinkError(
            "Use an individual X post URL; profiles, searches and Spaces aren't supported."
        )
    post_id = match.group(1)
    # I still download the whole post when a link ends in /video/2. All means all.
    return Post(url=f"https://x.com/i/web/status/{post_id}", id=post_id)
