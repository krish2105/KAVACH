"""`kavach-stt` — see, fetch and switch speech models (Phase 21).

Four verbs, and `use stock` is the one that matters: the phase asked for a
toggle rather than a replacement, so getting back to the model that already
works has to be one short command that cannot fail.
"""

from __future__ import annotations

import argparse
import sys

from . import stt_models


def _row(name: str, model: stt_models.SpeechModel, active: str) -> str:
    mark = "→" if name == active else " "
    state = "installed" if stt_models.is_installed(name) else "not downloaded"
    if model.repo_id is None:
        state = "built in"
    size = f"{model.size_gb:.2f} GB" if model.size_bytes >= 1e9 \
        else f"{model.size_bytes / 1e6:.0f} MB"
    return (f"  {mark} {name:<8} {size:>8}  {model.licence:<11} {state:<16}"
            f"{model.note}")


def cmd_list(_args) -> int:
    active = stt_models.selected_name()
    print("\n  SPEECH MODELS      → = active\n")
    print(f"    {'name':<8} {'size':>8}  {'licence':<11} {'state':<16}note")
    print(f"    {'-' * 8} {'-' * 8}  {'-' * 11} {'-' * 16}{'-' * 40}")
    for name in ("stock", "apex", "swift", "prime", "trelis"):
        print(_row(name, stt_models.get(name), active))
    print("\n  Every non-stock model is a Hugging Face checkpoint converted to")
    print("  GGML locally, so whisper.cpp stays the only backend.")
    print("  Sizes are the download; the converted file is smaller.\n")
    return 0


def cmd_status(_args) -> int:
    print(f"  active: {stt_models.describe_active()}")
    print(f"  resolves to: {stt_models.resolve()}")
    return 0


def cmd_use(args) -> int:
    try:
        model = stt_models.select(args.name)
    except KeyError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    print(f"  speech model → {args.name}")
    if model.repo_id is not None and not stt_models.is_installed(args.name):
        print(f"  ⚠ not downloaded yet — KAVACH will keep using stock until:")
        print(f"      uv run kavach-stt pull {args.name}")
    print("  restart KAVACH for it to take effect.")
    return 0


def cmd_pull(args) -> int:
    from .convert_ggml import ConversionError, convert

    try:
        model = stt_models.get(args.name)
    except KeyError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    if model.repo_id is None:
        print("  stock is fetched by whisper.cpp itself — nothing to pull.")
        return 0

    if stt_models.is_installed(args.name) and not args.force:
        print(f"  {args.name} is already installed at {model.local_path()}")
        print("  --force to convert it again.")
        return 0

    print(f"  {args.name}: {model.repo_id}")
    print(f"  {model.size_gb:.2f} GB download, {model.licence}, "
          f"based on {model.base_model}")
    print("  downloading and converting — minutes, not seconds.\n")

    try:
        path = convert(model.repo_id, model.local_path())
    except ConversionError as exc:
        # Reported, never softened into "we'll just use stock". A conversion
        # that failed silently would look like a model that simply mishears.
        print(f"\n✗ conversion failed\n{exc}", file=sys.stderr)
        return 1

    size = path.stat().st_size / 1e9
    print(f"\n  ✓ {args.name} ready — {size:.2f} GB at {path}")
    print(f"  switch to it with:  uv run kavach-stt use {args.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Choose which model KAVACH listens with.")
    subs = parser.add_subparsers(dest="cmd")

    subs.add_parser("list", help="every known model, and what is installed")
    subs.add_parser("status", help="what is active right now")

    use = subs.add_parser("use", help="switch model (use `stock` to revert)")
    use.add_argument("name")

    pull = subs.add_parser("pull", help="download and convert a model")
    pull.add_argument("name")
    pull.add_argument("--force", action="store_true",
                      help="convert again even if it is already installed")

    args = parser.parse_args(argv)
    handlers = {"list": cmd_list, "status": cmd_status,
                "use": cmd_use, "pull": cmd_pull}
    return handlers.get(args.cmd, cmd_list)(args)


if __name__ == "__main__":
    raise SystemExit(main())
