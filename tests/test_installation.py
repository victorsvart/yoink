import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yoink.auth import SessionStore
from yoink.errors import YoinkError
from yoink.installation import (
    Receipt,
    browser_packages,
    configure,
    install_packages,
    load_receipt,
    record_path,
    remove_packages,
    uninstall,
    use_private_browser,
)


@pytest.fixture
def receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Receipt:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    root = tmp_path / "app"
    prefix = root / "tools/yoink-cli"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/yoink").write_text("app entry point")
    command = tmp_path / "bin/yoink"
    command.parent.mkdir()
    command.symlink_to(prefix / "bin/yoink")
    monkeypatch.setattr("sys.prefix", str(prefix))
    result = Receipt(str(root), str(command))
    result.save()
    return result


def confirmed() -> MagicMock:
    terminal = MagicMock()
    terminal.__enter__.return_value.readline.return_value = "yes\n"
    return terminal


def test_uninstall_removes_private_dependencies_and_keeps_preexisting_files(
    receipt: Receipt,
    tmp_path: Path,
) -> None:
    root = Path(receipt.root)
    for directory in ("python", "browsers"):
        (root / directory).mkdir()
        (root / directory / "owned").touch()
    shared = tmp_path / "existing-python"
    shared.mkdir()
    downloaded = tmp_path / "video.mp4"
    downloaded.touch()
    session = SessionStore(tmp_path / "session")
    session.directory.mkdir()
    session.path.write_text("private session")
    receipt.owned = ["python", "browsers"]
    receipt.save()
    with (
        patch("builtins.open", return_value=confirmed()),
        patch("yoink.auth.SessionStore", return_value=session),
    ):
        uninstall()
    assert not (root / "tools/yoink-cli").exists()
    assert not (root / "python").exists()
    assert not (root / "browsers").exists()
    assert not Path(receipt.command).is_symlink()
    assert not session.path.exists()
    assert shared.is_dir() and downloaded.is_file()


@pytest.mark.parametrize("answer", ["n\n", "\n", ""])
def test_uninstall_decline_does_not_mutate(receipt: Receipt, answer: str) -> None:
    terminal = confirmed()
    terminal.__enter__.return_value.readline.return_value = answer
    with (
        patch("builtins.open", return_value=terminal),
        pytest.raises(YoinkError, match="cancelled"),
    ):
        uninstall()
    assert (Path(receipt.root) / "tools/yoink-cli").is_dir()
    assert Path(receipt.command).exists()


def test_no_tty_is_not_consent(receipt: Receipt) -> None:
    with (
        patch("builtins.open", side_effect=OSError),
        pytest.raises(YoinkError, match="interactive"),
    ):
        uninstall()
    assert Path(receipt.command).exists()


def test_legacy_install_does_not_guess_ownership(receipt: Receipt) -> None:
    (Path(receipt.root) / "installation.json").unlink()
    with pytest.raises(YoinkError, match="no ownership receipt"):
        uninstall()
    assert Path(receipt.command).exists()


def test_source_install_cannot_delete_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.prefix", str(tmp_path / ".venv"))
    with pytest.raises(YoinkError, match="original package manager"):
        uninstall()


def test_replaced_command_and_runtime_symlink_are_kept(receipt: Receipt, tmp_path: Path) -> None:
    shared = tmp_path / "shared-browser"
    shared.mkdir()
    receipt.owned = ["browsers"]
    (Path(receipt.root) / "browsers").symlink_to(shared)
    command = Path(receipt.command)
    command.unlink()
    command.write_text("user's new command")
    receipt.save()
    with patch("builtins.open", return_value=confirmed()), patch("yoink.auth.SessionStore"):
        uninstall()
    assert command.read_text() == "user's new command"
    assert shared.is_dir()


def test_only_exact_installer_path_block_is_removed(receipt: Receipt, tmp_path: Path) -> None:
    profile = tmp_path / ".zshrc"
    original = "export USER_SETTING=1\n"
    block = "\n# yoink PATH\nexport PATH=bin:$PATH\n# end yoink PATH"
    profile.write_text(original + block + "\n# user's later edit\n")
    record_path(Path(receipt.root), profile, block)
    with patch("builtins.open", return_value=confirmed()), patch("yoink.auth.SessionStore"):
        uninstall()
    assert profile.read_text() == original + "# user's later edit\n"


def test_changed_path_block_is_preserved(receipt: Receipt, tmp_path: Path) -> None:
    profile = tmp_path / ".bashrc"
    block = "\n# yoink PATH\nexport PATH=bin:$PATH\n# end yoink PATH"
    record_path(Path(receipt.root), profile, block)
    profile.write_text(block.replace("bin", "other-bin"))
    before = profile.read_text()
    with patch("builtins.open", return_value=confirmed()), patch("yoink.auth.SessionStore"):
        uninstall()
    assert profile.read_text() == before


@pytest.mark.parametrize(
    "change", [{"owned": ["../user-files"]}, {"packages": {"--force": "1"}}, {"root": "/"}]
)
def test_damaged_receipt_never_removes_files(receipt: Receipt, change: dict[str, object]) -> None:
    path = Path(receipt.root) / "installation.json"
    data: dict[str, object] = json.loads(path.read_text())
    data.update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(YoinkError, match="damaged"):
        uninstall()
    assert Path(receipt.command).exists()


def test_configure_preserves_ownership_across_upgrades(receipt: Receipt) -> None:
    receipt.owned = ["python", "browsers"]
    receipt.save()
    configure(
        Path(receipt.root), Path(receipt.command), python_owned=False, ffmpeg=False, browser=False
    )
    updated = load_receipt(Path(receipt.root))
    assert updated and updated.owned == ["python", "browsers"]


def test_existing_browser_is_reused(receipt: Receipt, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    with (
        patch("playwright.sync_api.sync_playwright") as playwright,
        patch("yoink.installation._run") as run,
    ):
        playwright.return_value.__enter__.return_value.chromium.executable_path = receipt.command
        configure(
            Path(receipt.root),
            Path(receipt.command),
            python_owned=False,
            ffmpeg=False,
            browser=True,
        )
    run.assert_not_called()
    updated = load_receipt(Path(receipt.root))
    assert updated and "browsers" not in updated.owned


def test_missing_browser_is_installed_privately(
    receipt: Receipt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    with (
        patch("playwright.sync_api.sync_playwright") as playwright,
        patch("yoink.installation._run") as run,
        patch("yoink.installation.browser_packages", return_value=[]),
    ):
        playwright.return_value.__enter__.return_value.chromium.executable_path = "/missing/browser"
        configure(
            Path(receipt.root),
            Path(receipt.command),
            python_owned=False,
            ffmpeg=False,
            browser=True,
        )
    assert run.call_args.args[0][-2:] == ["install", "chromium"]
    updated = load_receipt(Path(receipt.root))
    assert updated and updated.owned == ["browsers"]
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(Path(receipt.root) / "browsers")


def test_private_browser_is_selected_on_future_commands(
    receipt: Receipt, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt.owned = ["browsers"]
    receipt.save()
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    use_private_browser()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(Path(receipt.root) / "browsers")


def test_only_new_system_packages_are_owned_even_on_failure(receipt: Receipt) -> None:
    before = {"existing": "1"}
    after = {"existing": "1", "ffmpeg": "2", "new-library": "3"}
    with (
        patch("sys.platform", "darwin"),
        patch("yoink.installation.package_snapshot", side_effect=[before, after]),
        patch(
            "yoink.installation.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "brew"),
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        install_packages(receipt, ["ffmpeg"])
    updated = load_receipt(Path(receipt.root))
    assert updated and updated.packages == {"ffmpeg": "2", "new-library": "3"}


def test_brew_keeps_changed_packages_and_new_dependents(receipt: Receipt) -> None:
    receipt.manager = "brew"
    receipt.packages = {"ffmpeg": "1", "lib": "1", "changed": "1", "unused": "1"}
    current = {**receipt.packages, "changed": "2", "user-app": "1"}

    def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
        users = {"ffmpeg": "user-app", "lib": "ffmpeg", "unused": ""}
        return subprocess.CompletedProcess(command, 0, users[command[-1]], "")

    with (
        patch("yoink.installation.package_snapshot", return_value=current),
        patch("yoink.installation._run", side_effect=run),
        patch("yoink.installation.subprocess.run") as remove,
    ):
        remove_packages(receipt)
    assert remove.call_args.args[0] == ["brew", "uninstall", "--formula", "unused"]
    assert remove.call_args.kwargs["env"]["HOMEBREW_NO_AUTOREMOVE"] == "1"


def test_apt_preserves_packages_that_would_remove_user_software(receipt: Receipt) -> None:
    receipt.manager = "dpkg"
    receipt.packages = {"ffmpeg": "1", "unused": "1"}
    calls: list[list[str]] = []

    def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = (
            "Remv ffmpeg [1]\nRemv user-app [1]\n" if "ffmpeg" in command else "Remv unused [1]\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    with (
        patch("yoink.installation.package_snapshot", return_value=receipt.packages),
        patch("yoink.installation._run", side_effect=run),
        patch("os.geteuid", return_value=0),
    ):
        remove_packages(receipt)
    assert calls[-1] == ["dpkg", "--remove", "unused"]


def test_browser_dependency_output_is_validated() -> None:
    result = subprocess.CompletedProcess(
        [], 1, "Missing system dependencies (2):\n  liba\n  libb\n", ""
    )
    with patch("yoink.installation.subprocess.run", return_value=result):
        assert browser_packages() == ["liba", "libb"]
    result.stdout = "unexpected upstream output"
    with patch("yoink.installation.subprocess.run", return_value=result), pytest.raises(YoinkError):
        browser_packages()


def test_failed_package_removal_keeps_app_and_receipt(receipt: Receipt) -> None:
    with (
        patch("builtins.open", return_value=confirmed()),
        patch(
            "yoink.installation.remove_packages",
            side_effect=subprocess.CalledProcessError(1, "dpkg"),
        ),
        pytest.raises(YoinkError, match="Yoink was kept"),
    ):
        uninstall()
    assert Path(receipt.command).exists()
    assert load_receipt(Path(receipt.root)) is not None


def test_keep_session_does_not_touch_saved_cookies(receipt: Receipt) -> None:
    with (
        patch("builtins.open", return_value=confirmed()),
        patch("yoink.auth.SessionStore") as store,
    ):
        uninstall(keep_session=True)
    store.assert_not_called()


def test_apt_refuses_to_upgrade_preexisting_packages(receipt: Receipt) -> None:
    result = subprocess.CompletedProcess([], 0, "Inst existing [1] (2 repository)\n", "")
    with (
        patch("sys.platform", "linux"),
        patch("yoink.installation.package_snapshot", return_value={"existing:amd64": "1"}),
        patch("yoink.installation._run", return_value=result) as run,
        pytest.raises(YoinkError, match="upgraded"),
    ):
        install_packages(receipt, ["ffmpeg"])
    assert run.call_count == 1
    assert "--simulate" in run.call_args.args[0]
    assert not receipt.packages
