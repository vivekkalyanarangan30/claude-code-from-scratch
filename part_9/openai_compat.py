"""9.4 — A second provider behind the same interface: OpenAI-style chat completions (Ollama, DeepSeek, OpenRouter).

The agent only speaks the Messages API, so this translates both ways. Some things don't survive the trip:
thinking blocks are dropped, is_error has no equivalent, and tool arguments arrive as a string that may not be JSON.
"""
import json

import httpx

from common import APIError, sse, stream_events

STOP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}


def to_openai(system, messages, tools):
    out = [{"role": "system", "content": system if isinstance(system, str) else system[0]["text"]}]
    for m in messages:
        content = [{"type": "text", "text": m["content"]}] if isinstance(m["content"], str) else m["content"]
        if m["role"] == "assistant":
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                     for b in content if b["type"] == "tool_use"]
            text = "".join(b["text"] for b in content if b["type"] == "text")
            out.append({"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})})
            continue
        for b in content:
            if b["type"] == "tool_result":
                prefix = "Error: " if b.get("is_error") else ""
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": prefix + str(b["content"])})
        text = "\n".join(b["text"] for b in content if b["type"] == "text")
        if text:
            out.append({"role": "user", "content": text})
    functions = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                   "parameters": t["input_schema"]}} for t in tools]
    return out, functions


def from_openai(body):
    choice = body["choices"][0]
    message, content = choice["message"], []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        raw = call["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {"_unparsed_arguments": raw}
        content.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": args})
    usage = body.get("usage", {})
    return {"id": body.get("id", "chatcmpl"), "content": content,
            "stop_reason": STOP.get(choice.get("finish_reason"), "end_turn"),
            "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}}


class OpenAIChat:
    def __init__(self, model, base_url, api_key, window):
        self.name, self.url, self.key, self.window = model, base_url.rstrip("/") + "/chat/completions", api_key, window
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))

    async def stream(self, system, messages, tools):
        """One non-streaming call, replayed as the Messages API event stream the agent already understands."""
        chat, functions = to_openai(system, messages, tools)
        body = {"model": self.name, "messages": chat, "temperature": 0, **({"tools": functions} if functions else {})}
        try:
            reply = await self.http.post(self.url, json=body, headers={"authorization": f"Bearer {self.key}"})
        except httpx.TransportError as exc:
            raise APIError(None, f"connection failed: {exc}")
        if reply.status_code != 200:
            raise APIError(reply.status_code, reply.text[:300],
                           retry_after=float(reply.headers.get("retry-after", 0)) or None)
        for event in stream_events(from_openai(reply.json())):
            for line in sse(event):
                yield line
