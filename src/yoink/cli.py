"""commands and output here. extraction can deal with its own problems."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from yoink import __version__
from yoink.auth import SessionStore, import_browser_session, login
from yoink.browser_cookies import CookieBrowser
from yoink.doctor import check_system
from yoink.download import Downloader
from yoink.errors import YoinkError
from yoink.installation import uninstall, use_private_browser
from yoink.models import DownloadRequest, Progress, Quality
from yoink.providers.twitter import parse_post
from yoink.update import update
from yoink.update_notice import UpdateNotice


class Arguments(argparse.Namespace):
    command: str
    url: str
    quality: Quality
    output: Path
    timeout: int
    from_browser: CookieBrowser | None
    profile: str | None
    keep_session: bool
    check_only: bool
    repair: bool


def _timeout(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Timeout must be an integer number of seconds.") from None

    if not 1 <= seconds <= 3600:
        raise argparse.ArgumentTypeError("Timeout must be between 1 and 3600 seconds.")
    return seconds


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yoink",
        description="Download every video from an individual Twitter/X post.",
        epilog="Shortcut: yoink https://x.com/USER/status/ID",
    )
    parser.add_argument("--version", action="version", version=f"yoink {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="Download all videos attached to a post")
    download.add_argument("url", help="Individual Twitter/X post URL")
    download.add_argument(
        "--quality",
        type=Quality,
        choices=list(Quality),
        default=Quality.BEST,
        help="Maximum video height, or best/worst (default: best)",
    )
    download.add_argument("--output", "-o", type=Path, default=Path("."), help="Output directory")
    auth = commands.add_parser(
        "login", help="Sign in through a browser and save the session locally"
    )
    auth.add_argument(
        "--timeout", type=_timeout, default=300, help="Login timeout in seconds (default: 300)"
    )

    # i don't like the fact i have  to fallback to stealing cookies , but oh well
    auth.add_argument(
        "--from-browser",
        type=CookieBrowser,
        choices=list(CookieBrowser),
        help="Copy an existing browser's X session without making a new login attempt",
    )
    auth.add_argument("--profile", help="Browser profile name/path for --from-browser")
    commands.add_parser("logout", help="Delete the locally saved session")
    removal = commands.add_parser(
        "uninstall", help="Remove Yoink and dependencies added by its installer"
    )
    removal.add_argument("--keep-session", action="store_true", help="Keep Yoink's saved X session")
    refresh = commands.add_parser("update", help="Update Yoink while keeping dependency ownership")
    refresh_mode = refresh.add_mutually_exclusive_group()
    refresh_mode.add_argument(
        "--check", dest="check_only", action="store_true", help="Check without installing"
    )
    refresh_mode.add_argument(
        "--repair", action="store_true", help="Retry setup even when already on the latest release"
    )
    commands.add_parser("doctor", help="Check Python, downloader, FFmpeg, and browser dependencies")
    return parser


def _show_progress(progress: Progress) -> None:
    megabytes = progress.downloaded_bytes / (1024 * 1024)
    if progress.total_bytes:
        percent = min(100, progress.downloaded_bytes * 100 / progress.total_bytes)
        message = f"Downloading: {percent:5.1f}% ({megabytes:.1f} MiB)"
    else:
        message = f"Downloading: {megabytes:.1f} MiB"
    print(message, file=sys.stderr, flush=True)


def _confirm_safari_access() -> None:
    print(
        "\nSafari cookie access\n"
        "--------------------\n"
        "\n"
        "Full Disk Access must be enabled manually in macOS.\n"
        "Yoink cannot request or grant this permission automatically.\n"
        "\n"
        "  1. Open System Settings > Privacy & Security > Full Disk Access.\n"
        "  2. Enable access for the app running Yoink (Terminal, iTerm, etc.).\n"
        "  3. Quit and reopen that app if macOS asks, then run this command again.\n"
        "\n"
        "If access is already enabled, you can continue.\n"
        "Only X/Twitter cookies will be saved in Yoink's private session store.\n",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = input("Press Enter to read Safari cookies, or Ctrl+C to cancel: ")
    except EOFError:
        raise YoinkError(
            "Safari access was not confirmed. Run in an interactive terminal and press Enter "
            "to proceed. No cookies were read."
        ) from None

    if answer:
        raise YoinkError("Safari access cancelled. No cookies were read.")


def main(argv: Sequence[str] | None = None) -> int:
    values = list(argv if argv is not None else sys.argv[1:])
    if values and values[0].startswith(("https://", "http://")):
        values.insert(0, "download")
    args = Arguments()
    parser = _parser()
    parser.parse_args(values, namespace=args)
    if args.command == "login" and args.profile is not None and args.from_browser is None:
        parser.error("--profile requires --from-browser")
    store = SessionStore()
    notice = UpdateNotice.start() if args.command not in ("update", "uninstall") else None
    try:
        if args.command == "uninstall":
            uninstall(keep_session=args.keep_session)
            return 0
        use_private_browser()
        if args.command == "update":
            update(check_only=args.check_only, repair=args.repair)
            return 0
        if args.command == "doctor":
            checks = check_system()
            for check in checks:
                print(f"{'OK' if check.ready else 'MISSING'}  {check.name}: {check.detail}")
            print(f"Session location: {store.directory}")
            return 0 if all(check.ready for check in checks) else 1
        if args.command == "login":
            if args.from_browser is not None:
                if args.from_browser is CookieBrowser.SAFARI:
                    _confirm_safari_access()
                print("Reading the selected browser's session. Only X cookies will be saved.")
                import_browser_session(store, args.from_browser, profile=args.profile)
            else:
                print(
                    "Finish X login and 2FA in the browser. Waiting for your signed-in home page…"
                )
                login(store, timeout=args.timeout)
            print(
                "Signed-in session saved privately. "
                "You can now download posts your account can access."
            )
        elif args.command == "logout":
            removed = store.logout()
            print("Local session deleted." if removed else "No saved session to delete.")
        else:
            post = parse_post(args.url)
            print("Finding videos…", file=sys.stderr, flush=True)
            result = Downloader(store).download(
                DownloadRequest(post, args.quality, args.output),
                on_progress=_show_progress,
            )
            print(f"Complete: {len(result.files)} video(s).")
            for path in result.files:
                print(path)
        return 0
    except YoinkError as error:
        print(f"yoink: {error}", file=sys.stderr)
        return 1
    except OSError:
        print(
            "yoink: Could not read/write a file or start a dependency. "
            "Check directory permissions, free space, and 'yoink doctor'.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nyoink: Cancelled.", file=sys.stderr)
        return 130
    finally:
        if notice is not None:
            notice.show()
