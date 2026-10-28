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
from yoink.models import DownloadRequest, Progress, Quality
from yoink.providers.twitter import parse_post


class Arguments(argparse.Namespace):
    command: str
    url: str
    quality: Quality
    output: Path
    timeout: int
    from_browser: CookieBrowser | None
    profile: str | None


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
    auth.add_argument(
        "--from-browser",
        type=CookieBrowser,
        choices=list(CookieBrowser),
        help="Copy an existing browser's X session without making a new login attempt",
    )
    auth.add_argument("--profile", help="Browser profile name/path for --from-browser")
    commands.add_parser("logout", help="Delete the locally saved session")
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
        "Safari's cookies are protected by macOS privacy permissions.\n"
        "After you continue, Yoink will request access to read Safari's cookie database.\n"
        "Allow access if macOS asks. If no prompt appears and access is blocked, open\n"
        "System Settings > Privacy & Security > Full Disk Access and allow the terminal/app\n"
        "running Yoink (for example, Terminal or iTerm). You may need to quit and reopen it.\n"
        "Only X/Twitter cookies will be saved in Yoink's private session store.",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = input("Press Enter to proceed, or Ctrl+C to cancel: ")
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
    try:
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
