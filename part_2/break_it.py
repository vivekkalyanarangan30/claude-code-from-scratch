"""2.2 — Part 1's agent asked for a file that doesn't exist. Watch what one missing file does to the session."""
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from agent import Agent
from common import ToolResult, drive, load_part, sandbox
from fake import FakeModel
from scripts import SCRIPTS

part_1 = load_part(1, "agent")


async def session(agent):
    for prompt in ("what's in config.py?", "ok, try settings.py"):
        print(f"> {prompt}")
        try:
            for event in await drive(agent, prompt):
                if isinstance(event, ToolResult):
                    print(f"  ⎿  {'error: ' if event.is_error else ''}{event.output.splitlines()[0]}")
            print(f"  ⏺ {agent.messages[-1]['content'][-1]['text']}")
        except Exception as exc:
            print(f"  ✗ {type(exc).__name__}: {exc}")


print("── Part 1: a tool error is an exception")
asyncio.run(session(part_1.Agent(FakeModel(SCRIPTS, delay=0), sandbox(reset=True))))
print("\nThe first error killed the turn before the tool_use got its tool_result, so every later\n"
      "request is rejected. The session is dead.\n")
print("── Part 2: a tool error is a result")
asyncio.run(session(Agent(FakeModel(SCRIPTS, delay=0), sandbox(reset=True))))
