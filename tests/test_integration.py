"""I use real HTTP downloads, FFmpeg, and Chromium here.

I fake Twitter metadata. I'm not signing into X every time I run a test.
"""

import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from playwright.sync_api import sync_playwright

from yoink.auth import SessionCookie, SessionStore, wait_for_login
from yoink.download import Downloader
from yoink.errors import YoinkError
from yoink.json_data import object_data, object_list, parse_object
from yoink.models import DownloadRequest, Progress, Quality
from yoink.providers.twitter import parse_post


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def media_server(tmp_path: Path) -> Iterator[str]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is needed for real media integration tests")
    source = tmp_path / "source"
    source.mkdir()
    for height in (360, 720):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=blue:size={height * 16 // 9}x{height}:rate=10",
                "-t",
                "0.3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source / f"{height}.mp4"),
            ],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.3",
            "-c:a",
            "aac",
            str(source / "audio.m4a"),
        ],
        check=True,
        capture_output=True,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(source)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def fixture_metadata(base_url: str, *, broken: bool = False) -> str:
    formats: list[dict[str, object]] = [
        {
            "format_id": str(height),
            "url": f"{base_url}/{height}.mp4",
            "height": height,
            "width": height * 16 // 9,
            "vcodec": "h264",
            "acodec": "none",
            "ext": "mp4",
        }
        for height in (360, 720)
    ]
    formats.append(
        {
            "format_id": "audio",
            "url": f"{base_url}/audio.m4a",
            "vcodec": "none",
            "acodec": "aac",
            "ext": "m4a",
        }
    )
    entries: list[dict[str, object]] = [
        {
            "id": str(index),
            "title": f"Local video {index}",
            "uploader_id": "fixture",
            "extractor": "twitter",
            "extractor_key": "Twitter",
            "formats": formats,
        }
        for index in (1, 2)
    ]
    if broken:
        entries[1]["formats"] = [
            {
                "format_id": "broken",
                "url": f"{base_url}/missing.mp4",
                "height": 360,
                "vcodec": "h264",
                "acodec": "none",
                "ext": "mp4",
            }
        ]
    return json.dumps(
        {
            "_type": "playlist",
            "id": "123",
            "title": "Local post",
            "entries": entries,
            "extractor": "twitter",
            "extractor_key": "Twitter",
            "webpage_url": base_url,
        }
    )


@pytest.mark.parametrize(
    "quality, height", [(Quality.P360, 360), (Quality.BEST, 720), (Quality.WORST, 360)]
)
def test_real_download_quality_merge_and_reuse(
    tmp_path: Path,
    media_server: str,
    quality: Quality,
    height: int,
) -> None:
    request = DownloadRequest(parse_post("https://x.com/a/status/123"), quality, tmp_path / "out")
    downloader = Downloader(SessionStore(tmp_path / "session"))
    progress: list[Progress] = []
    with patch("yoink.download.extract_metadata", return_value=fixture_metadata(media_server)):
        result = downloader.download(request, on_progress=progress.append)
        mtimes = [path.stat().st_mtime_ns for path in result.files]
        repeated = downloader.download(request)
    assert len(result.files) == 2
    assert len(set(result.files)) == 2
    assert repeated.files == result.files
    assert [path.stat().st_mtime_ns for path in result.files] == mtimes
    assert any(event.downloaded_bytes > 0 for event in progress)
    for path in result.files:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        streams = [object_data(item) for item in object_list(parse_object(probe.stdout)["streams"])]
        assert {stream.get("codec_type") for stream in streams} == {"video", "audio"}
        assert (
            next(stream["height"] for stream in streams if stream.get("codec_type") == "video")
            == height
        )


def test_real_partial_failure_keeps_complete_file(tmp_path: Path, media_server: str) -> None:
    request = DownloadRequest(
        parse_post("https://x.com/a/status/123"), Quality.BEST, tmp_path / "out"
    )
    with (
        patch(
            "yoink.download.extract_metadata",
            return_value=fixture_metadata(media_server, broken=True),
        ),
        pytest.raises(YoinkError, match="incomplete"),
    ):
        Downloader(SessionStore(tmp_path / "session")).download(request)
    assert len(list(request.output.glob("*.mp4"))) == 1


def test_real_browser_cookie_roundtrip(tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Run playwright install chromium for the real browser test")
        browser = playwright.chromium.launch()
        try:
            cookies = tuple(
                SessionCookie(".x.com", "/", True, None, name, "synthetic-test-value", True)
                for name in ("auth_token", "ct0")
            )
            store = SessionStore(tmp_path / "private")
            store.save(cookies)
            loaded = store.load()
            assert loaded is not None
            context = browser.new_context()
            for cookie in loaded:
                cookie.restore(context)
            restored = context.cookies("https://x.com/")
            assert {cookie.get("name") for cookie in restored} == {"auth_token", "ct0"}
            assert all(cookie.get("httpOnly") and cookie.get("secure") for cookie in restored)
        finally:
            browser.close()


def test_real_browser_login_outcomes() -> None:
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Run playwright install chromium for the real browser test")
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(
                '<a data-testid="AppTabBar_Home_Link" hidden>Home</a>'
                "<pre>õ��� onboarding errors: Weâ€™ve temporarily limited your login. "
                "Please try again later. synthetic-private-data</pre>"
            )
            with pytest.raises(YoinkError, match="temporarily limited") as error:
                wait_for_login(page, timeout=1)
            assert "synthetic-private-data" not in str(error.value)

            page.set_content(
                '<a data-testid="AppTabBar_Home_Link">Home</a>'
                "<p hidden>We've temporarily limited your login.</p>"
            )
            wait_for_login(page, timeout=1)

            page.set_content(
                '<a data-testid="AppTabBar_Home_Link">Home</a>'
                "<p>A post discussing: We've temporarily limited your login.</p>"
            )
            wait_for_login(page, timeout=1)
        finally:
            browser.close()
