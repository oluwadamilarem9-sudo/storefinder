"""Windows console encoding must not abort discovery logging."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_safe_print_survives_unicode(monkeypatch):
    from utils.console import safe_print

    class Buffer:
        def __init__(self):
            self.chunks: list[bytes] = []

        def write(self, data: bytes):
            self.chunks.append(data)
            return len(data)

        def flush(self):
            return None

    class BrokenStdout:
        encoding = "cp1252"

        def __init__(self):
            self.buffer = Buffer()

        def write(self, _data):
            raise UnicodeEncodeError("charmap", "ı", 0, 1, "test")

        def flush(self):
            return None

    sink = BrokenStdout()
    monkeypatch.setattr(sys, "stdout", sink)
    safe_print("Theme name with Turkish ı and emoji 🙂")
    assert sink.buffer.chunks
    assert b"Theme name" in sink.buffer.chunks[0]
