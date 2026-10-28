import fcntl
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from yoink.errors import YoinkError
from yoink.installation import Receipt, uninstall
from yoink.update_notice import (
    CACHE_FILE,
    CHECK_INTERVAL,
    LOCK_FILE,
    UpdateNotice,
    read_cache,
    record_latest,
    refresh,
)

NOW = 1_000_000.0


@pytest.fixture(autouse=True)
def enable_automatic_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YOINK_NO_UPDATE_CHECK", raising=False)


def cache(receipt: Receipt, *, checked_at: float = NOW, latest: str | None = "9.0.0") -> Path:
    path = Path(receipt.root) / CACHE_FILE
    path.write_text(json.dumps({"checked_at": checked_at, "latest": latest}))
    return path


def test_fresh_cache_uses_no_worker_or_network(
    receipt: Receipt,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache(receipt)
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW + 1),
        patch("yoink.update_notice.subprocess.Popen") as worker,
        patch("yoink.update_notice.download") as network,
    ):
        notice = UpdateNotice.start()
        assert notice is not None
        notice.show()
    worker.assert_not_called()
    network.assert_not_called()
    output = capsys.readouterr()
    assert "Yoink 9.0.0 is available. Run 'yoink update'." in output.err
    assert output.out == ""


def test_cold_check_reserves_one_detached_worker(receipt: Receipt) -> None:
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW),
        patch("yoink.update_notice.subprocess.Popen") as worker,
        patch("yoink.update_notice.download") as network,
    ):
        assert UpdateNotice.start() is not None
        assert UpdateNotice.start() is not None
    worker.assert_called_once()
    assert worker.call_args.args[0][-2:] == ["-m", "yoink.update_notice"]
    assert worker.call_args.kwargs["start_new_session"] is True
    assert worker.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert worker.call_args.kwargs["stdout"] == subprocess.DEVNULL
    assert worker.call_args.kwargs["stderr"] == subprocess.DEVNULL
    worker.return_value.wait.assert_not_called()
    network.assert_not_called()
    assert read_cache(Path(receipt.root)).checked_at == NOW


def test_stale_check_runs_in_background_and_keeps_known_notice(receipt: Receipt) -> None:
    cache(receipt, checked_at=NOW - CHECK_INTERVAL)
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW),
        patch("yoink.update_notice.subprocess.Popen") as worker,
    ):
        UpdateNotice.start()
    worker.assert_called_once()
    assert read_cache(Path(receipt.root)).latest == "9.0.0"


def test_worker_refreshes_the_cache_with_short_network_timeout(receipt: Receipt) -> None:
    cache(receipt, latest=None)
    with patch("yoink.update_notice.download", return_value=b"9.0.0\n") as network:
        refresh()
    assert network.call_args.kwargs == {"limit": 64, "timeout": 2}
    assert read_cache(Path(receipt.root)).latest == "9.0.0"


def test_failed_request_is_quiet_and_not_retried_on_every_command(
    receipt: Receipt,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache(receipt, latest=None)
    with patch("yoink.update_notice.download", side_effect=YoinkError("offline")):
        refresh()
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW + 1),
        patch("yoink.update_notice.subprocess.Popen") as worker,
    ):
        notice = UpdateNotice.start()
        assert notice is not None
        notice.show()
    worker.assert_not_called()
    assert capsys.readouterr() == ("", "")


def test_busy_lock_does_not_wait_or_spawn(receipt: Receipt) -> None:
    descriptor = os.open(Path(receipt.root) / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (
            patch("sys.stderr.isatty", return_value=True),
            patch("yoink.update_notice.subprocess.Popen") as worker,
        ):
            assert UpdateNotice.start() is not None
        worker.assert_not_called()
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("interactive, disabled", [(False, False), (True, True)])
def test_noninteractive_and_opt_out_skip_all_cache_work(
    receipt: Receipt,
    monkeypatch: pytest.MonkeyPatch,
    interactive: bool,
    disabled: bool,
) -> None:
    if disabled:
        monkeypatch.setenv("YOINK_NO_UPDATE_CHECK", "1")
    with (
        patch("sys.stderr.isatty", return_value=interactive),
        patch("yoink.update_notice.managed_installation") as installation,
        patch("yoink.update_notice.subprocess.Popen") as worker,
    ):
        assert UpdateNotice.start() is None
    installation.assert_not_called()
    worker.assert_not_called()
    assert not (Path(receipt.root) / CACHE_FILE).exists()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"checked_at": true}',
        '{"checked_at": 1000000}',
        '{"checked_at": 1e999}',
        '{"checked_at": 1000000, "latest": "\\u001b[bad"}',
        "x" * 4097,
    ],
)
def test_damaged_cache_is_ignored_and_refreshed(receipt: Receipt, content: str) -> None:
    (Path(receipt.root) / CACHE_FILE).write_text(content)
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW),
        patch("yoink.update_notice.subprocess.Popen") as worker,
    ):
        assert UpdateNotice.start() is not None
    worker.assert_called_once()


def test_future_timestamp_cannot_disable_checks_forever(receipt: Receipt) -> None:
    cache(receipt, checked_at=NOW + CHECK_INTERVAL)
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW),
        patch("yoink.update_notice.subprocess.Popen") as worker,
    ):
        UpdateNotice.start()
    worker.assert_called_once()


def test_unwritable_cache_does_not_break_command(receipt: Receipt) -> None:
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("yoink.update_notice._write_cache", side_effect=OSError),
        patch("yoink.update_notice.subprocess.Popen") as worker,
    ):
        assert UpdateNotice.start() is None
    worker.assert_not_called()


def test_failed_spawn_is_quiet_and_cached(receipt: Receipt) -> None:
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW),
        patch("yoink.update_notice.subprocess.Popen", side_effect=OSError) as worker,
    ):
        assert UpdateNotice.start() is None
        assert UpdateNotice.start() is not None
    assert worker.call_count == 1


def test_manual_check_supersedes_an_older_background_response(receipt: Receipt) -> None:
    cache(receipt, latest=None)

    def fetch(url: str, *, limit: int, timeout: float) -> bytes:
        with patch("time.time", return_value=NOW + 1):
            record_latest(Path(receipt.root), "9.0.1")
        return b"9.0.0"

    with patch("yoink.update_notice.download", side_effect=fetch):
        refresh()
    assert read_cache(Path(receipt.root)).latest == "9.0.1"


def test_explicit_check_updates_cache_without_changing_ownership(receipt: Receipt) -> None:
    receipt_path = Path(receipt.root) / "installation.json"
    original = receipt_path.read_bytes()
    with patch("time.time", return_value=NOW):
        record_latest(Path(receipt.root), "9.0.0")
    assert receipt_path.read_bytes() == original
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("time.time", return_value=NOW + 1),
        patch("yoink.update_notice.subprocess.Popen") as worker,
    ):
        UpdateNotice.start()
    worker.assert_not_called()


def test_installed_version_hides_old_notices(
    receipt: Receipt,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache(receipt, latest="0.1.0")
    with patch("sys.stderr.isatty", return_value=True):
        UpdateNotice(Path(receipt.root)).show()
    assert capsys.readouterr() == ("", "")


def test_uninstall_removes_cache_and_blocks_late_worker_write(receipt: Receipt) -> None:
    cache(receipt)
    with patch("builtins.open") as terminal, patch("yoink.auth.SessionStore"):
        terminal.return_value.__enter__.return_value.readline.return_value = "y\n"
        uninstall()
    record_latest(Path(receipt.root), "9.0.0", attempt=NOW)
    assert not (Path(receipt.root) / CACHE_FILE).exists()
    assert not (Path(receipt.root) / LOCK_FILE).exists()


def test_cache_symlink_never_reads_or_modifies_its_target(receipt: Receipt, tmp_path: Path) -> None:
    outside = tmp_path / "user-file"
    outside.write_text("keep this")
    (Path(receipt.root) / CACHE_FILE).symlink_to(outside)
    assert read_cache(Path(receipt.root)).latest is None
    assert outside.read_text() == "keep this"
