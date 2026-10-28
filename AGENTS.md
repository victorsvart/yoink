# Yoink

Twitter/X video downloader. Python package in src/yoink, yt-dlp extraction,
Playwright interactive authentication. No official X API credentials required.

## Workflow

- Start each feature from main on a short-lived feature branch.
- Run tests and lint before completing a feature.
- Rebase the feature onto main, fast-forward main, then delete the feature branch.
- Do not create pull requests or merge requests. Do not create merge commits.
- GitHub Git operations use the personal SSH key configured in core.sshCommand.

## Development

Install: `python3 -m venv .venv && .venv/bin/pip install -e '.[browser,dev]'`
Check: `.venv/bin/pytest && .venv/bin/ruff check .`
Never commit cookies, browser profiles, downloaded media, or credentials.
