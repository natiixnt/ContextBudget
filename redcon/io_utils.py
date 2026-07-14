"""Filesystem helpers shared across redcon."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically.

    The content is written to a temporary file in the same directory, flushed
    and fsynced, then ``os.replace``-d over the target. A concurrent reader -
    another agent packing the same repo, or the VS Code panel tailing the run
    feed - therefore sees either the previous file or the new one in full,
    never a half-written truncated file.

    The temp file is created alongside the target because ``os.replace`` is only
    atomic when source and destination share a filesystem; it is atomic on both
    POSIX and Windows in that case. On any failure the temp file is removed so
    interrupted writes do not litter ``.redcon/``.
    """
    target = Path(path)
    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
