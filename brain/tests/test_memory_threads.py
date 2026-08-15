"""The store is used from three threads, and used to allow only one.

`MemoryStore` opened its sqlite connection at construction — on the main
thread — and `remember()` is called from the voice loop's thread and from the
API's `asyncio.to_thread` worker. Every one of those raised::

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

**Nobody could have hit this before.** The module was built, tested and
constructed by nothing, so its only caller was a single-threaded test. Wiring
it up was what surfaced the bug — and it surfaced as silence, because the
caller swallowed the exception at debug level.

Two fixes, both needed: the connection allows cross-thread use, and a lock
serialises access. `check_same_thread=False` alone would trade a loud error
for a corrupt index.
"""

import threading

from kavach.memory.store import MemoryStore


def test_a_write_from_another_thread_succeeds(tmp_path):
    store = MemoryStore(path=tmp_path / "m.db")
    failures = []

    def write():
        try:
            store.remember("a turn from a worker thread", collection="turns")
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=write)
    thread.start()
    thread.join(timeout=30)

    assert not failures, failures[0]


def test_concurrent_writes_do_not_corrupt_the_index(tmp_path):
    """`check_same_thread=False` without a lock trades a loud error for a
    corrupt index, which is strictly worse."""
    store = MemoryStore(path=tmp_path / "m.db")
    failures = []

    def write(n):
        try:
            store.remember(f"turn number {n} with enough text to store",
                           collection="turns")
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not failures, failures[0]
    assert store.count() == 4


def test_a_read_from_another_thread_succeeds(tmp_path):
    store = MemoryStore(path=tmp_path / "m.db")
    store.remember("something worth finding later", collection="turns")
    results = []

    def read():
        results.append(store.search("something worth finding"))

    thread = threading.Thread(target=read)
    thread.start()
    thread.join(timeout=30)

    assert results and len(results[0]) >= 1
