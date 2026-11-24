"""The local model, spoken as the Messages API. Not part of the course: like fake.py, it's a stand-in.

Ollama has its own /v1/messages endpoint, but with thinking off it drops qwen3's tool calls, and its
template forgets a tool call that follows text. With thinking on, qwen3 1.7B often thinks for
thousands of tokens at about 23 a second; with it off, it describes what it would do and stops.
So this renders qwen3's chat format itself, lets the model think for at most THINK_BUDGET tokens,
closes the think block, and parses the <tool_call> blocks out of the answer.
"""
import json
import os
import re

import httpx

from common import APIError, sse

OPTIONS = {"temperature": 0, "top_k": 1, "seed": 42, "num_ctx": 16384}
THINK_BUDGET = int(os.environ.get("THINK_BUDGET", 256))  # tokens of thinking per step
ANSWER_BUDGET = 1024
TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
TOOLS_HEADER = ("# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
                "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n")
TOOLS_FOOTER = ("\n</tools>\n\nFor each function call, return a json object with function name and arguments "
                "within <tool_call></tool_call> XML tags:\n<tool_call>\n"
                '{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>')


def text_of(content):
    return content if isinstance(content, str) else "\n".join(b["text"] for b in content if b["type"] == "text")


def render(system, messages, tools) -> str:
    """qwen3's chat template, ending in an empty think block (events() replaces it with real thinking)."""
    system = system if isinstance(system, str) else system[0]["text"]
    if tools:
        listed = "\n".join(json.dumps({"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}) for t in tools)
        system = f"{system}\n\n{TOOLS_HEADER}{listed}{TOOLS_FOOTER}"
    out = [f"<|im_start|>system\n{system}<|im_end|>\n"]
    for m in messages:
        blocks = [{"type": "text", "text": m["content"]}] if isinstance(m["content"], str) else m["content"]
        if m["role"] == "assistant":
            parts = [b["text"].strip() for b in blocks if b["type"] == "text" and b["text"].strip()]
            parts += [f'<tool_call>\n{json.dumps({"name": b["name"], "arguments": b["input"]})}\n</tool_call>'
                      for b in blocks if b["type"] == "tool_use"]
            out.append(f"<|im_start|>assistant\n{chr(10).join(parts)}<|im_end|>\n")
            continue
        results = [f"<tool_response>\n{'Error: ' if b.get('is_error') else ''}{text_of(b['content'])}\n</tool_response>"
                   for b in blocks if b["type"] == "tool_result"]
        words = text_of([b for b in blocks if b["type"] == "text"])
        out.append(f"<|im_start|>user\n{chr(10).join(results + ([words] if words else []))}<|im_end|>\n")
    out.append("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    return "".join(out)


def parse(raw: str, next_id) -> list:
    """The reply as content blocks: the text before the first <tool_call>, then one tool_use per call."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    text, _, rest = raw.partition("<tool_call>")
    content = [{"type": "text", "text": text.strip()}] if text.strip() else []
    for body in TOOL_CALL.findall("<tool_call>" + rest if rest else ""):
        try:
            call = json.loads(body)
            name, args = call["name"], call.get("arguments", {})
        except (json.JSONDecodeError, KeyError, TypeError):
            content.append({"type": "text", "text": f"<tool_call>{body}</tool_call>"})
            continue
        content.append({"type": "tool_use", "id": next_id(), "name": name, "input": args if isinstance(args, dict) else {}})
    return content


class LocalQwen:
    def __init__(self, model, base_url, window=OPTIONS["num_ctx"]):
        self.name, self.url, self.window = model, base_url.rstrip("/") + "/api/generate", window
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))
        self.calls, self.fresh, self.running = 0, False, 0

    async def forget(self):
        """Unload the model, which empties every one of Ollama's prompt-cache slots.

        Reusing a cached prefix changes the arithmetic just enough that the same conversation can take a
        different path. Within a conversation each request extends the last the same way every run; a
        cache left over from another conversation is the only thing that varies. Skipped while another
        request is running, so subagents that start together don't unload each other."""
        await self.http.post(self.url, json={"model": self.name, "keep_alive": 0})
        self.fresh = True

    def next_id(self):
        self.calls += 1
        return f"toolu_local_{self.calls:04d}"

    async def chunks(self, prompt, num_predict, stop=None):
        if not self.fresh and not self.running:
            await self.forget()
        self.running += 1
        body = {"model": self.name, "prompt": prompt, "raw": True, "stream": True,
                "options": {**OPTIONS, "num_predict": num_predict, **({"stop": stop} if stop else {})}}
        try:
            async with self.http.stream("POST", self.url, json=body) as reply:
                if reply.status_code != 200:
                    raise APIError(reply.status_code, (await reply.aread()).decode(errors="replace")[:300])
                async for line in reply.aiter_lines():
                    if line.strip():
                        yield json.loads(line)
        except httpx.TransportError as exc:
            raise APIError(None, f"connection failed: {exc}")
        finally:
            self.running -= 1

    async def stream(self, system, messages, tools):
        """The reply as server-sent-event lines: the agent parses them like any other stream."""
        async for event in self.events(system, messages, tools):
            for line in sse(event):
                yield line

    async def events(self, system, messages, tools):
        """Think for at most THINK_BUDGET tokens, close the think block, then answer."""
        if len(messages) == 1:
            self.fresh = False  # a new conversation (a session, /clear or a subagent) starts from an empty cache
        head = render(system, messages, tools)
        head = head[:head.rindex("<think>")] + "<think>\n"
        yield {"type": "message_start", "message": {"id": "msg_local", "type": "message", "role": "assistant",
                                                    "content": [], "usage": {"input_tokens": 0, "output_tokens": 0}}}
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}
        thought, spent = "", 0
        async for chunk in self.chunks(head, THINK_BUDGET, stop=["</think>"]):
            piece = chunk.get("thinking") or chunk.get("response") or ""
            if piece:
                thought += piece
                yield {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": piece}}
            spent += chunk.get("eval_count", 0)
        yield {"type": "content_block_stop", "index": 0}

        prompt = f"{head}{thought.strip()}\n</think>\n\n"
        yield {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}
        raw, shown = "", 0
        async for chunk in self.chunks(prompt, ANSWER_BUDGET):
            raw += chunk.get("response", "")
            safe = visible_length(raw)
            if safe > shown:
                piece = raw[shown:safe]
                yield {"type": "content_block_delta", "index": 1,
                       "delta": {"type": "text_delta", "text": piece.lstrip() if shown == 0 else piece}}
                shown = safe
            spent += chunk.get("eval_count", 0)
        yield {"type": "content_block_stop", "index": 1}
        calls = [b for b in parse(raw, self.next_id) if b["type"] == "tool_use"]
        for i, block in enumerate(calls, start=2):
            yield {"type": "content_block_start", "index": i,
                   "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}
            yield {"type": "content_block_delta", "index": i,
                   "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}}
            yield {"type": "content_block_stop", "index": i}
        # Ollama reports only the uncached part of the prompt, so count it the way the fake does.
        usage = {"input_tokens": len(prompt) // 4, "output_tokens": spent}
        yield {"type": "message_delta", "delta": {"stop_reason": "tool_use" if calls else "end_turn"}, "usage": usage}
        yield {"type": "message_stop"}

    async def complete(self, system, messages, tools):
        """The same reply, all at once, for Parts 1 and 2."""
        blocks, partial, stop, usage = {}, {}, "end_turn", {}
        async for e in self.events(system, messages, tools):
            if e["type"] == "content_block_start":
                blocks[e["index"]], partial[e["index"]] = dict(e["content_block"]), ""
            elif e["type"] == "content_block_delta":
                d, b = e["delta"], blocks[e["index"]]
                if d["type"] == "text_delta":
                    b["text"] += d["text"]
                elif d["type"] == "thinking_delta":
                    b["thinking"] += d["thinking"]
                else:
                    partial[e["index"]] += d["partial_json"]
            elif e["type"] == "content_block_stop" and blocks[e["index"]]["type"] == "tool_use":
                blocks[e["index"]]["input"] = json.loads(partial[e["index"]] or "{}")
            elif e["type"] == "message_delta":
                stop, usage = e["delta"]["stop_reason"], e["usage"]
        content = [b for i, b in sorted(blocks.items()) if b["type"] == "tool_use" or b.get("text", "").strip()]
        if not content:
            content = [{"type": "text", "text": "(the model returned an empty reply)"}]
        return {"role": "assistant", "content": content, "stop_reason": stop, "usage": usage}


def visible_length(raw: str) -> int:
    """How much of the reply is safe to show: text before any <tool_call>, holding back a possible start of one."""
    if "<tool_call>" in raw:
        return len(raw.split("<tool_call>")[0])
    hold = next((n for n in range(len("<tool_call>"), 0, -1) if raw.endswith("<tool_call>"[:n])), 0)
    return len(raw) - hold
