"""MODEL=fake | anthropic:<model> | ollama:<model>. All three speak the Messages API."""
import os

import httpx

from common import OLLAMA, APIError
from fake import FakeModel
from local import LocalQwen
from scripts import SCRIPTS


REPLAYABLE = {"temperature": 0}


class Messages:
    def __init__(self, model, base_url, api_key, settings):
        self.name, self.url, self.key = model, base_url.rstrip("/") + "/v1/messages", api_key
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))

    async def stream(self, system, messages, tools):
        """POST with stream=true and hand back the raw lines: parsing them is the agent's job (sse.py)."""
        body = {"model": self.name, "max_tokens": 8192, "system": system, "messages": messages,
                "tools": tools, "stream": True, **self.settings}
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        async with self.http.stream("POST", self.url, headers=headers, json=body) as reply:
            if reply.status_code != 200:
                raise APIError(reply.status_code, (await reply.aread()).decode(errors="replace")[:300],
                               retry_after=float(reply.headers.get("retry-after", 0)) or None)
            async for line in reply.aiter_lines():
                yield line


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
