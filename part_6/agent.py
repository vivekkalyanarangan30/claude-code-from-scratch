"""6.1–6.5 — Durable execution. Every message, intent and result is on disk before the agent moves on."""
import asyncio

import session
import sse
import tools
from common import Ask, Notice, Text, Thinking, ToolCall, ToolResult
from judge import judge
from permissions import MODES, Gate

SYSTEM = """You are a coding agent. You work in the project folder; all paths are relative to it.
You change things only by calling tools. Writing code in a reply changes nothing.
To see what is in a file, Read it. Glob finds files by name; Grep searches inside files.
Read a file before you Edit it. In Edit, copy old_string exactly from Read's output, without the line numbers.
When a tool returns an error, read it and correct your next call.
If a result says the outcome is unknown, check what happened before doing it again.
The user may send you messages while you work; follow the latest one.
Run the tests before you say you are done. Then reply in one short sentence."""
MAX_TURNS = 25
INTERRUPTED = "Interrupted by the user before this finished. Ask before running it again."
PLAN_REMINDER = "(Plan mode is on: use read-only tools to investigate, then reply with a plan. Change nothing.)"
FILE_CHANGES = {"Edit", "Write"}


class Agent:
    def __init__(self, model, root, log=None):
        self.model, self.root = model, root
        self.ctx = tools.Context(root)
        self.messages = []
        self.tokens = [0, 0]
        self.commands = {"clear": self.clear, "mode": self.set_mode, "permissions": self.show_rules,
                         "rewind": self.rewind}
        self.steering, self.requests = [], []
        self.gate = Gate(root)
        self.log = log or session.Log(root)
        self.reply, self.results, self.touched = None, None, set()

    @classmethod
    def resume(cls, model, root, path):
        """Rebuild an agent from its log. Returns (agent, how many tool calls had no recorded result)."""
        agent = cls(model, root, session.Log(root, path))
        agent.messages, missing = session.replay(session.read(path))
        if missing:
            agent.add({"role": "user", "content": missing})
        agent.requests = [m["content"] for m in agent.messages if isinstance(m["content"], str)]
        return agent, sum(r["content"] in (session.UNKNOWN, session.NOT_RUN) for r in missing or [])

    def add(self, message):
        self.messages.append(message)
        self.log.append("message", message=message)

    def steer(self, text):
        """Called by the screen when the user types mid-turn. The model reads it at the next tool boundary."""
        self.steering.append(text)
        self.requests.append(text)

    async def turn(self, prompt):
        self.requests.append(prompt)
        self.log.append("turn", at=len(self.messages), prompt=prompt)
        self.touched = set()
        if self.gate.mode == "plan":
            prompt = [{"type": "text", "text": prompt}, {"type": "text", "text": PLAN_REMINDER}]
        self.add({"role": "user", "content": prompt})
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
                self.add({"role": "assistant", "content": said})
        if self.results is not None:
            asked = [b for b in self.messages[-1]["content"] if b["type"] == "tool_use"]
            done = {r["tool_use_id"] for r in self.results}
            self.results += [{"type": "tool_result", "tool_use_id": b["id"], "content": INTERRUPTED, "is_error": True}
                             for b in asked if b["id"] not in done]
            self.add({"role": "user", "content": self.results})
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
            self.add({"role": "assistant", "content": reply.content})
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
            self.add({"role": "user", "content": results + said})
        yield Notice(f"stopped after {MAX_TURNS} model calls")

    async def run_tool(self, block):
        """Write-ahead: checkpoint the file and log the intent, run, then log the result."""
        if block["name"] in FILE_CHANGES:
            self.checkpoint(block["input"].get("path", ""))
        self.log.append("intent", id=block["id"], name=block["name"], input=block["input"])
        try:
            output, is_error = await tools.run(self.ctx, block["name"], block["input"]), False
        except Exception as exc:
            output, is_error = tools.describe(exc), True
        self.log.append("result", id=block["id"], output=output, is_error=is_error)
        return output, is_error

    def checkpoint(self, path):
        """Save a file's content the first time this turn changes it, so /rewind can put it back."""
        try:
            target = tools.inside(self.root, path)
        except tools.ToolError:
            return
        if target in self.touched:
            return
        self.touched.add(target)
        content = target.read_text() if target.is_file() else None
        self.log.append("checkpoint", path=str(target.relative_to(self.root.resolve())), content=content)

    def rewind(self, _arg):
        """Undo the last turn: restore the files it changed and drop it from the conversation."""
        records = session.read(self.log.path)
        turns = [i for i, r in enumerate(records) if r["kind"] == "turn"]
        if not turns:
            return "nothing to rewind"
        start = turns[-1]
        restored = {}
        for r in records[start:]:
            if r["kind"] == "checkpoint" and r["path"] not in restored:
                restored[r["path"]] = r["content"]
        for path, content in restored.items():
            target = self.root / path
            if content is None:
                target.unlink(missing_ok=True)
            else:
                session.atomic_write(target, content)
        self.messages = self.messages[:records[start]["at"]]
        self.log.append("rewind", at=len(self.messages))
        self.ctx.seen.clear()
        files = ", ".join(restored) or "no files"
        return f"rewound \"{records[start]['prompt'][:40]}\" · restored {files} · commands it ran are not undone"

    def status(self):
        return (f"{self.gate.mode} mode · {self.model.name} · ↑{self.tokens[0]:,} ↓{self.tokens[1]:,} tokens · "
                f"{len(self.messages)} messages · {self.log.path.name}")

    def clear(self, _arg):
        self.messages.clear()
        self.ctx.seen.clear()
        self.log = session.Log(self.root)
        return "conversation cleared · new session"

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
