"""Local memory and file search.

One SQLite file, no server, no daemon: `sqlite-vec` keeps the vectors in the
same database as the text, so the whole index is a file you can copy, inspect
or delete. Embeddings come from `nomic-embed-text` through the Ollama that is
already running for the router — nothing new to install and nothing leaves the
machine.

Two collections:

* **turns** — what was said, so KAVACH has continuity across a session
* **files** — an explicitly indexed folder

**Indexing is never implicit.** KAVACH does not read your disk because it felt
like it: you name a folder, the index is listable, and it is deletable in one
command. An assistant that quietly built a searchable copy of your documents
would be a very different product from the one §7 describes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("kavach.memory.store")

DEFAULT_DB = Path.home() / ".kavach" / "memory.db"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
OLLAMA_HOST = "http://127.0.0.1:11434"

#: nomic-embed-text's output width. Asserted on first write so a model swap
#: fails loudly instead of silently corrupting the index.
EMBED_DIM = 768

#: Files worth indexing. Deliberately narrow — this is a notes-and-docs index,
#: not a filesystem crawler.
TEXT_SUFFIXES = frozenset({".md", ".txt", ".markdown", ".rst", ".org"})
MAX_FILE_BYTES = 512_000


@dataclass
class Memory:
    id: int
    collection: str
    text: str
    source: str
    created_at: float
    score: float = 0.0


class EmbeddingUnavailable(RuntimeError):
    """Ollama isn't running, or the embedding model isn't pulled."""


def embed(text: str, model: str = DEFAULT_EMBED_MODEL,
          host: str = OLLAMA_HOST, timeout: float = 30.0) -> list[float]:
    request = urllib.request.Request(
        f"{host}/api/embeddings",
        data=json.dumps({"model": model, "prompt": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.URLError as exc:
        raise EmbeddingUnavailable(
            f"cannot reach Ollama at {host} ({exc}). Start it with `ollama serve` "
            f"and pull the model with `ollama pull {model}`."
        ) from exc

    vector = payload.get("embedding")
    if not vector:
        raise EmbeddingUnavailable(f"{model} returned no embedding: {payload}")
    return vector


class MemoryStore:
    def __init__(self, path: Path | str = DEFAULT_DB,
                 model: str = DEFAULT_EMBED_MODEL, host: str = OLLAMA_HOST):
        self.path = Path(path)
        self.model = model
        self.host = host
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = self._connect()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        import sqlite_vec

        db = sqlite3.connect(str(self.path))
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        return db

    def _migrate(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id          INTEGER PRIMARY KEY,
                collection  TEXT NOT NULL,
                text        TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT '',
                created_at  REAL NOT NULL
            )
            """
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection ON memories(collection)"
        )
        self._db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vectors "
            f"USING vec0(embedding float[{EMBED_DIM}])"
        )
        self._db.commit()

    # ——— writing ———

    def remember(self, text: str, collection: str = "turns",
                 source: str = "") -> int | None:
        """Store one memory. Returns its id, or None if it was not worth storing."""
        text = (text or "").strip()
        if len(text) < 8:
            return None  # not worth a row or an embedding

        import sqlite_vec

        vector = embed(text, self.model, self.host)
        if len(vector) != EMBED_DIM:
            raise ValueError(
                f"{self.model} returned {len(vector)} dims, index expects "
                f"{EMBED_DIM}. Changing the embedding model means rebuilding "
                f"the index — delete {self.path} and re-index."
            )

        cursor = self._db.execute(
            "INSERT INTO memories(collection, text, source, created_at) "
            "VALUES (?, ?, ?, ?)",
            (collection, text, source, time.time()),
        )
        rowid = cursor.lastrowid
        self._db.execute(
            "INSERT INTO vectors(rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32(vector)),
        )
        self._db.commit()
        return rowid

    # ——— reading ———

    def search(self, query: str, limit: int = 5,
               collection: str | None = None) -> list[Memory]:
        import sqlite_vec

        vector = embed(query, self.model, self.host)
        # Over-fetch, then filter by collection in Python: vec0 KNN cannot be
        # combined with a WHERE clause on the joined table.
        rows = self._db.execute(
            """
            SELECT m.id, m.collection, m.text, m.source, m.created_at, v.distance
            FROM vectors v
            JOIN memories m ON m.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (sqlite_vec.serialize_float32(vector), limit * 4),
        ).fetchall()

        results = [
            Memory(id=r[0], collection=r[1], text=r[2], source=r[3],
                   created_at=r[4], score=1.0 / (1.0 + r[5]))
            for r in rows
            if collection is None or r[1] == collection
        ]
        return results[:limit]

    def count(self, collection: str | None = None) -> int:
        if collection is None:
            return self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return self._db.execute(
            "SELECT COUNT(*) FROM memories WHERE collection = ?", (collection,)
        ).fetchone()[0]

    def sources(self, collection: str = "files") -> list[str]:
        """What has been indexed — so it can be audited and revoked."""
        return [
            r[0] for r in self._db.execute(
                "SELECT DISTINCT source FROM memories WHERE collection = ? "
                "AND source != '' ORDER BY source",
                (collection,),
            ).fetchall()
        ]

    # ——— forgetting ———

    def forget(self, collection: str | None = None) -> int:
        """Delete a collection, or everything. Memory you cannot delete is
        surveillance, so this is a first-class operation."""
        if collection is None:
            removed = self.count()
            self._db.execute("DELETE FROM vectors")
            self._db.execute("DELETE FROM memories")
        else:
            ids = [r[0] for r in self._db.execute(
                "SELECT id FROM memories WHERE collection = ?", (collection,)
            ).fetchall()]
            removed = len(ids)
            for rowid in ids:
                self._db.execute("DELETE FROM vectors WHERE rowid = ?", (rowid,))
            self._db.execute("DELETE FROM memories WHERE collection = ?", (collection,))
        self._db.commit()
        return removed

    # ——— explicit indexing ———

    def index_folder(self, folder: Path | str, recursive: bool = True) -> dict:
        """Index text files under a folder the user named.

        Never called implicitly. The folder is always something the user typed.
        """
        folder = Path(folder).expanduser().resolve()
        if not folder.is_dir():
            raise NotADirectoryError(f"{folder} is not a directory")

        pattern = "**/*" if recursive else "*"
        indexed, skipped = 0, 0

        for candidate in sorted(folder.glob(pattern)):
            if not candidate.is_file() or candidate.suffix.lower() not in TEXT_SUFFIXES:
                continue
            # Skip anything hidden or inside a dot-directory — .git, .venv and
            # friends are noise at best and secrets at worst.
            if any(part.startswith(".") for part in candidate.parts):
                skipped += 1
                continue
            if candidate.stat().st_size > MAX_FILE_BYTES:
                skipped += 1
                continue

            try:
                text = candidate.read_text(errors="ignore").strip()
            except Exception:
                skipped += 1
                continue

            if not text:
                skipped += 1
                continue

            for chunk in _chunk(text):
                self.remember(chunk, collection="files", source=str(candidate))
            indexed += 1

        log.info("indexed %d file(s) from %s (%d skipped)", indexed, folder, skipped)
        return {"folder": str(folder), "indexed": indexed, "skipped": skipped}

    def close(self) -> None:
        self._db.close()


def _chunk(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Split on paragraph boundaries where possible.

    Overlap so a sentence spanning a boundary is still findable from either
    side; cheap insurance against the answer being exactly where we cut.
    """
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            paragraph = text.rfind("\n\n", start, end)
            if paragraph > start + size // 2:
                end = paragraph
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [c for c in chunks if c]
