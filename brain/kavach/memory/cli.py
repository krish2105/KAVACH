"""Memory control.

    uv run kavach-memory status
    uv run kavach-memory index ~/Documents/notes
    uv run kavach-memory search "what did I decide about the router"
    uv run kavach-memory sources
    uv run kavach-memory forget files

Indexing is deliberately a command you type, never something KAVACH decides to
do. `sources` shows exactly what has been read and `forget` removes it — memory
you cannot audit or delete is surveillance.

**Every collection name comes from `sources.SOURCES`.** They were typed out
here as `choices=["turns", "files"]` while `SOURCES` held four, so `forget
actions` died in argparse: the collection recording what KAVACH *did* was the
only one that could not be deleted. `test_memory_sources.py` asserted every
source is purgeable and passed the whole time, because it reads the dict and
this file did not. Ninth instance of one-fact-in-two-places here.

**Indexing reads through `FileTools`**, which checks the kill switch and writes
`file.read` to the §7 log. It used to call `Path.read_text()` directly.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from ..hands.files import FileTools
from ..killswitch.core import KillSwitch, KillSwitchDisarmed
from ..killswitch.log import ActionLog
from .sources import SOURCES, index_actions, index_folder, index_messages
from .store import DEFAULT_DB, EmbeddingUnavailable, MemoryStore

#: Collections, in the order a person would want to read them.
COLLECTIONS = sorted(SOURCES)


def _open_store() -> MemoryStore:
    """Seam, so tests can point the CLI at a throwaway database."""
    return MemoryStore()


def _kill_switch() -> KillSwitch:
    """Seam, for the same reason. The real one reads `~/.kavach`."""
    return KillSwitch()


def _action_log() -> ActionLog:
    """Seam. What KAVACH did, which `index-actions` turns into memory."""
    return ActionLog()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kavach-memory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="how much is stored, and where")
    sub.add_parser("sources", help="every file that has been indexed")

    index = sub.add_parser("index", help="index a folder you name")
    index.add_argument("folder")
    index.add_argument("--no-recursive", action="store_true")

    # One subcommand per source, rather than `index <thing>` guessing what
    # kind of thing it was given. `index-actions` was the missing half of
    # Phase 10: KAVACH recorded every tool call it made and indexed none of
    # them, so it could recall what you said and never what it did.
    sub.add_parser("index-actions",
                   help="index what KAVACH did, from the action log")

    messages = sub.add_parser(
        "index-messages",
        help="index recent iMessages (needs Full Disk Access)")
    messages.add_argument("--limit", type=int, default=500)

    search = sub.add_parser("search", help="semantic search over memory")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--collection", default=None, choices=COLLECTIONS)

    forget = sub.add_parser("forget", help="delete a collection, or all of it")
    forget.add_argument("collection", nargs="?", default=None,
                        choices=COLLECTIONS)

    args = parser.parse_args(argv)

    # The gate comes first — before the store, before the disk, before
    # anything. `MacActions` and `FileTools` both order it this way, and the
    # reason is that every step after it is a step taken while latched.
    tools = None
    if args.command in ("index", "index-messages"):
        try:
            switch = _kill_switch()
            switch.guard(f"memory.{args.command}")
            tools = FileTools(switch)
        except KillSwitchDisarmed as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 3

    try:
        store = _open_store()
    except Exception as exc:
        print(f"✗ could not open memory at {DEFAULT_DB}: {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "status":
            print(f"  database : {store.path}")
            for collection in COLLECTIONS:
                count = store.count(collection)
                line = f"  {collection:<9}: {count}"
                if collection == "files":
                    line += (f" chunks from "
                             f"{len(store.sources('files'))} file(s)")
                print(line)
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
            result = index_folder(store, tools, args.folder,
                                  recursive=not args.no_recursive)
            print(f"  ✓ {result['indexed']} file(s) indexed, "
                  f"{result['skipped']} skipped")
            print(f"    review with `kavach-memory sources`, "
                  f"undo with `kavach-memory forget files`")
            return 0

        if args.command == "index-actions":
            written = index_actions(store, _action_log())
            print(f"  ✓ {written} action(s) indexed")
            print(f"    undo with `kavach-memory forget actions`")
            return 0

        if args.command == "index-messages":
            print(f"  reading up to {args.limit} recent message(s) …")
            written = index_messages(store, tools, limit=args.limit)
            print(f"  ✓ {written} message(s) indexed")
            print(f"    undo with `kavach-memory forget messages`")
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

    except KillSwitchDisarmed as exc:
        # Latched means nothing runs, reads included. Reported as its own
        # failure rather than as "0 files indexed", which would read like an
        # empty folder and send the user looking for the wrong problem.
        print(f"✗ kill switch is latched — nothing was read. {exc}",
              file=sys.stderr)
        return 3
    except EmbeddingUnavailable as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    except (NotADirectoryError, FileNotFoundError, PermissionError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
