"""Read-only checks for runtime dependencies."""

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
            checks.append(Check(package, False, "Run 'uv sync'."))
    for tool in ("ffmpeg", "ffprobe"):
        executable = shutil.which(tool)
        checks.append(
            Check(tool, executable is not None, executable or "Run 'brew install ffmpeg'.")
        )
    with sync_playwright() as playwright:
        installed = Path(playwright.chromium.executable_path).is_file()
    checks.append(
        Check(
            "Chromium",
            installed,
            "Installed" if installed else "Run 'uv run playwright install chromium'.",
        )
    )
    return tuple(checks)
