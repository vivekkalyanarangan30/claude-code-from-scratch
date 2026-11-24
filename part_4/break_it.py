"""4.1 — Press Esc while a command runs. Part 3 kills only the shell, then leaves the tool call unanswered."""
import asyncio
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from agent import Agent
from common import Ask, drive, load_part, sandbox
from fake import FakeModel
from scripts import SCRIPTS

part_3 = load_part(3, "agent")


def sleepers():
    mine = ["pgrep", "-U", str(os.getuid()), "-f", "^sleep 8$"]
    return len(subprocess.run(mine, capture_output=True, text=True).stdout.split())


async def esc_during_the_command(agent):
    async def consume():
        async for event in agent.turn("run something slow"):
            if isinstance(event, Ask):
                event.answer.set_result("yes")
    task = asyncio.create_task(consume())
    await asyncio.sleep(1)
    pressed = time.perf_counter()
    task.cancel()
    await asyncio.sleep(0.2)
    print(f"  `sleep 8` still running after Esc: {sleepers()}")
    try:
        await task
    except asyncio.CancelledError:
        pass
    print(f"  the turn actually stopped {time.perf_counter() - pressed:.1f}s after Esc")
    try:
        await drive(agent, "are you still there?")
        print(f"  > are you still there?\n  ⏺ {agent.messages[-1]['content'][-1]['text']}")
    except Exception as exc:
        print(f"  > are you still there?\n  ✗ {exc}")


for label, cls in (("Part 3", part_3.Agent), ("Part 4", Agent)):
    print(f"── {label}: Esc one second into `sleep 8 && echo done`")
    asyncio.run(esc_during_the_command(cls(FakeModel(SCRIPTS, delay=0), sandbox(reset=True))))
print("\nKilling the shell leaves `sleep` running and holding the output pipe, so cleanup waits for it.\n"
      "Part 4 kills the whole process group, then answers the tool call it interrupted.")
