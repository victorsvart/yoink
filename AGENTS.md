# Yoink — agent instructions

## Purpose and current state

Build a local CLI for downloading videos from links, starting with Twitter/X.
Read README.md for the intended scope and architecture. This is a planning-only
repository: no implementation, packaging, dependencies, or tests exist yet.
Do not describe planned commands as working features.

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
- Add development/install/test commands here when the toolchain exists.
- Test URL validation, authentication/session handling, quality selection,
  multi-video posts, and failure reporting as those features are introduced.
- Distinguish mocked tests from live download verification in progress reports.
- Update the README as functionality becomes available, including actual setup
  requirements and limitations. Keep the initial release focused.
