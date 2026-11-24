"""3.1–3.2 — Server-sent events by hand, and a reply assembled from its pieces."""
import json

from common import APIError


async def events(lines):
    """`event:`/`data:` lines in, one parsed event per blank line out."""
    data = []
    async for line in lines:
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line.strip() and data:
            yield json.loads("\n".join(data))
            data = []
    if data:
        yield json.loads("\n".join(data))


class Reply:
    """Accumulates a streamed message. Tool input is a JSON string in fragments until its block stops."""

    def __init__(self):
        self.blocks, self.partial, self.stop_reason = {}, {}, None
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def feed(self, event) -> str | None:
        """Take one event; return any new text so the caller can show it immediately."""
        kind = event["type"]
        if kind == "message_start":
            self.usage.update(event["message"].get("usage", {}))
        elif kind == "content_block_start":
            self.blocks[event["index"]] = dict(event["content_block"])
            self.partial[event["index"]] = ""
        elif kind == "content_block_delta":
            block, delta = self.blocks[event["index"]], event["delta"]
            if delta["type"] == "text_delta":
                block["text"] += delta["text"]
                return delta["text"]
            if delta["type"] == "input_json_delta":
                self.partial[event["index"]] += delta["partial_json"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] += delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] = block.get("signature", "") + delta["signature"]
        elif kind == "content_block_stop":
            block, raw = self.blocks[event["index"]], self.partial.pop(event["index"])
            if block["type"] == "tool_use" and raw:
                block["input"] = json.loads(raw)
            return None
        elif kind == "message_delta":
            self.stop_reason = event["delta"].get("stop_reason") or self.stop_reason
            self.usage.update({k: v for k, v in event.get("usage", {}).items() if v})
        elif kind == "error":
            raise APIError(None, event["error"].get("message", "stream error"))
        return None

    def block_done(self, event) -> dict | None:
        """The finished tool_use block, if this event just closed one."""
        if event["type"] == "content_block_stop":
            block = self.blocks[event["index"]]
            return block if block["type"] == "tool_use" else None
        return None

    @property
    def content(self):
        return [self.blocks[i] for i in sorted(self.blocks)]

    @property
    def input_tokens(self):
        u = self.usage
        return u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
