# Yoink

Grab a video. Keep moving.

Yoink will be a local command-line tool for downloading videos from links,
starting with Twitter/X. It will work without an X developer account or official
API key.

## Status

Planning only. This repository currently contains the project brief and agent
instructions; the CLI is not implemented yet.

## First release

- Download videos from individual Twitter/X post URLs.
- Download every video attached to a post, with quality selection and useful filenames.
- Show download progress and actionable errors.
- Support public posts and posts accessible to the signed-in user.
- Offer interactive browser login, including user-completed 2FA, and reuse the saved session.
- Keep passwords out of the CLI and session data out of Git.

## Intended interface

These commands describe the proposed interface; they do not run yet:

```bash
yoink login
yoink https://x.com/someone/status/123
yoink download https://x.com/someone/status/123 --quality 720p --output ./videos
yoink logout
```

## Proposed architecture

Start with a small Python CLI backed by yt-dlp for Twitter extraction and
video downloads. Use FFmpeg when media streams need assembly or merging.
Use Playwright for an interactive login flow and export the authenticated
session for the downloader to reuse.

Validate yt-dlp against representative Twitter links before building a custom
extractor. If needed, add a Playwright fallback that observes the media requests
made during playback. A player’s `blob:` URL is not itself the downloadable media.

“No official API key” does not mean “no API requests”: yt-dlp and the website may
use X’s internal endpoints. Website changes, expired sessions, and login challenges
will require maintenance.

Keep the CLI, authentication, and extraction logic separate so other sites can
be added later. Twitter/X is the only initial provider. Bulk account crawling,
unattended login challenge solving, and a hosted service are outside the first release.

## Development workflow

1. Start a short-lived feature branch from `main`.
2. Implement and verify one feature.
3. Rebase the feature branch onto the latest `main`.
4. Fast-forward `main` to the feature branch.
5. Delete the completed feature branch.

No pull requests, merge requests, or merge commits.
GitHub operations use the owner's personal SSH identity.

See [AGENTS.md](AGENTS.md) for instructions for coding agents.
