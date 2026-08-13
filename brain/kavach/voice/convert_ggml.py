"""Turn a Hugging Face Whisper checkpoint into GGML for whisper.cpp (Phase 21).

None of the Hinglish Whisper models ship GGML — they are all transformers
checkpoints — and KAVACH runs whisper.cpp. Rather than add a second STT
backend, the weights are converted once and the existing backend loads the
result: `pywhispercpp.Model` takes a path as readily as a name, so nothing in
the voice loop changes.

**The conversion itself is whisper.cpp's own `convert-h5-to-ggml.py`**, not a
reimplementation. Writing a GGML serialiser from scratch to save one download
would be exactly the kind of confident-but-wrong code §A exists to prevent.

It is fetched from a **pinned release tag** and its SHA-256 is **checked before
it runs**. Two reasons that matters: a script pulled from `master` can change
under you between runs, and this one executes locally with your Python. The
recorded hash was identical across v1.7.6, v1.8.0 and v1.8.2, so pinning costs
nothing in currency.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("kavach.voice.convert")

#: Pinned, and hash-checked. Verified 2026-08-13: byte-identical across
#: v1.7.6 / v1.8.0 / v1.8.2, 7891 bytes.
CONVERTER_TAG = "v1.8.2"
CONVERTER_URL = (
    "https://raw.githubusercontent.com/ggml-org/whisper.cpp/"
    f"{CONVERTER_TAG}/models/convert-h5-to-ggml.py"
)
CONVERTER_SHA256 = (
    "9cc282dfcd9a24da03ffa1b4123b5508ee935e19e369c33520b1a73f89440094"
)


class ConversionError(RuntimeError):
    """Raised with something actionable. Never swallowed into a fallback —
    a silent fall back to stock would look like the model was converted and
    simply not very good."""


def _fetch_converter(into: Path) -> Path:
    """Download the pinned converter and verify it before it is ever run."""
    import urllib.request

    target = into / "convert-h5-to-ggml.py"
    log.info("fetching whisper.cpp converter %s", CONVERTER_TAG)
    with urllib.request.urlopen(CONVERTER_URL, timeout=60) as response:
        body = response.read()

    digest = hashlib.sha256(body).hexdigest()
    if digest != CONVERTER_SHA256:
        raise ConversionError(
            "the whisper.cpp converter does not match its recorded hash.\n"
            f"  expected {CONVERTER_SHA256}\n  got      {digest}\n"
            "Refusing to run it. This is either a changed upstream file or "
            "something tampering with the download."
        )
    target.write_bytes(body)
    return target


def _whisper_assets_root() -> Path:
    """The directory the converter expects as 'path-to-whisper-repo'.

    It only reads `whisper/assets/mel_filters.npz` from it, and the
    `openai-whisper` package installs exactly that file — so the installed
    package stands in for a git clone and nothing is fetched at run time.
    """
    try:
        import whisper  # type: ignore
    except ImportError as exc:
        raise ConversionError(
            "conversion needs the mel filter bank from `openai-whisper`.\n"
            "  uv sync --group stt-convert"
        ) from exc

    root = Path(whisper.__file__).resolve().parent.parent
    if not (root / "whisper" / "assets" / "mel_filters.npz").exists():
        raise ConversionError(
            f"mel_filters.npz not found under {root} — the openai-whisper "
            "install looks incomplete."
        )
    return root


def download_checkpoint(repo_id: str, into: Path) -> Path:
    """Fetch the Hugging Face checkpoint. Gigabytes; resumable by the hub."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ConversionError(
            "downloading needs huggingface_hub.\n  uv sync --group stt-convert"
        ) from exc

    log.info("downloading %s (this is the slow part)", repo_id)
    path = snapshot_download(
        repo_id=repo_id,
        local_dir=str(into),
        # Only what the converter reads. The repos also carry sample audio and
        # tokenizer variants that would add hundreds of megabytes for nothing.
        allow_patterns=[
            "*.safetensors", "*.json", "*.txt", "*.bin",
        ],
        ignore_patterns=["audios/*", "*.msgpack", "*.h5", "*.onnx"],
    )
    return Path(path)


def _normalise_dtype(checkpoint: Path) -> Path:
    """Re-save a bfloat16 checkpoint as float32, if that is what it is.

    whisper.cpp's converter calls `.numpy()` on each tensor, and NumPy has no
    bfloat16 — so a bf16 checkpoint dies with `Got unsupported ScalarType
    BFloat16` partway through. Apex is bf16; Swift is not, which is why the
    cheap model converted first time and the recommended one did not.

    Fixed *before* the converter runs rather than by patching it: the script is
    pinned and hash-checked precisely so it is the upstream file and not
    something we edited. Casting the input keeps that guarantee intact.
    """
    import torch

    config = checkpoint / "config.json"
    try:
        import json

        raw = json.loads(config.read_text())
        # Both spellings: transformers renamed `torch_dtype` to `dtype`, and
        # Apex uses the new one — which is why the first version of this check
        # silently did nothing and the conversion failed exactly as before.
        dtype = raw.get("dtype") or raw.get("torch_dtype") or ""
    except Exception:
        dtype = ""

    if str(dtype).lower() not in ("bfloat16", "bf16"):
        return checkpoint

    log.info("checkpoint is bfloat16 — re-saving as float32 for the converter")
    try:
        from transformers import WhisperForConditionalGeneration
    except ImportError as exc:
        raise ConversionError(
            "casting a bfloat16 checkpoint needs transformers.\n"
            "  uv sync --group stt-convert"
        ) from exc

    target = checkpoint.parent / f"{checkpoint.name}-fp32"
    model = WhisperForConditionalGeneration.from_pretrained(
        str(checkpoint), torch_dtype=torch.float32,
    )
    model.save_pretrained(str(target), safe_serialization=True)

    # The converter also reads the tokenizer and config files, which
    # save_pretrained does not copy.
    for extra in ("vocab.json", "added_tokens.json", "merges.txt",
                  "tokenizer.json", "tokenizer_config.json",
                  "special_tokens_map.json", "normalizer.json",
                  "preprocessor_config.json"):
        source = checkpoint / extra
        if source.exists():
            shutil.copy2(source, target / extra)

    return target


def convert(repo_id: str, output: Path, quantise: bool = False) -> Path:
    """Download `repo_id`, convert to GGML, and put it at `output`.

    Returns the written path. Raises ConversionError with something you can
    act on — never a partial file left behind pretending to be a model.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    whisper_root = _whisper_assets_root()

    with tempfile.TemporaryDirectory(prefix="kavach-convert-") as tmp:
        work = Path(tmp)
        script = _fetch_converter(work)
        checkpoint = download_checkpoint(repo_id, work / "checkpoint")
        checkpoint = _normalise_dtype(checkpoint)

        out_dir = work / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        log.info("converting to ggml — this takes a few minutes")
        done = subprocess.run(
            [sys.executable, str(script), str(checkpoint),
             str(whisper_root), str(out_dir)],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "")[-1500:]
            raise ConversionError(
                f"whisper.cpp's converter failed on {repo_id}:\n{tail}"
            )

        produced = sorted(out_dir.glob("ggml-model*.bin"))
        if not produced:
            raise ConversionError(
                f"the converter reported success but wrote no model. "
                f"Contents: {[p.name for p in out_dir.iterdir()]}"
            )

        # Moved into place only once it exists and is whole, so a failed run
        # never leaves something that `is_installed()` would believe.
        shutil.move(str(produced[0]), str(output))

    log.info("wrote %s (%.2f GB)", output, output.stat().st_size / 1e9)
    return output
