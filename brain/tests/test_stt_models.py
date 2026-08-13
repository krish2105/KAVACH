"""Selectable speech models (Phase 21).

> Replace or add as an alternate STT model … alongside the existing stock
> Whisper model. Make it a config toggle, not a hard replacement — I want to be
> able to switch back.

"Not a hard replacement" is the whole specification, and most of these tests
are about that one sentence: stock is what you get unless you asked for
otherwise, switching back actually reverts, and a Hinglish model that is
selected but missing degrades to stock instead of taking the voice loop down
with it. A speech assistant that will not start because of a *preference* is
worse than one that mishears you.

The rest guard the registry itself. Every entry has to declare its licence,
size and base model, because a model nobody can audit should not be reachable
by forgetting a field — and because the ready-made GGML this phase declined to
use is exactly the case that rule exists for.
"""

import json

import pytest

from kavach.voice import stt_models


def _fake_model(path):
    """A file big enough to pass the failed-download guard.

    A 12-byte stand-in fails is_installed() on purpose — MIN_USABLE_BYTES
    exists so a download killed early is not mistaken for a model.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * (stt_models.MIN_USABLE_BYTES + 1))
    return path


@pytest.fixture(autouse=True)
def config_in_tmp(tmp_path, monkeypatch):
    """Never touch the real ~/.kavach while testing a preference file."""
    monkeypatch.setattr(stt_models, "CONFIG_PATH", tmp_path / "stt.json")
    monkeypatch.setattr(stt_models, "MODEL_DIR", tmp_path / "models")
    return tmp_path


# ═══ 1. stock is the default, and stays reachable ═══

def test_an_untouched_install_uses_stock():
    assert stt_models.selected_name() == "stock"
    assert stt_models.resolve() == stt_models.STOCK.identifier


def test_choosing_a_model_round_trips(config_in_tmp):
    stt_models.select("apex")

    assert stt_models.selected_name() == "apex"
    assert json.loads((config_in_tmp / "stt.json").read_text())["model"] == "apex"


def test_switching_back_to_stock_really_reverts():
    """The sentence the phase turns on."""
    stt_models.select("apex")
    stt_models.select("stock")

    assert stt_models.selected_name() == "stock"
    assert stt_models.resolve() == stt_models.STOCK.identifier


def test_an_unknown_model_is_refused():
    """Refused loudly. A typo silently leaving you on stock would look like
    the toggle not working."""
    with pytest.raises(KeyError):
        stt_models.select("whisper-hinglish-9000")

    assert stt_models.selected_name() == "stock"


# ═══ 2. a missing model must not break speech ═══

def test_a_selected_but_missing_model_falls_back_to_stock(caplog):
    """The load-bearing one.

    The model file lives outside the repo and is gigabytes; it can be deleted,
    half-downloaded, or on a drive that did not mount. None of that should stop
    KAVACH listening — it should mishear you in English, having said why.
    """
    stt_models.select("apex")

    with caplog.at_level("WARNING"):
        resolved = stt_models.resolve()

    assert resolved == stt_models.STOCK.identifier
    assert any("apex" in r.message.lower() for r in caplog.records)


def test_a_downloaded_model_is_used(config_in_tmp):
    stt_models.select("apex")
    path = _fake_model(stt_models.MODEL_DIR / "apex.bin")

    assert stt_models.resolve() == str(path)


def test_installed_reports_what_is_actually_on_disk(config_in_tmp):
    assert stt_models.is_installed("apex") is False

    _fake_model(stt_models.MODEL_DIR / "apex.bin")

    assert stt_models.is_installed("apex") is True


def test_an_empty_file_does_not_count_as_installed(config_in_tmp):
    """A download killed at byte zero leaves a file behind."""
    path = stt_models.MODEL_DIR / "apex.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")

    assert stt_models.is_installed("apex") is False


def test_a_corrupt_config_falls_back_rather_than_raising(config_in_tmp):
    (config_in_tmp / "stt.json").write_text("{not json")

    assert stt_models.selected_name() == "stock"


# ═══ 3. the registry is auditable ═══

def test_every_model_declares_its_licence_size_and_base():
    """A model nobody can audit must not be addable by forgetting a field.

    This phase declined a ready-made GGML precisely because its licence read
    "other" while the weights it derived from were apache-2.0. That is only
    catchable if every entry is required to state its provenance.
    """
    for name, model in stt_models.REGISTRY.items():
        assert model.licence, f"{name} has no licence"
        assert model.size_bytes > 0, f"{name} has no size"
        assert model.base_model, f"{name} does not say what it is built on"
        assert model.note, f"{name} does not say when to pick it"


def test_no_model_has_an_unaudited_licence():
    """Permissive licences only, spelled out rather than 'other'."""
    allowed = {"apache-2.0", "mit"}
    for name, model in stt_models.REGISTRY.items():
        assert model.licence.lower() in allowed, \
            f"{name} has licence {model.licence!r}"


def test_hinglish_models_are_marked_as_such():
    hinglish = [n for n, m in stt_models.REGISTRY.items() if m.hinglish]
    assert hinglish, "the phase added no Hinglish models"
    assert "stock" not in hinglish


def test_the_registry_records_real_verified_sizes():
    """Guards against the sizes drifting into guesses.

    These were read off the Hugging Face file listings, and the whole point of
    the phase's research was that the name told you nothing about them — Trelis
    is 6.17 GB, not the small model it sounds like.
    """
    assert stt_models.REGISTRY["apex"].size_bytes == 1_617_825_448
    assert stt_models.REGISTRY["swift"].size_bytes == 290_403_936
    assert stt_models.REGISTRY["trelis"].size_bytes == 6_174_117_192


def test_stock_is_not_downloadable_through_the_registry():
    """pywhispercpp fetches stock itself; pulling it here would be a second,
    divergent download path for the model that already works."""
    assert stt_models.STOCK.repo_id is None
