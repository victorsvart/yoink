import os
from pathlib import Path

import pytest

from yoink.installation import Receipt


@pytest.fixture
def receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Receipt:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    root = tmp_path / "app"
    prefix = root / "tools/yoink-cli"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/yoink").write_text("app entry point")
    command = tmp_path / "bin/yoink"
    command.parent.mkdir()
    command.symlink_to(prefix / "bin/yoink")
    monkeypatch.setattr("sys.prefix", str(prefix))
    result = Receipt(str(root), str(command))
    result.save()
    return result
