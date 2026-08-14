"""The wake-word training config, and the augmentation it silently skipped.

v2 trained with **no room reverb and no background noise**, and nothing said
so. The trainer downloads MIT RIRs and MUSAN noise into `data_dir`, but reads
them from `augmentation.rir_paths`, which defaults to `./data/rirs` — derived
independently of `data_dir`. v2 set `data_dir: ./wakeword/data` and left the
augmentation paths alone, so they resolved to directories that do not exist.

`AudioAugmentor._collect_wavs` skips missing directories, and `apply_rir()`
returns the audio unchanged when the list is empty. No error, no warning for
the RIR half; the noise half at least logged "No background noise files found,
skipping". 735 files sat unused.

The result measured 0.858 on a file and 0.019 on the same audio through a
microphone, while reporting recall 0.835 on synthetic held-out clips.

These tests fail before a training run rather than after it.
"""

from pathlib import Path

import pytest
import yaml

BRAIN = Path(__file__).resolve().parents[1]
CONFIGS = sorted((BRAIN / "wakeword").glob("kavach*.yaml"))

#: The config that produces the model actually in use. Older ones are kept for
#: provenance and are not held to this.
CURRENT = BRAIN / "wakeword" / "kavach-v4.yaml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def resolved(paths: list[str]) -> list[Path]:
    return [Path(p) if Path(p).is_absolute() else BRAIN / p for p in paths]


def test_there_is_a_current_config():
    assert CURRENT.exists(), f"{CURRENT.name} is missing"


def test_the_current_config_declares_where_the_rirs_are():
    """Without this the default wins and it points at nothing."""
    aug = load(CURRENT).get("augmentation") or {}

    assert aug.get("rir_paths"), \
        "augmentation.rir_paths is unset, so the default ./data/rirs applies " \
        "— and the downloader does not put them there"
    assert aug.get("background_paths"), \
        "augmentation.background_paths is unset; background noise will be " \
        "skipped with one log line and no error"


def test_the_declared_rir_paths_actually_contain_rirs():
    """The check that turns a silent no-op into a failure.

    An empty list is not an error to the augmentor — it just stops adding
    reverb, and the run completes with confident numbers.
    """
    aug = load(CURRENT)["augmentation"]

    for directory in resolved(aug["rir_paths"]):
        wavs = list(directory.glob("**/*.wav")) if directory.exists() else []
        assert wavs, (
            f"{directory} holds no .wav files, so every RIR convolution will "
            f"be skipped silently. Run `uv run livekit-wakeword setup`."
        )


def test_the_declared_background_paths_actually_contain_noise():
    aug = load(CURRENT)["augmentation"]

    for directory in resolved(aug["background_paths"]):
        wavs = list(directory.glob("**/*.wav")) if directory.exists() else []
        assert wavs, f"{directory} holds no .wav files — noise will be skipped"


def test_the_paths_point_inside_the_configured_data_dir():
    """The whole bug in one assertion.

    `setup` downloads to `data_dir`; the augmentor reads `augmentation.*_paths`.
    Nothing in the package ties those together, so they drifted apart and the
    run reported success.
    """
    config = load(CURRENT)
    data_dir = (BRAIN / config["data_dir"]).resolve()
    aug = config["augmentation"]

    for directory in resolved(aug["rir_paths"] + aug["background_paths"]):
        assert data_dir in directory.resolve().parents or \
            directory.resolve() == data_dir, (
            f"{directory} is outside data_dir ({data_dir}), so `setup` will "
            f"download to one place and training will read another"
        )


@pytest.mark.parametrize("config_path", CONFIGS, ids=lambda p: p.stem)
def test_every_config_names_a_distinct_model(config_path):
    """Two configs writing the same model_name overwrite each other's export,
    and `find_wake_model()` takes the newest — which would silently swap the
    model under a calibration measured against the other one."""
    names = [load(p).get("model_name") for p in CONFIGS]

    assert names.count(load(config_path)["model_name"]) == 1, \
        f"{config_path.name} shares model_name with another config"
