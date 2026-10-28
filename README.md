# Yoink

Grab a video. Keep moving.

Yoink is a typed Python CLI for downloading videos from individual Twitter/X
posts without an X developer account or official API key.

## Status

The initial CLI is implemented. A live four-video public post has been downloaded
and verified. Interactive login/2FA and authenticated downloads have not yet been
verified against a real signed-in X account. See verification details below.

## Features

- Download videos from individual Twitter/X post URLs.
- Download every video attached to a post, with quality selection and useful filenames.
- Show download progress and actionable errors.
- Support public posts and posts accessible to the signed-in user.
- Offer interactive browser login, including user-completed 2FA, and reuse the saved session.
- Keep passwords out of the CLI and session data out of Git.

## Setup and use

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), FFmpeg, and ffprobe.
The development environment uses Python 3.13, selected by `.python-version`;
uv manages an isolated `.venv` and locked dependencies. On macOS:

```bash
brew install uv ffmpeg
uv sync --locked
uv run playwright install chromium
uv run yoink doctor

uv run yoink login
uv run yoink 'https://x.com/USER/status/ID'
uv run yoink download 'https://x.com/USER/status/ID' --quality 720p --output ./videos
uv run yoink logout
```

On other systems, install uv and FFmpeg through their supported installers, then
run the same uv commands. Linux may need `uv run playwright install --with-deps chromium`.
macOS on Apple Silicon is the locally verified platform.

For a standalone `yoink` command outside this directory, optionally run
`uv tool install --editable . --python 3.13`. Use `uv tool update-shell` if uv's
tools directory needs to be added to PATH.

- Supports `x.com` and `twitter.com` post URLs, including mobile links.
- Downloads every video returned by yt-dlp, even for a `/video/2` URL. This may
  include videos from a quoted post.
- `--quality best` is the default; `worst` selects the lowest available quality.
  `360p`, `480p`, `720p`, `1080p`, and `2160p` set a **maximum video height**, without
  upscaling. A lower available height is used; no matching format is an error.
  Portrait videos follow the same height rule.
- Filenames include uploader, post ID, video ID, shortened title, and format ID.
  Different selected formats have different names. Existing files are checked
  and reused rather than overwritten.
- Progress goes to stderr; completed paths go to stdout. Separate audio and video
  streams are merged into MP4 with FFmpeg; single streams retain their format.
- Exit `0` requires every expected video to have a nonempty file and a video stream
  verified by ffprobe. This checks the stream/container, not every decoded frame.
  Missing fragments, failures, or incomplete posts exit `1`; invalid options exit
  `2`; cancellation exits `130`. Completed files are kept after partial failure.
  Retry to resume/reuse them. Move or remove a corrupt existing file before retrying.

## Login and privacy

Try public posts without login. If X requires authentication, run `uv run yoink login`
and complete login, 2FA, and challenges yourself in Chromium. Yoink waits for the
signed-in home page, saves only X cookies, and closes the browser. It never reads
or stores password form fields. The timeout defaults to five minutes; extend it
with `uv run yoink login --timeout 600`.

Sessions live outside the repository in the OS user data directory:

- macOS: `~/Library/Application Support/yoink/cookies.txt`
- Linux: `~/.local/share/yoink/cookies.txt` (or the XDG data directory)

Directories use `0700`; cookie files use `0600`. Session storage inside a Git
repository is rejected. Temporary cookie copies and extraction metadata are
private and removed when downloads finish or fail. Raw downloader/browser
diagnostics, authorization headers, and signed media URLs are not printed.

`logout` deletes local cookies; it does not revoke the session on X's servers.
Expired or rejected sessions require `login` again. Access still depends on what
your account can view.

## Architecture and limitations

The CLI uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) for extraction and downloads,
FFmpeg for merging, and Playwright for interactive login and cookie reuse.

No custom browser extractor or browser download fallback is implemented.

“No official API key” does not mean “no API requests”: yt-dlp and the website may
use X’s internal endpoints. Website changes, expired sessions, and login challenges
will require maintenance.

Modules under `src/yoink` separate CLI parsing (`cli.py`), typed values (`models.py`),
URL validation (`providers/twitter.py`), authentication (`auth.py`), extraction and
downloads (`download.py`), runtime JSON validation (`json_data.py`), and dependency
checks (`doctor.py`). Twitter/X is the only provider. Profiles, searches, account
crawling, live streams, Spaces, unattended challenges, and hosting are outside scope.

To update extraction support:

```bash
uv lock --upgrade-package yt-dlp
uv sync --locked
```

## Development and verification

VS Code settings select `.venv`, enable strict Pylance checking, format Python on
save with Ruff, and discover pytest tests. Open this folder in VS Code; select
`.venv/bin/python` if prompted. Recommended extensions are listed in `.vscode`.

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pyright
uv run pytest
uv build
```

Verification on 2026-10-28:

- A live public [four-video X post](https://x.com/UltimaShadowX/status/1577719286659006464)
  downloaded through Yoink with a 720p limit. All four files passed ffprobe.
  Repeating the command reused completed files.
- Automated tests cover URL validation, CLI failures, cookie permissions/expiry,
  mocked browser login success/failure, and download failure reporting.
- Local integration tests use real yt-dlp HTTP downloads, FFmpeg merging,
  best/worst/height selection, repeated downloads, partial failures, and real
  Chromium cookie restoration. Twitter metadata is substituted; these tests do
  not contact X or log in to an account.
- Real interactive X login/2FA and authenticated downloads remain unverified.
  Some older public fixtures returned no video or upstream media errors.

Tests need no X credentials or internet access. Media integration tests require
FFmpeg/ffprobe and a local HTTP listener; the browser test requires Chromium.
These tests skip if their dependencies are absent.

## Development workflow

1. Start a short-lived feature branch from `main`.
2. Implement and verify one feature.
3. Rebase the feature branch onto the latest `main`.
4. Fast-forward `main` to the feature branch.
5. Delete the completed feature branch.

No pull requests, merge requests, or merge commits.
GitHub operations use the owner's personal SSH identity.

See [AGENTS.md](AGENTS.md) for instructions for coding agents.
