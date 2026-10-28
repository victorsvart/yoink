"""one installer, even for updates. losing the ownership receipt would be pretty stupid."""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

from yoink import __version__
from yoink.errors import YoinkError
from yoink.installation import Receipt, managed_installation

RELEASES_URL = "https://github.com/victorsvart/yoink/releases"
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urlsplit(newurl).scheme != "https":
            raise YoinkError("The release download redirected away from HTTPS. Update stopped.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, *, limit: int = 1024 * 1024) -> bytes:
    try:
        opener = urllib.request.build_opener(HTTPSRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": f"yoink/{__version__}"})
        with opener.open(request, timeout=30) as response:
            data: bytes = response.read(limit + 1)
        if len(data) > limit:
            raise YoinkError("The release file was larger than expected. Update stopped.")
        return data
    except (OSError, urllib.error.URLError):
        raise YoinkError(
            "Could not download the release. Check your connection and try again."
        ) from None


def release_version(data: bytes) -> str:
    try:
        version = data.decode("ascii").strip()
    except UnicodeError:
        raise YoinkError("The release returned an invalid version. Nothing updated.") from None
    if not _VERSION.fullmatch(version):
        raise YoinkError("The release returned an invalid version. Nothing updated.")
    return version


def verified_installer(base: str) -> bytes:
    try:
        lines = download(f"{base}/SHA256SUMS").decode("ascii").splitlines()
    except UnicodeError:
        raise YoinkError("The release checksums are invalid. Nothing updated.") from None
    matches = [
        parts[0] for line in lines if len(parts := line.split()) == 2 and parts[1] == "install.sh"
    ]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{64}", matches[0]):
        raise YoinkError("The release has no valid installer checksum. Nothing updated.")
    installer = download(f"{base}/install.sh")
    if hashlib.sha256(installer).hexdigest() != matches[0]:
        raise YoinkError("Installer SHA-256 verification failed. Nothing updated.")
    return installer


def browser_enabled(receipt: Receipt) -> bool:
    if receipt.browser is not None:
        return receipt.browser
    if "browsers" in receipt.owned:
        return True
    # old receipts didn't record the choice. keep whatever is actually available
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).is_file()


def update(*, check_only: bool = False, repair: bool = False) -> None:
    receipt = managed_installation()
    command = Path(receipt.command)
    target = Path(receipt.root) / "tools/yoink-cli/bin/yoink"
    if not command.is_symlink() or command.resolve() != target:
        raise YoinkError("The yoink launcher has changed. Update stopped to preserve it.")
    print("Checking for a newer Yoink release...", flush=True)
    latest = release_version(download(f"{RELEASES_URL}/latest/download/VERSION", limit=64))
    current = tuple(int(part) for part in __version__.split("."))
    available = tuple(int(part) for part in latest.split("."))
    if available < current or (available == current and not repair):
        print(f"Yoink {__version__} is up to date.")
        return
    if available == current:
        print(f"Checking the setup for Yoink {latest}...", flush=True)
    else:
        print(f"Yoink {latest} is available (installed: {__version__}).", flush=True)
    if check_only:
        print("Run 'yoink update' to install it.")
        return
    bash = shutil.which("bash")
    if bash is None:
        raise YoinkError("bash is required to update this installation.")
    print("Downloading and verifying the release installer...", flush=True)
    content = verified_installer(f"{RELEASES_URL}/download/v{latest}")
    environment = dict(
        os.environ,
        YOINK_INSTALL_DIR=receipt.root,
        YOINK_BIN_DIR=str(command.parent),
    )
    try:
        with tempfile.TemporaryDirectory(prefix="yoink-update-") as temporary:
            installer = Path(temporary) / "install.sh"
            installer.write_bytes(content)
            arguments = [bash, str(installer), "--version", latest]
            if not browser_enabled(receipt):
                arguments.append("--without-browser")
            print(
                "Keeping this installation's dependency ownership history and saved session.",
                flush=True,
            )
            result = subprocess.run(arguments, env=environment, check=False)
            if result.returncode == 130:
                raise KeyboardInterrupt
            if result.returncode:
                raise YoinkError(
                    "Update did not complete. The ownership receipt was kept. "
                    "Resolve the error above, then run 'yoink update --repair'."
                )
            installed = subprocess.run(
                [str(command), "--version"], capture_output=True, text=True, check=False
            )
            if installed.returncode or installed.stdout.strip() != f"yoink {latest}":
                raise YoinkError("Could not confirm the updated version. Run 'yoink doctor'.")
    finally:
        print("Removed temporary update files.", flush=True)
    print(f"Updated to Yoink {latest}.")
