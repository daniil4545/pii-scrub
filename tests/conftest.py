import io
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from pii_scrub.cli import main


@dataclass
class Result:
    code: int
    out: str
    err: str


@pytest.fixture
def run(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Result]:
    """Runs the CLI in process: main(argv), stdin is replaced with bytes."""

    def call(argv: list[str], stdin: bytes = b"") -> Result:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8"))
        code = main(argv)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return call


@pytest.fixture
def write_input(tmp_path: Path) -> Callable[[str | bytes], Path]:
    def write(content: str | bytes) -> Path:
        path = tmp_path / "call.txt"
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    return write
