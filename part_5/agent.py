"""5.1–5.6 — Permissions. Every tool call passes the gate; in auto mode a judge answers before you do."""
import asyncio

import sse
import tools
from judge import judge
from permissions import MODES, Gate
from common import Ask, Notice, Text, Thinking, ToolCall, ToolResult

SYSTEM = """You are a coding agent. You work in the project folder; all paths are relative to it.
You change things only by calling tools. Writing code in a reply changes nothing.
To see what is in a file, Read it. Glob finds files by name; Grep searches inside files.
Read a file before you Edit it. In Edit, copy old_string exactly from Read's output, without the line numbers.
When a tool returns an error, read it and correct your next call.
The user may send you messages while you work; follow the latest one.
Run the tests before you say you are done. Then reply in one short sentence."""
MAX_TURNS = 25
INTERRUPTED = "Interrupted by the user before this finished. Ask before running it again."
PLAN_REMINDER = "(Plan mode is on: use read-only tools to investigate, then reply with a plan. Change nothing.)"


class Agent:
    def __init__(self, model, root):
        self.model, self.root = model, root
        self.ctx = tools.Context(root)
        self.messages = []
        self.tokens = [0, 0]
        self.commands = {"clear": self.clear, "mode": self.set_mode, "permissions": self.show_rules}
        self.steering, self.requests = [], []
        self.gate = Gate(root)

    def steer(self, text):
        """Called by the screen when the user types mid-turn. The model reads it at the next tool boundary."""
        self.steering.append(text)
        self.requests.append(text)

    async def turn(self, prompt):
        self.requests.append(prompt)
        if self.gate.mode == "plan":
            prompt = [{"type": "text", "text": prompt}, {"type": "text", "text": PLAN_REMINDER}]
        self.messages.append({"role": "user", "content": prompt})
        self.reply, self.results = None, None
        try:
            async for event in self.loop():
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            self.repair()
            raise

    def repair(self):
        """Restore the invariant after a cancel: keep what was said, answer every open tool call."""
        if self.reply is not None:
            said = [b for b in self.reply.content if b["type"] == "text" and b["text"].strip()]
            if said:
                self.messages.append({"role": "assistant", "content": said})
        if self.results is not None:
            asked = [b for b in self.messages[-1]["content"] if b["type"] == "tool_use"]
            done = {r["tool_use_id"] for r in self.results}
            self.results += [{"type": "tool_result", "tool_use_id": b["id"], "content": INTERRUPTED, "is_error": True}
                             for b in asked if b["id"] not in done]
            self.messages.append({"role": "user", "content": self.results})
        self.reply, self.results = None, None

    async def loop(self):
        for _ in range(MAX_TURNS):
            self.reply = reply = sse.Reply()
            async for event in sse.events(self.model.stream(SYSTEM, self.messages, tools.SCHEMAS)):
                if text := reply.feed(event):
                    yield Text(text)
                elif event.get("delta", {}).get("type") == "thinking_delta":
                    yield Thinking(event["delta"]["thinking"])
                if block := reply.block_done(event):
                    yield ToolCall(block["id"], block["name"], block["input"])
            self.tokens[0] += reply.input_tokens
            self.tokens[1] += reply.usage["output_tokens"]
            self.messages.append({"role": "assistant", "content": reply.content})
            self.reply, self.results = None, []

            for block in reply.content:
                if block["type"] != "tool_use":
                    continue
                tool = tools.TOOLS.get(block["name"])
                decision, reason = self.gate.check(tool, block["input"]) if tool else ("allow", "")
                if decision == "ask" and self.gate.mode == "auto":
                    if await judge(self.model, self.requests, block["name"], block["input"]) == "allow":
                        decision = "allow"
                        yield Notice("auto mode: the judge allowed this")
                    else:
                        yield Notice("auto mode: the judge wasn't sure, so you decide")
                if decision == "ask":
                    ask = Ask(f"Allow {block['name']}?" + (f" ({reason})" if reason else ""),
                              always=self.gate.suggest(tool, block["input"]))
                    yield ask
                    answer = await ask.answer
                    if answer == "always":
                        self.gate.allow(ask.always)
                    decision, reason = ("deny", "the user denied this tool call") if answer == "no" else ("allow", "")
                if decision == "deny":
                    output, is_error = f"Not run: {reason}.", True
                else:
                    output, is_error = await self.run_tool(block)
                yield ToolResult(block["id"], output, is_error)
                self.results.append({"type": "tool_result", "tool_use_id": block["id"], "content": output,
                                     **({"is_error": True} if is_error else {})})

            results, self.results = self.results, None
            said = [{"type": "text", "text": f"The user sent this while you were working: {text}"}
                    for text in self.steering]
            self.steering.clear()
            if reply.stop_reason != "tool_use" and not said:
                return
            self.messages.append({"role": "user", "content": results + said})
        yield Notice(f"stopped after {MAX_TURNS} model calls")

    async def run_tool(self, block):
        """Return (output, is_error). Only called once the gate has said yes."""
        try:
            return await tools.run(self.ctx, block["name"], block["input"]), False
        except Exception as exc:
            return tools.describe(exc), True

    def status(self):
        return f"{self.gate.mode} mode · {self.model.name} · ↑{self.tokens[0]:,} ↓{self.tokens[1]:,} tokens · {len(self.messages)} messages"

    def clear(self, _arg):
        self.messages.clear()
        self.ctx.seen.clear()
        return "conversation cleared"

    def cycle_mode(self):
        return self.gate.cycle()

    def set_mode(self, arg):
        if arg not in MODES:
            return f"mode is {self.gate.mode}; choose one of: {', '.join(MODES)}"
        self.gate.mode = arg
        return f"{arg} mode"

    def show_rules(self, _arg):
        rules = self.gate.rules
        return (f"allow: {', '.join(rules['allow']) or 'none'}\n     deny: {', '.join(rules['deny']) or 'none'}"
                f"\n     saved in {self.gate.path.relative_to(self.root)}")
