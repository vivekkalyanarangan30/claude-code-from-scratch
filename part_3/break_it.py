"""3.1 — Part 2 shows nothing until the whole reply exists. Time the first words, before and after streaming."""
import asyncio
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from agent import Agent
from common import Text, load_part, sandbox
from fake import FakeModel
from scripts import SCRIPTS

PROMPT = "explain the bug in cart.py"
part_2 = load_part(2, "agent")


async def first_words(agent):
    start = time.perf_counter()
    first = None
    async for event in agent.turn(PROMPT):
        if isinstance(event, Text) and first is None:
            first = time.perf_counter() - start
    return first, time.perf_counter() - start


for label, cls in (("Part 2, one reply", part_2.Agent), ("Part 3, streamed ", Agent)):
    first, total = asyncio.run(first_words(cls(FakeModel(SCRIPTS, delay=0.3), sandbox(reset=True))))
    print(f"{label}: first words after {first:.1f}s, turn done after {total:.1f}s")
print("\nSame model, same speed. Streaming doesn't make the model faster; it stops the screen looking frozen.")
