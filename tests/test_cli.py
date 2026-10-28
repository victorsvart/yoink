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


def test_download_defaults_to_user_downloads_directory(tmp_path: Path) -> None:
    with (
        patch("yoink.cli.Path.home", return_value=tmp_path),
        patch("yoink.cli.Downloader.download", return_value=DownloadResult(())) as download,
    ):
        assert main(["https://x.com/NASA/status/123"]) == 0
    request = download.call_args.args[0]
    assert request.output == tmp_path / "Downloads"


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
    with (
        patch("yoink.cli.import_browser_session") as imported,
        patch("yoink.cli.login") as login,
        patch("builtins.input") as consent,
    ):
        assert main(["login", "--from-browser", "chrome", "--profile", "Profile 1"]) == 0
    assert imported.call_args.args[1] is CookieBrowser.CHROME
    assert imported.call_args.kwargs["profile"] == "Profile 1"
    login.assert_not_called()
    consent.assert_not_called()
    assert "saved privately" in capsys.readouterr().out


def test_profile_requires_browser() -> None:
    with pytest.raises(SystemExit) as error:
        main(["login", "--profile", "Profile 1"])
    assert error.value.code == 2


def test_uninstall_dispatches_without_login_or_download() -> None:
    with (
        patch("yoink.cli.uninstall") as removal,
        patch("yoink.cli.Downloader.download") as download,
        patch("yoink.cli.login") as login,
    ):
        assert main(["uninstall", "--keep-session"]) == 0
    removal.assert_called_once_with(keep_session=True)
    download.assert_not_called()
    login.assert_not_called()


def test_update_check_dispatches_without_login_or_download() -> None:
    with (
        patch("yoink.cli.update") as refresh,
        patch("yoink.cli.Downloader.download") as download,
        patch("yoink.cli.login") as login,
        patch("yoink.cli.UpdateNotice.start") as notice,
    ):
        assert main(["update", "--check"]) == 0
    refresh.assert_called_once_with(check_only=True, repair=False)
    download.assert_not_called()
    login.assert_not_called()
    notice.assert_not_called()


def test_notice_does_not_change_failure_status_or_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("yoink.cli.UpdateNotice.start") as notice,
        patch("yoink.cli.Downloader.download", side_effect=YoinkError("download failed")),
    ):
        assert main(["https://x.com/a/status/1"]) == 1
    notice.return_value.show.assert_called_once()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "arguments",
    [["--help"], ["--version"], ["update", "--check"], ["uninstall", "--keep-session"]],
)
def test_metadata_update_and_uninstall_never_spawn_notice(arguments: list[str]) -> None:
    with (
        patch("yoink.cli.UpdateNotice.start") as notice,
        patch("yoink.cli.update"),
        patch("yoink.cli.uninstall"),
    ):
        if arguments[0].startswith("--"):
            with pytest.raises(SystemExit) as result:
                main(arguments)
            assert result.value.code == 0
        else:
            assert main(arguments) == 0
    notice.assert_not_called()


def test_failed_import_does_not_report_success(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch("yoink.cli.import_browser_session", side_effect=YoinkError("No active X session")),
        patch("builtins.input", return_value=""),
    ):
        assert main(["login", "--from-browser", "safari"]) == 1
    assert "saved privately" not in capsys.readouterr().out


def test_safari_waits_for_enter_before_reading_cookies(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch("yoink.cli.import_browser_session") as imported,
        patch("yoink.cli.login") as login,
    ):

        def agree(prompt: str) -> str:
            imported.assert_not_called()
            assert "Press Enter" in prompt
            notice = capsys.readouterr().err
            assert "Full Disk Access must be enabled manually" in notice
            assert "cannot request or grant this permission automatically" in notice
            assert "Full Disk Access" in notice
            assert "Only X/Twitter cookies" in notice
            return ""

        with patch("builtins.input", side_effect=agree):
            assert main(["login", "--from-browser", "safari"]) == 0
    imported.assert_called_once()
    assert imported.call_args.args[1] is CookieBrowser.SAFARI
    login.assert_not_called()


@pytest.mark.parametrize("response", ["no", "cancel", "yes"])
def test_safari_requires_enter_to_continue(
    response: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("yoink.cli.import_browser_session") as imported,
        patch("builtins.input", return_value=response),
    ):
        assert main(["login", "--from-browser", "safari"]) == 1
    imported.assert_not_called()
    output = capsys.readouterr()
    assert "No cookies were read" in output.err
    assert "saved privately" not in output.out


@pytest.mark.parametrize("interruption, code", [(EOFError, 1), (KeyboardInterrupt, 130)])
def test_safari_interrupted_consent_does_not_read_cookies(
    interruption: type[BaseException],
    code: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("yoink.cli.import_browser_session") as imported,
        patch("builtins.input", side_effect=interruption),
    ):
        assert main(["login", "--from-browser", "safari"]) == code
    imported.assert_not_called()
    output = capsys.readouterr()
    assert "Reading the selected browser" not in output.out
    assert "saved privately" not in output.out
