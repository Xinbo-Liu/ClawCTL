from __future__ import annotations

import sys
from typing import TextIO


def _require_stream(stream: TextIO | None, *, label: str) -> TextIO:
    if stream is None:
        raise RuntimeError(f'{label} is unavailable')
    return stream


def stdout_write(text: str) -> None:
    _require_stream(sys.stdout, label='stdout').write(text)


def stderr_write(text: str) -> None:
    _require_stream(sys.stderr, label='stderr').write(text)
