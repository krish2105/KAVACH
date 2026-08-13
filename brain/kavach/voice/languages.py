"""Multilingual replies.

Whisper `large-v3-turbo` already transcribes many languages and reports which
one it heard; Kokoro ships voices for nine. This module is the mapping between
them — Whisper's ISO code to a Kokoro voice and the espeak language code its
phonemiser needs.

**The wake word stays English-only, deliberately.** It was trained on English
renderings of "KAVACH" and its metrics (recall 0.9915) describe English
speech. Claiming multilingual wake detection would be a lie the numbers do not
support. Everything *after* the wake word is multilingual.

Unmapped languages fall back to English rather than guessing at a voice: a
reply spoken in the wrong language is worse than one spoken in the default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("kavach.voice.languages")

DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class LanguageVoice:
    """How to speak a given language."""

    #: Kokoro voice id.
    voice: str
    #: espeak-ng language code that Kokoro's phonemiser expects.
    espeak: str
    name: str


#: Whisper ISO-639-1 → Kokoro voice. Only languages Kokoro actually has voices
#: for appear here; anything else falls back to English.
VOICES: dict[str, LanguageVoice] = {
    "en": LanguageVoice("af_heart", "en-us", "English"),
    "hi": LanguageVoice("hf_alpha", "hi", "Hindi"),
    "es": LanguageVoice("ef_dora", "es", "Spanish"),
    "fr": LanguageVoice("ff_siwis", "fr-fr", "French"),
    "it": LanguageVoice("if_sara", "it", "Italian"),
    "pt": LanguageVoice("pf_dora", "pt-br", "Portuguese"),
    "ja": LanguageVoice("jf_alpha", "ja", "Japanese"),
    "zh": LanguageVoice("zf_xiaobei", "cmn", "Mandarin"),
}


def voice_for(language: str | None) -> LanguageVoice:
    """Pick a voice for a detected language, falling back to English.

    Whisper occasionally reports a locale ("en-GB") or an unsupported language
    entirely; both resolve to something speakable rather than raising in the
    middle of a turn.
    """
    if not language:
        return VOICES[DEFAULT_LANGUAGE]

    code = language.strip().lower().replace("_", "-").split("-")[0]
    chosen = VOICES.get(code)
    if chosen is None:
        log.info("no Kokoro voice for %r; replying in English", language)
        return VOICES[DEFAULT_LANGUAGE]
    return chosen


def is_supported(language: str | None) -> bool:
    if not language:
        return False
    return language.strip().lower().replace("_", "-").split("-")[0] in VOICES


def supported_languages() -> list[str]:
    return sorted(v.name for v in VOICES.values())
