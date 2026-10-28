import hashlib
from pathlib import Path

import pytest

from scripts.build_release import validate_version, write_checksums


def project(root: Path, *, version: str = "0.1.0") -> None:
    (root / "src/yoink").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    (root / "src/yoink/__init__.py").write_text(f'__version__ = "{version}"\n')


def test_release_tag_matches_package(tmp_path: Path) -> None:
    project(tmp_path)
    assert validate_version("v0.1.0", tmp_path) == "0.1.0"


@pytest.mark.parametrize("tag", ["main", "v0.2.0", "v0.1.0-beta", "v0.1.0;echo nope"])
def test_rejects_invalid_release_tag(tmp_path: Path, tag: str) -> None:
    project(tmp_path)
    with pytest.raises(ValueError):
        validate_version(tag, tmp_path)


def test_rejects_inconsistent_cli_version(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "src/yoink/__init__.py").write_text('__version__ = "0.2.0"\n')
    with pytest.raises(ValueError, match=r"yoink\.__version__"):
        validate_version("v0.1.0", tmp_path)


def test_asset_checksums_are_complete_and_repeatable(tmp_path: Path) -> None:
    (tmp_path / "wheel.whl").write_bytes(b"package")
    (tmp_path / "VERSION").write_text("0.1.0\n")
    write_checksums(tmp_path)
    first = (tmp_path / "SHA256SUMS").read_text()
    write_checksums(tmp_path)
    assert (tmp_path / "SHA256SUMS").read_text() == first
    for line in first.splitlines():
        digest, name = line.split()
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    assert len(first.splitlines()) == 2
