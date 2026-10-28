"""checking dependencies before finding out halfway through a download."""

import shutil
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from playwright.sync_api import sync_playwright


@dataclass(frozen=True)
class Check:
    name: str
    ready: bool
    detail: str


def check_system() -> tuple[Check, ...]:
    checks = [Check("Python", sys.version_info >= (3, 13), sys.version.split()[0])]
    for package in ("yt-dlp", "playwright"):
        try:
            checks.append(Check(package, True, version(package)))
        except PackageNotFoundError:
            checks.append(Check(package, False, "Reinstall Yoink using the installer."))
    for tool in ("ffmpeg", "ffprobe"):
        executable = shutil.which(tool)
        checks.append(
            Check(
                tool, executable is not None, executable or "Install FFmpeg or rerun the installer."
            )
        )
    with sync_playwright() as playwright:
        installed = Path(playwright.chromium.executable_path).is_file()
    checks.append(
        Check(
            "Chromium",
            installed,
            "Installed" if installed else "Rerun the installer to install Chromium.",
        )
    )
    return tuple(checks)
