"""0.1 — The screen with a puppet behind it. It looks like an agent; there is no model anywhere."""
import asyncio
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tui
from common import Ask, Text, ToolCall, ToolResult, sandbox


class Puppet:
    def __init__(self, root):
        self.root, self.turns = root, 0

    async def turn(self, prompt):
        self.turns += 1
        for word in f"You said {prompt!r}. I'm a puppet, so I'll do what I always do: list the files.".split(" "):
            yield Text(word + " ")
            await asyncio.sleep(0.05)
        yield ToolCall("puppet_1", "Bash", {"command": "ls -la"})
        ask = Ask("Run `ls -la`?")
        yield ask
        if await ask.answer == "no":
            yield ToolResult("puppet_1", "denied", is_error=True)
            return
        await asyncio.sleep(0.8)
        listing = subprocess.run(["ls", "-la"], cwd=self.root, capture_output=True, text=True).stdout
        yield ToolResult("puppet_1", listing)
        yield Text("\nThat's the whole act. Everything you just saw was five kinds of event.")

    def status(self):
        return f"puppet · {self.turns} turns"


if __name__ == "__main__":
    root = sandbox()
    tui.run(Puppet(root), "part 0 · a puppet", root, "none · it's a puppet")
