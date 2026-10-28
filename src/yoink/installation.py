"""keeping receipts. guessing who installed a shared dependency is a bad uninstall plan."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast

from yoink.errors import YoinkError

_RECEIPT = "installation.json"
_PACKAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9+_.:@/-]*")


@dataclass
class Receipt:
    root: str
    command: str
    owned: list[str] = field(default_factory=list[str])
    manager: str = ""
    packages: dict[str, str] = field(default_factory=dict[str, str])
    path_file: str = ""
    path_block: str = ""

    def save(self) -> None:
        destination = Path(self.root) / _RECEIPT
        with tempfile.NamedTemporaryFile(mode="w", dir=self.root, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(asdict(self), stream, indent=2)
        try:
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def load_receipt(root: Path) -> Receipt | None:
    path = root / _RECEIPT
    if not path.exists():
        return None
    try:
        if path.is_symlink():
            raise ValueError
        raw: object = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError
        data = cast(dict[str, object], raw)
        if set(data) != set(Receipt.__dataclass_fields__):
            raise ValueError
        for key in ("root", "command", "manager", "path_file", "path_block"):
            if not isinstance(data[key], str):
                raise ValueError
        owned, packages = data["owned"], data["packages"]
        if not isinstance(owned, list) or not isinstance(packages, dict):
            raise ValueError
        if any(value not in ("python", "browsers") for value in cast(list[object], owned)):
            raise ValueError
        if not all(
            isinstance(name, str) and _PACKAGE.fullmatch(name) and isinstance(version, str)
            for name, version in cast(dict[object, object], packages).items()
        ):
            raise ValueError
        receipt = Receipt(
            root=cast(str, data["root"]),
            command=cast(str, data["command"]),
            owned=cast(list[str], owned),
            manager=cast(str, data["manager"]),
            packages=cast(dict[str, str], packages),
            path_file=cast(str, data["path_file"]),
            path_block=cast(str, data["path_block"]),
        )
        if receipt.root != str(root) or receipt.manager not in ("", "brew", "dpkg"):
            raise ValueError
        if receipt.packages and not receipt.manager:
            raise ValueError
        if not Path(receipt.command).is_absolute():
            raise ValueError
        return receipt
    except (ValueError, TypeError, UnicodeError):
        raise YoinkError("The installation receipt is damaged. Nothing was removed.") from None


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=capture,
        check=True,
        env=dict(os.environ, LC_ALL="C"),
    )


def _root_command(command: list[str]) -> list[str]:
    return command if os.geteuid() == 0 else ["sudo", *command]


def package_snapshot(manager: str) -> dict[str, str]:
    if manager == "brew":
        output = _run(["brew", "list", "--formula", "--versions"], capture=True).stdout
        return {
            parts[0]: " ".join(parts[1:]) for line in output.splitlines() if (parts := line.split())
        }
    output = _run(
        ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Status}\\n"],
        capture=True,
    ).stdout
    return {
        parts[0]: parts[1]
        for line in output.splitlines()
        if len(parts := line.split("\t")) == 3 and parts[2] == "installed"
    }


def install_packages(receipt: Receipt, packages: list[str]) -> None:
    if not packages:
        return
    manager = "brew" if sys.platform == "darwin" else "dpkg"
    before = package_snapshot(manager)
    receipt.manager = manager
    receipt.save()
    print("Installing missing system packages; recording what Yoink adds...", flush=True)
    try:
        if sys.platform == "darwin":
            environment = dict(
                os.environ,
                HOMEBREW_NO_INSTALL_UPGRADE="1",
                HOMEBREW_NO_AUTO_UPDATE="1",
                HOMEBREW_NO_INSTALL_CLEANUP="1",
                HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK="1",
            )
            subprocess.run(["brew", "install", *packages], env=environment, check=True)
        else:
            command = [
                "apt-get",
                "install",
                "--no-upgrade",
                "--no-remove",
                "--no-install-recommends",
            ]
            simulation = _run([*command, "--simulate", *packages], capture=True).stdout
            changed = {match[1] for match in re.finditer(r"^Inst (\S+)", simulation, re.MULTILINE)}
            existing = {name.split(":")[0] for name in before}
            if any(name.split(":")[0] in existing for name in changed):
                raise YoinkError(
                    "System dependencies need an existing package upgraded. "
                    "Install them manually first."
                )
            _run(_root_command(["apt-get", "update"]))
            _run(_root_command([*command, "-y", *packages]))
    finally:
        after = package_snapshot(manager)
        receipt.packages.update(
            {name: version for name, version in after.items() if name not in before}
        )
        receipt.save()


def browser_packages() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install-deps", "--dry-run", "chromium"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return []
    lines = result.stdout.splitlines()
    packages = [line.strip() for line in lines if line.startswith("  ")]
    if not packages or not any(line.startswith("Missing system dependencies (") for line in lines):
        raise YoinkError(
            "Could not determine Chromium's missing system packages. Configure them manually."
        )
    if not all(_PACKAGE.fullmatch(package) for package in packages):
        raise YoinkError("Chromium returned an invalid system package list.")
    return packages


def configure(
    root: Path, command: Path, *, python_owned: bool, ffmpeg: bool, browser: bool
) -> None:
    if root.is_symlink() or root.resolve() != root:
        raise YoinkError("The installation directory must not contain symlinks.")
    if Path(sys.prefix) != root / "tools" / "yoink-cli":
        raise YoinkError("The app environment does not match this installation directory.")
    receipt = load_receipt(root) or Receipt(str(root), str(command))
    if receipt.command != str(command):
        raise YoinkError("This installation already uses a different command directory.")
    if python_owned and "python" not in receipt.owned:
        receipt.owned.append("python")
    receipt.save()
    if ffmpeg:
        install_packages(receipt, ["ffmpeg"])
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise YoinkError("FFmpeg installation did not provide ffmpeg and ffprobe.")
    if not browser:
        return
    use_private_browser()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        installed = Path(playwright.chromium.executable_path).is_file()
    if installed:
        print("Reusing the installed Chromium browser.", flush=True)
        return
    private = root / "browsers"
    if "browsers" not in receipt.owned:
        if private.exists() or private.is_symlink():
            raise YoinkError(
                "The private browser directory already exists without an ownership receipt."
            )
        receipt.owned.append("browsers")
        receipt.save()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(private)
    if sys.platform == "linux":
        install_packages(receipt, browser_packages())
    print("Installing Chromium in Yoink's private directory...", flush=True)
    _run([sys.executable, "-m", "playwright", "install", "chromium"])


def use_private_browser() -> None:
    prefix = Path(sys.prefix)
    root = prefix.parent.parent
    if prefix != root / "tools" / "yoink-cli":
        return
    receipt = load_receipt(root)
    if receipt and "browsers" in receipt.owned:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root / "browsers"))


def record_path(root: Path, profile: Path, block: str) -> None:
    receipt = load_receipt(root)
    if receipt is None:
        raise YoinkError("No installation receipt was found.")
    if not receipt.path_block:
        receipt.path_file, receipt.path_block = str(profile), block + "\n"
        receipt.save()


def remove_packages(receipt: Receipt) -> None:
    if not receipt.packages:
        return
    current = package_snapshot(receipt.manager)
    candidates = {
        name for name, version in receipt.packages.items() if current.get(name) == version
    }
    for name in sorted(set(receipt.packages) - candidates):
        print(f"Keeping changed or already removed system package: {name}")
    if receipt.manager == "brew":
        # a new app might depend on these by now. don't rip its legs off
        changed = True
        while changed:
            changed = False
            for name in sorted(candidates):
                users = set(
                    _run(["brew", "uses", "--installed", name], capture=True).stdout.split()
                )
                if users - candidates:
                    candidates.remove(name)
                    changed = True
                    print(f"Keeping {name}: another installed package needs it.")
        if candidates:
            environment = dict(os.environ, HOMEBREW_NO_AUTOREMOVE="1")
            subprocess.run(
                ["brew", "uninstall", "--formula", *sorted(candidates)], env=environment, check=True
            )
    elif candidates:
        # apt's simulation finds dependencies we'd break. never let it remove those too
        while candidates:
            simulation = _run(
                ["apt-get", "--simulate", "--no-auto-remove", "remove", *sorted(candidates)],
                capture=True,
            ).stdout
            removed = set(re.findall(r"^Remv (\S+)", simulation, re.MULTILINE))
            if not removed - candidates:
                break
            protected: set[str] = set()
            for name in candidates:
                trial = _run(
                    ["apt-get", "--simulate", "--no-auto-remove", "remove", name],
                    capture=True,
                ).stdout
                if set(re.findall(r"^Remv (\S+)", trial, re.MULTILINE)) - candidates:
                    protected.add(name)
            if not protected:
                protected = candidates.copy()
            for name in sorted(protected):
                print(f"Keeping {name}: another installed package needs it.")
            candidates -= protected
        if candidates:
            # dpkg checks dependencies again, without apt's automatic extra removals
            _run(_root_command(["dpkg", "--remove", *sorted(candidates)]))
    receipt.packages = {}
    receipt.save()


def uninstall(*, keep_session: bool = False) -> None:
    prefix = Path(sys.prefix)
    root = prefix.parent.parent
    if prefix != root / "tools" / "yoink-cli" or prefix.resolve() != prefix:
        raise YoinkError(
            "This command supports installer-managed copies. "
            "Remove source/tool installs with their original package manager."
        )
    receipt = load_receipt(root)
    if receipt is None:
        raise YoinkError(
            "This installation has no ownership receipt. Rerun the current installer first; "
            "older shared dependencies will be preserved."
        )
    print("\nUninstall Yoink\n--------------\n")
    print(f"Remove: {prefix}\nRemove command: {receipt.command}")
    for name in receipt.owned:
        print(f"Remove private runtime: {root / name}")
    if receipt.packages:
        print("Remove unchanged system packages added by Yoink, when no other package needs them:")
        print("  " + ", ".join(sorted(receipt.packages)))
    print("Remove the unchanged PATH entry added by the installer. Downloaded videos stay put.")
    print("Keep the saved X session." if keep_session else "Remove Yoink's saved X session.")
    try:
        with (
            open("/dev/tty", encoding="utf-8") as reader,
            open("/dev/tty", "w", encoding="utf-8") as writer,
        ):
            writer.write("\nProceed? [y/N] ")
            writer.flush()
            answer = reader.readline().strip().lower()
    except OSError:
        raise YoinkError(
            "Uninstall needs confirmation in an interactive terminal. Nothing removed."
        ) from None
    if answer not in ("y", "yes"):
        raise YoinkError("Uninstall cancelled. Nothing removed.")
    try:
        remove_packages(receipt)
    except subprocess.CalledProcessError:
        raise YoinkError(
            "Could not remove managed system packages. Yoink was kept. "
            "Resolve the package manager error and rerun 'yoink uninstall'."
        ) from None
    if receipt.path_file and receipt.path_block:
        profile = Path(receipt.path_file)
        if profile.is_file() and not profile.is_symlink():
            text = profile.read_text()
            if text.count(receipt.path_block) == 1:
                profile.write_text(text.replace(receipt.path_block, "", 1))
            else:
                print("Keeping PATH configuration: the installer entry was changed.")
    from yoink.auth import SessionStore

    if not keep_session:
        SessionStore().logout()
    command = Path(receipt.command)
    if command.is_symlink() and command.resolve() == prefix / "bin" / "yoink":
        command.unlink()
    elif command.exists() or command.is_symlink():
        print(f"Keeping changed command: {command}")
    for name in receipt.owned:
        path = root / name
        if path.is_symlink():
            print(f"Keeping changed runtime symlink: {path}")
        elif path.exists():
            print(f"Removing {path}...", flush=True)
            shutil.rmtree(path)
    print(f"Removing {prefix}...", flush=True)
    shutil.rmtree(prefix)
    (root / _RECEIPT).unlink()
    print("Yoink uninstalled. Pre-existing software and downloaded videos were kept.")


class Arguments(argparse.Namespace):
    operation: str
    root: Path
    command: Path | None
    profile: Path | None
    block: str | None
    python_owned: bool
    ffmpeg: bool
    browser: bool


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("configure", "record-path"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--command", type=Path)
    parser.add_argument("--python-owned", action="store_true")
    parser.add_argument("--ffmpeg", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--block")
    args = Arguments()
    parser.parse_args(namespace=args)
    try:
        if args.operation == "record-path":
            if args.profile is None or args.block is None:
                parser.error("record-path needs --profile and --block")
            record_path(args.root, args.profile, args.block)
        else:
            if args.command is None:
                parser.error("configure needs --command")
            configure(
                args.root,
                args.command,
                python_owned=args.python_owned,
                ffmpeg=args.ffmpeg,
                browser=args.browser,
            )
    except (YoinkError, subprocess.CalledProcessError, OSError) as error:
        print(f"yoink installer: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
