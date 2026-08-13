"""Which speech model KAVACH listens with (Phase 21).

> Replace or add as an alternate STT model … alongside the existing stock
> Whisper model. Make it a config toggle, not a hard replacement.

**Stock is the default and always will be.** A Hinglish model is used only
because you selected one, and `use stock` genuinely reverts — that is the
sentence the phase turns on, and `resolve()` is written so that every failure
path leads back to stock rather than to an exception.

## What the research changed

The model named in the spec is real and apache-2.0, and almost nothing else
about it matched expectations:

* It is `Trelis/whisper-hinglish-preview`, lowercase, and **6.17 GB** — a
  large-v3 fine-tune, not the small model the name suggests.
* **None of the Hinglish Whispers ship GGML.** They are transformers
  checkpoints; KAVACH runs whisper.cpp. That gap is invisible on the model card
  and is the reason `convert_ggml.py` exists at all.
* `Oriserve/…-Apex` is a fine-tune of **`large-v3-turbo`** — precisely the model
  KAVACH already runs — at the same 809 M parameters and 1.62 GB, with 23× the
  downloads. It is the closest thing to a drop-in that exists, so it is the one
  worth trying first.

A ready-made GGML of the Trelis model exists at 1.08 GB and is deliberately
**not** here: 0 downloads, single-file repo, and a licence reading `other`
while the weights it derives from are apache-2.0. Every entry below has to
declare its provenance, which is what makes that kind of thing visible.

Sizes are byte counts read from the Hugging Face file listings, not estimates.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("kavach.voice.stt_models")

CONFIG_PATH = Path.home() / ".kavach" / "stt.json"
MODEL_DIR = Path.home() / ".kavach" / "models"

#: A file smaller than this is a failed download, not a model.
MIN_USABLE_BYTES = 1_000_000


@dataclass(frozen=True)
class SpeechModel:
    name: str
    #: What pywhispercpp is given: a well-known name for stock, or a path for
    #: anything we converted ourselves.
    identifier: str | None
    #: Hugging Face repo. None for stock, which pywhispercpp fetches itself.
    repo_id: str | None
    size_bytes: int
    licence: str
    base_model: str
    note: str
    hinglish: bool = False

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1_000_000_000

    def local_path(self) -> Path:
        return MODEL_DIR / f"{self.name}.bin"


STOCK = SpeechModel(
    name="stock",
    identifier="large-v3-turbo",
    repo_id=None,
    size_bytes=1_600_000_000,
    licence="MIT",
    base_model="openai/whisper-large-v3-turbo",
    note="Default. Best general English; treats Hinglish as accented English.",
)

REGISTRY: dict[str, SpeechModel] = {
    "stock": STOCK,
    "apex": SpeechModel(
        name="apex",
        identifier=None,
        repo_id="Oriserve/Whisper-Hindi2Hinglish-Apex",
        size_bytes=1_617_825_448,
        licence="apache-2.0",
        base_model="openai/whisper-large-v3-turbo",
        note="Start here. Same size and base as stock, so it swaps in cleanly.",
        hinglish=True,
    ),
    "prime": SpeechModel(
        name="prime",
        identifier=None,
        repo_id="Oriserve/Whisper-Hindi2Hinglish-Prime",
        size_bytes=6_174_112_072,
        licence="apache-2.0",
        base_model="openai/whisper-large-v3",
        note="Most downloaded, but 4x the size — and its own README says Apex "
             "supersedes it.",
        hinglish=True,
    ),
    "swift": SpeechModel(
        name="swift",
        identifier=None,
        repo_id="Oriserve/Whisper-Hindi2Hinglish-Swift",
        size_bytes=290_403_936,
        licence="apache-2.0",
        base_model="openai/whisper-base",
        note="Tiny and fast. Lower accuracy — good for testing the pipeline.",
        hinglish=True,
    ),
    "trelis": SpeechModel(
        name="trelis",
        identifier=None,
        repo_id="Trelis/whisper-hinglish-preview",
        size_bytes=6_174_117_192,
        licence="apache-2.0",
        base_model="ARTPARK-IISc/whisper-large-v3-vaani-hindi",
        note="The one you named. Purpose-built for code-switching, but 6.17 GB.",
        hinglish=True,
    ),
}


def get(name: str) -> SpeechModel:
    """Look one up. Raises KeyError with the valid names, not a bare miss."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown speech model {name!r}. "
            f"Known: {', '.join(sorted(REGISTRY))}"
        ) from None


# ——— what is selected ———

def selected_name() -> str:
    """The chosen model's name, or "stock".

    Every failure — no file, unreadable file, unknown name inside it — answers
    "stock". A preference file is not worth failing to listen over.
    """
    try:
        data = json.loads(CONFIG_PATH.read_text())
        name = str(data.get("model", "stock"))
    except Exception:
        return "stock"
    return name if name in REGISTRY else "stock"


def select(name: str) -> SpeechModel:
    """Choose a model. Raises KeyError if it is not a real one."""
    model = get(name)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"model": name}, indent=2))
    log.info("speech model set to %s", name)
    return model


def is_installed(name: str) -> bool:
    """Whether the converted GGML is actually on disk and non-trivial."""
    model = get(name)
    if model.repo_id is None:
        return True  # stock: pywhispercpp fetches it on demand
    path = model.local_path()
    try:
        return path.exists() and path.stat().st_size >= MIN_USABLE_BYTES
    except Exception:
        return False


def resolve() -> str:
    """What to hand pywhispercpp — a name for stock, a path otherwise.

    Falls back to stock, loudly, if the selected model is not on disk. The file
    is gigabytes and lives outside the repo: it can be deleted, half-downloaded,
    or on a drive that did not mount, and none of that should stop KAVACH
    listening. Mishearing you in English beats refusing to start.
    """
    name = selected_name()
    model = REGISTRY.get(name, STOCK)

    if model.repo_id is None:
        return STOCK.identifier  # type: ignore[return-value]

    if not is_installed(name):
        log.warning(
            "speech model %r is selected but not downloaded (%s) — "
            "falling back to stock. Run: kavach-stt pull %s",
            name, model.local_path(), name,
        )
        return STOCK.identifier  # type: ignore[return-value]

    return str(model.local_path())


def describe_active() -> str:
    """One line for the doctor and the CLI."""
    name = selected_name()
    if name == "stock":
        return "stock (large-v3-turbo)"
    installed = is_installed(name)
    return f"{name}{'' if installed else ' — NOT downloaded, using stock'}"
