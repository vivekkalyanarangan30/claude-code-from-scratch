"""1.1 — The agent loop. Call the model, run what it asks for, send the results back, repeat."""
import tools
from common import Ask, Notice, Text, ToolCall, ToolResult

SYSTEM = """You are a coding agent. You work in the project folder; all paths are relative to it.
You change things only by calling tools. Writing code in a reply changes nothing.
Work like this, one tool call at a time:
1. Run the tests with Bash to see what fails.
2. Read the file that has the bug.
3. Call Write with the complete fixed file.
4. Run the tests again.
Reply in text only when the tests pass, in one short sentence."""
MAX_TURNS = 25
INPUT_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


class Agent:
    def __init__(self, model, root):
        self.model, self.root = model, root
        self.messages = []
        self.tokens = [0, 0]
        self.commands = {"clear": self.clear}

    async def turn(self, prompt):
        self.messages.append({"role": "user", "content": prompt})
        for _ in range(MAX_TURNS):
            reply = await self.model.complete(SYSTEM, self.messages, tools.SCHEMAS)
            self.tokens[0] += sum(reply["usage"].get(k, 0) for k in INPUT_KEYS)
            self.tokens[1] += reply["usage"]["output_tokens"]
            self.messages.append({"role": "assistant", "content": reply["content"]})

            results = []
            for block in reply["content"]:
                if block["type"] == "text":
                    yield Text(block["text"])
                if block["type"] != "tool_use":
                    continue
                yield ToolCall(block["id"], block["name"], block["input"])
                allowed = True
                if block["name"] in tools.NEEDS_APPROVAL:
                    ask = Ask(f"Allow {block['name']}?")
                    yield ask
                    allowed = await ask.answer != "no"
                output = await tools.run(self.root, block["name"], block["input"]) if allowed \
                    else "The user denied this tool call."
                yield ToolResult(block["id"], output)
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": output})

            if reply["stop_reason"] != "tool_use":
                return
            self.messages.append({"role": "user", "content": results})
        yield Notice(f"stopped after {MAX_TURNS} model calls")

    def status(self):
        return f"{self.model.name} · ↑{self.tokens[0]:,} ↓{self.tokens[1]:,} tokens · {len(self.messages)} messages"

    def clear(self, _arg):
        self.messages.clear()
        return "conversation cleared"
