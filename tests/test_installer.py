import hashlib
import os
import selectors
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@dataclass
class Installer:
    directory: Path
    assets: Path
    environment: dict[str, str]
    log: Path
    harness: Path

    def run(self, answers: str | None, *arguments: str) -> subprocess.CompletedProcess[str]:
        command = ["/bin/bash", str(self.harness), *arguments]
        if answers is None:
            return subprocess.run(
                command,
                env=self.environment,
                input="y\n",
                text=True,
                capture_output=True,
                start_new_session=True,
                timeout=20,
            )
        if sys.platform == "darwin":
            command = ["/usr/bin/script", "-q", "/dev/null", *command]
        else:
            command = ["/usr/bin/script", "-q", "-e", "-c", shlex.join(command), "/dev/null"]
        with subprocess.Popen(
            command,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process:
            assert process.stdin is not None and process.stdout is not None
            captured = bytearray()
            replies = answers.splitlines(keepends=True)
            sent = 0
            deadline = time.monotonic() + 20
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        if time.monotonic() > deadline:
                            raise subprocess.TimeoutExpired(command, 20)
                        if not selector.select(timeout=0.1):
                            continue
                        data = os.read(process.stdout.fileno(), 4096)
                        if not data:
                            break
                        captured.extend(data)
                        if captured.count(b"[y/N]") > sent and sent < len(replies):
                            process.stdin.write(replies[sent].encode())
                            process.stdin.flush()
                            sent += 1
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
            return subprocess.CompletedProcess(command, process.returncode, captured.decode(), "")


@pytest.fixture
def installer(tmp_path: Path) -> Installer:
    binary = tmp_path / "bin"
    binary.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "VERSION").write_text("0.1.0\n")
    (assets / "constraints.txt").write_text("yt-dlp==2026.10.26\n")
    (assets / "yoink_cli-0.1.0-py3-none-any.whl").write_bytes(b"synthetic wheel")
    (assets / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in assets.iterdir()
            if path.name != "SHA256SUMS"
        )
    )
    log = tmp_path / "calls"
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{binary}:/usr/bin:/bin",
            "SHELL": "/bin/bash",
            "YOINK_INSTALL_DIR": str(tmp_path / "installed"),
            "YOINK_BIN_DIR": str(tmp_path / "commands"),
            "MOCK_ASSETS": str(assets),
            "MOCK_LOG": str(log),
            "MOCK_ROOT": str(tmp_path),
            "MOCK_SYSTEM": "Darwin",
        }
    )
    (tmp_path / "ffmpeg-ready").touch()
    (binary / "python3").symlink_to(sys.executable)
    executable(binary / "uname", '#!/bin/sh\nprintf "%s\\n" "$MOCK_SYSTEM"\n')
    executable(
        binary / "brew", '#!/bin/sh\necho brew >>"$MOCK_LOG"\ntouch "$MOCK_ROOT/ffmpeg-ready"\n'
    )
    executable(
        binary / "curl",
        f"""#!{sys.executable}
import os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith("https://"))
destination = pathlib.Path(args[args.index("--output") + 1])
with open(os.environ["MOCK_LOG"], "a") as stream: stream.write(url + "\\n")
if url.startswith("https://astral.sh/"):
    destination.write_text('#!/bin/sh\\nmkdir -p "$UV_UNMANAGED_INSTALL"\\n'
        'cp "$MOCK_ROOT/fake-uv" "$UV_UNMANAGED_INSTALL/uv"\\n'
        'chmod +x "$UV_UNMANAGED_INSTALL/uv"\\n')
else:
    shutil.copyfile(pathlib.Path(os.environ["MOCK_ASSETS"]) / url.rsplit("/", 1)[1], destination)
""",
    )
    executable(
        tmp_path / "fake-uv",
        f"""#!{sys.executable}
import os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as stream:
    stream.write("helper " + sys.argv[0] + "\\n")
    stream.write("uv " + " ".join(args) + "\\n")
if args == ["--version"]:
    print("uv " + os.environ.get("MOCK_UV_VERSION", "0.13.6"))
elif "find" in args:
    print(sys.executable)
elif "tool" in args and "install" in args:
    if os.environ.get("MOCK_UV_FAIL"):
        raise SystemExit(5)
    command = pathlib.Path(os.environ["UV_TOOL_BIN_DIR"]) / "yoink"
    command.write_text('#!/bin/sh\\necho "yoink 0.1.0"\\n')
    command.chmod(0o755)
    python = pathlib.Path(os.environ["UV_TOOL_DIR"]) / "yoink-cli/bin/python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text('#!/bin/sh\\necho "python $*" >>"$MOCK_LOG"\\n'
        'if [ "$1" = "-c" ]; then exit "${{MOCK_BROWSER_MISSING:-0}}"; fi\\n')
    python.chmod(0o755)
""",
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(f"""source {shlex.quote(str(INSTALLER))}
have() {{
    case "$1" in
        ffmpeg|ffprobe) [[ -f "$MOCK_ROOT/ffmpeg-ready" ]] ;;
        python3*) [[ -z "${{MOCK_PYTHON_MISSING:-}}" ]] && command -v "$1" >/dev/null 2>&1 ;;
        *) command -v "$1" >/dev/null 2>&1 ;;
    esac
}}
main "$@"
""")
    return Installer(tmp_path, assets, environment, log, harness)


def test_decline_installs_nothing(installer: Installer) -> None:
    result = installer.run("n\n")
    assert result.returncode == 1, result.stdout
    assert "Cancelled. Nothing installed" in result.stdout
    assert not (installer.directory / "installed").exists()
    assert "astral.sh" not in installer.log.read_text()
    assert "brew\n" not in installer.log.read_text()


def test_piped_yes_is_not_terminal_consent(installer: Installer) -> None:
    result = installer.run(None)
    assert result.returncode == 1
    assert not (installer.directory / "installed").exists()


def test_install_reuses_dependencies_and_removes_temporary_helper(installer: Installer) -> None:
    result = installer.run("y\nn\n")
    assert result.returncode == 0, result.stdout
    assert (installer.directory / "commands/yoink").is_file()
    calls = installer.log.read_text()
    assert "brew\n" not in calls
    assert "playwright install" not in calls
    assert "python install" not in calls
    assert "--constraints" in calls and "#sha256=" in calls
    assert "-m yoink.installation configure --browser" in calls
    assert "Removed temporary downloads and installer-only helpers" in result.stdout
    assert not (installer.directory / "installed/uv").exists()
    assert not (installer.directory / "installed/python").exists()
    assert not (installer.directory / "installed/releases").exists()
    helper_url = next(line for line in calls.splitlines() if "astral.sh" in line)
    assert helper_url.endswith("/install.sh")
    helper_path = next(
        line.removeprefix("helper ") for line in calls.splitlines() if line.startswith("helper ")
    )
    assert not Path(helper_path).exists()


def test_existing_uv_is_reused(installer: Installer) -> None:
    source = installer.directory / "fake-uv"
    executable(installer.directory / "bin/uv", source.read_text())
    result = installer.run("y\nn\n", "--without-browser")
    assert result.returncode == 0, result.stdout
    assert "astral.sh" not in installer.log.read_text()
    assert "Reuse your existing uv" in result.stdout
    assert (installer.directory / "bin/uv").is_file()


def test_missing_python_is_installed_for_runtime(installer: Installer) -> None:
    installer.environment["MOCK_PYTHON_MISSING"] = "1"
    result = installer.run("y\nn\n", "--without-browser")
    assert result.returncode == 0, result.stdout
    assert "Installing Python 3.13 for Yoink" in result.stdout
    assert "python install --no-bin 3.13" in installer.log.read_text()


def test_old_uv_is_left_alone_and_temporary_helper_is_used(installer: Installer) -> None:
    executable(installer.directory / "bin/uv", (installer.directory / "fake-uv").read_text())
    installer.environment["MOCK_UV_VERSION"] = "0.1.0"
    result = installer.run("y\nn\n", "--without-browser")
    assert result.returncode == 0, result.stdout
    assert "temporary helper will be used" in result.stdout
    assert "astral.sh" in installer.log.read_text()
    assert (installer.directory / "bin/uv").is_file()


def test_failed_install_cleans_up_temporary_helper(installer: Installer) -> None:
    installer.environment["MOCK_UV_FAIL"] = "1"
    result = installer.run("y\n", "--without-browser")
    assert result.returncode != 0
    assert "Installed. Try:" not in result.stdout
    calls = installer.log.read_text()
    helper = next(
        line.removeprefix("helper ") for line in calls.splitlines() if line.startswith("helper ")
    )
    assert not Path(helper).exists()


def test_missing_ffmpeg_and_browser_install_only_after_consent(installer: Installer) -> None:
    (installer.directory / "ffmpeg-ready").unlink()
    installer.environment["MOCK_BROWSER_MISSING"] = "1"
    result = installer.run("y\nn\n")
    assert result.returncode == 0, result.stdout
    calls = installer.log.read_text()
    assert "-m yoink.installation configure --ffmpeg --browser" in calls


@pytest.mark.parametrize("asset", ["yoink_cli-0.1.0-py3-none-any.whl", "constraints.txt"])
def test_corrupt_download_never_installs(installer: Installer, asset: str) -> None:
    (installer.assets / asset).write_bytes(b"corrupted")
    result = installer.run("y\nn\n")
    assert result.returncode == 1
    assert "Checksum failed" in result.stdout
    assert not (installer.directory / "installed").exists()


def test_no_homebrew_gives_ordered_manual_options(installer: Installer) -> None:
    (installer.directory / "ffmpeg-ready").unlink()
    (installer.directory / "bin/brew").unlink()
    result = installer.run("y\n")
    assert result.returncode == 1
    assert result.stdout.index("1. Install Homebrew") < result.stdout.index("2. Install ffmpeg")
    assert not (installer.directory / "installed").exists()


def test_uninstall_through_real_terminal(installer: Installer) -> None:
    code = """import sys
from pathlib import Path
from yoink.installation import Receipt, uninstall
root = Path(sys.argv[1]) / "installed"
prefix = root / "tools/yoink-cli"
(prefix / "bin").mkdir(parents=True)
(prefix / "bin/yoink").touch()
command = root.parent / "yoink"
command.symlink_to(prefix / "bin/yoink")
Receipt(str(root), str(command)).save()
sys.prefix = str(prefix)
uninstall(keep_session=True)
"""
    installer.harness.write_text(shlex.join([sys.executable, "-c", code, str(installer.directory)]))
    result = installer.run("y\n")
    assert result.returncode == 0, result.stdout
    assert "Yoink uninstalled" in result.stdout
    assert not (installer.directory / "yoink").is_symlink()
    assert not (installer.directory / "installed/tools/yoink-cli").exists()
