"""Concurrent agents must not clobber each other's summary-cache writes.

Each agent process loads the JSON cache into memory, adds its own entries, and
flushes the whole file on ``save()``. Without a merge the last writer overwrote
everyone else's keys (last-writer-wins per *file*). ``_save`` now re-reads the
file and merges per key under a lock, so every process's entries survive.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import pytest

from redcon.cache.backends import LocalFileSummaryCacheBackend
from redcon.schemas.models import CACHE_FILE

_HAS_FCNTL = importlib.util.find_spec("fcntl") is not None


def _summaries_on_disk(repo: Path) -> dict[str, str]:
    return json.loads((repo / CACHE_FILE).read_text(encoding="utf-8"))["summaries"]


def test_concurrent_backends_do_not_clobber_each_other(tmp_path: Path) -> None:
    # Both processes load the (empty) cache, then each writes a distinct key.
    a = LocalFileSummaryCacheBackend(tmp_path)
    b = LocalFileSummaryCacheBackend(tmp_path)
    a.put_summary("key-a", "value-a")
    b.put_summary("key-b", "value-b")

    a.save()
    b.save()  # b loaded before a saved, yet must not drop key-a

    assert _summaries_on_disk(tmp_path) == {"key-a": "value-a", "key-b": "value-b"}

    fresh = LocalFileSummaryCacheBackend(tmp_path)
    assert fresh.get_summary("key-a") == "value-a"
    assert fresh.get_summary("key-b") == "value-b"


def test_last_writer_wins_per_key(tmp_path: Path) -> None:
    a = LocalFileSummaryCacheBackend(tmp_path)
    a.put_summary("shared", "from-a")
    a.save()

    b = LocalFileSummaryCacheBackend(tmp_path)  # loads {shared: from-a}
    b.put_summary("shared", "from-b")
    b.save()

    assert _summaries_on_disk(tmp_path)["shared"] == "from-b"


def test_merge_covers_fragments_and_slices(tmp_path: Path) -> None:
    a = LocalFileSummaryCacheBackend(tmp_path)
    b = LocalFileSummaryCacheBackend(tmp_path)
    a.put_fragment("frag-a", "ref-a")
    a.put_slice("slice-a", "data-a")
    b.put_fragment("frag-b", "ref-b")
    b.put_slice("slice-b", "data-b")

    a.save()
    b.save()

    data = json.loads((tmp_path / CACHE_FILE).read_text(encoding="utf-8"))
    assert data["fragments"] == {"frag-a": "ref-a", "frag-b": "ref-b"}
    assert data["slices"] == {"slice-a": "data-a", "slice-b": "data-b"}


def test_corrupt_cache_file_does_not_lose_writes(tmp_path: Path) -> None:
    # A half-written or corrupt file must not discard this process's entries.
    (tmp_path / CACHE_FILE).write_text("{ not valid json", encoding="utf-8")

    backend = LocalFileSummaryCacheBackend(tmp_path)
    backend.put_summary("k", "v")
    backend.save()

    assert _summaries_on_disk(tmp_path) == {"k": "v"}


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl file locking (POSIX)")
def test_concurrent_saves_under_thread_contention(tmp_path: Path) -> None:
    # Each thread is a separate backend racing to save at the same instant; the
    # file lock must serialize the read-merge-write so no key is lost.
    n = 16
    ready = threading.Barrier(n)

    def worker(i: int) -> None:
        backend = LocalFileSummaryCacheBackend(tmp_path)
        backend.put_summary(f"k{i}", f"v{i}")
        ready.wait()  # maximize overlap of the save window
        backend.save()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert set(_summaries_on_disk(tmp_path)) == {f"k{i}" for i in range(n)}
