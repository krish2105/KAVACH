"""`kavach-waketrain` — train v4 on the synthetic corpus plus real recordings.

One command, because the interesting step has to happen in the middle of
someone else's pipeline::

    clone      reuse v3's generated clips instead of re-running VoxCPM
    inject     the user's recordings, as clean round-0 clips
    augment    the library's own: RIR, noise, alignment, then features
    train      the library's own
    export     ONNX, which `find_wake_model()` picks up as the newest

The clone is what makes this bearable. Generating 3000 VoxCPM clips takes
hours, and v3's are already on disk and are exactly what v4 wants — the whole
difference between the two models is the real audio added to them. Copied with
APFS clones where available, so it costs neither the time nor the 1.3GB.

Nothing here reimplements generation, augmentation, training or export. Every
one of those is the library's, called in its own order, with one directory
modified in between. That is the entire trick.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .wakeinject import inject, next_clip_number, plan_injection
from .wakerecord import REAL_DIR

RULE = "─" * 62

BRAIN = Path(__file__).resolve().parents[2]
CONFIG = BRAIN / "wakeword" / "kavach-v4.yaml"
#: The corpus to clone rather than regenerate.
DONOR = BRAIN / "wakeword" / "output" / "kavach_v3"
TARGET = BRAIN / "wakeword" / "output" / "kavach_v4"

#: Copies of each recording. 42 takes against 3000 synthetic clips is 1% and
#: would not move the model; at 25 it is a quarter of the positive set. Each
#: copy draws its own impulse response, noise and jitter, so they are siblings
#: rather than repeated rows — but they are still 42 utterances, and that is
#: the honest limit of this run.
DEFAULT_COPIES = 25

_SPLITS = ("positive_train", "positive_test", "negative_train", "negative_test",
           "background_train", "background_test")


def _clone(src: Path, dst: Path) -> None:
    """Copy the donor corpus, preferring an APFS clone.

    `cp -c` makes this near-instant and free on disk. It falls back to a real
    copy elsewhere rather than failing, because being slow is not a reason to
    stop.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["cp", "-c", "-R", str(src), str(dst)], check=True,
                       capture_output=True)
    except Exception:
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _strip_augmented(corpus: Path) -> int:
    """Remove `_rN` files so augmentation regenerates them.

    They are derived from round 0 and would otherwise be stale: the whole point
    of this run is that round 0 now contains clips they were never made from.
    Leaving them would train partly on v3's corpus while claiming to be v4.
    """
    removed = 0
    for split in _SPLITS:
        for path in (corpus / split).glob("clip_*_r*.wav"):
            path.unlink()
            removed += 1
    return removed


#: Training needs a dependency group the runtime deliberately does not carry —
#: torch alone dwarfs the rest of this project. `sys.executable` is the plain
#: venv and does not have it, so every step goes through uv with the group.
_STEP_CMD = ["uv", "run", "--group", "wakeword-training", "python",
             "-m", "livekit.wakeword"]

#: Third-party imports `livekit-wakeword` never declares. An AST scan found
#: seven; these are the ones this pipeline reaches. Checked up front because
#: the failures are spread across the run — torchaudio dies at augment, and
#: onnxscript dies at export, which is AFTER the training hour is spent.
_REQUIRED = ("torchaudio", "audiomentations", "onnx", "onnxscript", "nltk")


def _preflight() -> list[str]:
    """Which required modules are missing from the step environment."""
    missing: list[str] = []
    for name in _REQUIRED:
        result = subprocess.run(
            [*_STEP_CMD[:-2], "-c", f"import {name}"],
            cwd=BRAIN, capture_output=True,
        )
        if result.returncode != 0:
            missing.append(name)
    return missing


def _run_step(name: str, *args: str) -> int:
    print(f"\n{RULE}\n  {name}\n{RULE}", flush=True)
    started = time.time()
    result = subprocess.run([*_STEP_CMD, *args], cwd=BRAIN)
    took = time.time() - started
    if result.returncode != 0:
        print(f"\n  ✗ {name} failed after {took / 60:.1f} min", file=sys.stderr)
    else:
        print(f"\n  ✓ {name} in {took / 60:.1f} min")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the v4 wake word.")
    parser.add_argument("--copies", type=int, default=DEFAULT_COPIES,
                        help=f"copies of each recording (default {DEFAULT_COPIES})")
    parser.add_argument("--plan", action="store_true",
                        help="show what would happen and stop")
    parser.add_argument("--skip-clone", action="store_true",
                        help="the v4 corpus is already prepared")
    parser.add_argument("--resume", action="store_true",
                        help="corpus already cloned AND injected — go straight "
                             "to augment. Injecting twice would duplicate every "
                             "recording in the corpus, silently.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    takes = REAL_DIR / "positive"

    print(RULE)
    print("  KAVACH wake word v4 — synthetic corpus + real recordings")
    print(RULE)

    if not DONOR.exists():
        print(f"  ✗ no corpus to clone at {DONOR}", file=sys.stderr)
        print("    generate one first: uv run livekit-wakeword generate "
              "wakeword/kavach-v4.yaml", file=sys.stderr)
        return 1

    # The plan first, always. The share is the number that decides whether the
    # run is worth the hours, and it must be visible before they are spent.
    if not (args.skip_clone or args.resume) and TARGET.exists():
        print(f"  ✗ {TARGET} already exists.", file=sys.stderr)
        print("    Remove it to start clean, or pass --skip-clone to reuse it.",
              file=sys.stderr)
        return 1

    probe = TARGET if (args.skip_clone or args.resume) else DONOR
    try:
        preview = plan_injection(takes, into=probe / "positive_train",
                                 copies=args.copies)
    except (ValueError, FileNotFoundError) as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return 1

    print(f"  recordings   {len(preview.sources)}")
    if args.resume:
        # NOT preview.describe(). On a resume the corpus already contains the
        # injected clips, so describing a fresh injection reports the share a
        # SECOND pass would produce — a plausible, wrong number, which is the
        # exact failure mode this project keeps finding.
        total = next_clip_number(TARGET / "positive_train")
        donor = next_clip_number(DONOR / "positive_train")
        real = max(0, total - donor)
        print(f"  corpus       {total} clips, {real} of them real "
              f"→ real audio is {real / total * 100:.0f}%")
    else:
        print(f"  copies each  {args.copies}")
        print(f"  {preview.describe()}")
    print(f"  config       {CONFIG.name}")
    print(RULE)

    if preview.share < 0.10:
        print(f"  ⚠  real audio would be {preview.share * 100:.0f}% of the positive")
        print("     set. Below about 10% a retrain is unlikely to move anything.")

    # Before the clone and the injection, not after. The first run of this
    # died on `torchaudio` at the augment step, having already copied 1.3GB
    # and written 1050 clips — and the same class of failure waits at export,
    # where `onnxscript` is missing and the training hour is already spent.
    missing = _preflight()
    if missing:
        print(f"\n  ✗ the training environment is missing: {', '.join(missing)}")
        print("    These are livekit-wakeword's undeclared dependencies.")
        print("    Fix: uv sync --group wakeword-training")
        return 1
    print("  deps         ok (torchaudio, audiomentations, onnx, onnxscript, nltk)")

    if args.plan:
        return 0

    if not (args.skip_clone or args.resume):
        print(f"\n  cloning {DONOR.name} → {TARGET.name} "
              f"(reusing generated clips rather than re-running VoxCPM)")
        started = time.time()
        _clone(DONOR, TARGET)
        removed = _strip_augmented(TARGET)
        print(f"  cloned in {time.time() - started:.1f}s, "
              f"removed {removed} stale augmented clips")

    into = TARGET / "positive_train"
    if args.resume:
        # Deliberately not re-injected. A second pass would add another copy of
        # every recording under fresh indices — the corpus would look larger and
        # be more lopsided, and nothing would say so.
        print(f"  resuming with {next_clip_number(into)} clips already in place")
    else:
        before = next_clip_number(into)
        plan = plan_injection(takes, into=into, copies=args.copies)
        written = inject(plan)
        print(f"  injected {len(written)} real clips "
              f"({before} → {next_clip_number(into)})")

    config = str(CONFIG.relative_to(BRAIN))
    for name, step in (("augment + extract features", "augment"),
                       ("train", "train"),
                       ("export to ONNX", "export")):
        if _run_step(name, step, config) != 0:
            return 1

    print(f"\n{RULE}")
    print("  v4 trained. Now measure it — against v3, on the same recordings:")
    print("      uv run python wakeword/realmic_eval.py")
    print("      uv run kavach-waketune")
    print("  Training metrics are not evidence here: v2 reported recall 0.835")
    print("  and FPPH 0.00 while scoring 0.019 on real audio.")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
