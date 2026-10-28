"""building release files from the lockfile, not whatever PyPI feels like today."""

import argparse
import ast
import hashlib
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_version(tag: str, root: Path = ROOT) -> str:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Release tags must look like v0.1.0.")
    version = tag.removeprefix("v")
    project = tomllib.loads((root / "pyproject.toml").read_text())
    if project["project"]["version"] != version:
        raise ValueError("Release tag and pyproject.toml version do not match.")
    source = ast.parse((root / "src/yoink/__init__.py").read_text())
    versions = [
        node.value.value
        for node in source.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    if versions != [version]:
        raise ValueError("Release tag and yoink.__version__ do not match.")
    return version


def write_checksums(directory: Path) -> None:
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(directory.iterdir())
        if path.name != "SHA256SUMS" and path.is_file()
    ]
    (directory / "SHA256SUMS").write_text("".join(lines))


def build_release(tag: str, output: Path) -> None:
    version = validate_version(tag)
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["uv", "build", "--out-dir", str(output)], cwd=ROOT, check=True)
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--no-header",
            "--format",
            "requirements-txt",
            "--output-file",
            str(output / "constraints.txt"),
        ],
        cwd=ROOT,
        check=True,
    )
    expected = output / f"yoink_cli-{version}-py3-none-any.whl"
    if not expected.is_file():
        raise RuntimeError("The build did not produce the expected release wheel.")
    shutil.copyfile(ROOT / "install.sh", output / "install.sh")
    (output / "VERSION").write_text(version + "\n")
    write_checksums(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/release")
    args = parser.parse_args()
    build_release(args.tag, args.output.resolve())


if __name__ == "__main__":
    main()
