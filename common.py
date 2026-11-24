"""The contract between the screen and the agent: an agent turn is a stream of these events."""
import asyncio
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent
LOCAL_MODEL, BASE_MODEL = "cc-qwen3", "qwen3:1.7b"
LOCAL_WINDOW = 16384  # num_ctx in Modelfile
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA = OLLAMA if OLLAMA.startswith(("http://", "https://")) else f"http://{OLLAMA}"


def pick_model() -> tuple[str, str]:
    """Return (MODEL spec, reason): $MODEL if set, else cc-qwen3 if Ollama serves it, else the fake."""
    if spec := os.environ.get("MODEL"):
        return spec, "set by $MODEL"
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=0.5) as reply:
            names = {m["name"].removesuffix(":latest") for m in json.load(reply)["models"]}
    except (OSError, ValueError, KeyError, TypeError):
        return "fake", "Ollama isn't running, so a scripted model stands in"
    if LOCAL_MODEL in names:
        return f"ollama:{LOCAL_MODEL}", "qwen3 1.7B, local, 16k context"
    if BASE_MODEL in names:
        return "fake", f"run `ollama create {LOCAL_MODEL} -f Modelfile` first; a scripted model stands in"
    return "fake", f"`ollama pull {BASE_MODEL}` for a real model; a scripted one stands in"


class APIError(Exception):
    """An HTTP error from a model API. `status` is None when the connection itself failed."""

    def __init__(self, status: int | None, message: str, retry_after: float | None = None):
        super().__init__(f"{status or 'connection'} {message}")
        self.status, self.retry_after = status, retry_after


@dataclass
class Text:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    id: str
    output: str
    is_error: bool = False
    display: str | None = None


@dataclass
class Ask:
    question: str
    always: str | None = None
    answer: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


@dataclass
class Notice:
    text: str


@dataclass
class Thinking:
    text: str


def sandbox(reset: bool = False) -> Path:
    """Return sandbox/, a disposable copy of workspace/ that every demo works on."""
    target = REPO / "sandbox"
    if reset and target.exists():
        shutil.rmtree(target)
    if not target.exists():
        shutil.copytree(REPO / "workspace", target)
    return target


async def drive(agent, prompt: str, approve: bool = True) -> list:
    """Run one turn headless, answering every Ask; returns the events. Used by tests and break_it."""
    events = []
    async for event in agent.turn(prompt):
        events.append(event)
        if isinstance(event, Ask):
            event.answer.set_result("yes" if approve else "no")
    return events


def load_part(n: int, *modules: str):
    """Import modules from part_<n> without clobbering the current part's modules of the same name."""
    part = REPO / f"part_{n}"
    local = {p.stem for p in part.glob("*.py")}
    saved = {name: sys.modules.pop(name) for name in local if name in sys.modules}
    sys.path.insert(0, str(part))
    try:
        loaded = [importlib.import_module(m) for m in modules]
    finally:
        sys.path.remove(str(part))
        for name in local:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    return loaded[0] if len(loaded) == 1 else loaded


def stream_events(message: dict):
    """The Messages API stream for a finished message: the events a server sends while writing it."""
    usage = message["usage"]
    yield {"type": "message_start", "message": {"id": message.get("id", "msg"), "type": "message", "role": "assistant",
                                                "content": [], "usage": {**usage, "output_tokens": 0}}}
    for i, block in enumerate(message["content"]):
        kind = block["type"]
        if kind == "text":
            yield {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}
            for piece in re.findall(r"\s*\S+\s*", block["text"]) or [block["text"]]:
                yield {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": piece}}
        elif kind == "tool_use":
            yield {"type": "content_block_start", "index": i,
                   "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}
            raw = json.dumps(block["input"])
            step = max(1, len(raw) // 4)
            for at in range(0, len(raw), step):
                yield {"type": "content_block_delta", "index": i,
                       "delta": {"type": "input_json_delta", "partial_json": raw[at:at + step]}}
        elif kind == "thinking":
            yield {"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": ""}}
            yield {"type": "content_block_delta", "index": i,
                   "delta": {"type": "thinking_delta", "thinking": block["thinking"]}}
        yield {"type": "content_block_stop", "index": i}
    yield {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"]},
           "usage": {"output_tokens": usage["output_tokens"]}}
    yield {"type": "message_stop"}


def sse(event: dict) -> list[str]:
    """One event as server-sent-event lines."""
    return [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]


def agent_path() -> str:
    """PATH for the agent's commands: the course's python3, the system's tools, no pip. Same on every shell."""
    shims = Path(tempfile.gettempdir()) / f"cc-agent-bin-{os.getuid()}"
    shims.mkdir(exist_ok=True)
    python = os.path.realpath(sys.executable)
    for name in ("python3", "python"):
        link = shims / name
        if not link.is_symlink() or os.readlink(link) != python:
            link.unlink(missing_ok=True)
            link.symlink_to(python)
    for name in ("pip", "pip3"):
        script = shims / name
        script.write_text("#!/bin/sh\necho 'pip is disabled here: the agent cannot install packages.' >&2\nexit 1\n")
        script.chmod(0o755)
    return os.pathsep.join([str(shims), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])


class Faulty:
    """Wraps any model: FAULTS="529,529" makes its next calls fail with those statuses, to rehearse retries."""

    def __init__(self, model, faults: str):
        self.model, self.faults = model, [int(s) for s in faults.split(",") if s.strip()]
        self.name, self.window = model.name, getattr(model, "window", 200_000)

    async def stream(self, system, messages, tools):
        if self.faults:
            status = self.faults.pop(0)
            raise APIError(status, {429: "rate_limit_error", 529: "overloaded_error"}.get(status, "api_error"))
        async for line in self.model.stream(system, messages, tools):
            yield line


def with_faults(model):
    return Faulty(model, os.environ["FAULTS"]) if os.environ.get("FAULTS") else model
