"""Console helpers that must never abort discovery on Windows encoding errors."""

from __future__ import annotations

import sys


def safe_print(message: str) -> None:
    """Print for operators without raising UnicodeEncodeError on cp1252 consoles."""
    try:
        print(message, flush=True)
        return
    except UnicodeEncodeError:
        pass
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    payload = (str(message) + "\n").encode(encoding, errors="replace")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        buffer.flush()
        return
    sys.stdout.write(payload.decode(encoding, errors="replace"))
    sys.stdout.flush()
