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
# Llama 3.2 3B, not Qwen3 4B. Both are named in spec §3; this one is chosen on
# measurement.
#
# Qwen3 narrates its deliberation as ordinary prose — not inside <think> tags,
# so `think: false` removes nothing and there is no structure to strip. Live,
# it read "We are in a scenario where I am KAVACH... As an AI, I don't have..."
# aloud and into the HUD transcript. Appending Qwen's documented /no_think
# token made it worse: it reasoned about the token.
#
# Measured side by side on the same prompt and system message:
#   qwen3:4b     1911ms, empty or narrated
#   llama3.2:3b   421ms, "The ocean covers over 70% of the Earth's surface."
DEFAULT_MODEL = "llama3.2:3b"

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

# Phrased as a rule about output, not as a scenario to reason about.
#
# The previous wording described a situation ("You are KAVACH, a voice
# assistant... you are being read aloud") and Qwen3 answered it as a puzzle,
# narrating its deliberation aloud: "We are in a scenario where I am KAVACH...
# The user asks... As an AI, I don't have..." Every word of that reached the
# speaker and the HUD transcript.
#
# Naming the failure explicitly is what stopped it. `reasoning.cleanup` is the
# safety net for when it does not.
_RESPOND_SYSTEM = (
    "Answer in one short spoken sentence. Nothing else.\n"
    "Never explain your reasoning. Never restate the question. Never mention "
    "being an AI, a model, or an assistant. Never write 'the user asks' or "
    "'my reply is'.\n"
    "If you cannot do something, say so in one sentence and stop.\n"
    "Correct: \"It's twenty past four.\"\n"
    "Wrong: \"The user asks the time. As an AI I can check the clock. "
    "So, my reply is: it's twenty past four.\""
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
        raw = response.get("message", {}).get("content", "").strip()
        # Second line of defence — see reasoning.cleanup.
        from .cleanup import clean_reply

        return clean_reply(raw)
