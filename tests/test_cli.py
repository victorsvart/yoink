from pathlib import Path
from unittest.mock import patch

import pytest

from yoink.browser_cookies import CookieBrowser
from yoink.cli import main
from yoink.errors import YoinkError
from yoink.models import DownloadResult, Quality


def test_shorthand_passes_typed_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = DownloadResult((tmp_path / "video.mp4",))
    with patch("yoink.cli.Downloader.download", return_value=result) as download:
        assert (
            main(["https://x.com/NASA/status/123", "--quality", "720p", "-o", str(tmp_path)]) == 0
        )
    request = download.call_args.args[0]
    assert request.post.id == "123"
    assert request.quality is Quality.P720
    assert request.output == tmp_path
    assert "Complete: 1" in capsys.readouterr().out


def test_download_failure_never_reports_success(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("yoink.cli.Downloader.download", side_effect=YoinkError("The post is incomplete.")):
        assert main(["download", "https://x.com/NASA/status/123"]) == 1
    output = capsys.readouterr()
    assert "incomplete" in output.err
    assert "Complete:" not in output.out


def test_invalid_url_does_not_run_downloader(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("yoink.cli.Downloader.download") as download:
        assert main(["download", "https://evil.test/"]) == 1
    download.assert_not_called()
    assert "post URL" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["download", "https://x.com/a/status/1", "--quality", "potato"],
        ["login", "--timeout", "0"],
        ["login", "--timeout", "NaN"],
        ["login", "--timeout", "3601"],
    ],
)
def test_invalid_options(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_interrupt_exit_status(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("yoink.cli.Downloader.download", side_effect=KeyboardInterrupt):
        assert main(["https://x.com/a/status/1"]) == 130
    assert "Complete:" not in capsys.readouterr().out


def test_browser_import_does_not_launch_login(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("yoink.cli.import_browser_session") as imported, patch("yoink.cli.login") as login:
        assert main(["login", "--from-browser", "chrome", "--profile", "Profile 1"]) == 0
    assert imported.call_args.args[1] is CookieBrowser.CHROME
    assert imported.call_args.kwargs["profile"] == "Profile 1"
    login.assert_not_called()
    assert "saved privately" in capsys.readouterr().out


def test_profile_requires_browser() -> None:
    with pytest.raises(SystemExit) as error:
        main(["login", "--profile", "Profile 1"])
    assert error.value.code == 2


def test_failed_import_does_not_report_success(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("yoink.cli.import_browser_session", side_effect=YoinkError("No active X session")):
        assert main(["login", "--from-browser", "safari"]) == 1
    assert "saved privately" not in capsys.readouterr().out
