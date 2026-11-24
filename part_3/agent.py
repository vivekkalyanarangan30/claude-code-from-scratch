"""3.3 — The same loop, streamed: text is shown as it arrives, tool calls as soon as their block closes."""
import sse
import tools
from common import Ask, Notice, Text, Thinking, ToolCall, ToolResult

SYSTEM = """You are a coding agent. You work in the project folder; all paths are relative to it.
You change things only by calling tools. Writing code in a reply changes nothing.
To see what is in a file, Read it. Glob finds files by name; Grep searches inside files.
Read a file before you Edit it. In Edit, copy old_string exactly from Read's output, without the line numbers.
When a tool returns an error, read it and correct your next call.
Run the tests before you say you are done. Then reply in one short sentence."""
MAX_TURNS = 25


class Agent:
    def __init__(self, model, root):
        self.model, self.root = model, root
        self.ctx = tools.Context(root)
        self.messages = []
        self.tokens = [0, 0]
        self.commands = {"clear": self.clear}

    async def turn(self, prompt):
        self.messages.append({"role": "user", "content": prompt})
        for _ in range(MAX_TURNS):
            reply = sse.Reply()
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

            results = []
            for block in reply.content:
                if block["type"] != "tool_use":
                    continue
                output, is_error = await self.run_tool(block)
                if isinstance(output, Ask):
                    yield output
                    output, is_error = await self.run_tool(block, approved=await output.answer != "no")
                yield ToolResult(block["id"], output, is_error)
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": output,
                                **({"is_error": True} if is_error else {})})

            if reply.stop_reason != "tool_use":
                return
            self.messages.append({"role": "user", "content": results})
        yield Notice(f"stopped after {MAX_TURNS} model calls")

    async def run_tool(self, block, approved=None):
        """Return (output, is_error), or an Ask if the user must approve first."""
        if block["name"] in tools.NEEDS_APPROVAL and approved is None:
            return Ask(f"Allow {block['name']}?"), False
        if approved is False:
            return "The user denied this tool call.", True
        try:
            return await tools.run(self.ctx, block["name"], block["input"]), False
        except Exception as exc:
            return tools.describe(exc), True

    def status(self):
        return f"{self.model.name} · ↑{self.tokens[0]:,} ↓{self.tokens[1]:,} tokens · {len(self.messages)} messages"

    def clear(self, _arg):
        self.messages.clear()
        self.ctx.seen.clear()
        return "conversation cleared"
