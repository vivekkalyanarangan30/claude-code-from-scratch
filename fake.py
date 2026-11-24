"""A scripted model that speaks the Messages API shape and enforces its rules. No key, no network."""
import asyncio
import json
import os

from common import APIError, sse, stream_events


class FakeAPIError(APIError):
    def __init__(self, message):
        super().__init__(400, message)


def say(text):
    return {"type": "text", "text": text}


def call(name, **args):
    return {"type": "tool_use", "name": name, "input": args}


def blocks(content):
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def tokens(obj) -> int:
    """The fake's tokenizer: about four characters per token."""
    return len(obj if isinstance(obj, str) else json.dumps(obj)) // 4


def validate(messages):
    """Reject what the real API rejects. Like the API, consecutive same-role messages are merged first."""
    if not messages or messages[0]["role"] != "user":
        raise FakeAPIError("invalid_request_error: first message must use the user role")
    merged = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += blocks(m["content"])
        else:
            merged.append({"role": m["role"], "content": list(blocks(m["content"]))})
    for i, (prev, cur) in enumerate(zip(merged, merged[1:] + [{"role": "user", "content": []}]), start=1):
        if prev["role"] != "assistant":
            continue
        asked = {b["id"] for b in prev["content"] if b["type"] == "tool_use"}
        answered = {b["tool_use_id"] for b in cur["content"] if b["type"] == "tool_result"}
        if asked - answered:
            raise FakeAPIError(
                f"invalid_request_error: messages.{i}: `tool_use` ids were found without "
                f"`tool_result` blocks immediately after: {', '.join(sorted(asked - answered))}")


def prefix_blocks(system, messages):
    """The request as the cache sees it: a list of blocks, cacheable up to the last cache_control mark."""
    flat = [b for b in (system if isinstance(system, list) else [{"type": "text", "text": system}])]
    flat += [b for m in messages for b in blocks(m["content"])]
    marks = [i for i, b in enumerate(flat) if isinstance(b, dict) and "cache_control" in b]
    clean = [json.dumps({k: v for k, v in b.items() if k != "cache_control"}, sort_keys=True) for b in flat]
    return clean, (marks[-1] + 1 if marks else 0)


class FakeModel:
    """Replays SCRIPTS[keyword] one step per call, picked by a keyword in the latest user words.

    Off by default, on for the lessons that need them: FAKE_WINDOW (context limit), FAKE_FAULTS
    (e.g. "529,529": errors for the next calls), and prompt caching when a request marks cache_control.
    """

    def __init__(self, scripts: dict, delay: float | None = None, window: int | None = None, faults=None):
        self.scripts, self.ids, self.name = scripts, 0, "fake"
        self.delay = float(os.environ.get("FAKE_DELAY", 0.6)) if delay is None else delay
        self.window = window or int(os.environ.get("FAKE_WINDOW", 200_000))
        spec = os.environ.get("FAKE_FAULTS", "")
        self.faults = list(faults) if faults is not None else [int(s) for s in spec.split(",") if s.strip()]
        self.cached: list[str] = []

    def reply(self, system, messages, tools):
        if self.faults:
            status = self.faults.pop(0)
            if status:
                raise APIError(status, {429: "rate_limit_error", 529: "overloaded_error"}.get(status, "api_error"),
                               retry_after=None)
        validate(messages)
        flat, cacheable = prefix_blocks(system, messages)
        total = tokens(flat) + tokens(tools)
        if total > self.window:
            raise FakeAPIError(f"invalid_request_error: prompt is too long: {total} tokens > {self.window} maximum")
        hit = 0
        while hit < min(len(self.cached), cacheable) and self.cached[hit] == flat[hit]:
            hit += 1
        read, written = tokens("".join(flat[:hit])), tokens("".join(flat[hit:cacheable]))
        if cacheable:
            self.cached = flat[:cacheable]
        content = [dict(block) for block in self.next_step(messages)]
        for block in content:
            if block["type"] == "tool_use":
                self.ids += 1
                block["id"] = f"toolu_fake_{self.ids:03d}"
        usage = {"input_tokens": total - read - written, "output_tokens": tokens(content)}
        if cacheable:
            usage |= {"cache_read_input_tokens": read, "cache_creation_input_tokens": written}
        return {"id": f"msg_fake_{self.ids}", "role": "assistant", "content": content, "usage": usage,
                "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"}

    async def complete(self, system, messages, tools):
        message = self.reply(system, messages, tools)
        await asyncio.sleep(self.delay * (1 + message["usage"]["output_tokens"] / 20))
        return message

    async def stream(self, system, messages, tools):
        """The same reply as server-sent-event lines, a few words at a time."""
        message = self.reply(system, messages, tools)
        await asyncio.sleep(self.delay)
        for event in stream_events(message):
            if event["type"] == "content_block_delta":
                delta = event["delta"]
                piece = delta.get("text") or delta.get("partial_json") or delta.get("thinking") or ""
                await asyncio.sleep(self.delay * max(1, tokens(piece)) / 20)
            for line in sse(event):
                yield line

    def next_step(self, messages):
        start = max(i for i, m in enumerate(messages) if m["role"] == "user" and (
            isinstance(m["content"], str) or any(b["type"] == "text" for b in m["content"])))
        words = messages[start]["content"]
        words = words if isinstance(words, str) else " ".join(b["text"] for b in words if b["type"] == "text")
        prompt, step = words.lower(), (len(messages) - start) // 2
        last = messages[-1]["content"]
        if isinstance(last, list) and any("denied" in str(b.get("content", "")) for b in last):
            return [say("Understood, I won't do that. What would you like instead?")]
        for keyword, (_example, steps) in self.scripts.items():
            if keyword in prompt:
                if step >= len(steps):
                    return [say("(the fake model's script is finished)")]
                return steps[step](messages) if callable(steps[step]) else steps[step]
        examples = "\n".join(f'  • "{example}"' for example, _ in self.scripts.values() if example)
        return [say("I'm the scripted fake model, so I only know a few prompts:\n" + examples +
                    "\nSet MODEL=anthropic:claude-sonnet-5-5 or MODEL=ollama:<model> for a real one.")]
