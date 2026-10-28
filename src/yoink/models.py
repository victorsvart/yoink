"""I share typed requests and results between the CLI and downloader."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Quality(StrEnum):
    BEST = "best"
    WORST = "worst"
    P360 = "360p"
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"
    P2160 = "2160p"

    @property
    def selector(self) -> str:
        """I translate the quality choice into yt-dlp's format-selection syntax."""
        # I have to speak yt-dlp here: '+' combines streams; '/' tries fallbacks in order.
        # bv*/wv* select best/worst video, which may already include audio;
        # ba/wa select audio-only, b/w combined video+audio, and bv/wv video-only.
        if self is Quality.BEST:
            return "bv*+ba/b/bv"
        if self is Quality.WORST:
            return "wv*+wa/w/wv"

        # I cap every video alternative. A fallback isn't a license to ignore --quality.
        limit = int(self.value.removesuffix("p"))
        return f"bv*[height<={limit}]+ba/b[height<={limit}]/bv[height<={limit}]"


@dataclass(frozen=True)
class Post:
    url: str
    id: str


@dataclass(frozen=True)
class DownloadRequest:
    post: Post
    quality: Quality
    output: Path


@dataclass(frozen=True)
class Progress:
    downloaded_bytes: int
    total_bytes: int | None


@dataclass(frozen=True)
class DownloadResult:
    files: tuple[Path, ...]
