"""Memory and file-search tests.

These hit the real embedding model through Ollama, so they skip cleanly on a
machine where it isn't running rather than failing for the wrong reason.

The property that matters most here isn't retrieval quality — it's that
indexing is EXPLICIT and reversible. An assistant that quietly built a
searchable copy of your documents would be a different product from the one
§7 describes.
"""

from pathlib import Path

import pytest

from kavach.memory.store import EmbeddingUnavailable, MemoryStore, _chunk, embed


def _ollama_ready() -> bool:
    try:
        embed("probe")
        return True
    except EmbeddingUnavailable:
        return False


needs_ollama = pytest.mark.skipif(
    not _ollama_ready(), reason="ollama + nomic-embed-text not available"
)


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(path=tmp_path / "memory.db")
    yield s
    s.close()


# ——— chunking is pure logic and always testable ———

def test_short_text_is_one_chunk():
    assert _chunk("hello world") == ["hello world"]


def test_long_text_is_split_with_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12))
    chunks = _chunk(text)
    assert len(chunks) > 1
    assert all(chunks)
    # Overlap means the total is longer than the input, not shorter.
    assert sum(len(c) for c in chunks) >= len(text) * 0.9


def test_chunking_prefers_paragraph_boundaries():
    text = "A" * 800 + "\n\n" + "B" * 800
    chunks = _chunk(text, size=1000, overlap=50)
    assert chunks[0].endswith("A")


# ——— storage ———

@needs_ollama
def test_remembering_and_finding_a_turn(store):
    store.remember("The user's dentist appointment is on Thursday at 3pm",
                   collection="turns")
    store.remember("KAVACH was told the wifi password is hunter2",
                   collection="turns")

    hits = store.search("when is my dentist appointment", limit=2)
    assert hits
    assert "dentist" in hits[0].text.lower()


@needs_ollama
def test_trivially_short_text_is_not_stored(store):
    assert store.remember("ok") is None
    assert store.count() == 0


@needs_ollama
def test_collections_are_searchable_separately(store):
    store.remember("a note about kubernetes networking", collection="files")
    store.remember("the user asked about kubernetes yesterday", collection="turns")

    turns = store.search("kubernetes", collection="turns")
    assert turns and all(m.collection == "turns" for m in turns)


# ——— indexing is explicit and reversible ———

@needs_ollama
def test_indexing_only_touches_the_named_folder(store, tmp_path):
    wanted = tmp_path / "notes"
    wanted.mkdir()
    (wanted / "a.md").write_text("Project KAVACH uses a device-scoped allowlist.")

    elsewhere = tmp_path / "private"
    elsewhere.mkdir()
    (elsewhere / "secret.md").write_text("This folder was never named by the user.")

    store.index_folder(wanted)

    # Compare resolved paths, not substrings: on macOS tmp_path itself lives
    # under /private/var/folders/…, so a naive `"private" not in sources`
    # matches the temp directory and fails for the wrong reason.
    sources = [Path(s).resolve() for s in store.sources("files")]
    assert sources, "nothing was indexed"
    assert all(s.is_relative_to(wanted.resolve()) for s in sources), (
        f"indexed outside the named folder: {sources}"
    )
    assert not any(s.is_relative_to(elsewhere.resolve()) for s in sources), (
        "indexed a folder the user never named"
    )


@needs_ollama
def test_hidden_folders_are_skipped(store, tmp_path):
    """.git and .venv are noise at best and secrets at worst."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config.md").write_text("token = abcdef123456")
    (root / "readme.md").write_text("A perfectly ordinary readme file here.")

    store.index_folder(root)

    assert not any(".git" in s for s in store.sources("files"))


@needs_ollama
def test_non_text_files_are_ignored(store, tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "notes.md").write_text("Something worth indexing goes here.")
    (root / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    (root / "app.py").write_text("print('code is not a note')")

    result = store.index_folder(root)
    assert result["indexed"] == 1


@needs_ollama
def test_what_was_indexed_can_be_listed_and_deleted(store, tmp_path):
    """Memory you cannot audit or delete is surveillance."""
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "one.md").write_text("The first document about local-first design.")
    store.index_folder(folder)

    assert store.sources("files"), "cannot see what was indexed"
    assert store.count("files") > 0

    removed = store.forget("files")
    assert removed > 0
    assert store.count("files") == 0
    assert store.sources("files") == []


@needs_ollama
def test_forgetting_one_collection_leaves_the_other(store):
    store.remember("a turn worth keeping around", collection="turns")
    store.remember("an indexed file chunk here", collection="files")

    store.forget("files")

    assert store.count("turns") == 1
    assert store.count("files") == 0


def test_indexing_a_missing_folder_raises_clearly(store, tmp_path):
    with pytest.raises(NotADirectoryError):
        store.index_folder(tmp_path / "does-not-exist")
