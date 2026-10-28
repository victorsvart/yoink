# Yoink

Grab a video. Keep moving.

Yoink is a typed Python CLI for downloading videos from individual Twitter/X
posts without an X developer account or official API key.

## Install

On macOS or Linux:

```bash
curl -fsSL https://github.com/victorsvart/yoink/releases/latest/download/install.sh | bash
```

The installer checks what's already installed, lists anything missing, and asks
before making changes. It installs the released package, verifies its SHA-256
checksum, and puts `yoink` in `~/.local/bin`. No clone or `uv run` needed.

Compatible Python, uv, FFmpeg, and Chromium installations are reused. Missing
runtime dependencies stay installed; temporary installer helpers are removed.
Python packages live in Yoink's own environment. Adding the command directory to
your shell's PATH requires separate consent.

On macOS, if FFmpeg is missing and Homebrew isn't installed:

1. Install [Homebrew](https://brew.sh), then run `brew install ffmpeg`.
2. Or install both `ffmpeg` and `ffprobe` from [FFmpeg's downloads](https://ffmpeg.org/download.html)
   and put them on your PATH.

Rerun the installer after setup. On Linux, automatic system dependency installation
uses apt on Debian/Ubuntu. To skip Chromium when using an existing browser session:

```bash
curl -fsSL https://github.com/victorsvart/yoink/releases/latest/download/install.sh | bash -s -- --without-browser
```

For a specific release, add `--version 0.1.4`.

## Update

```bash
yoink update
```

Checks the latest stable release, verifies its SHA-256 checksums, and asks before
updating Yoink or installing missing dependencies. Existing tools are reused;
the ownership receipt, saved session, install location, and Chromium choice carry
forward so uninstall still knows what belongs to Yoink.

To check without changing anything:

```bash
yoink update --check
```

Interactive commands show a notice when a newer version is known. Release checks
run in the background at most once every 24 hours, so commands don't wait for
GitHub. Offline failures stay quiet. Notices go to stderr; noninteractive commands skip
the automatic check. Set `YOINK_NO_UPDATE_CHECK=1` to disable automatic checks.
`yoink update` always checks immediately, regardless of the cache.

If dependency setup was interrupted, use `yoink update --repair` to retry it.
Versions before 0.1.2 need one more installer run to get these commands.

## Uninstall

```bash
yoink uninstall
```

Shows what will be removed and asks for confirmation. Removes Yoink, its private
Python and Chromium downloads, the saved X session, and the PATH entry added by
the installer. Use `yoink uninstall --keep-session` to keep the saved login.
Downloaded videos stay where they are.

Only system packages recorded as newly installed by Yoink are eligible for removal.
Pre-existing tools, changed packages, and dependencies needed by other installed
software are kept. Installations made before ownership tracking was added keep
older dependencies whose ownership is unknown; rerun the current installer first.

## Download

```bash
yoink 'https://x.com/USER/status/ID'
yoink download 'https://x.com/USER/status/ID' --quality 720p --output ./videos
```

Downloads all videos in the post. Files go into `~/Downloads` unless you
set `--output`. Existing files are reused; completed files are kept if another
video fails.

Quality defaults to `best`. You can also use `worst`, `360p`, `480p`, `720p`,
`1080p`, or `2160p`. A resolution sets the maximum video height: `720p` picks the
best available video at or below 720 pixels tall, without upscaling.

## Sign in

Public posts may work without login. For posts that need it, reuse a browser
where you're already signed into X:

```bash
yoink login --from-browser chrome
yoink login --from-browser safari
```

Choose the command for your browser. To select a particular profile:

```bash
yoink login --from-browser chrome --profile "Profile 1"
```

See `yoink login --help` for the full browser list.

**Safari needs manual setup:** enable the terminal app running Yoink under
**System Settings > Privacy & Security > Full Disk Access**. Quit and reopen
that app if macOS asks. Yoink cannot grant this permission for you.

Interactive browser login is also available:

```bash
yoink login
```

Complete sign-in and 2FA in the browser. This login method is experimental and
may be blocked by X. If X limits new login attempts, use an existing browser
session or wait before trying again.

Only X/Twitter cookies are saved. Passwords aren't stored. `yoink doctor` shows
the session directory. To delete Yoink's copy of the session:

```bash
yoink logout
```

This doesn't sign you out of X elsewhere.

## Limits

Individual Twitter/X posts only. No profile downloads, account crawling, live
streams, or Spaces. Signing in doesn't unlock posts your account can't view.
Extraction comes from [yt-dlp](https://github.com/yt-dlp/yt-dlp); changes to X can
break it. Check for a newer Yoink release if downloads stop working.

## Development

For working on the source:

```bash
git clone https://github.com/victorsvart/yoink.git
cd yoink
uv sync --locked
uv run playwright install chromium
uv run yoink --help
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pyright
uv run pytest
uv build
```

Push a version tag such as `v0.1.0` to build a release. The tag must match the
package version. GitHub Actions runs the checks on macOS and Linux, then publishes
the wheel, source archive, locked dependency constraints, installer, and checksums.
