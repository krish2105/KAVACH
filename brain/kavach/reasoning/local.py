"""Local fast model via Ollama (spec §3: Qwen3 4B).

Two jobs, both deliberately small:

* **classify_intent** — one cheap forward pass for the router's second pass
  (§5), returning structured JSON rather than prose to parse.
* **respond** — answer simple intents without a network round trip.

Measured on this machine (qwen3:4b, 8 cases, ~877 ms/call while the GPU was
busy training): **8/8 correct** with the tuned system prompt below. An earlier
terse prompt got "read my last three emails and draft a reply" wrong — the
prompt was the problem, not the model.

**Its self-reported confidence is worthless**: every single call returned
exactly 0.95, correct or not. The model emits a constant, not a calibrated
number. That matters because §4 #3 maps confidence to the orb's outer shell —
so the router derives confidence from *how* a decision was reached and
deliberately discards this field. Trusting it would make the ring lie.

Thinking mode is disabled (`think: false`). Qwen3 otherwise emits a reasoning
preamble that triples latency for no benefit on classification.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("kavach.reasoning.local")

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["simple", "complex"]},
        "confidence": {"type": "number"},
    },
    "required": ["intent", "confidence"],
}

_CLASSIFY_SYSTEM = (
    "Classify a spoken request to a macOS voice assistant.\n"
    "simple  = ONE obvious device action (open an app, set volume) or one "
    "fact lookup (the time, the battery level).\n"
    "complex = needs judgement, multiple steps, or reading and reasoning over "
    "content the assistant must first go and fetch.\n"
    "When in doubt answer complex. Answer only with the JSON object."
)

_RESPOND_SYSTEM = (
    "You are KAVACH, a voice assistant on this Mac. You are being read aloud, "
    "so reply in ONE short spoken sentence. No markdown, no lists, no "
    "preamble. If you cannot do something, say so plainly in one sentence."
)


class OllamaUnavailable(RuntimeError):
    """Ollama isn't running or the model isn't pulled."""


class LocalModel:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: float = 20.0,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    # ——— transport ———

    def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        request = urllib.request.Request(
            f"{self.host}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.URLError as exc:
            raise OllamaUnavailable(
                f"cannot reach Ollama at {self.host} ({exc}). Start it with:\n"
                f"  ollama serve"
            ) from exc

    def available(self) -> bool:
        try:
            tags = self._post("/api/show", {"model": self.model}, timeout=5.0)
            return "error" not in tags
        except Exception:
            return False

    # ——— router support ———

    def classify_intent(self, utterance: str) -> dict | None:
        """Return ``{"intent": "simple"|"complex", "confidence": float}``.

        Returns None on any failure — the router then falls through to its own
        safe default rather than treating a broken model as a verdict.
        """
        try:
            response = self._post("/api/chat", {
                "model": self.model,
                "stream": False,
                "think": False,
                "messages": [
                    {"role": "system", "content": _CLASSIFY_SYSTEM},
                    {"role": "user", "content": utterance},
                ],
                "format": _CLASSIFY_SCHEMA,
                "options": {"temperature": 0, "num_predict": 64},
            }, timeout=10.0)
        except OllamaUnavailable:
            return None

        content = response.get("message", {}).get("content", "")
        try:
            verdict = json.loads(content)
        except json.JSONDecodeError:
            log.warning("classifier returned non-JSON: %r", content[:120])
            return None

        if verdict.get("intent") not in {"simple", "complex"}:
            return None
        return verdict

    # ——— simple answers ———

    def respond(self, utterance: str, context: str = "") -> str:
        messages = [{"role": "system", "content": _RESPOND_SYSTEM}]
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": utterance})

        response = self._post("/api/chat", {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": messages,
            "options": {"temperature": 0.3, "num_predict": 120},
        })
        return response.get("message", {}).get("content", "").strip()
