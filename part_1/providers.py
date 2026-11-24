"""MODEL=fake | anthropic:<model> | ollama:<model>. All three speak the Messages API."""
import os

import httpx

from common import OLLAMA
from fake import FakeModel
from local import LocalQwen
from scripts import SCRIPTS


REPLAYABLE = {"temperature": 0}


class Messages:
    def __init__(self, model, base_url, api_key, settings):
        self.name, self.url, self.key = model, base_url.rstrip("/") + "/v1/messages", api_key
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))

    async def complete(self, system, messages, tools):
        reply = await self.http.post(self.url, headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                                     json={"model": self.name, "max_tokens": 8192, "system": system,
                                           "messages": messages, "tools": tools, **self.settings})
        if reply.status_code != 200:
            raise RuntimeError(f"API {reply.status_code}: {reply.text[:300]}")
        body = reply.json()
        if not body.get("content"):  # Ollama sends null for an empty reply; the API never does
            body["content"] = [{"type": "text", "text": "(the model returned an empty reply)"}]
        return body


def make(spec):
    kind, _, name = spec.partition(":")
    if kind == "fake":
        return FakeModel(SCRIPTS)
    if kind == "anthropic":
        if "ANTHROPIC_API_KEY" not in os.environ:
            raise SystemExit("MODEL=anthropic:... needs ANTHROPIC_API_KEY in the environment")
        return Messages(name, "https://api.anthropic.com", os.environ["ANTHROPIC_API_KEY"], REPLAYABLE)
    if kind == "ollama":
        return LocalQwen(name, OLLAMA)
    raise SystemExit(f"unknown MODEL {spec!r}: use fake, anthropic:<model> or ollama:<model>")
