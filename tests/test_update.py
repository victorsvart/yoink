import hashlib
import io
import json
import subprocess
import urllib.error
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from unittest.mock import patch

import pytest

from yoink.errors import YoinkError
from yoink.installation import Receipt, configure, load_receipt
from yoink.update import browser_enabled, download, release_version, update, verified_installer


@pytest.fixture
def releases() -> dict[str, bytes]:
    installer = b"#!/bin/bash\nexit 0\n"
    return {
        "VERSION": b"0.9.0\n",
        "SHA256SUMS": f"{hashlib.sha256(installer).hexdigest()}  install.sh\n".encode(),
        "install.sh": installer,
    }


def test_update_preserves_receipt_paths_packages_and_browser_choice(
    receipt: Receipt,
    releases: dict[str, bytes],
    tmp_path: Path,
) -> None:
    receipt.owned = ["python", "browsers"]
    receipt.manager = "brew"
    receipt.packages = {"ffmpeg": "1", "library": "2"}
    receipt.path_file, receipt.path_block = str(tmp_path / ".zshrc"), "original PATH block"
    receipt.browser = False
    receipt.save()
    before = (Path(receipt.root) / "installation.json").read_bytes()
    temporary: list[Path] = []

    def fetch(url: str, *, limit: int = 1024 * 1024) -> bytes:
        return releases[url.rsplit("/", 1)[-1]]

    def run(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = False,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "yoink 0.9.0\n", "")
        temporary.append(Path(command[1]).parent)
        assert Path(command[1]).read_bytes() == releases["install.sh"]
        assert env and env["YOINK_INSTALL_DIR"] == receipt.root
        assert env["YOINK_BIN_DIR"] == str(Path(receipt.command).parent)
        assert command[-3:] == ["--version", "0.9.0", "--without-browser"]
        # the real installer's configure step keeps all earlier ownership entries
        configure(
            Path(receipt.root),
            Path(receipt.command),
            python_owned=False,
            ffmpeg=False,
            browser=False,
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    with (
        patch("yoink.update.download", side_effect=fetch),
        patch("yoink.update.subprocess.run", side_effect=run),
    ):
        update()
    assert (Path(receipt.root) / "installation.json").read_bytes() == before
    assert temporary and not temporary[0].exists()


@pytest.mark.parametrize("version", [b"0.1.0", b"0.1.2"])
def test_no_downgrade_or_reinstall_when_current(
    receipt: Receipt,
    version: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("yoink.update.__version__", "0.1.2"),
        patch("yoink.update.download", return_value=version) as fetch,
        patch("yoink.update.subprocess.run") as run,
    ):
        update()
    fetch.assert_called_once()
    run.assert_not_called()
    assert "up to date" in capsys.readouterr().out


def test_check_only_does_not_download_or_execute_installer(receipt: Receipt) -> None:
    with (
        patch("yoink.update.download", return_value=b"0.9.0") as fetch,
        patch("yoink.update.subprocess.run") as run,
    ):
        update(check_only=True)
    fetch.assert_called_once()
    run.assert_not_called()


@pytest.mark.parametrize(
    "checksums", [b"", b"x  install.sh\n", b"\xff", (b"a" * 64 + b"  install.sh\n") * 2]
)
def test_missing_or_invalid_checksum_never_executes(
    receipt: Receipt,
    checksums: bytes,
) -> None:
    with (
        patch("yoink.update.download", side_effect=[b"0.9.0", checksums]),
        patch("yoink.update.subprocess.run") as run,
        pytest.raises(YoinkError),
    ):
        update()
    run.assert_not_called()


def test_tampered_installer_never_executes(receipt: Receipt, releases: dict[str, bytes]) -> None:
    with (
        patch("yoink.update.download", side_effect=[b"0.9.0", releases["SHA256SUMS"], b"tampered"]),
        patch("yoink.update.subprocess.run") as run,
        pytest.raises(YoinkError, match="SHA-256"),
    ):
        update()
    run.assert_not_called()


def test_failed_update_keeps_receipt_and_cleans_helper(receipt: Receipt) -> None:
    receipt.browser = False
    receipt.save()
    original = (Path(receipt.root) / "installation.json").read_bytes()
    paths: list[Path] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        paths.append(Path(command[1]))
        return subprocess.CompletedProcess(command, 1, "", "")

    with (
        patch("yoink.update.download", return_value=b"0.9.0"),
        patch("yoink.update.verified_installer", return_value=b"verified"),
        patch("yoink.update.subprocess.run", side_effect=run),
        pytest.raises(YoinkError, match="did not complete"),
    ):
        update()
    assert paths and not paths[0].parent.exists()
    after = load_receipt(Path(receipt.root))
    assert after and after.browser is False
    assert after.packages == receipt.packages
    assert (Path(receipt.root) / "installation.json").read_bytes() == original


def test_replaced_launcher_is_preserved_before_network(receipt: Receipt) -> None:
    command = Path(receipt.command)
    command.unlink()
    command.write_text("user's replacement")
    with (
        patch("yoink.update.download") as fetch,
        pytest.raises(YoinkError, match="launcher has changed"),
    ):
        update()
    fetch.assert_not_called()
    assert command.read_text() == "user's replacement"


def test_legacy_receipt_is_loaded_without_claiming_existing_dependencies(receipt: Receipt) -> None:
    path = Path(receipt.root) / "installation.json"
    data: dict[str, object] = json.loads(path.read_text())
    del data["browser"]
    path.write_text(json.dumps(data))
    loaded = load_receipt(Path(receipt.root))
    assert loaded and loaded.browser is None
    assert loaded.owned == [] and loaded.packages == {}


def test_configure_remembers_browser_opt_out(receipt: Receipt) -> None:
    configure(
        Path(receipt.root), Path(receipt.command), python_owned=False, ffmpeg=False, browser=False
    )
    loaded = load_receipt(Path(receipt.root))
    assert loaded and not browser_enabled(loaded)


@pytest.mark.parametrize(
    "data", [b"0.9.0-beta", b"v0.9.0", b"../../bad", b"0.9.0;echo bad", b"\xff"]
)
def test_invalid_release_versions_are_rejected(data: bytes) -> None:
    with pytest.raises(YoinkError, match="invalid version"):
        release_version(data)


def test_download_rejects_oversized_response() -> None:
    with patch("urllib.request.build_opener") as opener:
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = b"12345"
        with pytest.raises(YoinkError, match="larger than expected"):
            download("https://example.com", limit=4)


def test_network_error_is_actionable() -> None:
    with (
        patch("urllib.request.build_opener", side_effect=urllib.error.URLError("offline")),
        pytest.raises(YoinkError, match="connection"),
    ):
        download("https://example.com")


def test_https_redirect_cannot_downgrade() -> None:
    from yoink.update import HTTPSRedirect

    with pytest.raises(YoinkError, match="HTTPS"):
        HTTPSRedirect().redirect_request(
            urllib.request.Request("https://example.com"),
            io.BytesIO(),
            302,
            "redirect",
            HTTPMessage(),
            "http://example.com",
        )


def test_verified_installer_requires_exact_hash(releases: dict[str, bytes]) -> None:
    with patch(
        "yoink.update.download", side_effect=[releases["SHA256SUMS"], releases["install.sh"]]
    ):
        assert verified_installer("https://example.com") == releases["install.sh"]


def test_repair_retries_current_release_without_losing_history(receipt: Receipt) -> None:
    receipt.browser = False
    receipt.save()
    with (
        patch("yoink.update.__version__", "0.9.0"),
        patch("yoink.update.download", return_value=b"0.9.0"),
        patch("yoink.update.verified_installer", return_value=b"verified"),
        patch(
            "yoink.update.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, "yoink 0.9.0\n", ""),
            ],
        ) as run,
    ):
        update(repair=True)
    assert run.call_count == 2
    assert load_receipt(Path(receipt.root)) == receipt


def test_success_requires_confirming_installed_version(receipt: Receipt) -> None:
    receipt.browser = False
    with (
        patch("yoink.update.managed_installation", return_value=receipt),
        patch("yoink.update.download", return_value=b"0.9.0"),
        patch("yoink.update.verified_installer", return_value=b"verified"),
        patch(
            "yoink.update.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, "yoink 0.1.0\n", ""),
            ],
        ),
        pytest.raises(YoinkError, match="confirm"),
    ):
        update()
