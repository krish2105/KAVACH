"""Memory control.

    uv run kavach-memory status
    uv run kavach-memory index ~/Documents/notes
    uv run kavach-memory search "what did I decide about the router"
    uv run kavach-memory sources
    uv run kavach-memory forget files

Indexing is deliberately a command you type, never something KAVACH decides to
do. `sources` shows exactly what has been read and `forget` removes it — memory
you cannot audit or delete is surveillance.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .store import DEFAULT_DB, EmbeddingUnavailable, MemoryStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kavach-memory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="how much is stored, and where")
    sub.add_parser("sources", help="every file that has been indexed")

    index = sub.add_parser("index", help="index a folder you name")
    index.add_argument("folder")
    index.add_argument("--no-recursive", action="store_true")

    search = sub.add_parser("search", help="semantic search over memory")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--collection", default=None,
                        choices=["turns", "files"])

    forget = sub.add_parser("forget", help="delete a collection, or all of it")
    forget.add_argument("collection", nargs="?", default=None,
                        choices=["turns", "files"])

    args = parser.parse_args(argv)

    try:
        store = MemoryStore()
    except Exception as exc:
        print(f"✗ could not open memory at {DEFAULT_DB}: {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "status":
            print(f"  database : {store.path}")
            print(f"  turns    : {store.count('turns')}")
            print(f"  files    : {store.count('files')} chunks "
                  f"from {len(store.sources('files'))} file(s)")
            return 0

        if args.command == "sources":
            sources = store.sources("files")
            if not sources:
                print("  nothing indexed. `kavach-memory index <folder>` to add some.")
                return 0
            print(f"  {len(sources)} indexed file(s):")
            for source in sources:
                print(f"    {source}")
            return 0

        if args.command == "index":
            print(f"  indexing {args.folder} …")
            result = store.index_folder(args.folder, recursive=not args.no_recursive)
            print(f"  ✓ {result['indexed']} file(s) indexed, "
                  f"{result['skipped']} skipped")
            print(f"    review with `kavach-memory sources`, "
                  f"undo with `kavach-memory forget files`")
            return 0

        if args.command == "search":
            hits = store.search(args.query, limit=args.limit,
                                collection=args.collection)
            if not hits:
                print("  no matches")
                return 0
            for hit in hits:
                when = datetime.fromtimestamp(hit.created_at).strftime("%Y-%m-%d %H:%M")
                where = hit.source or hit.collection
                print(f"\n  [{hit.score:.3f}] {where}  ({when})")
                print(f"    {hit.text[:220]}")
            print()
            return 0

        if args.command == "forget":
            removed = store.forget(args.collection)
            target = args.collection or "everything"
            print(f"  ✓ removed {removed} record(s) from {target}")
            return 0

    except EmbeddingUnavailable as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    except (NotADirectoryError, FileNotFoundError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
