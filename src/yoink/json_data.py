"""I validate subprocess JSON. Valid syntax isn't a trustworthy schema."""

import json
from collections.abc import Mapping
from typing import cast

from yoink.errors import YoinkError


def object_data(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise YoinkError("The downloader returned invalid metadata. Try updating yt-dlp.")
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise YoinkError("The downloader returned invalid metadata. Try updating yt-dlp.")
    return cast(Mapping[str, object], value)


def parse_object(text: str) -> Mapping[str, object]:
    try:
        value: object = json.loads(text)
    except ValueError:
        raise YoinkError("The downloader returned invalid metadata. Try updating yt-dlp.") from None
    return object_data(value)


def object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise YoinkError("The downloader returned an invalid video list. Try updating yt-dlp.")
    return cast(list[object], value)
