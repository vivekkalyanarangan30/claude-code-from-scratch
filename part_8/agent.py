"""8.3 — A stop hook: when the model says it's done, the harness may disagree and send it back to work."""
import asyncio

import context
import session
import sse
import tools
from common import Ask, Notice, Text, Thinking, ToolCall, ToolResult
from judge import judge
from permissions import MODES, Gate

BASE = """You are a coding agent. You work in the project folder; all paths are relative to it.
You change files only by calling tools. Code written in a reply changes nothing.

Work in this order, one tool call at a time:
1. Run the project's test command with Bash and read the error.
2. Read the source file the error points to. Never Edit a file you have not Read.
3. Edit one line: old_string is a whole line copied exactly from Read's output, without the line number.
4. Run the tests again. Repeat from step 2 until they pass, then reply in one short sentence.

Never edit the tests to make them pass. When a tool returns an error, read it and correct your next call.
"""
MAX_TURNS = 25
INTERRUPTED = "Interrupted by the user before this finished. Ask before running it again."
PLAN_REMINDER = "(Plan mode is on: use read-only tools to investigate, then reply with a plan. Change nothing.)"
FILE_CHANGES = {"Edit", "Write"}
CLEAR_AT, COMPACT_AT = 0.5, 0.8


class Agent:
    def __init__(self, model, root, log=None):
        self.model, self.root = model, root
        self.ctx = tools.Context(root)
        self.messages = []
        self.tokens = [0, 0]
        self.commands = {"clear": self.clear, "mode": self.set_mode, "permissions": self.show_rules,
                         "rewind": self.rewind, "compact": self.request_compact, "context": self.show_context}
        self.steering, self.requests = [], []
        self.gate = Gate(root)
        self.log = log or session.Log(root)
        self.reply, self.results, self.touched = None, None, set()
        self.system = context.system_prompt(root, BASE)
        self.window = getattr(model, "window", 200_000)
        self.cache, self.compact_next = [0, 0], False
        self.stop_hook, self.max_turns = None, MAX_TURNS

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
        for _ in range(self.max_turns):
            async for notice in self.fit():
                yield notice
            self.reply = reply = sse.Reply()
            system, messages = context.cache_marks(self.system, self.messages)
            async for event in sse.events(self.model.stream(system, messages, tools.SCHEMAS)):
                if text := reply.feed(event):
                    yield Text(text)
                elif event.get("delta", {}).get("type") == "thinking_delta":
                    yield Thinking(event["delta"]["thinking"])
                if block := reply.block_done(event):
                    yield ToolCall(block["id"], block["name"], block["input"])
            self.tokens[0] += reply.input_tokens
            self.tokens[1] += reply.usage["output_tokens"]
            self.cache[0] += reply.usage.get("cache_read_input_tokens", 0)
            self.cache[1] += reply.usage.get("cache_creation_input_tokens", 0)
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
                final = " ".join(b["text"] for b in reply.content if b["type"] == "text")
                again = await self.stop_hook(final) if self.stop_hook else None
                if not again:
                    return
                yield Notice("stop hook: not done yet, sending the task back")
                said = [{"type": "text", "text": again}]
            self.add({"role": "user", "content": results + said})
        yield Notice(f"stopped after {self.max_turns} model calls")

    def used(self) -> int:
        return context.estimate(self.system, self.messages, tools.SCHEMAS)

    async def fit(self):
        """Before each call: clear old tool output past half the window, compact past 80%."""
        if self.used() > self.window * CLEAR_AT and (n := context.clear_old_results(self.messages)):
            yield Notice(f"cleared {n} old tool output{'' if n == 1 else 's'} · "
                         f"context now {self.used() * 100 // self.window}% full")
        if self.compact_next or self.used() > self.window * COMPACT_AT:
            self.compact_next = False
            before = self.used()
            await self.compact()
            yield Notice(f"compacted the conversation: {before:,} → {self.used():,} tokens")

    async def compact(self):
        """Ask the model for a summary, then restart the transcript from it. The todo list rides along."""
        reply = sse.Reply()
        ask = self.messages + [{"role": "user", "content": context.SUMMARIZE}]
        async for event in sse.events(self.model.stream(self.system, ask, tools.SCHEMAS)):
            reply.feed(event)
        summary = " ".join(b["text"] for b in reply.content if b["type"] == "text").strip()
        files = sorted(str(p.relative_to(self.root.resolve())) for p in self.ctx.seen)
        restart = (f"{context.COMPACTED}\n\nWhat the user asked: {' / '.join(self.requests)}\n\n"
                   f"Summary:\n{summary}\n\nFiles you read or changed: {', '.join(files) or 'none'}\n"
                   f"Todo list:\n{context.render_todos(self.ctx.todos)}")
        self.messages = []
        self.log.append("rewind", at=0)
        self.add({"role": "user", "content": restart})
        self.ctx.seen.clear()

    def request_compact(self, _arg):
        self.compact_next = True
        return "the conversation will be compacted before the next model call"

    def show_context(self, _arg):
        parts = {"system prompt": context.estimate(self.system), "tool schemas": context.estimate(tools.SCHEMAS),
                 "messages": context.estimate(self.messages)}
        rows = [f"{name:<14}{n:>8,} tokens" for name, n in parts.items()]
        return "\n     ".join(rows + [f"{'total':<14}{sum(parts.values()):>8,} of {self.window:,}",
                                    f"cache: {self.cache[0]:,} tokens read, {self.cache[1]:,} written"])

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
        used = self.used()
        return (f"{self.gate.mode} mode · {self.model.name} · context {used / 1000:.1f}k/{self.window / 1000:.0f}k "
                f"({used * 100 // self.window}%) · ↑{self.tokens[0]:,} ↓{self.tokens[1]:,} · cache read {self.cache[0]:,}")

    def clear(self, _arg):
        self.messages.clear()
        self.ctx.seen.clear()
        self.log = session.Log(self.root)
        self.system = context.system_prompt(self.root, BASE)
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
