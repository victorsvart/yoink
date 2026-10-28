# Yoink — agent instructions

## Purpose and current state

Build a local CLI for downloading videos from links, starting with Twitter/X.
Read README.md for the scope, architecture, and verification limits. The initial
typed Python CLI is implemented with uv packaging, yt-dlp, Playwright, and tests.
Do not describe unverified authenticated downloads or a browser extraction fallback
as proven working features.

## Product direction

- Support Twitter/X first. Keep provider logic separate for future expansion.
- Require no official X API key or developer account.
- Prefer yt-dlp for extraction and downloads, FFmpeg for media assembly, and
  Playwright for interactive login and session reuse.
- Test representative links before deciding whether custom browser extraction
  is necessary. Do not promise a browser fallback until it is implemented and tested.
- Let users complete login and 2FA in the browser. Do not store passwords.
- Keep cookies and browser state outside the repository with restrictive file
  permissions. Never commit or log credentials, cookies, or authorization headers.
- Report expired sessions, inaccessible posts, missing videos, and download failures
  clearly. Never report success for a failed or partial download.

## Git workflow — required

- Start each feature from main on a short-lived feature branch.
- Complete appropriate verification before integrating the feature.
- Rebase onto the latest main, fast-forward main, then delete the feature branch.
- Do not create pull requests, merge requests, or merge commits.
- Do not force-push main or delete unrelated branches.
- Use the personal SSH identity for GitHub Git operations. This repository's
  core.sshCommand is configured for ~/.ssh/id_ed25519 with IdentitiesOnly=yes.
  Do not switch to a work identity or alter global SSH configuration.

## Implementation and verification

- Prefer a small Python package with a CLI entry point named yoink.
- Keep CLI parsing, authentication, and provider/download logic separate.
- Use Python 3.13+ with the project runtime pinned in `.python-version`.
- Setup: `uv sync --locked`, `uv run playwright install chromium`, and
  `uv run yoink doctor`. FFmpeg/ffprobe must also be installed on PATH.
- Verify: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  `uv run pyright`, `uv run pytest`, and `uv build`.
- Keep both mypy and Pyright strict. Validate untyped subprocess data at runtime;
  do not hide typing failures with blanket ignores or untyped application APIs.
- Integration tests use real local media downloads/merges and Chromium, but substitute
  Twitter metadata. Real account login and authenticated downloads need separate live
  verification; never use real credentials in automated tests.
- Test URL validation, authentication/session handling, quality selection,
  multi-video posts, and failure reporting as those features are introduced.
- Distinguish mocked tests from live download verification in progress reports.
- Update the README as functionality becomes available, including actual setup
  requirements and limitations. Keep the initial release focused.
