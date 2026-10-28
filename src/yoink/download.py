"""wrapping yt-dlp without dumping its raw diagnostics into the terminal."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from yoink.auth import SessionStore
from yoink.errors import YoinkError, extraction_error
from yoink.json_data import object_data, object_list, parse_object
from yoink.models import DownloadRequest, DownloadResult, Progress

_BASE = (
    sys.executable,
    "-m",
    "yt_dlp",
    "--ignore-config",
    "--no-plugin-dirs",
    "--no-cache-dir",
    "--no-clean-info-json",
    "--no-colors",
    "--no-warnings",
    "--abort-on-error",
    "--abort-on-unavailable-fragments",
    "--socket-timeout",
    "30",
    "--retries",
    "3",
    "--fragment-retries",
    "3",
    "--yes-playlist",
)
_FILE_PREFIX = "YOINK_FILE:"
_PROGRESS_PREFIX = "YOINK_PROGRESS:"


def count_videos(metadata: Mapping[str, object]) -> int:
    entries = metadata.get("entries")
    if entries is None:
        return _count_single_video(metadata)

    videos = object_list(entries)
    if not videos:
        raise YoinkError("This post does not contain a downloadable video.")

    return sum(count_videos(object_data(entry)) for entry in videos)


def _count_single_video(metadata: Mapping[str, object]) -> int:
    if not metadata.get("formats") or not isinstance(metadata.get("id"), str):
        raise YoinkError("This post does not contain a downloadable video.")
    if metadata.get("is_live") is True:
        raise YoinkError("Live videos are not supported. Retry when the broadcast has ended.")

    return 1


def _private_write(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def verify_media(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0 or path.suffix in {".part", ".ytdl"}:
        raise YoinkError(
            "The downloader did not produce a complete video file. Retry the download."
        )
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise YoinkError(
            "Media verification timed out; the download is not confirmed complete."
        ) from None
    if probe.returncode:
        raise YoinkError("The downloaded file failed media verification. Retry the download.")
    streams = object_list(parse_object(probe.stdout).get("streams"))
    if not any(object_data(stream).get("codec_type") == "video" for stream in streams):
        raise YoinkError("The downloaded file contains no video stream.")


def parse_progress(line: str) -> Progress:
    data = parse_object(line)
    downloaded = data.get("downloaded")
    total = data.get("total")
    estimate = data.get("estimate")
    if not isinstance(downloaded, int) or downloaded < 0:
        return Progress(0, None)
    total_bytes = total if isinstance(total, int) and total > 0 else estimate
    return Progress(
        downloaded, total_bytes if isinstance(total_bytes, int) and total_bytes > 0 else None
    )


class Downloader:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def download(
        self,
        request: DownloadRequest,
        *,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> DownloadResult:
        for tool in ("ffmpeg", "ffprobe"):
            if shutil.which(tool) is None:
                raise YoinkError(
                    "FFmpeg and ffprobe are required. Install them with 'brew install ffmpeg'."
                )
        cookies = self.store.load()
        output = request.output.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        # signed URLs and cookie copies stay in a private temp directory, then get deleted
        with tempfile.TemporaryDirectory(prefix="yoink-") as temporary:
            directory = Path(temporary)
            cookie_args: list[str] = []
            if cookies:
                cookie_path = directory / "cookies.txt"
                _private_write(
                    cookie_path,
                    "# Netscape HTTP Cookie File\n"
                    + "\n".join(cookie.to_netscape() for cookie in cookies)
                    + "\n",
                )
                cookie_args = ["--cookies", str(cookie_path)]
            metadata = extract_metadata(request, cookie_args)
            expected = count_videos(parse_object(metadata))
            info_path = directory / "info.json"
            _private_write(info_path, metadata)
            command = [
                *_BASE,
                *cookie_args,
                "--load-info-json",
                str(info_path),
                "--no-simulate",
                "--format",
                request.quality.selector,
                "--merge-output-format",
                "mp4",
                "--no-overwrites",
                "--restrict-filenames",
                "--trim-filenames",
                "200",
                "--paths",
                str(output),
                "--output",
                f"%(uploader_id,uploader|x)s_{request.post.id}_%(id)s_"
                "%(title).80B_f%(format_id)s.%(ext)s",
                "--newline",
                "--progress",
                "--progress-delta",
                "0.5",
                "--progress-template",
                "download:" + _PROGRESS_PREFIX + '{"downloaded":%(progress.downloaded_bytes|0)d,'
                '"total":%(progress.total_bytes|0)d,"estimate":%(progress.total_bytes_estimate|0)d}',
                "--print",
                "after_move:" + _FILE_PREFIX + "%(filepath)j",
            ]
            files: list[Path] = []
            diagnostics: list[str] = []
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ) as process:
                try:
                    assert process.stdout is not None
                    for raw in process.stdout:
                        line = raw.strip()
                        if line.startswith(_FILE_PREFIX):
                            try:
                                value: object = json.loads(line.removeprefix(_FILE_PREFIX))
                            except ValueError:
                                raise YoinkError(
                                    "The downloader returned an invalid output path."
                                ) from None
                            if not isinstance(value, str):
                                raise YoinkError("The downloader returned an invalid output path.")
                            path = Path(value).resolve()
                            if not path.is_relative_to(output):
                                raise YoinkError(
                                    "The downloader returned a path outside the output directory."
                                )
                            files.append(path)
                        elif line.startswith(_PROGRESS_PREFIX):
                            if on_progress:
                                on_progress(parse_progress(line.removeprefix(_PROGRESS_PREFIX)))
                        else:
                            # keeping a small buffer in memory. still not printing its secrets
                            diagnostics.append(line[-4096:])
                            diagnostics = diagnostics[-20:]
                    returncode = process.wait()
                except BaseException:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise
            if returncode:
                error = extraction_error("\n".join(diagnostics), authenticated=bool(cookies))
                raise YoinkError(f"{error} The post is incomplete; any completed files were kept.")
            if len(files) != expected or len(set(files)) != expected:
                raise YoinkError(
                    "Not every video was downloaded. The post is incomplete; retry the download."
                )
            for path in files:
                verify_media(path)
            return DownloadResult(tuple(files))


def extract_metadata(request: DownloadRequest, cookie_args: Sequence[str]) -> str:
    try:
        extraction = subprocess.run(
            [
                *_BASE,
                *cookie_args,
                "--skip-download",
                "--dump-single-json",
                "--format",
                request.quality.selector,
                "--",
                request.post.url,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise YoinkError("X extraction timed out. Check your connection and retry.") from None
    if extraction.returncode:
        raise extraction_error(extraction.stderr, authenticated=bool(cookie_args))
    return extraction.stdout
