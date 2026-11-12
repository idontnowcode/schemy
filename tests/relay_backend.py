"""Test-only launcher: the real app with a relay AI. Every prompt the app sends is written to
``<dir>/NNN.prompt.md`` (and its JSON schema to ``NNN.schema.json``); the call waits until ``NNN.answer.txt``
appears and returns that text as the model's answer. A person, or an assistant acting as the model, reads each
prompt and writes the answer, so a whole flow can be run with real answers and no API key.

    AICS_RELAY_DIR=/tmp/relay python -m tests.relay_backend --no-auth --workspace <dir> --ui-dir desktop/dist

Used for the real-flow test in docs/real-flow-test-report.md. Not part of the app.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ai_engine import LlmResult, ModelInfo
from backend.__main__ import main

QUEUE = Path(os.environ.get("AICS_RELAY_DIR") or "relay-ai")
_lock = threading.Lock()


def _next() -> int:
    with _lock:
        QUEUE.mkdir(parents=True, exist_ok=True)
        n = max((int(p.name[:3]) for p in QUEUE.glob("[0-9][0-9][0-9].prompt.md")), default=0) + 1
        (QUEUE / f"{n:03d}.prompt.md").touch()
        return n


class RelayAi:
    def __init__(self, provider: str, model: str):
        self.name = provider
        self.model = model or "relay"

    def generate(self, *, prompt, attachments=(), json_schema=None, max_output_tokens=8192, temperature=0.0):
        n = _next()
        head = {"n": n, "max_output_tokens": max_output_tokens, "json_schema": bool(json_schema),
                "attachments": [a.name for a in attachments], "prompt_chars": len(prompt),
                "at": time.strftime("%H:%M:%S")}
        if json_schema:
            (QUEUE / f"{n:03d}.schema.json").write_text(json.dumps(json_schema, ensure_ascii=False, indent=1))
        for a in attachments:  # e.g. a datasheet PDF to summarize: saved next to the prompt for whoever answers
            (QUEUE / f"{n:03d}.{Path(a.name).name}").write_bytes(a.data)
        (QUEUE / f"{n:03d}.prompt.md").write_text(json.dumps(head, ensure_ascii=False) + "\n\n" + prompt)
        answer, started = QUEUE / f"{n:03d}.answer.txt", time.time()
        while not answer.exists():
            time.sleep(0.5)
        time.sleep(0.2)  # let the writer finish
        text = answer.read_text()
        (QUEUE / f"{n:03d}.done.json").write_text(json.dumps({"seconds": round(time.time() - started, 1),
                                                              "answer_chars": len(text)}))
        return LlmResult(text=text, model=self.model, finish_reason="STOP",
                         usage={"prompt_token_count": len(prompt) // 3, "candidates_token_count": len(text) // 3})

    def list_models(self):
        return [ModelInfo(self.model, "Relay (answers written by hand)", 1_000_000, 65_536)]


class MemoryKeys:
    available = True

    def get(self, provider):
        # An AI key for whichever provider is asked, and nothing else. A distributor key (Mouser, DigiKey) must
        # read as unset, or the design's datasheet step would send part-number searches to the real services.
        from backend.datasheet_search import KEY_NAMES

        if any(provider in names for names in KEY_NAMES.values()):
            return None, None
        return "relay-key-0000", "keyring"

    def set(self, provider, key):
        pass

    def delete(self, provider):
        return False


def configure(settings) -> None:
    settings.keystore = MemoryKeys()
    settings.ai_provider_factory = lambda provider, key, model, timeout: RelayAi(provider, model)


if __name__ == "__main__":
    raise SystemExit(main(configure=configure))
