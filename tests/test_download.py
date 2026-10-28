import io
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yoink.auth import SessionStore
from yoink.download import Downloader, count_videos, parse_progress, verify_media
from yoink.errors import YoinkError, extraction_error
from yoink.json_data import parse_object
from yoink.models import DownloadRequest, Quality
from yoink.providers.twitter import parse_post


def request(tmp_path: Path) -> DownloadRequest:
    return DownloadRequest(parse_post("https://x.com/a/status/123"), Quality.BEST, tmp_path / "out")


def metadata(count: int = 2) -> str:
    return json.dumps(
        {
            "entries": [
                {"id": str(i), "formats": [{"url": "private-media-url"}]} for i in range(count)
            ]
        }
    )


def fake_process(lines: str, code: int = 0) -> MagicMock:
    process = MagicMock()
    process.stdout = io.StringIO(lines)
    process.wait.return_value = code
    context = MagicMock()
    context.__enter__.return_value = process
    return context


@pytest.mark.parametrize(
    "diagnostic, fragment",
    [
        ("No video could be found", "does not contain"),
        ("Requested format is not available", "quality"),
        ("HTTP 429", "rate limiting"),
        ("HTTP 404", "unavailable"),
        ("Login required", "yoink login"),
        ("Connection timed out", "connection"),
    ],
)
def test_safe_error_classification(diagnostic: str, fragment: str) -> None:
    error = extraction_error(
        diagnostic + " auth_token=SECRET https://media.test/?token=SECRET", authenticated=False
    )
    assert fragment in str(error)
    assert "SECRET" not in str(error)


def test_rejected_saved_session_is_actionable() -> None:
    assert "saved session" in str(extraction_error("403 forbidden", authenticated=True))


def test_nested_playlist_count() -> None:
    assert count_videos(parse_object(metadata(4))) == 4


@pytest.mark.parametrize("text", ["{}", '{"entries":[]}', '{"entries":[null]}', "[]", "bad"])
def test_bad_or_missing_metadata_is_failure(text: str) -> None:
    with pytest.raises(YoinkError):
        count_videos(parse_object(text))


def test_total_and_estimated_progress() -> None:
    assert parse_progress('{"downloaded":100,"total":"NA","estimate":200}').total_bytes == 200
    assert parse_progress('{"downloaded":"NA"}').downloaded_bytes == 0


def test_all_videos_required(tmp_path: Path) -> None:
    output = request(tmp_path).output
    process = fake_process("YOINK_FILE:" + json.dumps(str(output / "one.mp4")) + "\n")
    with (
        patch(
            "yoink.download.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, metadata(), ""),
        ),
        patch("yoink.download.subprocess.Popen", return_value=process),
        patch("yoink.download.verify_media") as verify,
        pytest.raises(YoinkError, match="Not every video"),
    ):
        Downloader(SessionStore(tmp_path / "session")).download(request(tmp_path))
    verify.assert_not_called()


def test_partial_failure_never_succeeds(tmp_path: Path) -> None:
    process = fake_process("ERROR: secret-cookie\n", code=1)
    with (
        patch(
            "yoink.download.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, metadata(), ""),
        ),
        patch("yoink.download.subprocess.Popen", return_value=process),
        pytest.raises(YoinkError, match="post is incomplete") as error,
    ):
        Downloader(SessionStore(tmp_path / "session")).download(request(tmp_path))
    assert "secret-cookie" not in str(error.value)


def test_extraction_failure_is_redacted(tmp_path: Path) -> None:
    with (
        patch(
            "yoink.download.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "403 SECRET"),
        ),
        pytest.raises(YoinkError, match="yoink login") as error,
    ):
        Downloader(SessionStore(tmp_path / "session")).download(request(tmp_path))
    assert "SECRET" not in str(error.value)


def test_rejects_output_outside_destination(tmp_path: Path) -> None:
    process = fake_process('YOINK_FILE:"/tmp/outside.mp4"\n')
    with (
        patch(
            "yoink.download.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, metadata(1), ""),
        ),
        patch("yoink.download.subprocess.Popen", return_value=process),
        pytest.raises(YoinkError, match="outside"),
    ):
        Downloader(SessionStore(tmp_path / "session")).download(request(tmp_path))


def test_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.mp4"
    path.touch()
    with pytest.raises(YoinkError, match="complete video"):
        verify_media(path)


def test_rejects_audio_only_file(tmp_path: Path) -> None:
    path = tmp_path / "audio.mp4"
    path.write_bytes(b"example")
    with (
        patch(
            "yoink.download.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, '{"streams":[{"codec_type":"audio"}]}', ""
            ),
        ),
        pytest.raises(YoinkError, match="no video stream"),
    ):
        verify_media(path)
