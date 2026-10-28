import pytest

from yoink.errors import YoinkError
from yoink.providers.twitter import parse_post


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/NASA/status/123",
        "https://twitter.com/NASA/status/123?s=20",
        "http://mobile.twitter.com/NASA/status/123/video/2",
        "https://www.x.com/i/web/status/123/",
        "https://x.com/i/status/123",
        "https://x.com/statuses/123#video",
    ],
)
def test_post_normalization(url: str) -> None:
    post = parse_post(url)
    assert post.url == "https://x.com/i/web/status/123"
    assert post.id == "123"


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/NASA",
        "https://x.com/search?q=hello",
        "https://x.com/i/spaces/123",
        "https://x.com.evil.test/NASA/status/123",
        "https://evil.test/x.com/NASA/status/123",
        "https://x.com@evil.test/NASA/status/123",
        "https://user@x.com/NASA/status/123",
        "https://x.com:443/NASA/status/123",
        "file:///tmp/video.mp4",
        "x.com/NASA/status/123",
        "https://x.com/NASA/status/nope",
        "https://x.com/NASA/status/0",
        "https://x.com/NASA/status/123/video/0",
        "https://x.com/NASA/status/123/extra",
        "https://x.com/NASA/status/123\n",
        "https://[broken/NASA/status/123",
    ],
)
def test_rejects_non_post_urls(url: str) -> None:
    with pytest.raises(YoinkError):
        parse_post(url)
